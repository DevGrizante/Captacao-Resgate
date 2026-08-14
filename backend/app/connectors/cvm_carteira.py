"""
Composição da carteira pelo CDA da CVM — o bucket de verdade.

>>> ISTO É O BUCKET (IPCA+ / CDI+ / LF / Misto).
    Não confundir com `services/perfil_indexador.py`, que mede outra coisa: a
    exposição do cotista. Aqui medimos QUE PAPEL O FUNDO CARREGA, que é a
    pergunta da mesa de crédito. As duas divergem muito, e a divergência é
    informação — ver a nota no fim deste texto.

DE ONDE VEM CADA PEDAÇO

    BLC_4  debêntures     -> código do papel, indexador via SND (99,4% do valor)
    BLC_5  LF/CDB/DPGE    -> `DS_INDEXADOR_POSFX` declarado no próprio arquivo
    BLC_6  títulos IF ext.-> idem BLC_5
    BLC_8  CRI/CRA/NP     -> instrumento sim, indexador não (dilui o mix)

    BLC_1 (títulos públicos) e BLC_2 (cotas de fundos) ficam de fora: são caixa
    e alocação, não a tese de crédito. Entram só no denominador de "quanto do
    PL é a carteira de crédito".

>>> O SIGILO E POR QUE OLHAMOS PARA TRÁS

O CDA do mês corrente é inútil para isto: o administrador pode omitir posições
por alguns meses, e o que sobra é uma amostra enviesada (esconde-se justamente
a posição que dá alfa). Medido no nosso universo:

    CDA de 202605 (3 meses atrás) .. 45,9% do PL sigiloso, R$ 738 bi visíveis
    CDA de 202603 (5 meses atrás) ..  0,0% do PL sigiloso, R$ 1.617 bi visíveis

O sigilo cai de vez entre o 3º e o 4º mês (o arquivo CONFID encolhe de 9,4 MB
para 1,4 MB). Por isso lemos o CDA com `CDA_DEFASAGEM_MESES` de atraso — 4 por
padrão. O preço é uma carteira de ~4 meses atrás, o que é aceitável para um
bucket: fundo de crédito não troca de mandato em um trimestre. O `carteira_data`
viaja junto para a tela dizer de quando é.

Ainda assim a guarda é POR FUNDO, não pelo mês: quem continuar com mais de
`CDA_SIGILO_MAXIMO_PCT` do PL sigiloso sai sem composição, em vez de entrar com
uma carteira pela metade.

>>> A DIVERGÊNCIA COM O PERFIL DA COTA (medida, não teórica)

Cruzando os 3.000 fundos com composição contra o perfil por volatilidade:

                    cota: inflação   cota: pós
    carteira CDI+              61         752      (92% concordam)
    carteira IPCA+            713         349      (33% DIVERGEM)
    carteira LF/banco          42         605

Um terço dos fundos com carteira IPCA+ entrega ao cotista um retorno de
pós-fixado: são as casas que compram debênture IPCA+ e travam em CDI via swap.
É exatamente por isso que os dois campos existem separados — e é por isso que a
inferência por volatilidade sozinha dizia "95% pós".
"""
from __future__ import annotations

import io
import logging
import time
import unicodedata
import zipfile
from datetime import date

import pandas as pd
import requests

from app.config import CACHE_DIR, settings
from app.connectors import snd_debentures
from app.utils import so_digitos

logger = logging.getLogger("cvm_carteira")

URL_CDA = "https://dados.cvm.gov.br/dados/FI/DOC/CDA/DADOS/cda_fi_{aaaamm}.zip"

# Sufixo = versão do schema: mudou coluna, o cache antigo deixa de ser achado.
_CACHE = CACHE_DIR / "cvm_carteira_v1.parquet"

# Blocos que contêm crédito privado. BLC_1 (público) e BLC_2 (cotas) não entram.
BLOCOS_CREDITO = (4, 5, 6, 8)


def _sem_acento(v) -> str:
    s = unicodedata.normalize("NFKD", str(v or ""))
    return "".join(c for c in s if not unicodedata.combining(c)).upper()


