"""
Conector CVM — dados abertos oficiais (gratuitos).

Fontes usadas:
  - INFORME DIÁRIO  (inf_diario_fi_AAAAMM.zip)  -> PL, captação, resgate, cotistas
  - CADASTRO        (cad_fi.csv)                -> CNPJ, nome, gestor, classe
  - CDA (opcional)  (cda_fi_AAAAMM.zip)         -> composição p/ classificar indexador

Estratégia:
  * Baixa os últimos N meses de informe diário e monta as janelas
    (diária/semanal/mensal/semestral) por CNPJ.
  * Junta com o cadastro para ter gestor e nome.
  * A composição por indexador (pct_lf/ipca/cdi) idealmente vem do Quantum
    (mais fresca) ou do CDA. Aqui deixamos um gancho: se o CDA não estiver
    disponível, o fundo entra com composição neutra e cai em MISTO até ser
    enriquecido pelo Quantum.

IMPORTANTE: este conector faz requisições de rede. Em ambiente sem acesso a
dados.cvm.gov.br ele falha graciosamente e o app pode cair no mock.
"""
from __future__ import annotations

import io
import zipfile
from datetime import date, timedelta

import pandas as pd
import requests

from app.config import CACHE_DIR, settings
from app.connectors.base import DataConnector


class CVMConnector(DataConnector):
    name = "cvm"

    def __init__(self) -> None:
        self.base = settings.CVM_BASE_URL.rstrip("/")
        self.meses = settings.CVM_MESES_INFORME

    # ---------- utilidades ----------
    def _meses_recentes(self, n: int) -> list[str]:
        """Retorna ['AAAAMM', ...] dos últimos n meses, do mais antigo ao mais novo."""
        hoje = date.today().replace(day=1)
        meses = []
        cur = hoje
        for _ in range(n):
            meses.append(cur.strftime("%Y%m"))
            # volta um mês
            cur = (cur - timedelta(days=1)).replace(day=1)
        return list(reversed(meses))

    def _baixar_zip_csv(self, url: str, csv_prefix: str) -> pd.DataFrame:
        """Baixa um .zip da CVM e concatena os CSVs internos que começam com csv_prefix."""
        cache_file = CACHE_DIR / (url.split("/")[-1] + ".parquet")
        if cache_file.exists():
            return pd.read_parquet(cache_file)

        resp = requests.get(url, timeout=120)
        resp.raise_for_status()
        frames = []
        with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
            for nome in zf.namelist():
                if nome.lower().endswith(".csv") and nome.lower().startswith(csv_prefix.lower()):
                    with zf.open(nome) as fh:
                        frames.append(pd.read_csv(fh, sep=";", encoding="latin-1", low_memory=False))
        if not frames:
            # alguns meses vêm com CSV único sem prefixo
            with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
                for nome in zf.namelist():
                    if nome.lower().endswith(".csv"):
                        with zf.open(nome) as fh:
                            frames.append(pd.read_csv(fh, sep=";", encoding="latin-1", low_memory=False))
        df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
        try:
            df.to_parquet(cache_file)
        except Exception:
            pass  # cache é best-effort
        return df

    def _baixar_cadastro(self) -> pd.DataFrame:
        url = "https://dados.cvm.gov.br/dados/FI/CAD/DADOS/cad_fi.csv"
        cache_file = CACHE_DIR / "cad_fi.parquet"
        if cache_file.exists():
            return pd.read_parquet(cache_file)
        resp = requests.get(url, timeout=120)
        resp.raise_for_status()
        df = pd.read_csv(io.BytesIO(resp.content), sep=";", encoding="latin-1", low_memory=False)
        try:
            df.to_parquet(cache_file)
        except Exception:
            pass
        return df

    def _baixar_cda(self) -> dict:
        """
        Baixa os últimos meses de CDA (até 3, para não pesar demais).
        Retorna um dicionário {cnpj: {"pct_lf": X, "pct_ipca": Y, "pct_cdi": Z}}.
        A classificação por indexador é feita via aproximação textual nas colunas descritivas.
        """
        meses = self._meses_recentes(min(self.meses, 3))
        frames = []
        for aaaamm in meses:
            url = f"{self.base}/CDA/DADOS/cda_fi_{aaaamm}.zip"
            cache_file = CACHE_DIR / f"cda_fi_{aaaamm}.parquet"
            if cache_file.exists():
                try:
                    frames.append(pd.read_parquet(cache_file))
                    continue
                except Exception:
                    pass

            try:
                resp = requests.get(url, timeout=120)
                resp.raise_for_status()
                mes_frames = []
                with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
                    for nome in zf.namelist():
                        if nome.lower().startswith("cda_fi_blc_") and nome.lower().endswith(".csv"):
                            with zf.open(nome) as fh:
                                df = pd.read_csv(fh, sep=";", encoding="latin-1", low_memory=False)
                                # Garantir que as colunas essenciais existam
                                if "CNPJ_FUNDO_CLASSE" in df.columns and "VL_MERC_POS_FINAL" in df.columns:
                                    # Manter apenas as colunas que podem conter as strings de proxy e as vitais
                                    str_cols = [c for c in df.columns if c in ["TP_APLIC", "TP_ATIVO", "DS_ATIVO", "DENOM_SOCIAL"]]
                                    df_sub = df[["CNPJ_FUNDO_CLASSE", "VL_MERC_POS_FINAL"] + str_cols].copy()
                                    
                                    # Unifica as colunas descritivas em uma só string concatenada para facilitar busca
                                    df_sub["desc_concat"] = df_sub[str_cols].fillna("").agg(" ".join, axis=1).str.upper()
                                    mes_frames.append(df_sub[["CNPJ_FUNDO_CLASSE", "VL_MERC_POS_FINAL", "desc_concat"]])
                
                if mes_frames:
                    df_mes = pd.concat(mes_frames, ignore_index=True)
                    frames.append(df_mes)
                    try:
                        df_mes.to_parquet(cache_file)
                    except Exception:
                        pass
            except Exception as e:
                print(f"[CVM] falha ao baixar CDA {aaaamm}: {e}")

        if not frames:
            return {}

        cda = pd.concat(frames, ignore_index=True)
        cda["VL_MERC_POS_FINAL"] = pd.to_numeric(cda["VL_MERC_POS_FINAL"], errors="coerce").fillna(0.0)
        cda.rename(columns={"CNPJ_FUNDO_CLASSE": "cnpj"}, inplace=True)
        
        # Manter apenas o dado do mês mais recente processado (ou soma de todas as janelas que capturamos)
        # O ideal é usar apenas o mais recente disponível por CNPJ. Aqui faremos agrupamento pela soma 
        # geral pois pegamos poucos meses, ou melhor ainda, agregamos por fundo na totalidade baixada.
        
        def classificar_cda_row(texto):
            # 1) LF
            if "LETRA FINANCEIRA" in texto or " LF " in texto:
                return "lf"
            # 2) IPCA (NTN-B, debênture IPCA, CRI, CRA)
            if "IPCA" in texto or "NTN-B" in texto or "CRI " in texto or "CRA " in texto or "CERTIFICADO DE RECEBIVEIS" in texto:
                return "ipca"
            # 3) CDI (CDB, FIDC, deb CDI/DI, operacao compromissada)
            if "CDI" in texto or " DI " in texto or "CDB" in texto or "FIDC" in texto or "COMPROMISSADA" in texto or "DEBENTURE" in texto:
                # Debêntures genéricas sem 'IPCA' no nome são assumidas como CDI (comum no mercado)
                return "cdi"
            return "outros"

        cda["classe"] = cda["desc_concat"].apply(classificar_cda_row)
        
        agrupado = cda.groupby(["cnpj", "classe"])["VL_MERC_POS_FINAL"].sum().unstack(fill_value=0.0)
        
        res = {}
        for cnpj, row in agrupado.iterrows():
            total = row.sum()
            if total > 0:
                res[cnpj] = {
                    "pct_lf": float(row.get("lf", 0.0) / total),
                    "pct_ipca": float(row.get("ipca", 0.0) / total),
                    "pct_cdi": float(row.get("cdi", 0.0) / total),
                }
            else:
                res[cnpj] = {"pct_lf": 0.0, "pct_ipca": 0.0, "pct_cdi": 0.0}
                
        return res

    # ---------- pipeline principal ----------
    def carregar_fundos(self) -> list[dict]:
        meses = self._meses_recentes(self.meses)

        # 1) Informe diário — empilha todos os meses.
        informes = []
        for aaaamm in meses:
            url = f"{self.base}/INF_DIARIO/DADOS/inf_diario_fi_{aaaamm}.zip"
            try:
                dfm = self._baixar_zip_csv(url, "inf_diario_fi")
                informes.append(dfm)
            except Exception as e:  # noqa: BLE001
                print(f"[CVM] falha ao baixar informe {aaaamm}: {e}")
        if not informes:
            raise RuntimeError("Nenhum informe diário da CVM foi baixado.")

        inf = pd.concat(informes, ignore_index=True)
        # Normaliza nomes de coluna (a CVM mudou o layout ao longo do tempo)
        col_cnpj = _first_col(inf, ["CNPJ_FUNDO", "CNPJ_FUNDO_CLASSE"])
        col_data = _first_col(inf, ["DT_COMPTC"])
        col_capt = _first_col(inf, ["CAPTC_DIA"])
        col_resg = _first_col(inf, ["RESG_DIA"])
        col_pl = _first_col(inf, ["VL_PATRIM_LIQ"])

        inf = inf[[c for c in [col_cnpj, col_data, col_capt, col_resg, col_pl] if c]].copy()
        inf.columns = ["cnpj", "data", "captacao", "resgate", "pl"][: len(inf.columns)]
        inf["data"] = pd.to_datetime(inf["data"], errors="coerce")
        inf["fluxo"] = inf["captacao"].fillna(0) - inf["resgate"].fillna(0)

        # 2) Monta janelas por CNPJ relativas à data mais recente disponível.
        ref = inf["data"].max()
        janelas = {
            "diaria": ref - timedelta(days=1),
            "semanal": ref - timedelta(days=7),
            "mensal": ref - timedelta(days=30),
            "semestral": ref - timedelta(days=182),
        }
        agg = {}
        for nome_j, corte in janelas.items():
            sub = inf[inf["data"] > corte]
            agg[nome_j] = sub.groupby("cnpj")["fluxo"].sum()

        pl_ult = inf.sort_values("data").groupby("cnpj")["pl"].last()
        
        corte_2w = ref - timedelta(days=14)
        pl_2semanas = inf[inf["data"] <= corte_2w].sort_values("data").groupby("cnpj")["pl"].last()
        
        # Agrupa PL por semana para a série temporal
        inf["semana"] = inf["data"].dt.to_period("W").apply(lambda r: r.start_time.strftime("%Y-%m-%d"))
        historico_pl = inf.groupby(["cnpj", "semana"])["pl"].last().unstack(fill_value=0.0)

        # 3) Cadastro para gestor/nome.
        try:
            cad = self._baixar_cadastro()
            col_c_cnpj = _first_col(cad, ["CNPJ_FUNDO", "CNPJ_FUNDO_CLASSE"])
            col_gestor = _first_col(cad, ["GESTOR"])
            col_nome = _first_col(cad, ["DENOM_SOCIAL"])
            cad = cad[[c for c in [col_c_cnpj, col_gestor, col_nome] if c]].copy()
            cad.columns = ["cnpj", "gestora", "nome"][: len(cad.columns)]
            cad = cad.drop_duplicates("cnpj").set_index("cnpj")
        except Exception as e:  # noqa: BLE001
            print(f"[CVM] falha no cadastro: {e}")
            cad = pd.DataFrame(columns=["gestora", "nome"])

        # 4) Composicao via CDA
        comp_cda = self._baixar_cda()

        # 5) Consolida por CNPJ.
        cnpjs = set(pl_ult.index) | set().union(*[set(s.index) for s in agg.values()])
        fundos: list[dict] = []
        for cnpj in cnpjs:
            pl = float(pl_ult.get(cnpj, 0.0) or 0.0)
            semanal = float(agg["semanal"].get(cnpj, 0.0))
            info = cad.loc[cnpj] if cnpj in cad.index else None
            
            cda_f = comp_cda.get(cnpj, {"pct_lf": 0.0, "pct_ipca": 0.0, "pct_cdi": 0.0})

            fundos.append({
                "cnpj": cnpj,
                "nome": (info["nome"] if info is not None else cnpj),
                "gestora": (info["gestora"] if info is not None and pd.notna(info["gestora"]) else "Não classificado"),
                "diaria": float(agg["diaria"].get(cnpj, 0.0)),
                "semanal": semanal,
                "mensal": float(agg["mensal"].get(cnpj, 0.0)),
                "semestral": float(agg["semestral"].get(cnpj, 0.0)),
                "pl": pl,
                "pl_2semanas": float(pl_2semanas.get(cnpj, pl)),
                "historico_pl": historico_pl.loc[cnpj].to_dict() if cnpj in historico_pl.index else {},
                # Composição inicial preenchida via CDA
                "pct_lf": cda_f["pct_lf"],
                "pct_ipca": cda_f["pct_ipca"],
                "pct_cdi": cda_f["pct_cdi"],
                "duration": None,
                "cotizacao_resgate": None,
                "taxa_adm": None,
                "aberto_captacao": True,
                "resgate_pct_pl_semana": (semanal / pl if pl > 0 else 0.0),
            })
        return fundos


def _first_col(df: pd.DataFrame, candidates: list[str]) -> str | None:
    for c in candidates:
        if c in df.columns:
            return c
    return None
