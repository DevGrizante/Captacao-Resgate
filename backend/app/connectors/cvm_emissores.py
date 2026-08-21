"""
Quem emitiu o papel bancário que cada fundo carrega — o mapa Tesouraria ↔ Asset.

>>> A PERGUNTA QUE ESTE MÓDULO RESPONDE

A mesa que intermedeia tesouraria e asset não precisa saber só que um fundo
"compra LF". Precisa saber **de qual banco**, **quanto**, **a que preço** e
**quando vence** — porque é disso que a conversa é feita:

    "A Asset X tem R$ 180 mi em LF do Banco Y a CDI + 0,9%, com R$ 60 mi
     vencendo em 4 meses."

Isso é uma ligação a fazer. "A Asset X compra LF" não é.

>>> DE ONDE VEM

Do bloco BLC_5 do CDA (depósitos a prazo e demais títulos de instituição
financeira): Letra Financeira, CDB/RDB, DPGE, LC/LH/LI. Medido no CDA de
2026-04, são 90.327 posições e R$ 877,7 bi, e a qualidade dos campos é a
melhor de todo o CDA:

    CNPJ_EMISSOR ......... 100% preenchido, 14 dígitos
    DT_VENC .............. 100%
    DS_INDEXADOR_POSFX ... 96,9%  (92% em "DI de um dia")
    PR_INDEXADOR_POSFX ... 100% do papel em DI  -> o % do CDI (100, 103,5…)
    PR_CUPOM_POSFX ....... 94%  -> o spread sobre o CDI, em % a.a.

>>> POR QUE AGRUPAR PELA RAIZ DO CNPJ, E NÃO PELO NOME

O campo `EMISSOR` é texto livre digitado pelo administrador, e a mesma
tesouraria aparece com dezenas de grafias. Medido: **795 nomes distintos que
são 179 emissores de verdade**. O Bradesco sozinho aparece como "BRADESCO",
"BANCO BRADESCO", "BANCO BRADESCO S.A.", "BCO BRADESCO SA" e mais seis
variações, somando R$ 156 bi em 1.764 fundos. Um ranking por nome quebraria
essa posição em dez pedaços e nenhum apareceria no topo — o maior emissor do
mercado sumiria da tela.

A raiz (8 primeiros dígitos) consolida também as várias inscrições do mesmo
conglomerado. O nome exibido é a grafia mais usada, ponderada por valor.

>>> PREÇO: DOIS CAMPOS, NÃO UM

Papel bancário indexado a CDI é cotado de duas formas, e o CDA reflete as duas:

    percentual do CDI ...... `PR_INDEXADOR_POSFX` = 103,5  -> "103,5% do CDI"
    CDI mais spread ........ `PR_CUPOM_POSFX`     = 0,9    -> "CDI + 0,9%"

A mediana do mercado é 100% do CDI com cupom de 0,9% a.a., ou seja a forma
dominante é "CDI + spread". Guardamos os dois e deixamos a tela mostrar o que
existir, em vez de converter um no outro — a conversão depende do nível do CDI
na data e produziria um número que ninguém negociou.
"""
from __future__ import annotations

import logging
import time
import unicodedata
from datetime import date

import pandas as pd

from app.config import CACHE_DIR, settings
from app.connectors import cvm_cda_arquivo
from app.utils import so_digitos

logger = logging.getLogger("cvm_emissores")

# O sufixo carrega o estado de `CDA_EXCLUIR_VENCIDOS`: os dois modos produzem
# tabelas diferentes (R$ 744,7 bi contra R$ 847,8 bi), e um nome só faria o
# parquet do modo anterior ser servido como se fosse do modo atual quando
# alguém virasse a chave. O TTL de 24h cobre a virada de mês, que move o corte.
_SUFIXO = "vivos" if settings.CDA_EXCLUIR_VENCIDOS else "todos"
_CACHE = CACHE_DIR / f"cvm_emissores_v2_{_SUFIXO}.parquet"
_CACHE_POSICOES = CACHE_DIR / f"cvm_posicoes_bancarias_v2_{_SUFIXO}.parquet"

