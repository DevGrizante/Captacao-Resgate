"""
Documentos periódicos da CVM — EXTRATO e PERFIL MENSAL.

São duas bases que ninguém tinha olhado no projeto e que entregam justamente
os campos que estavam marcados como "só o Quantum tem":

    EXTRATO  (FI/DOC/EXTRATO/DADOS/extrato_fi_AAAA.csv)
        TAXA_ADM, QT_DIA_CONVERSAO_COTA / QT_DIA_PAGTO_RESGATE (cotização),
        APLIC_MIN, PUBLICO_ALVO, ATIVO_CRED_PRIV, CONDOM, TAXA_PERFM

    PERFIL   (FI/DOC/PERFIL_MENSAL/DADOS/perfil_mensal_fi_AAAAMM.csv)
        PR_ATIVO_CRED_PRIV      % do PL em crédito privado
        PRAZO_CARTEIRA_TITULO   prazo médio da carteira (proxy de duration)
        PR_PATRIM_LIQ_MAIOR_COTST  concentração no maior cotista
        NR_COTST_* / PR_PL_COTST_*  quem compra o fundo, por tipo de investidor

COBERTURA (medida contra a planilha de 14/08/2026, 4.603 fundos):

    EXTRATO, só o arquivo de 2026 ........ 13,7%
    EXTRATO, unindo 2022..2026 ........... 68,9%   <- é o que usamos
    PERFIL, só o mês mais recente ......... 7,8%
    PERFIL, unindo 6 meses ............... 82,8%   <- é o que usamos

O EXTRATO é ANUAL: o fundo declara uma vez por ano, então olhar só o arquivo
do ano corrente descarta quase todo mundo. Unimos os anos e ficamos com a
declaração mais recente de cada fundo — por isso `extrato_data` viaja junto:
uma taxa de administração declarada em 2023 é informação real, mas o usuário
precisa saber que é de 2023.
"""
from __future__ import annotations

import io
import logging
import time
import zipfile
from datetime import date, timedelta

import pandas as pd
import requests

from app.config import CACHE_DIR, settings
from app.utils import so_digitos

logger = logging.getLogger("cvm_documentos")

URL_EXTRATO = "https://dados.cvm.gov.br/dados/FI/DOC/EXTRATO/DADOS/extrato_fi_{ano}.csv"
URL_PERFIL = (
    "https://dados.cvm.gov.br/dados/FI/DOC/PERFIL_MENSAL/DADOS/perfil_mensal_fi_{aaaamm}.csv"
)
URL_LAMINA = "https://dados.cvm.gov.br/dados/FI/DOC/LAMINA/DADOS/lamina_fi_{aaaamm}.zip"

# Sufixo = versão do schema: mudou coluna, o cache antigo deixa de ser achado.
_CACHE_EXTRATO = CACHE_DIR / "cvm_extrato_v2.parquet"
_CACHE_PERFIL = CACHE_DIR / "cvm_perfil.parquet"
_CACHE_LAMINA = CACHE_DIR / "cvm_lamina.parquet"

# Tipos de cotista do PERFIL que interessam a um broker: quem já compra o fundo.
COTISTAS = {
    "PR_PL_COTST_PF_VAREJO": "pf_varejo",
    "PR_PL_COTST_PF_PB": "pf_private",
    "PR_PL_COTST_PJ_NAO_FINANC_VAREJO": "pj_varejo",
    "PR_PL_COTST_PJ_NAO_FINANC_PB": "pj_private",
    "PR_PL_COTST_EFPC": "fundo_pensao",
    "PR_PL_COTST_RPPS": "rpps",
    "PR_PL_COTST_EAPC": "previdencia_aberta",
    "PR_PL_COTST_SEGUR": "seguradora",
    "PR_PL_COTST_BANCO": "banco",
    "PR_PL_COTST_CORRETORA_DISTRIB": "corretora",
    "PR_PL_COTST_DISTRIB": "distribuidor",
    "PR_PL_COTST_INVNR": "nao_residente",
}


def _fresco(caminho, ttl_horas: int) -> bool:
    return (
        caminho.exists()
        and (time.time() - caminho.stat().st_mtime) / 3600 < ttl_horas
    )


def _num(s):
    return pd.to_numeric(s, errors="coerce")


def _zero_e_vazio(s):
    """Zero nestes campos é 'não declarei', não 'medi e deu zero'.

    Conferido no arquivo: `PRAZO_CARTEIRA_TITULO` vem 0 em 6.512 dos 25.780
    fundos e `PR_PATRIM_LIQ_MAIOR_COTST` em cerca de 90% deles. Publicar isso
    como zero encheria a tela de "0 dias" e "0% de concentração" — números que
    parecem medidos e não são.
    """
    v = _num(s)
    return v.where(v > 0)


def _taxa_plausivel(s, teto: float):
    """Descarta taxa fora de escala.

    O campo `TAXA_ADM` chega a 40.000 em alguns fundos: quem preencheu pôs o
    valor em reais no lugar do percentual. Uma taxa de administração acima de
    `teto`% ao ano não existe no mercado de crédito — é erro de unidade, e
    entraria na média ponderada da gestora destruindo a coluna inteira.
    """
    v = _num(s)
    return v.where((v >= 0) & (v <= teto))


