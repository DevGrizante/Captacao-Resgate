"""
Composição da carteira pelo CDA da CVM — o bucket de verdade.

>>> ISTO ALIMENTA O BUCKET (LF / Incentivada / Tradicional / Misto).
    Não confundir com `services/perfil_indexador.py`, que mede outra coisa: a
    exposição do cotista. Aqui medimos QUE PAPEL O FUNDO CARREGA, que é a
    pergunta da mesa de crédito. As duas divergem muito, e a divergência é
    informação — ver a nota no fim deste texto.

    A regra que transforma estes números em bucket vive em
    `services/classifier.py`; aqui só se mede.

DE ONDE VEM CADA PEDAÇO

    BLC_4  debêntures     -> código do papel, indexador via SND (99,4% do valor)
    BLC_5  LF/CDB/DPGE    -> `DS_INDEXADOR_POSFX` declarado no próprio arquivo
    BLC_6  títulos IF ext.-> idem BLC_5
    BLC_8  CRI/CRA/NP     -> instrumento sim, indexador não (dilui o mix)
    BLC_8  futuro de DAP  -> a perna de hedge da debênture incentivada, que
                             separa "carrega juro real" de "fica só com o
                             spread de crédito". Ver `_posicao_dap`.

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

import logging
import re
import time
import unicodedata
import zipfile

import pandas as pd

from app.config import CACHE_DIR, settings
from app.connectors import cvm_cda_arquivo, snd_debentures
from app.utils import so_digitos

logger = logging.getLogger("cvm_carteira")

# Sufixo = versão do schema: mudou coluna, o cache antigo deixa de ser achado.
# v2 acrescentou as colunas de hedge em DAP.
_CACHE = CACHE_DIR / "cvm_carteira_v2.parquet"

# Blocos que contêm crédito privado. BLC_1 (público) e BLC_2 (cotas) não entram.
BLOCOS_CREDITO = (4, 5, 6, 8)

# O futuro de cupom de IPCA aparece no BLC_8 com dois rótulos diferentes,
# conforme o administrador: alguns preenchem `TP_ATIVO` ("Futuro de DAP:Cupom
# de DI x IPCA"), outros deixam "Contrato Futuro" genérico e põem o papel em
# `DS_ATIVO` ("FUT DAP/K35", "DAPFUTQ30", "Futuro de Cupom de IPCA - FUT DAP").
# Procurar "DAP" nos dois campos juntos cobre as duas convenções.
_RE_DAP = re.compile(r"\bDAP\b|DAPFUT|FUT ?DAP")


def _sem_acento(v) -> str:
    s = unicodedata.normalize("NFKD", str(v or ""))
    return "".join(c for c in s if not unicodedata.combining(c)).upper()


def _fresco() -> bool:
    return (
        _CACHE.exists()
        and (time.time() - _CACHE.stat().st_mtime) / 3600 < settings.CDA_TTL_HORAS
    )


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


def _posicao_dap(z: zipfile.ZipFile, mes: str) -> pd.Series:
    """Nocional em futuro de DAP por CNPJ — a perna de hedge da incentivada.

    O DAP (cupom de DI x IPCA) é o contrato com que o fundo de debênture
    incentivada trava a perna de inflação do papel que comprou em B + spread,
    ficando só com o spread de crédito. A presença e o tamanho dessa posição
    são o que separa esse produto do fundo que carrega juro real na cota.

    >>> O NOCIONAL É RECONSTRUÍDO, NÃO LIDO

    O CDA informa `QT_POS_FINAL` (contratos) e `VL_MERC_POS_FINAL` (o ajuste a
    mercado, que é o resultado do dia e não o tamanho da posição). Um DAP
    vendido aparece com ajuste de -R$ 1.020.593 num fundo que tem R$ 344 mi de
    exposição — usar o ajuste como tamanho subestimaria o hedge em duas ordens
    de grandeza. O nocional vem então dos contratos, pela face de R$ 100 mil da
    B3 (`DAP_NOCIONAL_CONTRATO`).

    É uma APROXIMAÇÃO: a face é o valor no vencimento, e o contrato negocia por
    PU descontado, então o nocional real é um pouco menor e varia com o prazo.
    Serve para o que é usado aqui — comparar ordem de grandeza contra a
    carteira IPCA+ e decidir se a posição trava a carteira ou é residual.
    Conferido contra o CDA de 2026-04: a razão nocional/carteira IPCA+ tem
    mediana 0,75 e p75 0,91, o que é o esperado de um hedge de fato.

    Compradas e vendidas são somadas em módulo de propósito: interessa o
    tamanho da posição em cupom de IPCA, e as duas pontas aparecem no mesmo
    fundo (rolagem de vencimento) sem que uma anule a outra economicamente.
    """
    nome = f"cda_fi_BLC_8_{mes}.csv"
    if nome not in z.namelist():
        return pd.Series(dtype="float64")

    df = pd.read_csv(z.open(nome), sep=";", encoding="latin-1", low_memory=False)
    if "TP_APLIC" not in df.columns or "QT_POS_FINAL" not in df.columns:
        logger.warning("BLC_8 %s sem as colunas de derivativo — sem sinal de DAP.", mes)
        return pd.Series(dtype="float64")

    futuros = df[df["TP_APLIC"].astype(str).str.contains("Futuro", na=False)]
    if futuros.empty:
        return pd.Series(dtype="float64")

    # O rótulo do contrato pode estar em qualquer um dos dois campos; ler os
    # dois juntos evita depender da convenção do administrador.
    def _texto(coluna: str) -> pd.Series:
        if coluna not in futuros.columns:
            return pd.Series("", index=futuros.index)
        return futuros[coluna].astype(str)

    rotulo = (_texto("TP_ATIVO") + " " + _texto("DS_ATIVO")).map(_sem_acento)
    dap = futuros[rotulo.str.contains(_RE_DAP, na=False)]
    if dap.empty:
        return pd.Series(dtype="float64")

    contratos = pd.to_numeric(dap["QT_POS_FINAL"], errors="coerce").abs()
    por_cnpj = (
        dap.assign(cnpj=dap["CNPJ_FUNDO_CLASSE"].map(so_digitos), ctr=contratos)
        .dropna(subset=["cnpj", "ctr"])
        .groupby("cnpj")["ctr"].sum()
    )
    logger.info("CDA %s: %d fundos com posição em futuro de DAP.", mes, len(por_cnpj))
    return por_cnpj * settings.DAP_NOCIONAL_CONTRATO


def carregar() -> pd.DataFrame:
    """Composição de crédito por CNPJ, indexada pelo CNPJ do fundo.

    Colunas: pct_lf, pct_ipca, pct_cdi, pct_pre (frações da carteira de
    crédito), pct_debenture, pct_cdb, pct_cri_cra, carteira_credito,
    carteira_pct_pl, carteira_idx_conhecido_pct, carteira_sigilo_pct,
    dap_nocional, dap_cobertura, carteira_data.

    `pct_ipca`/`pct_cdi`/`pct_pre` medem o indexador do que NÃO é LF: o
    classificador tira LF da conta primeiro (LF é instrumento, não indexador),
    e as frações precisam chegar já nessa convenção.
    """
    if _fresco():
        logger.info("CDA: usando cache local.")
        return pd.read_parquet(_CACHE)

    mes = cvm_cda_arquivo.mes_alvo()
    z = cvm_cda_arquivo.abrir(mes)
    if z is None:
        logger.warning("CDA %s indisponível — seguindo sem composição.", mes)
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

    _acrescentar_hedge_dap(z, mes, out)
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


def _acrescentar_hedge_dap(z: zipfile.ZipFile, mes: str, out: pd.DataFrame) -> None:
    """Preenche dap_nocional e dap_cobertura a partir do BLC_8.

    `dap_cobertura` é o nocional em DAP sobre o R$ da carteira indexada a IPCA
    — quanto da perna de inflação está travada. Dividir pela carteira IPCA+ e
    não pela carteira inteira é o que torna o número comparável entre fundos de
    tamanhos diferentes, e é o que o classificador precisa saber.

    >>> AQUI SÓ SE MEDE. Quem decide se a cobertura configura hedge é o
        classificador, contra `HEDGE_DAP_MINIMO`. A separação é necessária:
        o limiar é editável pelo painel de controle, e um booleano gravado no
        cache do CDA congelaria a régua do dia em que o parquet foi escrito —
        mexer no painel não reclassificaria nada.

    Fundo sem posição em DAP fica com cobertura 0.0. Aqui a ausência é
    informação de verdade, não lacuna: o CDA lista as posições em derivativo de
    todos os fundos, então não aparecer significa não ter.
    """
    out["dap_nocional"] = 0.0
    out["dap_cobertura"] = 0.0

    nocional = _posicao_dap(z, mes)
    if nocional.empty:
        return

    out["dap_nocional"] = nocional.reindex(out.index).fillna(0.0)
    # A base é o R$ em papel IPCA+, não a carteira toda: `pct_ipca` já é a
    # fração ex-LF, e multiplicá-la pelo crédito devolve o valor em reais.
    carteira_ipca = out["pct_ipca"] * out["carteira_credito"]
    com_ipca = carteira_ipca > 0
    out.loc[com_ipca, "dap_cobertura"] = (
        out.loc[com_ipca, "dap_nocional"] / carteira_ipca[com_ipca]
    )
    logger.info(
        "CDA %s: %d fundos com carteira IPCA+ coberta por DAP (mediana %.2f).",
        mes, int((out["dap_cobertura"] > 0).sum()),
        out.loc[out["dap_cobertura"] > 0, "dap_cobertura"].median() or 0.0,
    )


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
