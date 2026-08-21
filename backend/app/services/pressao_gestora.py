"""
Pressão de compra e venda por GESTORA — a leitura que antecipa o secundário.

>>> A REGRA DE NEGÓCIO, EM TRÊS LINHAS

    captação líquida  ->  a gestora vai COMPRAR papel
    resgate líquido   ->  a gestora vai VENDER papel
    papel vencendo    ->  ela precisa ROLAR, capte ela ou não

A mesa é B2B: fala com tesouraria de um lado e asset do outro. O que ela
precisa saber não é quanto um fundo rendeu — é o que a casa vai ter de comprar
ou vender, e em que papel. Este módulo produz exatamente isso.

>>> POR QUE A UNIDADE É A GESTORA, E NÃO O FUNDO

Quem decide alocação é a casa. Um fundo isolado pode estar captando enquanto
outro da mesma gestora sofre resgate, e o que chega ao mercado é o LÍQUIDO
entre eles. Classificar fundo a fundo produziria uma lista onde a mesma casa
aparece dos dois lados — e nenhum dos dois lados seria acionável.

>>> AS TRÊS DIMENSÕES, E POR QUE JUNTAS

    DIREÇÃO      captação líquida menos resgate, na janela escolhida.
                 Diz o SINAL da pressão.
    COMPOSIÇÃO   em que papel a casa entra (LF, CDB, debênture) e a que
                 indexador (IPCA, CDI, pré). Diz ONDE a pressão vai bater.
    AGENDA       quanto vence em 3, 6 e 12 meses, pelo mesmo eixo.
                 Diz QUANDO, e é a dimensão que não depende do fluxo.

Sozinha, cada uma engana. Uma gestora com resgate pesado parece vendedora — mas
se ela tem R$ 2 bi vencendo no mês, o vencimento cobre o resgate e ela não
precisa vender nada. Uma gestora captando parece compradora — mas se metade da
carteira dela vence em 90 dias, o que ela vai fazer é rolar, não comprar novo.

O cruzamento das três é a leitura. Ver `_ler_pressao`.

>>> O QUE ISTO SUBSTITUI

A tentativa anterior classificava fundo por PRÊMIO SOBRE O CDI, num módulo
`services/subclassificacao.py` que foi REMOVIDO em 21/08/2026. Prêmio descreve o
produto para quem vende cota a investidor final, e esta mesa não opera com
pessoa física — o eixo era o errado, não a implementação.

Fica o registro para que a ideia não volte por engano: se um dia alguém propuser
classificar gestora por rentabilidade, a pergunta a fazer é "isso muda com quem
a mesa fala?". Não muda.

>>> O QUE NÃO ENTRA NA AGENDA, E POR QUÊ

CRI e CRA. O bloco BLC_8 do CDA não tem coluna de vencimento nenhuma — só
`DS_ATIVO` em texto livre. Eles contam no ESTOQUE (via `cvm_carteira`) mas não
na agenda, e `agenda_cobertura_pct` diz quanto do estoque da casa tem data
conhecida, para ninguém ler uma agenda leve como "não vence nada".
"""
from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field

import pandas as pd

from app.config import settings
from app.models.schemas import Fundo

logger = logging.getLogger("pressao_gestora")

# As janelas que a mesa negocia. 3 meses é o horizonte da conversa de rolagem;
# 12 fecha o ano. Passar disso deixa de ser agenda e vira duration.
JANELAS_MESES = (3, 6, 12)

# Os eixos do jeito que a mesa pensa: papel de banco é um mercado, crédito
# corporativo é outro, e dentro do corporativo o que separa é o indexador.
EIXOS = ("lf", "cdb", "ipca", "cdi", "pre", "outro")

DIRECAO_COMPRADOR = "comprador"
DIRECAO_VENDEDOR = "vendedor"
DIRECAO_NEUTRO = "neutro"

PERFIL_BANCARIO = "bancario"
PERFIL_IPCA = "debenture_ipca"
PERFIL_CDI = "debenture_cdi"
PERFIL_MISTO = "misto"
PERFIL_SEM_CARTEIRA = "sem_carteira"

ROTULOS_PERFIL = {
    PERFIL_BANCARIO: "Papel bancário",
    PERFIL_IPCA: "Debênture IPCA+",
    PERFIL_CDI: "Debênture CDI+",
    PERFIL_MISTO: "Misto",
    PERFIL_SEM_CARTEIRA: "Sem carteira legível",
}

ROTULOS_DIRECAO = {
    DIRECAO_COMPRADOR: "Comprador",
    DIRECAO_VENDEDOR: "Vendedor",
    DIRECAO_NEUTRO: "Neutro",
}