# Papel de funding de tesouraria, na granularidade que a mesa negocia.
#
# >>> O QUE O CDA NÃO SEPARA
# O `TP_ATIVO` diz apenas "Letra Financeira": não distingue LF simples de LFSC
# (subordinada de capital complementar) nem de LFSN (subordinada nível II).
# Essa quebra existe no Quantum, não no dado público — e importa, porque LFSC
# e LFSN são outro risco e outro preço. Quando a base do Quantum entrar, o
# campo `instrumento` é o ponto de enxerto: basta refiná-lo pelo código CETIP
# ou ISIN do papel, e todo o resto da tela continua valendo.
INSTRUMENTOS_BANCARIOS = ("lf", "cdb", "dpge")

# Teto de sanidade do cupom por indexador (% a.a.). O campo traz o valor em
# reais ou o PU no lugar do percentual em algumas linhas, e chega a 980. Sem o
# corte, uma linha errada destrói a leitura do papel inteiro. Os tetos são
# folgados de propósito: cortam o absurdo, não o caro.
_TETO_CUPOM = {"cdi": 15.0, "selic": 15.0, "ipca": 25.0, "pre": 40.0}

# Tipos do BLC_5 que são funding de tesouraria. "Outros" fica de fora: são
# 18 mil linhas sem tipo declarado, e chutar que são LF inflaria o ranking.
_TIPOS_BANCARIOS = ("LETRA FINANCEIRA", "CDB", "RDB", "DPGE", "LETRA DE CAMBIO",
                    "LETRA HIPOTECARIA", "LETRA IMOBILIARIA")

# % do CDI fora desta faixa é preenchimento errado (o campo chega a 0,6 e a 300).
_PCT_CDI_MIN, _PCT_CDI_MAX = 50.0, 200.0

# Faixas da curva de vencimento, em ordem. Os cortes são os da mesa: até 3
# meses é rolagem iminente, 3-12 meses é a agenda do ano, acima de 2 anos é
# posição estrutural que não volta a ser negociada tão cedo.
FAIXAS_PRAZO = {
    "ate_3m": "até 3m",
    "3_6m": "3 a 6m",
    "6_12m": "6 a 12m",
    "12_24m": "1 a 2 anos",
    "acima_24m": "acima de 2 anos",
}


def data_referencia() -> pd.Timestamp:
    """A data contra a qual prazo e vencimento são medidos.

    Com `CDA_EXCLUIR_VENCIDOS` ligado o universo passa a ser "o que ainda está
    vivo HOJE", e então medir prazo a partir da data-base do CDA seria
    incoerente: "vence em 12 meses" contado de abr/2026 significaria "vence até
    abr/2027", ou seja só 8 meses à frente de quem está lendo a tela.

    Desligado, volta a valer a data-base da carteira, que descreve a foto como
    ela foi declarada.
    """
    if settings.CDA_EXCLUIR_VENCIDOS:
        return pd.Timestamp(date.today())
    mes = cvm_cda_arquivo.mes_alvo()
    return pd.Timestamp(f"{mes[:4]}-{mes[4:]}-01") + pd.offsets.MonthEnd(0)


def _tirar_vencidos(df: pd.DataFrame, mes: str) -> pd.DataFrame:
    """Remove o papel que já venceu, mantendo o mês corrente.

    O CDA vem com 4 meses de defasagem de propósito, então ele lista papel que
    estava vivo na data-base e já liquidou quando alguém abre a tela. Medido em
    18/08/2026 sobre o CDA de 2026-04: R$ 103,1 bi em 8.775 posições — 12,2% do
    estoque — vencidas entre maio e julho.

    O mês corrente fica inteiro: parte dele ainda vence, e cortar por dia exato
    daria a falsa impressão de precisão numa carteira que é de quatro meses
    atrás.
    """
    if not settings.CDA_EXCLUIR_VENCIDOS:
        return df
    corte = pd.Timestamp(date.today()).to_period("M").to_timestamp()
    vencido = df["dt_venc"].notna() & (df["dt_venc"] < corte)
    if vencido.any():
        logger.info(
            "CDA %s: %d posições já vencidas antes de %s descartadas "
            "(R$ %.1f bi); sobram %d.",
            mes, int(vencido.sum()), corte.strftime("%m/%Y"),
            df.loc[vencido, "valor"].sum() / 1e9, int((~vencido).sum()),
        )
    return df[~vencido]


