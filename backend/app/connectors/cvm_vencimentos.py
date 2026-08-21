"""
Agenda de vencimento da carteira de crédito, por fundo — R$, data e eixo.

>>> A PERGUNTA QUE ISTO RESPONDE

    "Quanto da carteira desta gestora vence nos próximos 3 meses, e em quê?"

Papel que vence é papel que vai ter de ser rolado ou substituído. Numa mesa que
intermedeia tesouraria e asset, isso é pressão compradora com data marcada — e
independe de a gestora estar captando ou resgatando. Somada à direção do fluxo,
é o que permite ler o secundário com antecedência:

    gestora CAPTANDO  + agenda pesada  ->  compra o que vence e mais um tanto
    gestora RESGATANDO + agenda pesada ->  o vencimento cobre parte do resgate,
                                           e ela pode não precisar vender nada
    gestora RESGATANDO + agenda leve   ->  vendedora no secundário, agora

>>> POR QUE UM CONECTOR NOVO, E NÃO UMA COLUNA EM cvm_carteira

`cvm_carteira.py` responde "que fração da carteira é IPCA+" — ele AGREGA para
percentuais e joga fora a data de cada papel. A agenda é o oposto: ela precisa
de cada posição com o vencimento dela intacto.

`cvm_emissores.py` já preserva vencimento, mas só do BLC_5 (papel bancário).
Aqui as duas metades entram: o papel bancário E a debênture, que é onde está
metade do crédito corporativo.

>>> OS EIXOS SÃO OS DA MESA, NÃO OS DA CVM

    lf       Letra Financeira — instrumento próprio, não indexador. Sai
             separado porque é assim que a mesa pensa: papel de banco é um
             mercado, crédito corporativo é outro.
    cdb      CDB/RDB/DPGE — o resto do funding bancário.
    ipca     crédito corporativo corrigido por inflação (IPCA, IGP-M, INPC).
    cdi      crédito corporativo pós-fixado em DI/Selic.
    pre      prefixado.
    outro    tem vencimento, mas o indexador não foi identificado.

>>> COBERTURA MEDIDA (CDA de 2026-04)

    BLC_4 debêntures  231.874 posições, R$ 766,9 bi
                      DT_FIM_VIGENCIA válida em 99,7% das linhas / 99,5% do R$
                      eixo pelo SND em 99,7% das posições
    BLC_5 banco       90.662 posições, R$ 877,7 bi
                      DT_VENC válida em 100%

    CRI/CRA (BLC_8) FICA DE FORA da agenda: o bloco não tem coluna de
    vencimento nenhuma — só `DS_ATIVO` em texto livre. Entra no mix de
    instrumento (via cvm_carteira) mas não na agenda, e a tela precisa dizer
    isso em vez de deixar o usuário achar que aquele papel não vence nunca.
"""
from __future__ import annotations

import logging
import time
import zipfile

import pandas as pd

from app.config import CACHE_DIR, settings
from app.connectors import cvm_cda_arquivo, snd_debentures
from app.utils import so_digitos

logger = logging.getLogger("cvm_vencimentos")

# O sufixo é a versão do schema: mudou coluna, o cache antigo deixa de ser
# encontrado em vez de ser lido com colunas a menos.
_CACHE = CACHE_DIR / "cvm_vencimentos_v1.parquet"

EIXOS = ("lf", "cdb", "ipca", "cdi", "pre", "outro")

# Como a CVM escreve o tipo, e para que eixo bancário ele vai.
_BANCARIOS = {
    "LETRA FINANCEIRA": "lf",
    "CDB": "cdb",
    "RDB": "cdb",
    "DPGE": "cdb",
}


def _fresco() -> bool:
    return (
        _CACHE.exists()
        and (time.time() - _CACHE.stat().st_mtime) / 3600 < settings.CDA_TTL_HORAS
    )


def _eixo_bancario(tp_ativo) -> str | None:
    s = str(tp_ativo or "").upper()
    for chave, eixo in _BANCARIOS.items():
        if chave in s:
            return eixo
    return None


