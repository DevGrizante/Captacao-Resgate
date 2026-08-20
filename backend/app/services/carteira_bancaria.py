"""
A carteira de papel bancário vista pelo FUNDO, posição a posição.

>>> POR QUE ESTA TELA EXISTE AO LADO DA DE TESOURARIAS

São a mesma matéria-prima lida por duas pontas. `services/tesourarias.py`
pergunta "quem carrega o meu papel"; aqui a pergunta é a inversa e mais
concreta: **"o que exatamente este fundo tem na carteira?"** — emissor por
emissor, vencimento por vencimento, taxa por taxa.

A diferença não é cosmética. A tela de tesourarias agrega: prazo médio, spread
médio, faixas de vencimento. Isso responde "como está o meu funding no
mercado". Não responde "quando exatamente vence e a quanto ele comprou", que é
o que se precisa saber para ligar oferecendo a rolagem de um papel específico.

>>> NADA AQUI É MÉDIA

Cada linha é um papel: um emissor, uma data de vencimento, uma taxa. O CDA às
vezes quebra a mesma posição em várias linhas e essas são somadas, mas duas
posições do mesmo emissor com vencimentos ou taxas diferentes permanecem
separadas — juntá-las destruiria justamente a informação que a mesa procura.

A única média que sai daqui é o `spread_cdi` do resumo do fundo, e ela é
restrita ao papel pós-fixado em CDI/Selic. Misturar CDI (mediana +0,90%),
IPCA (+7,30%) e prefixado (12,80%) numa média só produziria um número que não
descreve nada.

>>> A MESMA CARTEIRA, LIDA PELAS DUAS PONTAS

O módulo indexa as posições duas vezes, por GESTORA e por EMISSOR, e serve as
duas leituras:

    gestora -> papel   `listar()` / `detalhe()`
        "o que esta casa tem na carteira?" — emissor por emissor.

    papel -> gestora   `listar_emissores()` / `detalhe_emissor()`
        "quem tem o meu papel, e vencendo quando?" — gestora por gestora.

A segunda não é a tela de Tesourarias com outro nome. Lá tudo é média (prazo
médio, spread médio, faixas de prazo), e média não diz com quem falar sobre o
bloco que vence em fev/27. Aqui o eixo continua sendo o mês de vencimento, e a
linha continua sendo um bloco concreto — só que agora com o emissor fixo e a
gestora variando.

Indexar as duas custa uma passada a mais na carga e nenhuma na requisição: os
dois dicionários apontam para os MESMOS dicionários de linha, então a memória
extra é a das listas, não a dos dados.

>>> ESCOPO: LF, CDB E DPGE

Só funding de tesouraria. Letra de câmbio/hipotecária/imobiliária e o "Outros"
do arquivo ficam fora. Ver `INSTRUMENTOS_BANCARIOS` em connectors/cvm_emissores.py,
que também documenta por que LFSC e LFSN não vêm separadas do dado público.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field

import pandas as pd

from app.connectors import cvm_emissores
from app.models.schemas import (
    CarregadorPapel,
    EmissorPapelBancario,
    EmissorPapelBancarioDetalhe,
    EmissorPapelBancarioNaLista,
    MesDoEmissor,
    FundoPapelBancario,
    FundoPapelBancarioDetalhe,
    Fundo,
    GestoraNoEmissor,
    PosicaoBancaria,
    TesourariaNaCarteira,
    VencimentoMes,
)

logger = logging.getLogger("carteira_bancaria")

# Pós-fixado em CDI/Selic: o único subconjunto em que uma média de spread
# descreve alguma coisa.
_INDEXADORES_POS = ("cdi", "selic")


@dataclass
class Contexto:
    """As posições indexadas pelas duas pontas, prontas para servir.

    Montado uma vez por carga. Os dois índices apontam para os mesmos
    dicionários de linha — `por_emissor` não é uma cópia dos dados.
    """
    por_gestora: dict = field(default_factory=dict)    # gestora -> list[dict]
    resumos: dict = field(default_factory=dict)        # gestora -> FundoPapelBancario
    ordenado: list = field(default_factory=list)       # resumos, maior valor primeiro
    # A ponta inversa: raiz do CNPJ do emissor -> as mesmas linhas.
    por_emissor: dict = field(default_factory=dict)    # raiz -> list[dict]
    resumos_emissor: dict = field(default_factory=dict)
    ordenado_emissor: list = field(default_factory=list)
    data: str | None = None

    def vazio(self) -> bool:
        return not self.por_gestora


def preparar(fundos: list[Fundo], posicoes: pd.DataFrame) -> Contexto:
    """Indexa as posições por GESTORA e pré-calcula o resumo de cada uma.

    >>> POR QUE A GESTORA, E NÃO O FUNDO

    Quem decide alocação é a casa. Uma asset com 40 fundos carregando LF do
    mesmo banco é UMA conversa, não 40 — e a posição que interessa à mesa é a
    somada, não a fatia de cada veículo.

    >>> SUBCLASSE NÃO PODE CONTAR DUAS VEZES

    O export lista a classe-mãe e cada subclasse como linhas separadas com o
    MESMO CNPJ. A tabela de posições já vem por CNPJ (uma linha por papel, não
    por subclasse), então somar posições entre CNPJs distintos é seguro. O PL,
    não: ele é o mesmo patrimônio, e por isso vem da linha de maior PL de cada
    CNPJ — que é a classe-mãe — antes de somar por gestora.
    """
    if posicoes is None or posicoes.empty:
        return Contexto()

    # A gestora vem do nosso universo, não do CDA: é o mesmo rótulo que o
    # usuário vê no resto do sistema, e o CDA traz a denominação social
    # completa, que ninguém reconhece.
    identidade: dict[str, Fundo] = {}
    for f in fundos:
        if not f.cnpj:
            continue
        atual = identidade.get(f.cnpj)
        # Entre subclasses do mesmo CNPJ, a de maior PL é a classe-mãe.
        if atual is None or (f.pl or 0) > (atual.pl or 0):
            identidade[f.cnpj] = f

    por_gestora: dict[str, list[dict]] = defaultdict(list)
    por_emissor: dict[str, list[dict]] = defaultdict(list)
    for linha in posicoes.to_dict("records"):
        fundo = identidade.get(linha["cnpj_fundo"])
        if fundo is None:
            continue
        # A gestora viaja DENTRO da linha porque a leitura pelo emissor precisa
        # dela e não tem por onde chegar ao fundo: lá a chave é o emissor, e
        # refazer o `identidade.get` por linha a cada requisição custaria uma
        # busca por posição num dossiê de 6 mil linhas.
        linha["gestora"] = fundo.gestora
        por_gestora[fundo.gestora].append(linha)
        por_emissor[linha["raiz_emissor"]].append(linha)

    data = None
    if "carteira_data" in posicoes.columns and len(posicoes):
        valores = posicoes["carteira_data"].dropna()
        data = str(valores.iloc[0]) if len(valores) else None

    resumos = {
        gestora: _resumir(gestora, identidade, linhas, data)
        for gestora, linhas in por_gestora.items()
    }
    ordenado = sorted(resumos.values(), key=lambda r: r.valor, reverse=True)

    # O share de cada emissor é fatia de um total que só existe depois de somar
    # todo mundo, então o universo é medido antes de resumir.
    universo = sum(r.valor for r in resumos.values())
    resumos_emissor = {
        raiz: _resumir_emissor(raiz, linhas, data, universo)
        for raiz, linhas in por_emissor.items()
    }
    ordenado_emissor = sorted(
        resumos_emissor.values(), key=lambda r: r.valor, reverse=True,
    )

    logger.info(
        "Carteira bancária: %d gestoras e %d emissores com LF/CDB/DPGE, "
        "%d posições, R$ %.1f bi.",
        len(por_gestora), len(por_emissor),
        sum(len(v) for v in por_gestora.values()), universo / 1e9,
    )
    return Contexto(por_gestora=dict(por_gestora), resumos=resumos,
                    ordenado=ordenado, por_emissor=dict(por_emissor),
                    resumos_emissor=resumos_emissor,
                    ordenado_emissor=ordenado_emissor, data=data)


def listar(ctx: Contexto, limite: int = 200, busca: str = "") -> list[FundoPapelBancario]:
    """Gestoras que carregam papel bancário, da maior para a menor."""
    if ctx.vazio():
        return []
    if not busca:
        return ctx.ordenado[:limite]
    q = busca.strip().lower()
    return [r for r in ctx.ordenado if q in r.gestora.lower()][:limite]


def detalhe(ctx: Contexto, gestora: str) -> FundoPapelBancarioDetalhe | None:
    """A carteira da gestora consolidada por emissor + tipo + mês de vencimento."""
    linhas = ctx.por_gestora.get(gestora)
    resumo = ctx.resumos.get(gestora)
    if not linhas or resumo is None:
        return None

    posicoes = _consolidar(linhas)
    # Ordem cronológica: a leitura natural é a agenda, do que vence primeiro
    # ao que vence por último; dentro do mês, o maior bloco primeiro. Papel sem
    # vencimento (perpétuo) vai para o fim, e não para o começo, que é onde a
    # string vazia cairia sozinha.
    posicoes.sort(key=lambda p: (p.mes_venc == "", p.mes_venc, -p.valor))

    # A agenda soma as posições CRUAS, não as consolidadas: o total do mês tem
    # que bater com a carteira independentemente de como as linhas se agruparam.
    por_mes: dict[str, list[float]] = defaultdict(list)
    for l in linhas:
        mes = _mes(l.get("mes_venc"))
        if mes:
            por_mes[mes].append(float(l["valor"]))

    return FundoPapelBancarioDetalhe(
        gestora=resumo,
        posicoes=posicoes,
        por_mes=[
            VencimentoMes(mes=m, valor=sum(vs), posicoes=len(vs))
            for m, vs in sorted(por_mes.items())
        ],
        por_emissor=_por_emissor(linhas),
    )


# ---------- a ponta do emissor ----------
def listar_emissores(ctx: Contexto, limite: int = 300,
                     busca: str = "") -> list[EmissorPapelBancarioNaLista]:
    """Emissores cujo papel está nas carteiras, do maior estoque ao menor."""
    if ctx.vazio():
        return []
    if not busca:
        return ctx.ordenado_emissor[:limite]
    q = busca.strip().lower()
    return [r for r in ctx.ordenado_emissor if q in r.emissor.lower()][:limite]


def detalhe_emissor(ctx: Contexto, raiz: str) -> EmissorPapelBancarioDetalhe | None:
    """Quem carrega o papel deste emissor, consolidado por gestora + tipo + mês.

    O espelho de `detalhe()`, e de propósito com a mesma forma: a tela troca a
    coluna da gestora pela do emissor e o resto continua sendo lido do mesmo
    jeito — mesma agenda, mesmos chips de concentração, mesma tabela.
    """
    linhas = ctx.por_emissor.get(raiz)
    resumo = ctx.resumos_emissor.get(raiz)
    if not linhas or resumo is None:
        return None

    posicoes = _consolidar_carregadores(linhas)
    # Mesma ordem da outra ponta: agenda cronológica, maior bloco primeiro
    # dentro do mês, perpétuo no fim.
    posicoes.sort(key=lambda p: (p.mes_venc == "", p.mes_venc, -p.valor))

    por_mes: dict[str, list[float]] = defaultdict(list)
    for l in linhas:
        mes = _mes(l.get("mes_venc"))
        if mes:
            por_mes[mes].append(float(l["valor"]))

    return EmissorPapelBancarioDetalhe(
        emissor=resumo,
        posicoes=posicoes,
        por_mes=[
            VencimentoMes(mes=m, valor=sum(vs), posicoes=len(vs))
            for m, vs in sorted(por_mes.items())
        ],
        por_gestora=_por_gestora(linhas),
    )


# ---------- internos ----------
# Corte que separa as duas formas de cotar papel pós-fixado. Abaixo dele o
# número é spread sobre o CDI ("CDI + 1,35%"); acima, é percentual do DI
# ("102% do DI"). Não há ambiguidade real na faixa: spread de papel bancário
# não passa de ~5% e percentual não desce de ~95%, então 90 fica no vazio
# entre as duas populações.
_CORTE_FORMA = 90.0


def _taxa_e_forma(linha: dict) -> tuple[float | None, str | None]:
    """Reduz os dois campos de preço do CDA a UM número mais o seu significado.

    O CDA guarda `PR_CUPOM_POSFX` (spread) e `PR_INDEXADOR_POSFX` (percentual
    do índice) em colunas separadas, e cada papel usa uma das duas. A tela
    mostra um número só, então a escolha acontece aqui — e o rótulo viaja
    junto, porque "1,35" e "102" não significam nada sem ele.

    >>> PREFIXADO SEGUE A MESMA RÉGUA, POR DECISÃO DO NEGÓCIO.
        Só o IPCA sai da regra do corte. Papel prefixado tem taxa cheia (~12,5%),
        cai abaixo de 90 e portanto aparece como "CDI + 12,54%". Fica registrado
        que o rótulo não descreve a remuneração desse papel — ele não paga CDI
        mais nada, paga 12,54% fixos. Para separá-lo de novo basta devolver
        `"pre"` quando `idx == "pre"`.
    """
    idx = str(linha.get("indexador") or "")
    spread = _num(linha.get("spread"))
    pct = _num(linha.get("pct_cdi"))

    if idx == "ipca":
        return spread, "ipca"

    taxa = spread if (spread is not None and spread > 0) else pct
    if taxa is None:
        return None, None
    return taxa, ("cdi_spread" if taxa < _CORTE_FORMA else "pct_di")


def _consolidar(linhas: list[dict]) -> list[PosicaoBancaria]:
    """Junta o que é o mesmo bloco: emissor + tipo + mês de vencimento.

    A FORMA DA TAXA ENTRA NA CHAVE, e isso não é detalhe. Um fundo pode ter, do
    mesmo emissor e vencendo no mesmo mês, um papel a "CDI + 1,35%" e outro a
    "102% do DI". Somar os dois numa linha só obrigaria a mediar 1,35 com 102 —
    o resultado seria ~51, que a tela mostraria como "51,7% do DI": um papel
    que ninguém emitiu e ninguém comprou. Separados, cada linha continua
    verdadeira, e o caso é raro o bastante para não poluir a leitura.

    A taxa da linha consolidada é média ponderada pelo volume: um bloco de
    R$ 200 mi a CDI+0,80% com uma ponta de R$ 1 mi a CDI+3% custa 0,81%, não
    1,90%.
    """
    grupos: dict[tuple, dict] = {}
    for l in linhas:
        taxa, forma = _taxa_e_forma(l)
        mes = _mes(l.get("mes_venc"))
        chave = (l["raiz_emissor"], l["instrumento"], mes, forma)
        g = grupos.get(chave)
        if g is None:
            g = grupos[chave] = {
                "raiz_emissor": l["raiz_emissor"], "emissor": l["emissor"],
                "instrumento": l["instrumento"], "mes_venc": mes,
                "indexador": str(l.get("indexador") or ""), "forma": forma,
                "valor": 0.0, "qtd": 0.0, "papeis": 0,
                "num": 0.0, "den": 0.0, "ligado": 0.0,
            }
        v = float(l["valor"])
        g["valor"] += v
        g["papeis"] += 1
        g["qtd"] += _num(l.get("quantidade")) or 0.0
        if l.get("ligado"):
            g["ligado"] += v
        if taxa is not None:
            g["num"] += taxa * v
            g["den"] += v

    return [
        PosicaoBancaria(
            raiz_emissor=g["raiz_emissor"],
            emissor=g["emissor"],
            instrumento=g["instrumento"],
            mes_venc=g["mes_venc"],
            indexador=g["indexador"],
            taxa=round(g["num"] / g["den"], 4) if g["den"] else None,
            forma=g["forma"],
            valor=g["valor"],
            papeis=g["papeis"],
            quantidade=g["qtd"] or None,
            pct_ligado=round(g["ligado"] / g["valor"] * 100, 1) if g["valor"] else None,
            # Metade ou mais do bloco vindo de dentro do grupo é o que
            # caracteriza a linha como intragrupo — mesma régua da aba de
            # tesourarias, para as duas telas não discordarem sobre o mesmo par.
            ligado=g["ligado"] >= g["valor"] * 0.5 if g["valor"] else False,
        )
        for g in grupos.values()
    ]


def _resumir(gestora: str, identidade: dict[str, Fundo], linhas: list[dict],
             data: str | None) -> FundoPapelBancario:
    """Resume a carteira bancária de uma gestora.

    O PL é somado uma vez por CNPJ, e não por linha de posição: um fundo com
    300 papéis do mesmo emissor entraria 300 vezes no denominador e faria o
    "% do PL" virar uma fração absurda.
    """
    valor = sum(float(l["valor"]) for l in linhas)
    por_instr: dict[str, float] = defaultdict(float)
    venc_3m = venc_12m = 0.0
    num_prazo = den_prazo = 0.0
    num_spread = den_spread = 0.0
    base = _base(data)
    limite_3m = base + pd.DateOffset(months=3)
    limite_12m = base + pd.DateOffset(years=1)

    for l in linhas:
        v = float(l["valor"])
        por_instr[l["instrumento"]] += v
        venc = l["dt_venc"]
        if pd.notna(venc):
            if venc <= limite_12m:
                venc_12m += v
            if venc <= limite_3m:
                venc_3m += v
            dias = (venc - base).days
            if 0 <= dias <= 30 * 365:
                num_prazo += dias * v
                den_prazo += v
        if l.get("indexador") in _INDEXADORES_POS and pd.notna(l.get("spread")):
            num_spread += float(l["spread"]) * v
            den_spread += v

    # PL da casa, contado UMA vez por CNPJ. `identidade` já guarda, para cada
    # CNPJ, a linha de maior PL — a classe-mãe — então somar aqui não repete o
    # patrimônio das subclasses.
    cnpjs = {l["cnpj_fundo"] for l in linhas}
    pls = [identidade[c].pl for c in cnpjs
           if c in identidade and identidade[c].pl is not None]
    pl = sum(pls) if pls else None

    fatia = lambda k: (por_instr.get(k, 0.0) / valor) if valor else None  # noqa: E731
    return FundoPapelBancario(
        gestora=gestora,
        fundos=len(cnpjs),
        valor=valor,
        posicoes=len(linhas),
        emissores=len({l["raiz_emissor"] for l in linhas}),
        pl=pl,
        pct_pl=round(valor / pl * 100, 1) if pl else None,
        spread_cdi=round(num_spread / den_spread, 2) if den_spread else None,
        prazo_dias=round(num_prazo / den_prazo) if den_prazo else None,
        valor_venc_3m=venc_3m,
        valor_venc_12m=venc_12m,
        pct_lf=fatia("lf"),
        pct_cdb=fatia("cdb"),
        pct_dpge=fatia("dpge"),
        carteira_data=data,
    )


def _resumir_emissor(raiz: str, linhas: list[dict], data: str | None,
                     universo: float) -> EmissorPapelBancarioNaLista:
    """Resume o estoque de um emissor espalhado pelas carteiras.

    Repete as contas de `_resumir` porque elas são as mesmas — prazo e taxa
    ponderados por valor, duas janelas de rolagem, mix por instrumento. O que
    não existe aqui é o PL: um emissor não tem patrimônio nesta base, e o lugar
    dele na tela é ocupado pelo `share_pct`, a fatia do papel bancário do
    universo.
    """
    valor = sum(float(l["valor"]) for l in linhas)
    por_instr: dict[str, float] = defaultdict(float)
    venc_3m = venc_12m = 0.0
    num_prazo = den_prazo = 0.0
    num_spread = den_spread = 0.0
    ligado = 0.0
    base = _base(data)
    limite_3m = base + pd.DateOffset(months=3)
    limite_12m = base + pd.DateOffset(years=1)
    # A agenda sai do mesmo laço: é a mesma passada, e o custo é um dicionário.
    agenda: dict[str, dict] = {}

    for l in linhas:
        v = float(l["valor"])
        por_instr[l["instrumento"]] += v
        if l.get("ligado"):
            ligado += v

        mes = agenda.get(_mes(l.get("mes_venc")))
        if mes is None:
            mes = agenda[_mes(l.get("mes_venc"))] = {
                "valor": 0.0, "posicoes": 0, "gestoras": set(),
                "num": 0.0, "den": 0.0, "instr": defaultdict(float),
            }
        mes["valor"] += v
        mes["posicoes"] += 1
        mes["gestoras"].add(l["gestora"])
        mes["instr"][l["instrumento"]] += v
        if l.get("indexador") in _INDEXADORES_POS and pd.notna(l.get("spread")):
            mes["num"] += float(l["spread"]) * v
            mes["den"] += v
        venc = l["dt_venc"]
        if pd.notna(venc):
            if venc <= limite_12m:
                venc_12m += v
            if venc <= limite_3m:
                venc_3m += v
            dias = (venc - base).days
            if 0 <= dias <= 30 * 365:
                num_prazo += dias * v
                den_prazo += v
        if l.get("indexador") in _INDEXADORES_POS and pd.notna(l.get("spread")):
            num_spread += float(l["spread"]) * v
            den_spread += v

    fatia = lambda k: (por_instr.get(k, 0.0) / valor) if valor else None  # noqa: E731
    # O nome vem da grafia de maior valor: o CDA traz a mesma casa escrita de
    # dezenas de formas, e a raiz do CNPJ é a única chave estável.
    nomes: dict[str, float] = defaultdict(float)
    for l in linhas:
        nomes[l["emissor"]] += float(l["valor"])

    return EmissorPapelBancarioNaLista(
        raiz=raiz,
        emissor=max(nomes, key=nomes.get),
        gestoras=len({l["gestora"] for l in linhas}),
        fundos=len({l["cnpj_fundo"] for l in linhas}),
        valor=valor,
        posicoes=len(linhas),
        share_pct=round(valor / universo * 100, 2) if universo else None,
        spread_cdi=round(num_spread / den_spread, 2) if den_spread else None,
        prazo_dias=round(num_prazo / den_prazo) if den_prazo else None,
        valor_venc_3m=venc_3m,
        valor_venc_12m=venc_12m,
        pct_lf=fatia("lf"),
        pct_cdb=fatia("cdb"),
        pct_dpge=fatia("dpge"),
        pct_ligado=round(ligado / valor * 100, 1) if valor else None,
        carteira_data=data,
        meses=[
            MesDoEmissor(
                mes=m,
                valor=d["valor"],
                posicoes=d["posicoes"],
                gestoras=len(d["gestoras"]),
                taxa=round(d["num"] / d["den"], 2) if d["den"] else None,
                pct_lf=d["instr"].get("lf", 0.0) / d["valor"] if d["valor"] else None,
                pct_cdb=d["instr"].get("cdb", 0.0) / d["valor"] if d["valor"] else None,
                pct_dpge=d["instr"].get("dpge", 0.0) / d["valor"] if d["valor"] else None,
            )
            # Ordem cronológica, com o perpétuo no fim — a mesma da agenda do
            # detalhe, para o seletor da lista e o do dossiê lerem igual.
            for m, d in sorted(agenda.items(), key=lambda kv: (kv[0] == "", kv[0]))
        ],
    )


def _consolidar_carregadores(linhas: list[dict]) -> list[CarregadorPapel]:
    """Junta o que é o mesmo bloco: gestora + tipo + mês de vencimento + forma.

    A mesma regra de `_consolidar`, com a gestora no lugar do emissor. A forma
    da taxa continua na chave pelo mesmo motivo: mediar "CDI + 1,35%" com
    "102% do DI" produziria um papel que ninguém emitiu.

    `fundos` conta os veículos por trás do bloco. Ele é informativo, não uma
    conversa a mais: quem decide alocação é a casa, e a mesa liga uma vez.
    """
    grupos: dict[tuple, dict] = {}
    for l in linhas:
        taxa, forma = _taxa_e_forma(l)
        mes = _mes(l.get("mes_venc"))
        chave = (l["gestora"], l["instrumento"], mes, forma)
        g = grupos.get(chave)
        if g is None:
            g = grupos[chave] = {
                "gestora": l["gestora"], "instrumento": l["instrumento"],
                "mes_venc": mes, "indexador": str(l.get("indexador") or ""),
                "forma": forma, "valor": 0.0, "qtd": 0.0, "papeis": 0,
                "num": 0.0, "den": 0.0, "ligado": 0.0, "fundos": set(),
            }
        v = float(l["valor"])
        g["valor"] += v
        g["papeis"] += 1
        g["fundos"].add(l["cnpj_fundo"])
        g["qtd"] += _num(l.get("quantidade")) or 0.0
        if l.get("ligado"):
            g["ligado"] += v
        if taxa is not None:
            g["num"] += taxa * v
            g["den"] += v

    return [
        CarregadorPapel(
            gestora=g["gestora"],
            instrumento=g["instrumento"],
            mes_venc=g["mes_venc"],
            indexador=g["indexador"],
            taxa=round(g["num"] / g["den"], 4) if g["den"] else None,
            forma=g["forma"],
            valor=g["valor"],
            papeis=g["papeis"],
            fundos=len(g["fundos"]),
            quantidade=g["qtd"] or None,
            pct_ligado=round(g["ligado"] / g["valor"] * 100, 1) if g["valor"] else None,
            ligado=g["ligado"] >= g["valor"] * 0.5 if g["valor"] else False,
        )
        for g in grupos.values()
    ]


def _por_gestora(linhas: list[dict]) -> list[GestoraNoEmissor]:
    """Concentração por casa dentro do estoque do emissor."""
    acc: dict[str, dict] = {}
    total = 0.0
    base = _base(None)   # a régua de prazo é sempre hoje; ver `_base`
    limite_12m = base + pd.DateOffset(years=1)
    for l in linhas:
        d = acc.setdefault(l["gestora"], {"valor": 0.0, "num": 0.0, "den": 0.0,
                                          "venc12": 0.0, "ligado": 0.0})
        v = float(l["valor"])
        d["valor"] += v
        total += v
        if l.get("ligado"):
            d["ligado"] += v
        venc = l["dt_venc"]
        if pd.notna(venc) and venc <= limite_12m:
            d["venc12"] += v
        if l.get("indexador") in _INDEXADORES_POS and pd.notna(l.get("spread")):
            d["num"] += float(l["spread"]) * v
            d["den"] += v

    saida = [
        GestoraNoEmissor(
            gestora=gestora,
            valor=d["valor"],
            pct_do_emissor=round(d["valor"] / total * 100, 1) if total else None,
            spread=round(d["num"] / d["den"], 2) if d["den"] else None,
            valor_venc_12m=d["venc12"],
            # Mesma régua de `_consolidar`: a marca de intragrupo só liga
            # quando a maior parte do que a casa carrega é do próprio grupo.
            ligado=d["ligado"] >= d["valor"] * 0.5 if d["valor"] else False,
        )
        for gestora, d in acc.items()
    ]
    saida.sort(key=lambda g: g.valor, reverse=True)
    return saida


def _por_emissor(linhas: list[dict]) -> list[TesourariaNaCarteira]:
    """Concentração por tesouraria dentro da gestora."""
    acc: dict[str, dict] = {}
    total = 0.0
    for l in linhas:
        raiz = l["raiz_emissor"]
        d = acc.setdefault(raiz, {"nome": l["emissor"], "valor": 0.0,
                                  "num": 0.0, "den": 0.0, "venc12": 0.0})
        v = float(l["valor"])
        d["valor"] += v
        total += v
        if l.get("indexador") in _INDEXADORES_POS and pd.notna(l.get("spread")):
            d["num"] += float(l["spread"]) * v
            d["den"] += v

    saida = [
        TesourariaNaCarteira(
            raiz=raiz,
            nome=d["nome"],
            valor=d["valor"],
            pct_do_bancario=round(d["valor"] / total * 100, 1) if total else None,
            spread=round(d["num"] / d["den"], 2) if d["den"] else None,
        )
        for raiz, d in acc.items()
    ]
    saida.sort(key=lambda t: t.valor, reverse=True)
    return saida


def _base(data: str | None):
    """A referência de prazo, delegada ao conector.

    Ela deixou de ser sempre a data-base da carteira: com o filtro de papel
    vencido ligado, o universo é "o que está vivo hoje" e a régua passa a ser
    hoje. Manter as duas decisões no mesmo lugar evita que a tela conte prazo
    de um jeito e filtre de outro. Ver `cvm_emissores.data_referencia`.
    """
    return cvm_emissores.data_referencia()


def _num(v):
    return None if v is None or pd.isna(v) else float(v)


def _mes(v) -> str:
    """"AAAA-MM", ou vazio quando o papel não tem vencimento.

    Papel perpétuo existe de verdade nesta base: a LFSC (letra financeira
    subordinada de capital complementar) não vence — a B3 a registra com data
    9999, que vira nulo na leitura. Sem este tratamento a linha apareceria
    agrupada sob o mês "nan" e ordenada em qualquer lugar.
    """
    return "" if v is None or pd.isna(v) else str(v)