def _sem_acento(v) -> str:
    s = unicodedata.normalize("NFKD", str(v or ""))
    return "".join(c for c in s if not unicodedata.combining(c)).upper().strip()


def _fresco(caminho) -> bool:
    return (
        caminho.exists()
        and (time.time() - caminho.stat().st_mtime) / 3600 < settings.CDA_TTL_HORAS
    )


def _instrumento(tp_ativo) -> str | None:
    s = _sem_acento(tp_ativo)
    if "LETRA FINANCEIRA" in s:
        return "lf"
    if "CDB" in s or "RDB" in s:
        return "cdb"
    if "DPGE" in s:
        return "dpge"
    if "LETRA" in s:  # câmbio / hipotecária / imobiliária
        return "outra_letra"
    return None


def _ler_blc5(mes: str) -> pd.DataFrame:
    """Lê e limpa o BLC_5 do CDA. Base comum das duas visões.

    Os dois consumidores — o mapa por par fundo×tesouraria (`carregar`) e as
    posições individuais (`carregar_posicoes`) — saem exatamente do mesmo
    arquivo e das mesmas regras de limpeza. Duplicar o parsing faria as duas
    telas divergirem no dia em que uma regra mudasse só de um lado.

    Devolve DataFrame vazio quando o CDA está indisponível: quem chama trata
    como "não temos o mapa", nunca como "o fundo não tem papel bancário".
    """
    z = cvm_cda_arquivo.abrir(mes)
    if z is None:
        logger.warning("CDA %s indisponível — seguindo sem papel bancário.", mes)
        return pd.DataFrame()

    nome = f"cda_fi_BLC_5_{mes}.csv"
    if nome not in z.namelist():
        logger.warning("CDA %s sem BLC_5 — seguindo sem papel bancário.", mes)
        return pd.DataFrame()

    df = pd.read_csv(z.open(nome), sep=";", encoding="latin-1", low_memory=False)
    faltando = {"CNPJ_EMISSOR", "EMISSOR", "VL_MERC_POS_FINAL"} - set(df.columns)
    if faltando:
        logger.warning("BLC_5 %s sem as colunas %s — formato mudou.", mes, faltando)
        return pd.DataFrame()

    # Posição negativa é ajuste/venda, não alocação.
    df = df[pd.to_numeric(df["VL_MERC_POS_FINAL"], errors="coerce") > 0].copy()
    df["instrumento"] = df["TP_ATIVO"].map(_instrumento)
    df = df[df["instrumento"].notna()]
    if df.empty:
        logger.warning("BLC_5 %s sem papel bancário identificável.", mes)
        return pd.DataFrame()

    df["cnpj_fundo"] = df["CNPJ_FUNDO_CLASSE"].map(so_digitos)
    df["cnpj_emissor"] = df["CNPJ_EMISSOR"].map(so_digitos)
    df = df[df["cnpj_fundo"].notna() & (df["cnpj_emissor"].str.len() == 14)]
    df["raiz_emissor"] = df["cnpj_emissor"].str[:8]
    df["valor"] = pd.to_numeric(df["VL_MERC_POS_FINAL"], errors="coerce")
    df["QT_POS_FINAL"] = pd.to_numeric(df.get("QT_POS_FINAL"), errors="coerce").fillna(0.0)
    # `EMISSOR_LIGADO = S` é posição intragrupo: o fundo da asset do banco
    # comprando papel do próprio banco. Para a mesa isso não é negócio
    # disputável, e sem a marca ela apareceria como o maior "cliente" do
    # emissor — a Bradesco Asset é a maior carregadora de papel do Bradesco.
    df["e_ligado"] = (
        df.get("EMISSOR_LIGADO", "").astype(str).str.strip().str.upper() == "S"
    )
    df["valor_ligado"] = df["valor"].where(df["e_ligado"], 0.0)
    # UM nome por raiz de CNPJ, escolhido sobre o arquivo inteiro. Limpar cada
    # linha isoladamente não basta: o administrador escreve "BRADESCO" numa
    # posição e "BANCO BRADESCO" na seguinte, e o mesmo banco apareceria com
    # dois nomes em linhas vizinhas da mesma carteira. A escolha acontece aqui,
    # antes das duas visões, para que ranking e detalhe não discordem.
    df["emissor"] = _nomes_canonicos(df)
    df["dt_venc"] = pd.to_datetime(df.get("DT_VENC"), errors="coerce")
    # Aqui, e não em cada consumidor: as duas telas (tesourarias e papel
    # bancário) precisam descrever o mesmo universo, senão os totais divergem.
    df = _tirar_vencidos(df, mes)

    _preparar_preco(df)
    return df