@dataclass
class Contexto:
    """O que a carga deixa pronto. Requisição nenhuma recalcula isto."""

    # {gestora: {eixo: R$}} — estoque com vencimento conhecido
    estoque: dict[str, dict[str, float]] = field(default_factory=dict)
    # {gestora: {meses: {eixo: R$}}} — a agenda
    agenda: dict[str, dict[int, dict[str, float]]] = field(default_factory=dict)
    # {gestora: R$} — carteira de crédito total, inclusive o que não tem data
    credito_total: dict[str, float] = field(default_factory=dict)
    carteira_data: str | None = None

    def vazio(self) -> bool:
        return not self.estoque


def preparar(fundos: list[Fundo], posicoes: pd.DataFrame) -> Contexto:
    """Agrega as posições por gestora. Roda UMA vez por carga.

    Args:
        fundos: o universo do painel, já classificado.
        posicoes: saída de `connectors/cvm_vencimentos.carregar()`.

    O custo é uma passada sobre ~300 mil linhas com dois `groupby`. Medido em
    ~0,4 s, contra o preço de fazer isso a cada requisição, que seria o mesmo
    trabalho multiplicado por quantas pessoas abrirem a tela.
    """
    ctx = Contexto()
    if posicoes.empty:
        logger.info("Sem agenda de vencimento — a tela de pressão fica vazia.")
        return ctx

    # A ponte fundo -> gestora. Uma gestora tem vários fundos, e é a soma deles
    # que chega ao mercado.
    gestora_de: dict[str, str] = {}
    for f in fundos:
        if f.cnpj:
            gestora_de[f.cnpj] = f.gestora

    df = posicoes[posicoes["cnpj"].isin(gestora_de)].copy()
    if df.empty:
        logger.warning("Nenhuma posição casou com o universo do painel.")
        return ctx

    df["gestora"] = df["cnpj"].map(gestora_de)
    ctx.carteira_data = str(df["carteira_data"].iloc[0])

    for (gestora, eixo), valor in df.groupby(["gestora", "eixo"])["valor"].sum().items():
        ctx.estoque.setdefault(gestora, {})[eixo] = float(valor)

    # A referência de "hoje" é o dia da leitura, não a data-base do CDA. A
    # carteira tem 4 meses de defasagem de propósito (o sigilo do mês corrente
    # esconde 46% do PL), então parte do papel que estava lá JÁ VENCEU. Contar
    # a partir de hoje é o que faz a agenda descrever o que ainda vai acontecer
    # em vez de incluir rolagem que já foi feita.
    hoje = pd.Timestamp.today().normalize()
    for meses in JANELAS_MESES:
        limite = hoje + pd.DateOffset(months=meses)
        janela = df[(df["vencimento"] > hoje) & (df["vencimento"] <= limite)]
        soma = janela.groupby(["gestora", "eixo"])["valor"].sum()
        for (gestora, eixo), valor in soma.items():
            ctx.agenda.setdefault(gestora, {}).setdefault(meses, {})[eixo] = float(valor)

    # `carteira_credito` do fundo inclui CRI/CRA, que não tem data. Guardá-lo
    # ao lado do estoque datado é o que permite dizer quanto da carteira a
    # agenda cobre — sem isso, uma casa cheia de CRI pareceria não ter nada
    # vencendo.
    por_gestora: dict[str, float] = defaultdict(float)
    for f in fundos:
        if f.carteira_credito:
            por_gestora[f.gestora] += float(f.carteira_credito)
    ctx.credito_total = dict(por_gestora)

    logger.info(
        "Pressão por gestora: %d casas, R$ %.1f bi datados, vencendo em 3m R$ %.1f bi.",
        len(ctx.estoque),
        sum(sum(e.values()) for e in ctx.estoque.values()) / 1e9,
        sum(a.get(3, {}).get(k, 0.0) for a in ctx.agenda.values() for k in EIXOS) / 1e9,
    )
    return ctx


# =============================================================================
#  A regra
# =============================================================================