def _do_blc4(z: zipfile.ZipFile, mes: str, mapa_snd: dict) -> pd.DataFrame:
    """Debêntures: vencimento do próprio bloco, indexador pelo SND."""
    nome = f"cda_fi_BLC_4_{mes}.csv"
    if nome not in z.namelist():
        return pd.DataFrame()

    df = pd.read_csv(
        z.open(nome), sep=";", encoding="latin-1", low_memory=False,
        usecols=["CNPJ_FUNDO_CLASSE", "TP_ATIVO", "CD_ATIVO",
                 "VL_MERC_POS_FINAL", "DT_FIM_VIGENCIA"],
    )
    df = df[df["TP_ATIVO"].astype(str).str.contains("nture", na=False)].copy()
    if df.empty:
        return pd.DataFrame()

    df["cnpj"] = df["CNPJ_FUNDO_CLASSE"].map(so_digitos)
    df["valor"] = pd.to_numeric(df["VL_MERC_POS_FINAL"], errors="coerce")
    df["vencimento"] = pd.to_datetime(df["DT_FIM_VIGENCIA"], errors="coerce")
    df["ticker"] = df["CD_ATIVO"].astype(str).str.strip().str.upper()
    # O SND devolve 'cdi' | 'ipca' | 'pre' | 'outro'. Papel que ele não conhece
    # fica em "outro" — nunca é chutado para o eixo dominante, que inflaria
    # justamente a leitura que a mesa vai usar para decidir.
    df["eixo"] = df["ticker"].map(mapa_snd).fillna("outro")
    df["instrumento"] = "debenture"
    return df[["cnpj", "instrumento", "eixo", "ticker", "valor", "vencimento"]]


def _do_blc5(z: zipfile.ZipFile, mes: str) -> pd.DataFrame:
    """Papel bancário: o próprio bloco declara vencimento e indexador."""
    nome = f"cda_fi_BLC_5_{mes}.csv"
    if nome not in z.namelist():
        return pd.DataFrame()

    df = pd.read_csv(
        z.open(nome), sep=";", encoding="latin-1", low_memory=False,
        usecols=["CNPJ_FUNDO_CLASSE", "TP_ATIVO", "VL_MERC_POS_FINAL", "DT_VENC"],
    )
    df["eixo"] = df["TP_ATIVO"].map(_eixo_bancario)
    # "Outros" do BLC_5 são 18 mil linhas sem tipo declarado. Chutar que são LF
    # inflaria o eixo que a mesa mais olha — ficam fora, como em cvm_emissores.
    df = df[df["eixo"].notna()].copy()
    if df.empty:
        return pd.DataFrame()

    df["cnpj"] = df["CNPJ_FUNDO_CLASSE"].map(so_digitos)
    df["valor"] = pd.to_numeric(df["VL_MERC_POS_FINAL"], errors="coerce")
    df["vencimento"] = pd.to_datetime(df["DT_VENC"], errors="coerce")
    df["instrumento"] = df["eixo"]
    df["ticker"] = None
    return df[["cnpj", "instrumento", "eixo", "ticker", "valor", "vencimento"]]


def carregar(mes: str | None = None) -> pd.DataFrame:
    """Uma linha por posição com vencimento conhecido.

    Colunas: cnpj, instrumento, eixo, ticker, valor, vencimento, carteira_data.

    Devolve DataFrame vazio se o CDA não estiver disponível — nunca levanta.
    Quem chama trata vazio como "não temos agenda", jamais como "nada vence".
    """
    if _fresco():
        logger.info("Agenda de vencimento: usando cache local.")
        return pd.read_parquet(_CACHE)

    mes = mes or cvm_cda_arquivo.mes_alvo()
    z = cvm_cda_arquivo.abrir(mes)
    if z is None:
        logger.warning("CDA %s indisponível — sem agenda de vencimento.", mes)
        return _vazio()

    mapa_snd = snd_debentures.carregar()
    if not mapa_snd:
        logger.warning(
            "SND fora do ar: as debêntures entram na agenda com eixo 'outro'."
        )

    with z:
        partes = [p for p in (_do_blc4(z, mes, mapa_snd), _do_blc5(z, mes)) if not p.empty]

    if not partes:
        return _vazio()

    out = pd.concat(partes, ignore_index=True)
    # Posição negativa é venda a descoberto ou ajuste; sem CNPJ não dá para
    # atribuir a ninguém; sem data não entra numa agenda. Os três saem.
    out = out[
        out["cnpj"].notna() & (out["valor"] > 0) & out["vencimento"].notna()
    ].reset_index(drop=True)
    out["carteira_data"] = f"{mes[:4]}-{mes[4:]}"

    try:
        out.to_parquet(_CACHE, index=False)
    except Exception as e:  # noqa: BLE001 — cache é best-effort
        logger.debug("Não consegui gravar %s: %s", _CACHE.name, e)

    logger.info(
        "Agenda de vencimento %s: %d posições, %d fundos, R$ %.1f bi. Por eixo: %s",
        mes, len(out), out["cnpj"].nunique(), out["valor"].sum() / 1e9,
        {k: f"R$ {v / 1e9:.1f} bi"
         for k, v in out.groupby("eixo")["valor"].sum().sort_values(ascending=False).items()},
    )
    return out


def _vazio() -> pd.DataFrame:
    return pd.DataFrame(columns=[
        "cnpj", "instrumento", "eixo", "ticker", "valor", "vencimento", "carteira_data",
    ])