def carregar() -> pd.DataFrame:
    """Posições de papel bancário por (fundo, emissor).

    Uma linha por par fundo × tesouraria, com:
        cnpj_fundo, raiz_emissor, emissor, valor,
        pct_cdi, spread            (medianas ponderadas por valor)
        prazo_dias                 (média ponderada, a partir de DT_VENC)
        valor_venc_12m             (quanto vence em até 12 meses)
        pct_lf, pct_cdb            (mix por instrumento dentro do par)
        carteira_data

    DataFrame vazio quando o CDA está indisponível — quem chama trata como
    "não temos o mapa", nunca como "o fundo não tem papel bancário".
    """
    if _fresco(_CACHE):
        logger.info("Emissores: usando cache local.")
        return pd.read_parquet(_CACHE)

    mes = cvm_cda_arquivo.mes_alvo()
    df = _ler_blc5(mes)
    if df.empty:
        return pd.DataFrame()

    # O MESMO recorte da visão por posição: LF, CDB e DPGE. Sem isto, a aba
    # de tesourarias somaria também letra de câmbio/hipotecária/imobiliária
    # e mostraria um total diferente da aba de papel bancário — a diferença
    # é pequena (R$ 0,1 bi), e é exatamente por ser pequena que ninguém
    # descobriria a causa ao notar que as duas telas discordam.
    df = df[df["instrumento"].isin(INSTRUMENTOS_BANCARIOS)].copy()
    if df.empty:
        logger.warning("BLC_5 %s sem LF/CDB/DPGE.", mes)
        return pd.DataFrame()

    _preparar_prazo(df, mes)
    out = _agregar_por_par(df)
    out["carteira_data"] = f"{mes[:4]}-{mes[4:]}"

    logger.info(
        "BLC_5 %s: R$ %.1f bi de papel bancário | %d emissores (de %d grafias) "
        "| %d fundos | %d pares fundo×tesouraria.",
        mes, out["valor"].sum() / 1e9, out["raiz_emissor"].nunique(),
        df["EMISSOR"].nunique(), out["cnpj_fundo"].nunique(), len(out),
    )

    try:
        out.to_parquet(_CACHE, index=False)
    except Exception as e:  # noqa: BLE001
        logger.debug("cache dos emissores: %s", e)
    return out


def _classificar_indexador(v) -> str:
    """DS_INDEXADOR_POSFX -> cdi | selic | ipca | pre | outro."""
    s = _sem_acento(v)
    if not s or s == "NAN":
        return "outro"
    if "DI DE UM DIA" in s or "CDI" in s:
        return "cdi"
    if "SELIC" in s:
        return "selic"
    if "IPCA" in s or "IGP" in s or "INPC" in s or "PRECO" in s:
        return "ipca"
    if "PREFIX" in s:
        return "pre"
    return "outro"


def _preparar_preco(df: pd.DataFrame) -> None:
    """Extrai indexador, % do CDI e spread, cada um na sua faixa plausível.

    O CDA cota papel bancário de duas formas e guarda as duas em campos
    diferentes: `PR_INDEXADOR_POSFX` é o percentual do índice (103,5 = "103,5%
    do CDI") e `PR_CUPOM_POSFX` é o spread sobre ele ("CDI + 0,9%"). A forma
    dominante no mercado é a segunda — a mediana do `PR_INDEXADOR_POSFX` é
    exatamente 100.

    O spread vale para TODOS os indexadores, não só CDI: a mediana é 0,90 em
    papel de DI, 7,30 em IPCA e 12,80 em prefixado (aí o "spread" é a própria
    taxa). Por isso o teto de sanidade é por indexador — um teto único cortaria
    o papel de IPCA legítimo ou deixaria passar lixo no de CDI.

    Fora de faixa vira NaN e nunca zero: a posição com preço ilegível precisa
    aparecer sem preço, e não a "CDI + 0%", que a mesa leria como um negócio
    feito na bacia das almas.
    """
    df["indexador"] = df.get("DS_INDEXADOR_POSFX", "").map(_classificar_indexador)

    # O percentual do índice só faz sentido em pós-fixado de CDI/Selic.
    pct = pd.to_numeric(df.get("PR_INDEXADOR_POSFX"), errors="coerce")
    e_pos = df["indexador"].isin(["cdi", "selic"])
    df["pct_cdi"] = pct.where(e_pos & pct.between(_PCT_CDI_MIN, _PCT_CDI_MAX))

    cupom = pd.to_numeric(df.get("PR_CUPOM_POSFX"), errors="coerce")
    teto = df["indexador"].map(_TETO_CUPOM)
    df["spread"] = cupom.where(cupom.notna() & teto.notna()
                               & (cupom >= 0) & (cupom <= teto))

    df["e_di"] = e_pos