def _perfil(estoque: dict[str, float]) -> tuple[str, str]:
    """Em que mercado esta casa opera. Devolve (perfil, motivo).

    Mesma forma hierárquica do `classifier.py` do fundo, e o mesmo corte
    (`THRESHOLD_MAJORITARIO`), que é editável pelo painel de controle. Ter duas
    réguas para a mesma pergunta em dois níveis faria a tela se contradizer.

    O papel bancário é testado PRIMEIRO e sobre a carteira inteira, porque LF
    não é indexador — é outro mercado, com outro interlocutor (a tesouraria do
    banco, não o emissor corporativo). Só o que sobra é medido por indexador.
    """
    total = sum(estoque.values())
    if total <= 0:
        return PERFIL_SEM_CARTEIRA, "Sem posição com vencimento conhecido."

    thr = settings.THRESHOLD_MAJORITARIO
    bancario = (estoque.get("lf", 0.0) + estoque.get("cdb", 0.0)) / total

    if bancario > thr:
        return PERFIL_BANCARIO, (
            f"{bancario:.0%} da carteira datada é papel bancário (LF/CDB), "
            f"acima do corte de {thr:.0%}. O interlocutor é a tesouraria."
        )

    base = total * (1 - bancario)
    if base <= 0:
        return PERFIL_MISTO, "Carteira toda bancária — sem base para medir indexador."

    ipca = estoque.get("ipca", 0.0) / base
    cdi = estoque.get("cdi", 0.0) / base

    if ipca > thr and ipca >= cdi:
        return PERFIL_IPCA, (
            f"{ipca:.0%} do crédito corporativo é indexado a IPCA, acima do "
            f"corte de {thr:.0%}."
        )
    if cdi > thr:
        return PERFIL_CDI, (
            f"{cdi:.0%} do crédito corporativo é pós-fixado em CDI, acima do "
            f"corte de {thr:.0%}."
        )
    return PERFIL_MISTO, (
        f"Nada domina a carteira: bancário {bancario:.0%}, IPCA {ipca:.0%}, "
        f"CDI {cdi:.0%} (corte de {thr:.0%})."
    )


def _direcao(fluxo: float, credito: float | None) -> tuple[str, str]:
    """Compradora ou vendedora, e com que convicção.

    O corte é RELATIVO ao tamanho da casa, não absoluto: R$ 50 mi numa gestora
    de R$ 400 mi é movimento de mesa; na de R$ 40 bi é ruído de um dia. Um piso
    fixo encheria a lista de gigantes que não fizeram nada.

    Sem carteira conhecida não dá para relativizar, e aí vale o sinal puro —
    dizer "neutro" seria esconder um fluxo que existe.
    """
    if not credito or credito <= 0:
        if fluxo > 0:
            return DIRECAO_COMPRADOR, f"Captação líquida de R$ {fluxo / 1e6:.1f} mi."
        if fluxo < 0:
            return DIRECAO_VENDEDOR, f"Resgate líquido de R$ {-fluxo / 1e6:.1f} mi."
        return DIRECAO_NEUTRO, "Sem fluxo na janela."

    peso = abs(fluxo) / credito
    if peso < settings.PRESSAO_LIMIAR_PCT:
        return DIRECAO_NEUTRO, (
            f"Fluxo de R$ {fluxo / 1e6:.1f} mi é {peso:.1%} da carteira de "
            f"crédito — abaixo do corte de {settings.PRESSAO_LIMIAR_PCT:.1%}."
        )
    if fluxo > 0:
        return DIRECAO_COMPRADOR, (
            f"Captação de R$ {fluxo / 1e6:.1f} mi, {peso:.1%} da carteira de crédito."
        )
    return DIRECAO_VENDEDOR, (
        f"Resgate de R$ {-fluxo / 1e6:.1f} mi, {peso:.1%} da carteira de crédito."
    )


def _ler_pressao(direcao: str, fluxo: float, vence_3m: float) -> str:
    """A frase que cruza fluxo e agenda — é o que a tela existe para dizer.

    O vencimento é dinheiro que ENTRA no caixa da gestora sem ela vender nada.
    Por isso ele soma com a captação e abate o resgate: uma casa com resgate de
    R$ 500 mi e R$ 800 mi vencendo não é vendedora no secundário, é compradora
    com R$ 300 mi sobrando. Ler só o fluxo inverteria o sinal dela.
    """
    if vence_3m <= 0:
        if direcao == DIRECAO_VENDEDOR:
            return ("Resgate sem vencimento no trimestre: precisa vender papel "
                    "em mercado para pagar.")
        if direcao == DIRECAO_COMPRADOR:
            return "Captação sem rolagem a fazer: compra nova, dinheiro novo."
        return "Sem fluxo relevante e sem vencimento no trimestre."

    caixa = fluxo + vence_3m
    if direcao == DIRECAO_VENDEDOR:
        if caixa >= 0:
            return (
                f"Resgate de R$ {-fluxo / 1e6:.0f} mi coberto por R$ "
                f"{vence_3m / 1e6:.0f} mi vencendo em 3 meses: sobra R$ "
                f"{caixa / 1e6:.0f} mi para recompra. Não é vendedora forçada."
            )
        return (
            f"Resgate de R$ {-fluxo / 1e6:.0f} mi contra R$ {vence_3m / 1e6:.0f} "
            f"mi vencendo: faltam R$ {-caixa / 1e6:.0f} mi. Pressão vendedora real."
        )
    if direcao == DIRECAO_COMPRADOR:
        return (
            f"Captação de R$ {fluxo / 1e6:.0f} mi mais R$ {vence_3m / 1e6:.0f} mi "
            f"vencendo: R$ {caixa / 1e6:.0f} mi para alocar em 3 meses."
        )
    return (
        f"Fluxo neutro, mas R$ {vence_3m / 1e6:.0f} mi vencem em 3 meses e "
        "precisam ser rolados."
    )


