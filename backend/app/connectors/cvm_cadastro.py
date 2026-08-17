"""
Cadastro de fundos da CVM — `registro_fundo_classe.zip` (dados abertos).

>>> POR QUE NÃO É O `cad_fi.csv`:
    Depois da Resolução CVM 175 o cadastro vivo migrou. Hoje o `cad_fi.csv`
    tem 21 fundos "EM FUNCIONAMENTO NORMAL" contra 46.572 cancelados — é o
    acervo legado. O registro atual está neste ZIP (~7 MB), com 34.250 fundos
    e 33.583 classes ativas.

O ZIP traz três CSVs; usamos dois:

    registro_fundo.csv   -> CNPJ_Fundo, Gestor, Administrador, Situacao
    registro_classe.csv  -> CNPJ_Classe, Patrimonio_Liquido, Forma_Condominio,
                            Classificacao_Anbima, Situacao
                            (ligado ao fundo por ID_Registro_Fundo)

O PL mora na CLASSE, não no fundo (33.026 classes com PL, contra 25.725
fundos). Como a planilha do Quantum traz o CNPJ do FUNDO, fazemos a ponte
fundo -> classe. Conferido no arquivo de 14/08/2026: dos 3.436 fundos da
planilha ativos na CVM, 100% têm exatamente uma classe ativa — a ponte não
gera ambiguidade.

`Data_Patrimonio_Liquido` costuma ser D-1 (mediana 11/08 num arquivo de 14/08),
então o PL é fresco — bem melhor que reconstruir PL pelo informe diário.
"""
from __future__ import annotations

import io
import logging
import time
import zipfile
from datetime import date, datetime, timedelta

import pandas as pd
import requests

from app.config import CACHE_DIR, settings
from app.connectors import cvm_carteira, cvm_documentos
from app.utils import so_digitos

logger = logging.getLogger("cvm_cadastro")

URL_ZIP = "https://dados.cvm.gov.br/dados/FI/CAD/DADOS/registro_fundo_classe.zip"
URL_INFORME = (
    "https://dados.cvm.gov.br/dados/FI/DOC/INF_DIARIO/DADOS/inf_diario_fi_{aaaamm}.zip"
)
ATIVO = "Em Funcionamento Normal"

_CACHE_FUNDOS = CACHE_DIR / "cvm_registro_fundos.parquet"
_CACHE_CLASSES = CACHE_DIR / "cvm_registro_classes.parquet"
# O sufixo é a versão do schema: quando as colunas mudam, o arquivo antigo
# simplesmente deixa de ser encontrado, em vez de ser lido com colunas a menos.
_CACHE_INFORME = CACHE_DIR / "cvm_metricas_diarias_v3.parquet"


def _cache_fresco() -> bool:
    if not (_CACHE_FUNDOS.exists() and _CACHE_CLASSES.exists()):
        return False
    idade_h = (time.time() - _CACHE_FUNDOS.stat().st_mtime) / 3600
    return idade_h < settings.CVM_CADASTRO_TTL_HORAS


def _baixar() -> tuple[pd.DataFrame, pd.DataFrame]:
    """Baixa o ZIP e devolve (fundos, classes), com cache em parquet."""
    if _cache_fresco():
        logger.info("Cadastro da CVM: usando cache local.")
        return pd.read_parquet(_CACHE_FUNDOS), pd.read_parquet(_CACHE_CLASSES)

    logger.info("Baixando cadastro da CVM (%s)…", URL_ZIP)
    resp = requests.get(URL_ZIP, timeout=settings.CVM_TIMEOUT_S)
    resp.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(resp.content)) as z:
        fundos = pd.read_csv(
            z.open("registro_fundo.csv"), sep=";", encoding="latin-1", low_memory=False,
            usecols=["ID_Registro_Fundo", "CNPJ_Fundo", "Denominacao_Social",
                     "Situacao", "Gestor", "Administrador"],
        )
        classes = pd.read_csv(
            z.open("registro_classe.csv"), sep=";", encoding="latin-1", low_memory=False,
            usecols=["ID_Registro_Fundo", "CNPJ_Classe", "Denominacao_Social",
                     "Situacao", "Patrimonio_Liquido", "Data_Patrimonio_Liquido",
                     "Forma_Condominio", "Classificacao", "Classificacao_Anbima"],
        )

    for df, caminho in ((fundos, _CACHE_FUNDOS), (classes, _CACHE_CLASSES)):
        try:
            df.to_parquet(caminho)
        except Exception as e:  # noqa: BLE001 — cache é best-effort
            logger.debug("Não consegui gravar %s: %s", caminho.name, e)

    logger.info("Cadastro da CVM: %d fundos, %d classes.", len(fundos), len(classes))
    return fundos, classes


