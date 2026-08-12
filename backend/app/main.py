"""
Entrypoint da API — Captação e Resgate · Crédito Privado.

Rodar (dentro de backend/):
    uvicorn app.main:app --reload --port 8000

Docs interativas: http://localhost:8000/docs
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.routers import api

app = FastAPI(
    title="Captação e Resgate · Crédito Privado",
    description="Consolidação de captação/resgate de fundos de crédito privado, "
                "classificados por indexador (IPCA+ / CDI+ / LF / Misto).",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api.router)


@app.get("/health")
def health():
    return {
        "status": "ok",
        "data_source": settings.DATA_SOURCE,
        "quantum_enabled": settings.QUANTUM_ENABLED,
    }