def extrato() -> pd.DataFrame:
    """Última declaração de EXTRATO de cada fundo, unindo vários anos.

    Colunas: taxa_adm, taxa_perfm, cotizacao_resgate, pagamento_resgate,
    aplicacao_minima, publico_alvo, credito_privado, condominio, extrato_data.
    """
    if _fresco(_CACHE_EXTRATO, settings.CVM_DOCUMENTOS_TTL_HORAS):
        logger.info("EXTRATO: usando cache local.")
        return pd.read_parquet(_CACHE_EXTRATO)

    ano_atual = date.today().year
    frames = []
    for ano in range(ano_atual - settings.CVM_EXTRATO_ANOS + 1, ano_atual + 1):
        try:
            resp = requests.get(URL_EXTRATO.format(ano=ano), timeout=settings.CVM_TIMEOUT_S)
            resp.raise_for_status()
            frames.append(pd.read_csv(
                io.BytesIO(resp.content), sep=";", encoding="latin-1", low_memory=False,
                usecols=lambda c: c in {
                    "CNPJ_FUNDO_CLASSE", "DT_COMPTC", "TAXA_ADM", "TAXA_PERFM",
                    "QT_DIA_CONVERSAO_COTA", "QT_DIA_PAGTO_RESGATE", "APLIC_MIN",
                    "PUBLICO_ALVO", "ATIVO_CRED_PRIV", "CONDOM",
                    # Índice da taxa de performance: é o benchmark que o fundo
                    # persegue, e a melhor pista declarada de indexador.
                    "PARAM_TAXA_PERFM",
                },
            ))
        except Exception as e:  # noqa: BLE001
            logger.warning("EXTRATO %s indisponível: %s", ano, e)

    if not frames:
        return pd.DataFrame()

    df = pd.concat(frames, ignore_index=True)
    df["cnpj"] = df["CNPJ_FUNDO_CLASSE"].map(so_digitos)
    df["extrato_data"] = pd.to_datetime(df["DT_COMPTC"], errors="coerce")
    df = df[df["cnpj"].notna() & df["extrato_data"].notna()]
    # Fica a declaração mais recente de cada fundo.
    df = df.sort_values("extrato_data").drop_duplicates("cnpj", keep="last")
    # Indexar ANTES de montar as colunas: o construtor do DataFrame alinha as
    # Series pelo índice delas, então passar `index=` com Series ainda
    # indexadas por número de linha produz um quadro inteiro de NaN.
    df = df.set_index("cnpj")

    # "Sem aplicação mínima" costuma vir como 0 ou 1 centavo/real — para o
    # broker isso é uma informação útil (entra com qualquer valor), então vira
    # 0 explícito em vez de sumir.
    aplic = _num(df.get("APLIC_MIN"))
    out = pd.DataFrame({
        "taxa_adm": _taxa_plausivel(df["TAXA_ADM"], settings.CVM_TAXA_ADM_TETO),
        "taxa_perfm": _taxa_plausivel(df.get("TAXA_PERFM"), 100),
        "cotizacao_resgate": _num(df.get("QT_DIA_CONVERSAO_COTA")),
        "pagamento_resgate": _num(df.get("QT_DIA_PAGTO_RESGATE")),
        "aplicacao_minima": aplic.where(aplic > 1, 0),
        "publico_alvo": df.get("PUBLICO_ALVO"),
        "credito_privado": df.get("ATIVO_CRED_PRIV").eq("S"),
        "condominio": df.get("CONDOM"),
        "indice_taxa_perfm": df.get("PARAM_TAXA_PERFM"),
        "extrato_data": df["extrato_data"].dt.date.astype(str),
    }, index=df.index)

    try:
        out.to_parquet(_CACHE_EXTRATO)
    except Exception as e:  # noqa: BLE001
        logger.debug("cache do extrato: %s", e)
    logger.info("EXTRATO: %d fundos (declarações de %d anos).", len(out),
                settings.CVM_EXTRATO_ANOS)
    return out