def _preparar_prazo(df: pd.DataFrame, mes: str) -> None:
    """Prazo até o vencimento, em dias, medido a partir do mês do CDA.

    A referência é a data-base da carteira e não hoje: o CDA vem com meses de
    defasagem, e usar `hoje` faria um papel de 6 meses aparecer como de 2. O
    calendário de rolagem que sai daqui precisa bater com a data que a tela
    mostra ao lado.
    """
    base = data_referencia()
    venc = pd.to_datetime(df.get("DT_VENC"), errors="coerce")
    prazo = (venc - base).dt.days
    # Prazo negativo é papel já vencido na data-base (erro de preenchimento);
    # acima de 30 anos é a data-limite 2050-12-31 que alguns usam como "sem
    # vencimento". Nenhum dos dois descreve prazo.
    df["prazo_dias"] = prazo.where(prazo.between(0, 30 * 365))
    df["vence_12m"] = df["prazo_dias"].between(0, 365)
    # A faixa é atribuída POSIÇÃO A POSIÇÃO e só depois somada. Derivar a curva
    # do prazo médio do par perderia o que interessa: um par com metade em 60
    # dias e metade em 5 anos tem média de 2,6 anos e nada vencendo nela.
    df["faixa_prazo"] = pd.cut(
        df["prazo_dias"],
        bins=[-1, 90, 180, 365, 730, 30 * 365],
        labels=list(FAIXAS_PRAZO),
    )


def _agregar_por_par(df: pd.DataFrame) -> pd.DataFrame:
    """Uma linha por (fundo, raiz do emissor), com preço ponderado por valor."""

    def _pond(g: pd.DataFrame, campo: str) -> float | None:
        """Média do campo ponderada pelo valor da posição.

        Ponderar importa: um par com R$ 200 mi a CDI+0,8% e R$ 1 mi a CDI+3%
        não está tomando a 1,9%. A média simples diria que sim.
        """
        sub = g[g[campo].notna()]
        peso = sub["valor"].sum()
        if not peso:
            return None
        return float((sub[campo] * sub["valor"]).sum() / peso)

    linhas = []
    for (cnpj, raiz), g in df.groupby(["cnpj_fundo", "raiz_emissor"], sort=False):
        valor = float(g["valor"].sum())
        por_instr = g.groupby("instrumento")["valor"].sum()
        por_faixa = g.groupby("faixa_prazo", observed=False)["valor"].sum()
        linhas.append({
            "cnpj_fundo": cnpj,
            "raiz_emissor": raiz,
            # A grafia que responde por mais dinheiro é a que vai para a tela.
            "emissor": _nome_dominante(g),
            "valor": valor,
            "pct_cdi": _pond(g, "pct_cdi"),
            "spread": _pond(g, "spread"),
            "prazo_dias": _pond(g, "prazo_dias"),
            "valor_venc_12m": float(g.loc[g["vence_12m"], "valor"].sum()),
            "valor_ligado": float(g["valor_ligado"].sum()),
            "pct_lf": float(por_instr.get("lf", 0.0)) / valor if valor else 0.0,
            "pct_cdb": float(por_instr.get("cdb", 0.0)) / valor if valor else 0.0,
            "posicoes": int(len(g)),
            **{f"venc_{chave}": float(por_faixa.get(chave, 0.0)) for chave in FAIXAS_PRAZO},
        })
    return pd.DataFrame(linhas)