def carregar(apenas_ativos: bool = True) -> pd.DataFrame:
    """Uma linha por CNPJ de fundo, já com o PL da classe correspondente.

    Colunas: cnpj (dígitos), nome, gestor, administrador, situacao, pl,
    pl_data, forma_condominio, classificacao, classificacao_anbima, cnpj_classe.
    """
    fundos, classes = _baixar()

    if apenas_ativos:
        classes = classes[classes["Situacao"] == ATIVO]
    classes = classes.copy()
    classes["pl"] = pd.to_numeric(classes["Patrimonio_Liquido"], errors="coerce")

    # Fundo com várias classes ativas é raro (21 em 33.546) e nenhum deles
    # aparece na planilha. Somamos o PL das classes e marcamos o caso.
    agg = classes.groupby("ID_Registro_Fundo").agg(
        pl=("pl", "sum"),
        classes=("pl", "size"),
        pl_data=("Data_Patrimonio_Liquido", "first"),
        forma_condominio=("Forma_Condominio", "first"),
        classificacao=("Classificacao", "first"),
        classificacao_anbima=("Classificacao_Anbima", "first"),
        cnpj_classe=("CNPJ_Classe", "first"),
    )

    out = fundos.copy()
    out["cnpj"] = out["CNPJ_Fundo"].map(so_digitos)
    out = out[out["cnpj"].notna()]
    # Situação ativa primeiro: se o mesmo CNPJ aparecer duas vezes, fica a viva.
    out["_ordem"] = (out["Situacao"] != ATIVO).astype(int)
    out = out.sort_values("_ordem").drop_duplicates("cnpj")

    out = out.join(agg, on="ID_Registro_Fundo")
    out = out.rename(columns={
        "Denominacao_Social": "nome",
        "Situacao": "situacao",
        "Gestor": "gestor",
        "Administrador": "administrador",
    })
    out["cnpj_classe"] = out["cnpj_classe"].map(so_digitos)
    return out.set_index("cnpj")[[
        "nome", "gestor", "administrador", "situacao", "pl", "classes",
        "pl_data", "forma_condominio", "classificacao", "classificacao_anbima",
        "cnpj_classe",
    ]]


def _meses_recentes(n: int) -> list[str]:
    cur = date.today().replace(day=1)
    out = []
    for _ in range(n):
        out.append(cur.strftime("%Y%m"))
        cur = (cur - timedelta(days=1)).replace(day=1)
    return out


