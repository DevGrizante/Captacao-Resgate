"""
Conector MOCK — serve dados fixos derivados do seu Excel real (4.693 fundos).

Serve para validar o front e o pipeline sem depender de rede. Os fluxos são
reais (extraídos do arquivo); duration/cotização/taxa/composição são sintéticos
mas estáveis, para você ver o comportamento antes de plugar CVM e Quantum.
"""
from __future__ import annotations

import json

from app.config import DATA_DIR
from app.connectors.base import DataConnector


class MockConnector(DataConnector):
    name = "mock"

    def carregar_fundos(self) -> list[dict]:
        caminho = DATA_DIR / "mock_fundos.json"
        if not caminho.exists():
            raise FileNotFoundError(
                f"mock_fundos.json não encontrado em {caminho}. "
                "Rode scripts/gerar_mock.py ou use DATA_SOURCE=cvm."
            )
        with open(caminho, encoding="utf-8") as f:
            return json.load(f)