def lamina() -> pd.DataFrame:
    """Índice de referência declarado na LAMINA de informações essenciais.

    Cobertura pequena (2,2% dos nossos fundos) porque lâmina é obrigação de
    fundo de varejo, e crédito privado é majoritariamente institucional. Mas é
    o benchmark declarado pelo próprio fundo, então quando existe, vale mais
    que qualquer inferência — por isso entra primeiro na cadeia.
    """
    if _fresco(_CACHE_LAMINA, settings.CVM_DOCUMENTOS_TTL_HORAS):
        logger.info("LAMINA: usando cache local.")
        return pd.read_parquet(_CACHE_LAMINA)

    cur = date.today().replace(day=1)
    frames = []
    for _ in range(settings.CVM_PERFIL_MESES):
        aaaamm = cur.strftime("%Y%m")
        cur = (cur - timedelta(days=1)).replace(day=1)
        try:
            resp = requests.get(URL_LAMINA.format(aaaamm=aaaamm), timeout=settings.CVM_TIMEOUT_S)
            resp.raise_for_status()
            with zipfile.ZipFile(io.BytesIO(resp.content)) as z:
                for nome in z.namelist():
                    # Só o CSV principal; os de carteira/rentabilidade têm
                    # outro grão e não interessam aqui.
                    if not nome.startswith("lamina_fi_2"):
                        continue
                    frames.append(pd.read_csv(
                        z.open(nome), sep=";", encoding="latin-1", low_memory=False,
                        usecols=lambda c: c in {"CNPJ_FUNDO_CLASSE", "DT_COMPTC", "INDICE_REFER"},
                    ))
        except Exception as e:  # noqa: BLE001
            logger.warning("LAMINA %s indisponível: %s", aaaamm, e)

    if not frames:
        return pd.DataFrame()

    df = pd.concat(frames, ignore_index=True)
    df["cnpj"] = df["CNPJ_FUNDO_CLASSE"].map(so_digitos)
    df["lamina_data"] = pd.to_datetime(df["DT_COMPTC"], errors="coerce")
    df = df[df["cnpj"].notna() & df["lamina_data"].notna()]
    df = df.sort_values("lamina_data").drop_duplicates("cnpj", keep="last")
    df = df.set_index("cnpj")  # ver comentário em extrato(): alinhamento

    out = pd.DataFrame({
        "indice_referencia": df["INDICE_REFER"],
        "lamina_data": df["lamina_data"].dt.date.astype(str),
    }, index=df.index)
    try:
        out.to_parquet(_CACHE_LAMINA)
    except Exception as e:  # noqa: BLE001
        logger.debug("cache da lâmina: %s", e)
    logger.info("LAMINA: %d fundos.", len(out))
    return out


def perfil_mensal() -> pd.DataFrame:
    """Último PERFIL MENSAL de cada fundo, unindo os últimos meses.

    Colunas: pct_credito_privado, prazo_carteira_dias, conc_maior_cotista_pct,
    perfil_* (fração do PL por tipo de investidor), perfil_data.
    """
    if _fresco(_CACHE_PERFIL, settings.CVM_DOCUMENTOS_TTL_HORAS):
        logger.info("PERFIL: usando cache local.")
        return pd.read_parquet(_CACHE_PERFIL)

    cur = date.today().replace(day=1)
    frames = []
    for _ in range(settings.CVM_PERFIL_MESES):
        aaaamm = cur.strftime("%Y%m")
        cur = (cur - timedelta(days=1)).replace(day=1)
        try:
            resp = requests.get(URL_PERFIL.format(aaaamm=aaaamm), timeout=settings.CVM_TIMEOUT_S)
            resp.raise_for_status()
            frames.append(pd.read_csv(
                io.BytesIO(resp.content), sep=";", encoding="latin-1", low_memory=False,
                usecols=lambda c: c in (
                    {"CNPJ_FUNDO_CLASSE", "DT_COMPTC", "PR_ATIVO_CRED_PRIV",
                     "PRAZO_CARTEIRA_TITULO", "PR_PATRIM_LIQ_MAIOR_COTST"}
                    | set(COTISTAS)
                ),
            ))
        except Exception as e:  # noqa: BLE001
            logger.warning("PERFIL %s indisponível: %s", aaaamm, e)

    if not frames:
        return pd.DataFrame()

    df = pd.concat(frames, ignore_index=True)
    df["cnpj"] = df["CNPJ_FUNDO_CLASSE"].map(so_digitos)
    df["perfil_data"] = pd.to_datetime(df["DT_COMPTC"], errors="coerce")
    df = df[df["cnpj"].notna() & df["perfil_data"].notna()]
    df = df.sort_values("perfil_data").drop_duplicates("cnpj", keep="last")
    df = df.set_index("cnpj")  # ver comentário em extrato(): alinhamento

    dados = {
        # % em crédito privado pode legitimamente ser zero — é medida, não
        # omissão. Já prazo e concentração vêm zerados quando não declarados.
        "pct_credito_privado": _num(df.get("PR_ATIVO_CRED_PRIV")),
        "prazo_carteira_dias": _zero_e_vazio(df.get("PRAZO_CARTEIRA_TITULO")),
        "conc_maior_cotista_pct": _zero_e_vazio(df.get("PR_PATRIM_LIQ_MAIOR_COTST")),
        "perfil_data": df["perfil_data"].dt.date.astype(str),
    }
    for col, apelido in COTISTAS.items():
        if col in df:
            dados[f"perfil_{apelido}"] = _num(df[col])

    out = pd.DataFrame(dados, index=df.index)
    try:
        out.to_parquet(_CACHE_PERFIL)
    except Exception as e:  # noqa: BLE001
        logger.debug("cache do perfil: %s", e)
    logger.info("PERFIL: %d fundos (%d meses unidos).", len(out), settings.CVM_PERFIL_MESES)
    return out