def metricas_diarias() -> pd.DataFrame:
    """Métricas por CNPJ derivadas do INFORME DIÁRIO.

    Devolve, indexado pelo CNPJ (só dígitos):
        pl, pl_data            PL do último dia em que o fundo declarou
        rentab_pct, rentab_dias  variação da cota no período baixado
        vol_pct                volatilidade anualizada da cota
        cotistas, cotistas_var_pct

    Por que o informe é a fonte primária de PL, à frente do registro:

      * cobertura maior — 79,6% dos fundos da planilha, contra 73,3% do
        registro; juntos chegam a 80,3%;
      * mais fresco — é declaração diária (D-1), enquanto o PL do registro tem
        data própria que às vezes atrasa meses;
      * só fundo vivo declara, então cancelado se resolve sozinho.

    Os dois concordam: nos 3.345 fundos em que ambos têm valor, a diferença
    mediana é de 0,08%.

    O informe usa `CNPJ_FUNDO_CLASSE`, que para fundo de classe única costuma
    ser o mesmo CNPJ que a planilha traz. Nem todo dia útil tem todos os
    fundos, então o PL de cada CNPJ vem da última data em que *ele* apareceu.
    """
    if _CACHE_INFORME.exists():
        idade_h = (time.time() - _CACHE_INFORME.stat().st_mtime) / 3600
        if idade_h < settings.CVM_INFORME_TTL_HORAS:
            logger.info("Métricas diárias da CVM: usando cache local.")
            return pd.read_parquet(_CACHE_INFORME)

    frames = []
    for aaaamm in _meses_recentes(settings.CVM_INFORME_MESES):
        try:
            resp = requests.get(URL_INFORME.format(aaaamm=aaaamm),
                                timeout=settings.CVM_TIMEOUT_S)
            resp.raise_for_status()
            with zipfile.ZipFile(io.BytesIO(resp.content)) as z:
                for nome in z.namelist():
                    if not nome.lower().endswith(".csv"):
                        continue
                    frames.append(pd.read_csv(
                        z.open(nome), sep=";", encoding="latin-1", low_memory=False,
                        usecols=["CNPJ_FUNDO_CLASSE", "DT_COMPTC", "VL_PATRIM_LIQ",
                                 "VL_QUOTA", "NR_COTST"],
                    ))
        except Exception as e:  # noqa: BLE001 — mês faltando não é fatal
            logger.warning("Informe diário %s indisponível: %s", aaaamm, e)

    if not frames:
        return _vazio_diario()

    inf = pd.concat(frames, ignore_index=True)
    inf["cnpj"] = inf["CNPJ_FUNDO_CLASSE"].map(so_digitos)
    inf["pl"] = pd.to_numeric(inf["VL_PATRIM_LIQ"], errors="coerce")
    inf["cota"] = pd.to_numeric(inf["VL_QUOTA"], errors="coerce")
    inf["data"] = pd.to_datetime(inf["DT_COMPTC"], errors="coerce")
    inf = inf[inf["cnpj"].notna() & inf["data"].notna()]
    inf = inf.sort_values("data")

    # --- PL: último dia declarado por CNPJ ---
    com_pl = inf[inf["pl"].notna()]
    ultima = com_pl.groupby("cnpj")["data"].transform("max")
    # Soma por segurança: se um dia trouxer subclasses separadas, elas somam.
    pl = com_pl[com_pl["data"] == ultima].groupby("cnpj").agg(
        pl=("pl", "sum"), pl_data=("data", "first"))

    # --- PL de ~30 dias atrás, para a variação de PL das gestoras ---
    corte = com_pl["data"].max() - timedelta(days=settings.CVM_PL_VAR_DIAS)
    antes = com_pl[com_pl["data"] <= corte]
    if not antes.empty:
        ult_antes = antes.groupby("cnpj")["data"].transform("max")
        pl["pl_anterior"] = (
            antes[antes["data"] == ult_antes].groupby("cnpj")["pl"].sum()
        )
    else:
        pl["pl_anterior"] = pd.NA

    # --- Cota: rentabilidade e volatilidade da série ---
    cotas = inf[inf["cota"].notna() & (inf["cota"] > 0)]
    g = cotas.groupby("cnpj")
    serie = pd.DataFrame({
        "cota_ini": g["cota"].first(),
        "cota_fim": g["cota"].last(),
        "d_ini": g["data"].first(),
        "d_fim": g["data"].last(),
        "pontos": g["cota"].size(),
    })
    serie["rentab_dias"] = (serie["d_fim"] - serie["d_ini"]).dt.days
    serie["rentab_pct"] = (serie["cota_fim"] / serie["cota_ini"] - 1) * 100
    # Série curta demais não descreve retorno — melhor não publicar nada.
    curto = serie["rentab_dias"] < settings.CVM_RENTAB_DIAS_MIN
    serie.loc[curto, ["rentab_pct", "rentab_dias"]] = pd.NA

    vol = g["cota"].apply(lambda s: s.pct_change().std() * (252 ** 0.5) * 100)
    serie["vol_pct"] = vol.where(~curto)

    # --- Cotistas: nível atual e variação no período ---
    cot = inf[inf["NR_COTST"].notna()]
    gc = cot.groupby("cnpj")["NR_COTST"]
    cotistas = pd.DataFrame({"cotistas": gc.last(), "_ini": gc.first()})
    cotistas["cotistas_var_pct"] = (
        (cotistas["cotistas"] / cotistas["_ini"] - 1) * 100
    ).where(cotistas["_ini"] > 0)

    out = pl.join(serie[["rentab_pct", "rentab_dias", "vol_pct"]], how="outer")
    out = out.join(cotistas[["cotistas", "cotistas_var_pct"]], how="outer")
    out = out.astype({"pl": "float64"}, errors="ignore")

    try:
        out.to_parquet(_CACHE_INFORME)
    except Exception as e:  # noqa: BLE001
        logger.debug("Não consegui gravar %s: %s", _CACHE_INFORME.name, e)

    logger.info(
        "Informe diário: %d CNPJs | PL até %s | rentabilidade para %d | cotistas para %d",
        len(out), out["pl_data"].max(), int(out["rentab_pct"].notna().sum()),
        int(out["cotistas"].notna().sum()),
    )
    return out