def resumir(ctx: Contexto, gestora: str, fluxo: float) -> dict | None:
    """A linha da tela para uma gestora. None se ela não tem carteira datada."""
    estoque = ctx.estoque.get(gestora)
    if not estoque:
        return None

    total = sum(estoque.values())
    agenda = ctx.agenda.get(gestora, {})
    credito = ctx.credito_total.get(gestora)

    perfil, perfil_motivo = _perfil(estoque)
    direcao, direcao_motivo = _direcao(fluxo, credito)
    vence_3m = sum(agenda.get(3, {}).values())

    return {
        "gestora": gestora,
        "direcao": direcao,
        "direcao_rotulo": ROTULOS_DIRECAO[direcao],
        "direcao_motivo": direcao_motivo,
        "perfil": perfil,
        "perfil_rotulo": ROTULOS_PERFIL[perfil],
        "perfil_motivo": perfil_motivo,
        "fluxo": fluxo,
        "carteira_datada": total,
        "carteira_credito": credito,
        # Quanto do estoque de crédito tem data conhecida. Abaixo de 100% há
        # CRI/CRA no meio, que não tem vencimento no CDA — e a agenda subestima.
        "agenda_cobertura_pct": (
            round(total / credito * 100, 1) if credito and credito > 0 else None
        ),
        "estoque_por_eixo": {e: estoque.get(e, 0.0) for e in EIXOS},
        "agenda": {
            f"m{meses}": {e: agenda.get(meses, {}).get(e, 0.0) for e in EIXOS}
            for meses in JANELAS_MESES
        },
        "vence_3m": vence_3m,
        "vence_6m": sum(agenda.get(6, {}).values()),
        "vence_12m": sum(agenda.get(12, {}).values()),
        "leitura": _ler_pressao(direcao, fluxo, vence_3m),
        "carteira_data": ctx.carteira_data,
    }


def listar(ctx: Contexto, fluxos: dict[str, float], limite: int = 500,
           direcao: str = "todas") -> list[dict]:
    """Todas as gestoras com carteira datada, da maior agenda para a menor.

    A ordenação é pelo que vence em 3 meses, e não pelo tamanho da casa: a tela
    existe para mostrar onde a pressão está prestes a aparecer, e a maior casa
    do mercado sem nada vencendo não é notícia.
    """
    linhas = []
    for gestora in ctx.estoque:
        linha = resumir(ctx, gestora, fluxos.get(gestora, 0.0))
        if linha is None:
            continue
        if direcao != "todas" and linha["direcao"] != direcao:
            continue
        linhas.append(linha)

    linhas.sort(key=lambda x: x["vence_3m"], reverse=True)
    return linhas[:limite]


def totais(ctx: Contexto) -> dict:
    """Os KPIs do topo da tela: o mercado inteiro, por eixo e por janela."""
    if ctx.vazio():
        return {}

    estoque = {e: 0.0 for e in EIXOS}
    for por_eixo in ctx.estoque.values():
        for eixo, valor in por_eixo.items():
            estoque[eixo] = estoque.get(eixo, 0.0) + valor

    agenda: dict[str, dict[str, float]] = {}
    for meses in JANELAS_MESES:
        soma = {e: 0.0 for e in EIXOS}
        for por_janela in ctx.agenda.values():
            for eixo, valor in por_janela.get(meses, {}).items():
                soma[eixo] = soma.get(eixo, 0.0) + valor
        agenda[f"m{meses}"] = soma

    return {
        "gestoras": len(ctx.estoque),
        "carteira_datada": sum(estoque.values()),
        "estoque_por_eixo": estoque,
        "agenda": agenda,
        "carteira_data": ctx.carteira_data,
    }
