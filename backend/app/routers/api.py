"""Endpoints da API de captação/resgate."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from app.models.schemas import (
    DashboardResponse,
    DossieResponse,
    FonteInfo,
    MoverGestora,
    StressFundo,
    FundoPapelBancario,
    FundoPapelBancarioDetalhe,
    TesourariaDossie,
    TesourariaResumo,
)
from app.services.pipeline import pipeline

router = APIRouter(prefix="/api", tags=["captacao"])


@router.get("/dashboard", response_model=DashboardResponse)
def get_dashboard(
    janela: str = Query("semanal"),
    indexador: str = Query("todos"),
    abertos: bool = Query(True)
):
    """Payload principal: KPIs, buckets, série temporal e ranking de gestoras."""
    return pipeline.dashboard(janela, indexador, abertos)


@router.get("/dossie/{gestora}", response_model=DossieResponse)
def get_dossie(
    gestora: str,
    janela: str = Query("semanal"),
    indexador: str = Query("todos"),
    abertos: bool = Query(True)
):
    """Painel lateral de uma gestora: resumo, mix, métricas, spark e fundos."""
    resp = pipeline.dossie(gestora, janela, indexador, abertos)
    if resp is None:
        raise HTTPException(status_code=404, detail=f"Gestora '{gestora}' não encontrada")
    return resp


@router.get("/movers", response_model=list[MoverGestora])
def get_movers(
    direcao: str = Query("pos", pattern="^(pos|neg)$"),
    limite: int = Query(6, ge=1, le=50),
    janela: str = Query("semanal"),
    indexador: str = Query("todos"),
    abertos: bool = Query(True)
):
    """Gestoras com maior variação % de PL (ganhando/perdendo)."""
    return pipeline.movers(direcao, limite, janela, indexador, abertos)


@router.get("/stress", response_model=list[StressFundo])
def get_stress(
    limite: int = Query(50, ge=1, le=200),
    janela: str = Query("semanal"),
    indexador: str = Query("todos"),
    abertos: bool = Query(True)
):
    """Fundos com resgate acima do limiar de estresse na semana."""
    return pipeline.stress(limite, janela, indexador, abertos)


@router.get("/tesourarias", response_model=list[TesourariaResumo])
def get_tesourarias(limite: int = Query(2000, ge=1, le=5000)):
    """Tesourarias emissoras ordenadas pelo estoque que os fundos carregam.

    Preço (% do CDI e spread) e prazo vêm ponderados por valor; `valor_venc_12m`
    é a agenda de rolagem. Tudo do BLC_5 do CDA, na data de `carteira_data`.
    """
    return pipeline.tesourarias(limite)


@router.get("/tesourarias/{raiz}", response_model=TesourariaDossie)
def get_tesouraria(raiz: str, limite: int = Query(40, ge=1, le=200)):
    """Dossiê de uma tesouraria: quem compra, o que vence e quem ainda não compra.

    `raiz` é a raiz do CNPJ do emissor (8 dígitos) — é a chave estável, porque
    o nome vem como texto livre e a mesma casa aparece com dezenas de grafias.
    """
    resp = pipeline.tesouraria(raiz, limite)
    if resp is None:
        raise HTTPException(
            status_code=404,
            detail=f"Tesouraria '{raiz}' não encontrada no universo desta carga.",
        )
    return resp


@router.get("/carteira-bancaria", response_model=list[FundoPapelBancario])
def get_carteira_bancaria(
    limite: int = Query(5000, ge=1, le=20000),
    busca: str = Query("", max_length=120),
):
    """Gestoras que carregam papel bancário (LF, CDB, DPGE), da maior à menor.

    A visão é da GESTORA: quem decide alocação é a casa, e a posição que
    interessa à mesa é a somada entre os fundos dela, não a fatia de cada
    veículo.

    O teto é alto de propósito. A tela soma os KPIs (estoque, papéis, taxa
    média, o que vence em 12 meses) sobre a lista que recebe, e um teto baixo
    faria esses totais descreverem só o topo do ranking enquanto se apresentam
    como o universo — um erro silencioso, que é o pior tipo. Hoje são ~2,2 mil
    fundos e o payload completo fica em ~790 KB.
    """
    return pipeline.carteira_bancaria(limite, busca)


@router.get("/carteira-bancaria/{gestora}", response_model=FundoPapelBancarioDetalhe)
def get_carteira_bancaria_gestora(gestora: str):
    """Os papéis de uma gestora, consolidados por emissor + tipo + mês.

    Cada linha é um bloco: mesmo emissor, mesmo tipo e mesmo mês de vencimento
    somam o volume, com a taxa média ponderada. Formas de taxa diferentes
    (spread sobre CDI vs. percentual do DI) ficam em linhas separadas.
    """
    resp = pipeline.carteira_bancaria_gestora(gestora)
    if resp is None:
        raise HTTPException(
            status_code=404,
            detail=f"Gestora '{gestora}' não tem papel bancário nesta carga.",
        )
    return resp


@router.get("/fonte", response_model=FonteInfo)
def get_fonte():
    """De onde vieram os dados em cache e o que essa fonte não entrega."""
    return pipeline.fonte_info()


@router.post("/admin/refresh")
def post_refresh():
    """Recarrega a fonte e recalcula o pipeline.

    Na fonte "vinculado" isso também re-varre a pasta do Outlook: se chegou um
    e-mail novo com anexo, é ele que passa a valer. É o botão para apertar
    depois que o e-mail da manhã cai na caixa.
    """
    pipeline.refresh()
    info = pipeline.fonte_info()
    return {
        "status": "ok",
        "message": "Pipeline recalculado.",
        "fonte": info.fonte,
        "arquivo": info.arquivo,
        "recebido_em": info.recebido_em,
    }
