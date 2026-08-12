"""
Configuração central. Lê variáveis de ambiente com defaults sensatos.

Copie `.env.example` para `.env` e ajuste conforme necessário. Nada aqui
guarda segredos em texto — o token do Quantum vem do ambiente.
"""
from __future__ import annotations

import os
from pathlib import Path

# Raiz do projeto (…/Captacao_Resgate)
BASE_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = BASE_DIR / "data"
CACHE_DIR = DATA_DIR / "cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)


class Settings:
    # --- Fonte de dados ---
    # "cvm"  -> baixa e processa dados abertos da CVM (real)
    # "mock" -> usa amostra fixa embutida (roda offline, ótimo p/ validar front)
    DATA_SOURCE: str = os.getenv("DATA_SOURCE", "mock")

    # --- CVM ---
    CVM_BASE_URL: str = os.getenv(
        "CVM_BASE_URL",
        "https://dados.cvm.gov.br/dados/FI/DOC",
    )
    # Quantos meses de informe diário puxar para montar as janelas
    CVM_MESES_INFORME: int = int(os.getenv("CVM_MESES_INFORME", "7"))

    # --- Quantum Axis (stub por enquanto) ---
    QUANTUM_ENABLED: bool = os.getenv("QUANTUM_ENABLED", "false").lower() == "true"
    QUANTUM_BASE_URL: str = os.getenv(
        "QUANTUM_BASE_URL", "https://api.quantumaxis.com.br"
    )
    QUANTUM_TOKEN: str = os.getenv("QUANTUM_TOKEN", "")

    # --- Classificação por indexador ---
    # Limiar para um fundo ser considerado "majoritário" num indexador
    THRESHOLD_MAJORITARIO: float = float(os.getenv("THRESHOLD_MAJORITARIO", "0.5"))
    # Limiar de resgate semanal (fração do PL) para virar sinal de estresse
    THRESHOLD_STRESS: float = float(os.getenv("THRESHOLD_STRESS", "0.05"))

    # --- Cache ---
    CACHE_TTL_HORAS: int = int(os.getenv("CACHE_TTL_HORAS", "12"))

    # --- CORS ---
    # Origens autorizadas a chamar a API (o front). Em produção, restrinja.
    CORS_ORIGINS: list[str] = os.getenv(
        "CORS_ORIGINS", "http://localhost:5500,http://127.0.0.1:5500,http://localhost:8080"
    ).split(",")


settings = Settings()