def _vazio_diario() -> pd.DataFrame:
    cols = ["pl", "pl_data", "pl_anterior", "rentab_pct", "rentab_dias",
            "vol_pct", "cotistas", "cotistas_var_pct"]
    return pd.DataFrame(columns=cols).set_index(pd.Index([], name="cnpj"))


class CVMCadastroEnricher:
    """Preenche o PL (e o cadastro oficial) casando pelo CNPJ do fundo.

    É o segundo enriquecedor do pipeline, ao lado do Quantum: a CVM entrega PL
    e cadastro; o Quantum entrega composição, duration, cotização e taxa.
    Nenhum dos dois inventa o que não tem.

    >>> O QUE NÃO FAZEMOS AQUI:
        `Forma_Condominio` (Aberto/Fechado) NÃO vira `aberto_captacao`. São
        coisas diferentes: condomínio aberto é a natureza jurídica do fundo;
        "aberto para captação" é o fundo estar aceitando aplicação hoje. Um
        fundo de condomínio aberto e soft-closed é comum. Quem responde isso é
        o Quantum — até lá o campo fica vazio.
    """

    name = "cvm_cadastro"

    def __init__(self) -> None:
        # O mock existe para rodar offline e dar sempre o mesmo resultado;
        # buscar PL na rede quebraria as duas promessas.
        self.enabled = settings.CVM_ENRIQUECER and settings.DATA_SOURCE != "mock"
        self.stats: dict = {}
        self._extrato: pd.DataFrame | None = None
        self._perfil: pd.DataFrame | None = None
        self._lamina: pd.DataFrame | None = None
        self._carteira: pd.DataFrame | None = None

    def disponivel(self) -> bool:
        return self.enabled

    def enriquecer_lote(self, fundos: list[dict]) -> list[dict]:
        self.stats = {
            "tentados": 0, "no_cadastro": 0, "com_pl": 0, "pl_informe": 0,
            "pl_registro": 0, "sem_pl": 0, "subclasses_sem_pl": 0,
            "pl_nao_credivel": 0, "com_extrato": 0, "com_perfil": 0,
            "com_carteira": 0,
        }
        if not self.enabled:
            return fundos

        if not any(f.get("cnpj") for f in fundos):
            logger.info("Nenhum fundo com CNPJ — enriquecimento da CVM ignorado.")
            return fundos

        cad, diario = self._fontes()
        self._extrato = _tenta(cvm_documentos.extrato, "EXTRATO")
        self._perfil = _tenta(cvm_documentos.perfil_mensal, "PERFIL")
        self._lamina = _tenta(cvm_documentos.lamina, "LAMINA")
        self._carteira = _tenta(cvm_carteira.carregar, "CDA (carteira)")
        if cad is None and diario is None:
            return fundos

        for f in fundos:
            cnpj = f.get("cnpj")
            if not cnpj:
                continue
            self.stats["tentados"] += 1

            # 1) Cadastro: nome oficial, gestor, administrador, classificação.
            if cad is not None and cnpj in cad.index:
                linha = cad.loc[cnpj]
                self.stats["no_cadastro"] += 1
                f["cvm_situacao"] = linha["situacao"]
                f["gestor_cvm"] = _texto(linha["gestor"])
                f["administrador"] = _texto(linha["administrador"])
                f["classificacao_anbima"] = _texto(linha["classificacao_anbima"])
                f["forma_condominio"] = _texto(linha["forma_condominio"])

            # 2) Documentos periódicos: taxa, cotização, público-alvo, perfil.
            self._aplicar_documentos(f, cnpj)

            # 3) Métricas de série (rentabilidade, volatilidade, cotistas).
            if diario is not None and cnpj in diario.index:
                linha = diario.loc[cnpj]
                f["rentab_pct"] = _float(linha.get("rentab_pct"))
                f["rentab_dias"] = _int(linha.get("rentab_dias"))
                f["vol_pct"] = _float(linha.get("vol_pct"))
                f["cotistas"] = _int(linha.get("cotistas"))
                f["cotistas_var_pct"] = _float(linha.get("cotistas_var_pct"))

            # 4) PL, uma vez por CNPJ. Subclasse não recebe: o PL do fundo já
            # foi creditado à linha da classe-mãe.
            if not f.get("primeira_do_cnpj", True):
                self.stats["subclasses_sem_pl"] += 1
                continue

            pl, pl_data, origem = self._melhor_pl(cnpj, cad, diario)
            if pl is None:
                self.stats["sem_pl"] += 1
                continue

            f["pl"] = pl
            f["pl_data"] = pl_data
            f["pl_fonte"] = origem
            if diario is not None and cnpj in diario.index:
                f["pl_anterior"] = _float(diario.at[cnpj, "pl_anterior"])
            self.stats["com_pl"] += 1
            self.stats[f"pl_{origem}"] += 1
            f["resgate_pct_pl_semana"] = (f.get("semanal") or 0.0) / pl

        logger.info(
            "CVM: %d CNPJs | %d no cadastro | PL para %d (%d do informe diário, "
            "%d do registro) | sem PL: %d sem fonte, %d subclasses, %d não crível",
            self.stats["tentados"], self.stats["no_cadastro"], self.stats["com_pl"],
            self.stats["pl_informe"], self.stats["pl_registro"],
            self.stats["sem_pl"], self.stats["subclasses_sem_pl"],
            self.stats["pl_nao_credivel"],
        )
        return fundos

    # ---------- internos ----------
    def _fontes(self) -> tuple[pd.DataFrame | None, pd.DataFrame | None]:
        """Carrega cadastro e informe. Sem rede, o app segue — só sem PL."""
        cad = diario = None
        try:
            cad = carregar(apenas_ativos=False)
        except Exception as e:  # noqa: BLE001
            logger.warning("Cadastro da CVM indisponível (%s).", e)
        try:
            diario = metricas_diarias()
            if diario.empty:
                diario = None
        except Exception as e:  # noqa: BLE001
            logger.warning("Informe diário da CVM indisponível (%s).", e)
        if cad is None and diario is None:
            logger.warning("Nenhuma fonte de PL disponível — seguindo sem PL.")
        return cad, diario

    def _aplicar_documentos(self, f: dict, cnpj: str) -> None:
        """Copia EXTRATO e PERFIL para o fundo, quando existirem."""
        if self._extrato is not None and cnpj in self._extrato.index:
            linha = self._extrato.loc[cnpj]
            f["taxa_adm"] = _float(linha.get("taxa_adm"))
            f["taxa_perfm"] = _float(linha.get("taxa_perfm"))
            # Cotização é o prazo de conversão; se faltar, o de pagamento serve
            # de aproximação — é o que o cotista sente na prática.
            f["cotizacao_resgate"] = (
                _int(linha.get("cotizacao_resgate"))
                or _int(linha.get("pagamento_resgate"))
            )
            f["pagamento_resgate"] = _int(linha.get("pagamento_resgate"))
            f["aplicacao_minima"] = _float(linha.get("aplicacao_minima"))
            f["publico_alvo"] = _texto(linha.get("publico_alvo"))
            f["credito_privado"] = bool(linha.get("credito_privado"))
            f["indice_taxa_perfm"] = _texto(linha.get("indice_taxa_perfm"))
            f["extrato_data"] = _texto(linha.get("extrato_data"))
            self.stats["com_extrato"] += 1

        if self._lamina is not None and cnpj in self._lamina.index:
            f["indice_referencia"] = _texto(self._lamina.at[cnpj, "indice_referencia"])

        if self._perfil is not None and cnpj in self._perfil.index:
            linha = self._perfil.loc[cnpj]
            f["pct_credito_privado"] = _float(linha.get("pct_credito_privado"))
            f["prazo_carteira_dias"] = _float(linha.get("prazo_carteira_dias"))
            f["conc_maior_cotista_pct"] = _float(linha.get("conc_maior_cotista_pct"))
            f["perfil_data"] = _texto(linha.get("perfil_data"))
            f["perfil_cotistas"] = {
                nome: v
                for col, nome in cvm_documentos.COTISTAS.items()
                if (v := _float(linha.get(f"perfil_{nome}")))
            }
            self.stats["com_perfil"] += 1

        # Composição da carteira -> é o que vira `bucket`. Vai para TODAS as
        # linhas do CNPJ, inclusive subclasses: elas dividem a mesma carteira,
        # e bucket é categoria, não valor somável (o mix pesa por PL, que a
        # subclasse não tem — então não há dupla contagem).
        if self._carteira is not None and cnpj in self._carteira.index:
            linha = self._carteira.loc[cnpj]
            # O motivo vem sempre: é o que explica na tela por que este fundo
            # apareceu no CDA e mesmo assim ficou sem bucket.
            f["carteira_motivo"] = _texto(linha.get("carteira_motivo"))
            if f["carteira_motivo"]:
                return
            for campo in ("pct_lf", "pct_ipca", "pct_cdi", "pct_pre",
                          "pct_debenture", "pct_cdb", "pct_cri_cra",
                          "carteira_credito", "carteira_pct_pl",
                          "carteira_idx_conhecido_pct", "carteira_sigilo_pct",
                          # Perna de hedge da incentivada: o classificador
                          # compara com HEDGE_DAP_MINIMO a cada reclassificação.
                          "dap_nocional", "dap_cobertura"):
                f[campo] = _float(linha.get(campo))
            f["carteira_data"] = _texto(linha.get("carteira_data"))
            self.stats["com_carteira"] += 1

    def _melhor_pl(self, cnpj, cad, diario) -> tuple[float | None, str | None, str | None]:
        """Informe diário primeiro (mais fresco e mais amplo), registro depois.

        Um PL abaixo do piso é descartado em vez de aceito: o registro tem
        placeholders de R$ 1 e PL negativo que, divididos pelo fluxo, virariam
        "-743.567.420% do PL" na tela de estresse.
        """
        candidatos = []
        if diario is not None and cnpj in diario.index:
            candidatos.append(("informe", diario.at[cnpj, "pl"], diario.at[cnpj, "pl_data"]))
        if cad is not None and cnpj in cad.index:
            linha = cad.loc[cnpj]
            # Do registro só aceitamos PL de fundo vivo: o de um cancelado é
            # histórico e não descreve o fundo hoje.
            if linha["situacao"] == ATIVO:
                candidatos.append(("registro", linha["pl"], linha["pl_data"]))

        for origem, pl, quando in candidatos:
            if pd.isna(pl) or pl < settings.CVM_PL_MINIMO:
                self.stats["pl_nao_credivel"] += 1
                continue
            return float(pl), _data_iso(quando), origem
        return None, None, None


def _tenta(fn, rotulo: str):
    """Chama um carregador de documento; sem rede, segue sem aquele bloco."""
    try:
        df = fn()
        return df if df is not None and not df.empty else None
    except Exception as e:  # noqa: BLE001
        logger.warning("%s indisponível (%s).", rotulo, e)
        return None


def _texto(v) -> str | None:
    return None if v is None or pd.isna(v) else str(v).strip() or None


def _float(v) -> float | None:
    return None if v is None or pd.isna(v) else float(v)


def _int(v) -> int | None:
    return None if v is None or pd.isna(v) else int(v)


def _data_iso(v) -> str | None:
    if v is None or pd.isna(v):
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(str(v)[:10], fmt).date().isoformat()
        except ValueError:
            continue
    return None