# Abreviações que o administrador usa e que estragam a leitura na tela.
# "B." aparece como abreviação de Banco em "B. VOLKSWAGEN" (R$ 3,9 bi). Sem
# expandir, a limpeza não reconhece o prefixo e o emissor fica partido em duas
# grafias — a fronteira de palavra garante que só o "B" isolado é atingido.
_ABREVIACOES = ((r"\bBCO\b", "BANCO"), (r"\bBC\b", "BANCO"), (r"\bB\b", "BANCO"),
                (r"\bFIN\b", "FINANCEIRA"))


def _nomes_canonicos(df: pd.DataFrame) -> pd.Series:
    """Mapeia cada raiz de CNPJ para uma única grafia, e devolve a coluna.

    Vence a grafia que responde por mais VALOR, depois da limpeza.

    Já foi "a mais longa", porque a de maior valor devolvia formas truncadas
    como "BCO BRASIL SA" e "ITAU UNIBANCO H". A limpeza resolveu isso na raiz —
    "BCO" vira "BANCO", o sufixo societário cai — e aí o critério de tamanho
    passou a escolher o pior nome: entre "VOLKSWAGEN" (R$ 12,9 bi somando as
    variantes) e "B VOLKSWAGEN" (R$ 3,9 bi), ganhava o segundo por ter dois
    caracteres a mais. Normalizadas, as grafias boas se somam e o valor vira o
    melhor voto.
    """
    limpo = df["EMISSOR"].astype(str).map(_limpar_nome)
    tabela = pd.DataFrame({"raiz": df["raiz_emissor"], "nome": limpo,
                           "valor": df["valor"]})
    escolha: dict[str, str] = {}
    for raiz, g in tabela.groupby("raiz", sort=False):
        por_nome = g.groupby("nome")["valor"].sum()
        por_nome = por_nome[por_nome.index.str.len() > 0]
        if not len(por_nome):
            continue
        escolha[raiz] = por_nome.idxmax()
    return df["raiz_emissor"].map(escolha).fillna(limpo)


def _limpar_nome(nome: str) -> str:
    """Normaliza a grafia do emissor para exibição.

    Tira sufixo societário, pontuação e a palavra "BANCO" — que não distingue
    nada numa lista em que todo mundo é banco, e só consome a largura da
    coluna. O objetivo não é achar o nome jurídico correto: é que a mesma
    tesouraria apareça escrita do mesmo jeito em todas as telas, e curta.

    >>> SEMPRE COM FRONTEIRA DE PALAVRA
        UNIBANCO, AGIBANK, OURIBANK, NUBANK e BLUEBANK contêm "BANC" e não
        podem ser tocados — um replace ingênuo transformaria "ITAU UNIBANCO"
        em "ITAU UNI".

    >>> "BANCO DO ..." FICA INTEIRO
        Tirar o "BANCO" de "BANCO DO BRASIL" deixaria "DO BRASIL"; tirar
        também o conector deixaria "BRASIL", que numa lista com "ABC BRASIL" e
        "RCI BRASIL" vira ambiguidade sobre um emissor de R$ 53,7 bi. Nesses
        casos o nome permanece como está.
    """
    import re

    s = _sem_acento(nome)
    for padrao, troca in _ABREVIACOES:
        s = re.sub(padrao, troca, s)
    s = re.sub(r"[.,/]", " ", s)

    # Forma jurídica no fim do nome: "… BANCO MULTIPLO", "… BANCO ALEMAO".
    # `MULTIP\w*` cobre as várias truncagens que o CDA traz no mesmo emissor
    # ("MULTIPLO", "MULTIPL", "MULTIP"), que de outro modo virariam nomes
    # diferentes para o mesmo banco.
    s = re.sub(r"\bBANCO\s+(MULTIP\w*|ALEMAO)\s*$", "", s).strip()
    # Duas vezes, e nesta ordem: tirar a forma jurídica costuma expor o sufixo
    # societário que estava atrás dela ("… S A BANCO MULTIPLO" -> "… S A").
    for _ in range(2):
        s = re.sub(r"\b(S\s*A|SA|LTDA|ME|EIRELI|BM|CFI|DTVM|CCVM)\b\s*$", "", s).strip()
    # "CONCORDIA BANCO", "PARANA BANCO", "SCANIA BANCO".
    s = re.sub(r"\bBANCO\s*$", "", s).strip()
    # "BANCO BRADESCO" -> "BRADESCO", mas "BANCO DO BRASIL" fica inteiro.
    s = re.sub(r"^BANCO\s+(?!(?:DO|DA|DE|DOS|DAS)\b)", "", s).strip()

    s = re.sub(r"[\s-]+$", "", s).strip()
    return re.sub(r"\s+", " ", s)