def _fresco() -> bool:
    return (
        _CACHE.exists()
        and (time.time() - _CACHE.stat().st_mtime) / 3600 < settings.CDA_TTL_HORAS
    )


def _mes_alvo() -> str:
    """AAAAMM de `CDA_DEFASAGEM_MESES` atrás."""
    hoje = date.today()
    total = hoje.year * 12 + (hoje.month - 1) - settings.CDA_DEFASAGEM_MESES
    return f"{total // 12:04d}{total % 12 + 1:02d}"


def _eixo_posfx(ds_indexador, titulo_posfx) -> str | None:
    """Traduz `DS_INDEXADOR_POSFX` do BLC_5/BLC_6 para o eixo de crédito."""
    s = _sem_acento(ds_indexador).strip()
    if s and s != "NAN":
        if "IPCA" in s or "IGP" in s or "INPC" in s or "PRECO" in s:
            return "ipca"
        if s.startswith("DI") or "CDI" in s or "SELIC" in s:
            return "cdi"
        if "PREFIX" in s:
            return "pre"
        return "outro"
    # Sem rótulo de índice: um papel marcado como pós-fixado é, na prática do
    # mercado bancário, CDI. Sem nem isso, não afirmamos.
    if str(titulo_posfx).strip().upper() == "S":
        return "cdi"
    return None


def _instrumento(tp_ativo) -> str:
    s = _sem_acento(tp_ativo)
    if "LETRA FINANCEIRA" in s:
        return "lf"
    if "CDB" in s or "RDB" in s or "DPGE" in s:
        return "cdb"
    if "RECEBIVEIS" in s:
        return "cri_cra"
    if "PROMISSORIA" in s or "COMMERCIAL" in s or "EXPORT" in s:
        return "np"
    return "outro"


def _linhas_credito(z: zipfile.ZipFile, mes: str, mapa_snd: dict) -> pd.DataFrame:
    """Achata BLC_4/5/6/8 numa tabela (cnpj, instrumento, eixo, valor)."""
    partes = []

    def _le(bloco: int) -> pd.DataFrame | None:
        nome = f"cda_fi_BLC_{bloco}_{mes}.csv"
        if nome not in z.namelist():
            return None
        return pd.read_csv(z.open(nome), sep=";", encoding="latin-1", low_memory=False)

    # --- BLC_4: debêntures. O indexador vem do SND, pelo código do papel. ---
    b4 = _le(4)
    if b4 is not None:
        deb = b4[b4["TP_ATIVO"].astype(str).str.contains("nture", na=False)].copy()
        if not deb.empty:
            deb["cnpj"] = deb["CNPJ_FUNDO_CLASSE"].map(so_digitos)
            deb["eixo"] = deb["CD_ATIVO"].astype(str).str.strip().map(mapa_snd)
            deb["instrumento"] = "debenture"
            partes.append(deb[["cnpj", "instrumento", "eixo", "VL_MERC_POS_FINAL"]])

    # --- BLC_5 e BLC_6: o próprio arquivo declara o indexador. ---
    for bloco in (5, 6):
        df = _le(bloco)
        if df is None or df.empty:
            continue
        df["cnpj"] = df["CNPJ_FUNDO_CLASSE"].map(so_digitos)
        df["eixo"] = [
            _eixo_posfx(a, b)
            for a, b in zip(df.get("DS_INDEXADOR_POSFX", ""), df.get("TITULO_POSFX", ""))
        ]
        df["instrumento"] = df["TP_ATIVO"].map(_instrumento)
        partes.append(df[["cnpj", "instrumento", "eixo", "VL_MERC_POS_FINAL"]])

    # --- BLC_8: só a parte que é crédito. Sem indexador — dilui o mix. ---
    b8 = _le(8)
    if b8 is not None and not b8.empty:
        tipo = b8["TP_ATIVO"].astype(str).map(_sem_acento)
        e_credito = tipo.str.contains("RECEBIVEIS|PROMISSORIA|COMMERCIAL|EXPORT", na=False)
        cred = b8[e_credito].copy()
        if not cred.empty:
            cred["cnpj"] = cred["CNPJ_FUNDO_CLASSE"].map(so_digitos)
            cred["instrumento"] = tipo[e_credito].map(
                lambda s: "cri_cra" if "RECEBIVEIS" in s else "np"
            )
            cred["eixo"] = None
            partes.append(cred[["cnpj", "instrumento", "eixo", "VL_MERC_POS_FINAL"]])

    if not partes:
        return pd.DataFrame()
    todas = pd.concat(partes, ignore_index=True)
    # Posição negativa é venda a descoberto/ajuste: não descreve alocação.
    return todas[todas["cnpj"].notna() & (todas["VL_MERC_POS_FINAL"] > 0)]


