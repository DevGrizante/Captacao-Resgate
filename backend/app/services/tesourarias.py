"""
As perguntas de uma mesa que intermedeia Tesouraria e Asset.

O dashboard principal responde "quem está captando". Este módulo responde a
camada seguinte, que é onde o negócio acontece:

    1. Quais tesourarias o mercado carrega, quanto, a que preço e a que prazo?
       -> `ranking()`
    2. Quem já compra do Banco X, quanto tem vencendo, e a que preço comprou?
       -> `dossie().compradores`
    3. Quem compra papel bancário e AINDA NÃO compra do Banco X?
       -> `dossie().oportunidades`   <- a lista de prospecção
    4. De quem esta Asset compra hoje?
       -> `por_gestora()`, que alimenta o dossiê da gestora

>>> A UNIDADE DE NEGÓCIO É A GESTORA, NÃO O FUNDO

A posição está declarada por fundo, mas quem decide alocação é a casa. Uma
Asset com 40 fundos que carregam LF do mesmo banco é UMA conversa, não 40. Por
isso tudo aqui agrega por gestora antes de sair.

>>> SUBCLASSE NÃO PODE CONTAR DUAS VEZES

O export lista a classe-mãe e cada subclasse como linhas separadas com o MESMO
CNPJ de fundo, e a carteira do CDA é atribuída a todas elas (elas dividem a
mesma carteira). Somar ingenuamente multiplicaria a posição de um fundo com 4
subclasses por 4. `_por_cnpj` resolve isso colapsando as linhas antes do join:
fluxo soma entre subclasses, PL não — é o mesmo dinheiro.

>>> O QUE ESTES NÚMEROS NÃO SÃO

Não são emissões, são POSIÇÕES declaradas no CDA da data-base, com a
defasagem descrita em `connectors/cvm_carteira.py`. Descrevem o estoque que o
mercado carrega, não o fluxo primário do mês. Para a conversa de mesa isso é o
que importa — mas apresentar como "o banco emitiu X" seria errado.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field

import pandas as pd

from app.connectors.cvm_emissores import FAIXAS_PRAZO
from app.models.schemas import (
    CompradorTesouraria,
    FaixaPrazo,
    Fundo,
    OportunidadeTesouraria,
    TesourariaDossie,
    TesourariaNaCarteira,
    TesourariaResumo,
)

logger = logging.getLogger("tesourarias")


@dataclass
class _Asset:
    """O que sabemos do fundo fora da carteira: de quem é e como está o fluxo."""
    gestora: str
    pl: float | None = None
    semanal: float = 0.0
    mensal: float = 0.0


@dataclass
class _Acumulado:
    """Somatório de posições, guardando o peso para as médias ponderadas."""
    valor: float = 0.0
    valor_venc_12m: float = 0.0
    valor_ligado: float = 0.0
    fundos: set = field(default_factory=set)
    gestoras: set = field(default_factory=set)
    _num: dict = field(default_factory=lambda: defaultdict(float))
    _den: dict = field(default_factory=lambda: defaultdict(float))

    def somar(self, linha, cnpj: str, gestora: str | None = None) -> None:
        valor = float(linha["valor"] or 0.0)
        self.valor += valor
        self.valor_venc_12m += float(linha.get("valor_venc_12m") or 0.0)
        self.valor_ligado += float(linha.get("valor_ligado") or 0.0)
        self.fundos.add(cnpj)
        if gestora:
            self.gestoras.add(gestora)
        # Preço e prazo entram ponderados pelo tamanho da posição: uma ponta de
        # R$ 1 mi a CDI+3% não move o custo de quem tem R$ 200 mi a CDI+0,8%.
        for campo in ("pct_cdi", "spread", "prazo_dias"):
            v = linha.get(campo)
            if v is not None and pd.notna(v):
                self._num[campo] += float(v) * valor
                self._den[campo] += valor

    def media(self, campo: str) -> float | None:
        den = self._den.get(campo, 0.0)
        return (self._num[campo] / den) if den else None


def disponivel(emissores: pd.DataFrame | None) -> bool:
    return emissores is not None and not emissores.empty


@dataclass
class Contexto:
    """Tudo o que as três telas precisam, derivado UMA vez por carga.

    A conversão do DataFrame de 23 mil pares para dicionários custava ~300 ms, e
    era refeita a cada clique — navegar entre tesourarias é justamente o gesto
    que a mesa mais repete. Como nada aqui depende do parâmetro da requisição,
    o trabalho todo sobe para o momento da carga.

    Invalidado junto com o pipeline: quem reconstrói os fundos reconstrói isto.
    A reclassificação NÃO invalida — ela mexe em bucket, e bucket não entra em
    nenhuma conta desta tela.
    """
    registros: list[dict] = field(default_factory=list)
    assets: dict = field(default_factory=dict)
    perfil: dict = field(default_factory=dict)
    bancario: dict = field(default_factory=dict)
    por_raiz: dict = field(default_factory=dict)
    por_gestora_idx: dict = field(default_factory=dict)
    ranking: list = field(default_factory=list)
    data: str | None = None

    def vazio(self) -> bool:
        return not self.registros


def preparar(fundos: list[Fundo], emissores: pd.DataFrame) -> Contexto:
    """Monta o contexto. Chamado pelo pipeline ao final de cada construção."""
    if not disponivel(emissores):
        return Contexto()

    assets = _por_cnpj(fundos)
    registros = emissores.to_dict("records")
    por_raiz: dict[str, list[dict]] = defaultdict(list)
    por_gestora_idx: dict[str, list[dict]] = defaultdict(list)
    do_universo: list[dict] = []

    for linha in registros:
        asset = assets.get(linha["cnpj_fundo"])
        if asset is None:
            # Fundo do CDA fora do nosso universo de crédito privado. Fica de
            # fora de propósito: estas telas descrevem o universo da tela.
            continue
        do_universo.append(linha)
        por_raiz[linha["raiz_emissor"]].append(linha)
        por_gestora_idx[asset.gestora].append(linha)

    ctx = Contexto(
        registros=do_universo,
        assets=assets,
        perfil=_perfil_gestoras(assets),
        bancario=_bancario_por_gestora(do_universo, assets),
        por_raiz=dict(por_raiz),
        por_gestora_idx=dict(por_gestora_idx),
        data=_data_carteira(emissores),
    )
    ctx.ranking = _montar_ranking(ctx)
    logger.info(
        "Mapa Tesouraria x Asset: %d tesourarias, %d pares no universo.",
        len(ctx.por_raiz), len(do_universo),
    )
    return ctx


def _perfil_gestoras(assets: dict[str, _Asset]) -> dict[str, _Asset]:
    """PL e fluxo somados por gestora, calculados UMA vez.

    Antes isto vivia dentro da montagem de cada linha, e o custo era
    gestoras × fundos a cada dossiê. Como as duas listas do dossiê (quem compra
    e quem não compra) precisam do mesmo número, uma passada resolve as duas.

    A entrada já vem colapsada por CNPJ, então subclasse não conta duas vezes.
    """
    por: dict[str, _Asset] = {}
    for a in assets.values():
        g = por.get(a.gestora)
        if g is None:
            g = por[a.gestora] = _Asset(gestora=a.gestora, pl=None)
        g.semanal += a.semanal
        g.mensal += a.mensal
        if a.pl is not None:
            g.pl = (g.pl or 0.0) + a.pl
    return por


def _por_cnpj(fundos: list[Fundo]) -> dict[str, _Asset]:
    """Colapsa as linhas por CNPJ de fundo, resolvendo o caso das subclasses.

    Fluxo soma (cada subclasse tem o seu); PL não (é o mesmo patrimônio
    creditado à classe-mãe); a gestora vem da linha de maior PL, que é a mãe.
    """
    por: dict[str, _Asset] = {}
    melhor_pl: dict[str, float] = {}
    for f in fundos:
        if not f.cnpj:
            continue
        a = por.get(f.cnpj)
        if a is None:
            a = por[f.cnpj] = _Asset(gestora=f.gestora)
            melhor_pl[f.cnpj] = -1.0
        a.semanal += f.semanal
        a.mensal += f.mensal
        pl = f.pl or 0.0
        if pl > melhor_pl[f.cnpj]:
            melhor_pl[f.cnpj] = pl
            a.gestora = f.gestora
            a.pl = f.pl
    return por


def _montar_ranking(ctx: Contexto) -> list[TesourariaResumo]:
    """Ranking completo, sem corte. O `limite` da API e so uma fatia disto."""
    acc: dict[str, _Acumulado] = defaultdict(_Acumulado)
    nomes: dict[str, tuple[float, str]] = {}
    for raiz, linhas in ctx.por_raiz.items():
        for linha in linhas:
            asset = ctx.assets[linha["cnpj_fundo"]]
            acc[raiz].somar(linha, linha["cnpj_fundo"], asset.gestora)
            anterior = nomes.get(raiz)
            if anterior is None or linha["valor"] > anterior[0]:
                nomes[raiz] = (linha["valor"], linha["emissor"])

    total = sum(a.valor for a in acc.values())
    saida = [
        TesourariaResumo(
            raiz=raiz,
            nome=nomes[raiz][1],
            valor=a.valor,
            share_pct=round(a.valor / total * 100, 2) if total else None,
            fundos=len(a.fundos),
            gestoras=len(a.gestoras),
            pct_cdi=_round(a.media("pct_cdi"), 2),
            spread=_round(a.media("spread"), 2),
            prazo_dias=_round(a.media("prazo_dias"), 0),
            valor_venc_12m=a.valor_venc_12m,
            pct_venc_12m=round(a.valor_venc_12m / a.valor * 100, 1) if a.valor else None,
            pct_ligado=round(a.valor_ligado / a.valor * 100, 1) if a.valor else None,
            carteira_data=ctx.data,
        )
        for raiz, a in acc.items()
    ]
    saida.sort(key=lambda x: x.valor, reverse=True)
    return saida


def ranking(ctx: Contexto, limite: int = 40) -> list[TesourariaResumo]:
    """Tesourarias ordenadas pelo estoque que o universo carrega delas."""
    return ctx.ranking[:limite]


def dossie(ctx: Contexto, raiz: str, limite: int = 40) -> TesourariaDossie | None:
    """Quem compra desta tesouraria, o que vence, e quem ainda nao compra."""
    linhas = ctx.por_raiz.get(raiz)
    if not linhas:
        return None

    por_gestora_acc: dict[str, _Acumulado] = defaultdict(_Acumulado)
    curva: dict[str, float] = defaultdict(float)
    nome, maior = "", -1.0
    for linha in linhas:
        asset = ctx.assets[linha["cnpj_fundo"]]
        por_gestora_acc[asset.gestora].somar(linha, linha["cnpj_fundo"])
        for chave in FAIXAS_PRAZO:
            curva[chave] += float(linha.get(f"venc_{chave}") or 0.0)
        if linha["valor"] > maior:
            maior, nome = linha["valor"], linha["emissor"]

    if not por_gestora_acc:
        return None

    vazio = _Asset(gestora="")
    compradores = [
        CompradorTesouraria(
            gestora=g,
            valor=a.valor,
            fundos=len(a.fundos),
            pct_cdi=_round(a.media("pct_cdi"), 2),
            spread=_round(a.media("spread"), 2),
            prazo_dias=_round(a.media("prazo_dias"), 0),
            valor_venc_12m=a.valor_venc_12m,
            # Metade ou mais da posicao vinda de dentro do grupo ja caracteriza
            # a linha como intragrupo para efeito de leitura da mesa.
            ligado=a.valor_ligado >= a.valor * 0.5 if a.valor else False,
            pl=ctx.perfil.get(g, vazio).pl,
            fluxo_semanal=ctx.perfil.get(g, vazio).semanal,
            fluxo_mensal=ctx.perfil.get(g, vazio).mensal,
            pct_do_bancario=(
                round(a.valor / ctx.bancario[g]["valor"] * 100, 1)
                if ctx.bancario.get(g, {}).get("valor") else None
            ),
        )
        for g, a in por_gestora_acc.items()
    ]
    compradores.sort(key=lambda c: c.valor, reverse=True)

    # ----- quem compra papel bancario e nao compra deste emissor -----
    ja_compram = set(por_gestora_acc)
    oportunidades = [
        OportunidadeTesouraria(
            gestora=g,
            valor_bancario=info["valor"],
            emissores=len(info["raizes"]),
            fundos=len(info["fundos"]),
            pl=ctx.perfil.get(g, vazio).pl,
            fluxo_semanal=ctx.perfil.get(g, vazio).semanal,
            fluxo_mensal=ctx.perfil.get(g, vazio).mensal,
            spread_medio=_round(info["acc"].media("spread"), 2),
        )
        for g, info in ctx.bancario.items()
        if g not in ja_compram
    ]
    oportunidades.sort(key=lambda o: o.valor_bancario, reverse=True)

    total_curva = sum(curva.values())
    return TesourariaDossie(
        resumo=_resumo_de(raiz, nome, por_gestora_acc, ctx.data),
        compradores=compradores[:limite],
        oportunidades=oportunidades[:limite],
        curva_vencimento=[
            FaixaPrazo(
                rotulo=rotulo,
                valor=curva.get(chave, 0.0),
                pct=(round(curva.get(chave, 0.0) / total_curva * 100, 1)
                     if total_curva else None),
            )
            for chave, rotulo in FAIXAS_PRAZO.items()
        ],
    )


def por_gestora(ctx: Contexto, gestora: str,
                limite: int = 8) -> tuple[list[TesourariaNaCarteira], float | None]:
    """De quais tesourarias esta casa compra. Devolve (lista, total bancario)."""
    linhas = ctx.por_gestora_idx.get(gestora)
    if not linhas:
        return [], None

    acc: dict[str, _Acumulado] = defaultdict(_Acumulado)
    nomes: dict[str, tuple[float, str]] = {}
    for linha in linhas:
        raiz = linha["raiz_emissor"]
        acc[raiz].somar(linha, linha["cnpj_fundo"])
        anterior = nomes.get(raiz)
        if anterior is None or linha["valor"] > anterior[0]:
            nomes[raiz] = (linha["valor"], linha["emissor"])

    total = sum(a.valor for a in acc.values())
    saida = [
        TesourariaNaCarteira(
            raiz=raiz,
            nome=nomes[raiz][1],
            valor=a.valor,
            pct_do_bancario=round(a.valor / total * 100, 1) if total else None,
            spread=_round(a.media("spread"), 2),
            prazo_dias=_round(a.media("prazo_dias"), 0),
            valor_venc_12m=a.valor_venc_12m,
        )
        for raiz, a in acc.items()
    ]
    saida.sort(key=lambda x: x.valor, reverse=True)
    return saida[:limite], total


# ---------- helpers ----------
def _bancario_por_gestora(registros: list[dict], assets: dict[str, _Asset]) -> dict:
    """Papel bancário total por gestora, com de quantos emissores ele vem."""
    info: dict[str, dict] = {}
    for linha in registros:
        asset = assets.get(linha["cnpj_fundo"])
        if asset is None:
            continue
        d = info.setdefault(asset.gestora, {
            "valor": 0.0, "raizes": set(), "fundos": set(), "acc": _Acumulado(),
        })
        d["valor"] += float(linha["valor"] or 0.0)
        d["raizes"].add(linha["raiz_emissor"])
        d["fundos"].add(linha["cnpj_fundo"])
        d["acc"].somar(linha, linha["cnpj_fundo"])
    return info


def _resumo_de(raiz: str, nome: str, por_gestora_acc: dict,
               data: str | None) -> TesourariaResumo:
    total = sum(a.valor for a in por_gestora_acc.values())
    geral = _Acumulado()
    for a in por_gestora_acc.values():
        geral.valor += a.valor
        geral.valor_venc_12m += a.valor_venc_12m
        geral.valor_ligado += a.valor_ligado
        geral.fundos |= a.fundos
        for campo in ("pct_cdi", "spread", "prazo_dias"):
            geral._num[campo] += a._num.get(campo, 0.0)
            geral._den[campo] += a._den.get(campo, 0.0)
    return TesourariaResumo(
        raiz=raiz,
        nome=nome,
        valor=total,
        fundos=len(geral.fundos),
        gestoras=len(por_gestora_acc),
        pct_cdi=_round(geral.media("pct_cdi"), 2),
        spread=_round(geral.media("spread"), 2),
        prazo_dias=_round(geral.media("prazo_dias"), 0),
        valor_venc_12m=geral.valor_venc_12m,
        pct_venc_12m=round(geral.valor_venc_12m / total * 100, 1) if total else None,
        pct_ligado=round(geral.valor_ligado / total * 100, 1) if total else None,
        carteira_data=data,
    )


def _data_carteira(emissores: pd.DataFrame) -> str | None:
    if emissores.empty or "carteira_data" not in emissores.columns:
        return None
    valores = emissores["carteira_data"].dropna()
    return str(valores.iloc[0]) if len(valores) else None


def _round(v, n):
    if v is None:
        return None
    return round(v, n) if n else round(v)
