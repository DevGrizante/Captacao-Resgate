"""Endpoints da API de captação/resgate."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from app.models.schemas import (
    DashboardResponse,
    DossieResponse,
    FonteInfo,
    MoverGestora,
    StressFundo,
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