def _nome_dominante(g: pd.DataFrame) -> str:
    """O nome do emissor do grupo. Já é único por raiz — ver `_nomes_canonicos`."""
    nomes = g["emissor"].dropna()
    return str(nomes.iloc[0]) if len(nomes) else ""


def carregar_posicoes() -> pd.DataFrame:
    """Papel bancário POSIÇÃO A POSIÇÃO, com vencimento e preço exatos.

    É a granularidade que a mesa negocia: um papel é um emissor, uma data de
    vencimento e uma taxa. A tabela de pares (`carregar`) resume isso por
    fundo × tesouraria e é o que alimenta o ranking; aqui nada é resumido.

    Colunas: cnpj_fundo, raiz_emissor, emissor, instrumento, dt_venc,
    mes_venc (AAAA-MM), indexador, pct_cdi, spread, valor, quantidade, ligado.

    Só entra o que é funding de tesouraria (`INSTRUMENTOS_BANCARIOS`): LF, CDB
    e DPGE. Letra de câmbio/hipotecária/imobiliária e o "Outros" do arquivo
    ficam fora — não são o produto desta conversa, e o "Outros" são 18 mil
    linhas sem tipo declarado que só sujariam o total.

    Linhas idênticas (mesmo fundo, emissor, vencimento, indexador e taxa) são
    somadas: o CDA às vezes quebra a mesma posição em várias linhas, e na tela
    elas seriam a mesma coisa repetida.
    """
    if _fresco(_CACHE_POSICOES):
        logger.info("Posições bancárias: usando cache local.")
        return pd.read_parquet(_CACHE_POSICOES)

    mes = cvm_cda_arquivo.mes_alvo()
    df = _ler_blc5(mes)
    if df.empty:
        return pd.DataFrame()

    df = df[df["instrumento"].isin(INSTRUMENTOS_BANCARIOS)].copy()
    if df.empty:
        logger.warning("BLC_5 %s sem LF/CDB/DPGE.", mes)
        return pd.DataFrame()

    df["mes_venc"] = df["dt_venc"].dt.strftime("%Y-%m")
    chaves = ["cnpj_fundo", "raiz_emissor", "emissor", "instrumento",
              "dt_venc", "mes_venc", "indexador", "pct_cdi", "spread"]
    out = (
        df.groupby(chaves, dropna=False, observed=True)
        .agg(valor=("valor", "sum"),
             quantidade=("QT_POS_FINAL", "sum"),
             ligado=("e_ligado", "max"))
        .reset_index()
    )
    out["carteira_data"] = f"{mes[:4]}-{mes[4:]}"

    logger.info(
        "BLC_5 %s: %d posições de LF/CDB/DPGE, R$ %.1f bi, %d fundos, "
        "%d vencimentos distintos.",
        mes, len(out), out["valor"].sum() / 1e9, out["cnpj_fundo"].nunique(),
        out["mes_venc"].nunique(),
    )
    try:
        out.to_parquet(_CACHE_POSICOES, index=False)
    except Exception as e:  # noqa: BLE001
        logger.debug("cache das posições: %s", e)
    return out


def nomes_por_raiz(emissores: pd.DataFrame) -> dict[str, str]:
    """Mapa {raiz: nome} escolhendo, para cada raiz, a grafia de maior valor.

    Feito sobre a tabela já agregada porque o nome precisa ser o mesmo no
    ranking e no dossiê — duas telas chamando o mesmo banco de nomes
    diferentes é o tipo de detalhe que faz a mesa desconfiar do resto.
    """
    if emissores.empty:
        return {}
    idx = emissores.groupby("raiz_emissor")["valor"].idxmax()
    return dict(zip(emissores.loc[idx, "raiz_emissor"], emissores.loc[idx, "emissor"],
                    strict=True))