def carregar() -> pd.DataFrame:
    """Composição de crédito por CNPJ, indexada pelo CNPJ do fundo.

    Colunas: pct_lf, pct_ipca, pct_cdi, pct_pre (frações da carteira de
    crédito), pct_debenture, pct_cdb, pct_cri_cra, carteira_credito,
    carteira_pct_pl, carteira_idx_conhecido_pct, carteira_sigilo_pct,
    carteira_data.

    `pct_ipca`/`pct_cdi`/`pct_pre` medem o indexador do que NÃO é LF: o
    classificador tira LF da conta primeiro (LF é instrumento, não indexador),
    e as frações precisam chegar já nessa convenção.
    """
    if _fresco():
        logger.info("CDA: usando cache local.")
        return pd.read_parquet(_CACHE)

    mes = _mes_alvo()
    try:
        resp = requests.get(URL_CDA.format(aaaamm=mes), timeout=settings.CVM_TIMEOUT_S)
        resp.raise_for_status()
        z = zipfile.ZipFile(io.BytesIO(resp.content))
    except Exception as e:  # noqa: BLE001
        logger.warning("CDA %s indisponível (%s) — seguindo sem composição.", mes, e)
        return pd.DataFrame()

    mapa_snd = snd_debentures.carregar()
    linhas = _linhas_credito(z, mes, mapa_snd)
    if linhas.empty:
        logger.warning("CDA %s sem linhas de crédito.", mes)
        return pd.DataFrame()

    credito = linhas.groupby("cnpj")["VL_MERC_POS_FINAL"].sum()

    por_instr = linhas.pivot_table(
        index="cnpj", columns="instrumento", values="VL_MERC_POS_FINAL", aggfunc="sum",
    ).reindex(credito.index).fillna(0.0)
    # O eixo só interessa fora da LF — o classificador remove LF antes de olhar
    # indexador, então somar o CDI da LF aqui contaria o mesmo dinheiro duas vezes.
    ex_lf = linhas[linhas["instrumento"] != "lf"]
    por_eixo = ex_lf.pivot_table(
        index="cnpj", columns="eixo", values="VL_MERC_POS_FINAL", aggfunc="sum",
    ).reindex(credito.index).fillna(0.0)

    col = lambda df, c: df[c] if c in df.columns else 0.0  # noqa: E731

    out = pd.DataFrame(index=credito.index)
    out["carteira_credito"] = credito
    out["pct_lf"] = col(por_instr, "lf") / credito
    out["pct_ipca"] = col(por_eixo, "ipca") / credito
    out["pct_cdi"] = col(por_eixo, "cdi") / credito
    out["pct_pre"] = col(por_eixo, "pre") / credito
    out["pct_debenture"] = col(por_instr, "debenture") / credito
    out["pct_cdb"] = col(por_instr, "cdb") / credito
    out["pct_cri_cra"] = (col(por_instr, "cri_cra") + col(por_instr, "np")) / credito
    # Quanto da carteira de crédito tem indexador conhecido. Abaixo da guarda o
    # mix seria uma leitura de metade da carteira apresentada como se fosse toda.
    conhecido = col(por_eixo, "ipca") + col(por_eixo, "cdi") + col(por_eixo, "pre")
    out["carteira_idx_conhecido_pct"] = (
        (conhecido + col(por_instr, "lf")) / credito * 100
    )

    _acrescentar_pl_e_sigilo(z, mes, out)
    out["carteira_data"] = f"{mes[:4]}-{mes[4:]}"

    _aplicar_guardas(out)
    ok = out["carteira_motivo"].isna()
    logger.info(
        "CDA %s: %d fundos com carteira de crédito, %d passaram nas guardas "
        "(R$ %.1f bi identificados). Barrados: %s",
        mes, len(out), int(ok.sum()), out.loc[ok, "carteira_credito"].sum() / 1e9,
        out["carteira_motivo"].value_counts().to_dict() or "nenhum",
    )

    try:
        out.to_parquet(_CACHE)
    except Exception as e:  # noqa: BLE001
        logger.debug("cache do CDA: %s", e)
    return out


def _acrescentar_pl_e_sigilo(z: zipfile.ZipFile, mes: str, out: pd.DataFrame) -> None:
    """Preenche carteira_pct_pl e carteira_sigilo_pct a partir do CDA."""
    # float, não pd.NA: com pd.NA a coluna vira object e quebra o parquet e o
    # `>=` das guardas mais adiante.
    out["carteira_pct_pl"] = float("nan")
    out["carteira_sigilo_pct"] = 0.0

    nome_pl = f"cda_fi_PL_{mes}.csv"
    if nome_pl not in z.namelist():
        return
    pl = pd.read_csv(z.open(nome_pl), sep=";", encoding="latin-1", low_memory=False)
    pl["cnpj"] = pl["CNPJ_FUNDO_CLASSE"].map(so_digitos)
    # Um CNPJ pode aparecer em mais de uma classe; o maior PL é o do fundo.
    serie_pl = pl.groupby("cnpj")["VL_PATRIM_LIQ"].max().reindex(out.index)
    valido = serie_pl > 0
    out.loc[valido, "carteira_pct_pl"] = (
        out.loc[valido, "carteira_credito"] / serie_pl[valido] * 100
    )

    nome_cf = f"cda_fi_CONFID_{mes}.csv"
    if nome_cf not in z.namelist():
        return
    cf = pd.read_csv(z.open(nome_cf), sep=";", encoding="latin-1", low_memory=False)
    cf["cnpj"] = cf["CNPJ_FUNDO_CLASSE"].map(so_digitos)
    sigilo = cf.groupby("cnpj")["VL_MERC_POS_FINAL"].sum().reindex(out.index).fillna(0.0)
    out.loc[valido, "carteira_sigilo_pct"] = (sigilo[valido] / serie_pl[valido] * 100)


def _aplicar_guardas(out: pd.DataFrame) -> None:
    """Marca em `carteira_motivo` o fundo cuja composição não é representativa.

    Três razões para barrar, todas por fundo e não pelo conjunto:

      * sigilo alto  -> vemos uma amostra enviesada da carteira;
      * indexador desconhecido em boa parte da carteira -> o mix seria chute;
      * carteira de crédito pequena demais perto do PL -> o bucket descreveria
        um canto do fundo como se fosse o fundo.

    Barrado fica sem `bucket` na tela ("sem classificação"), que é a resposta
    honesta — MISTO significa "olhamos e nenhum indexador domina", não "não
    conseguimos olhar".

    Marcamos em vez de descartar de propósito: o motivo é o que permite ao
    relatório de cobertura dizer POR QUE um fundo não tem bucket, em vez de
    deixá-lo sumir como se nunca tivesse aparecido no CDA.
    """
    motivo = pd.Series(pd.NA, index=out.index, dtype="object")
    # Sem PL não dá para medir o peso da carteira no fundo. Aí a guarda de PL
    # não se aplica — barrar por falta de PL descartaria justamente os fundos
    # sobre os quais já sabemos menos.
    pct_pl = pd.to_numeric(out["carteira_pct_pl"], errors="coerce")
    # Ordem inversa da prioridade: a última atribuição é a que prevalece, e
    # sigilo alto explica melhor que "indexador desconhecido" — quando há
    # sigilo, o indexador é desconhecido por consequência.
    motivo[pct_pl.notna() & (pct_pl < settings.CDA_CREDITO_MINIMO_PCT)] = "pouco crédito no PL"
    motivo[out["carteira_idx_conhecido_pct"] < settings.CDA_IDX_MINIMO_PCT] = "indexador desconhecido"
    motivo[out["carteira_sigilo_pct"].fillna(0.0) > settings.CDA_SIGILO_MAXIMO_PCT] = "carteira sob sigilo"
    out["carteira_motivo"] = motivo
