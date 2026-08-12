"""
Contrato comum a todos os conectores de fonte de dados.

Um conector entrega uma lista de fundos "crus" — cada um um dict com pelo menos:
    cnpj, nome, gestora, e os fluxos/composição disponíveis.
O pipeline (services/pipeline.py) é quem normaliza, classifica e agrega.
"""
from __future__ import annotations

from abc import ABC, abstractmethod


class DataConnector(ABC):
    """Fonte de dados de fundos."""

    name: str = "base"

    @abstractmethod
    def carregar_fundos(self) -> list[dict]:
        """Retorna a lista de fundos crus.

        Cada item deve conter, no mínimo:
            cnpj, nome, gestora,
            diaria, semanal, mensal, semestral (floats),
            pl (float),
            pct_lf, pct_ipca, pct_cdi (frações 0..1),
            duration, cotizacao_resgate, taxa_adm, aberto_captacao,
            resgate_pct_pl_semana
        Campos ausentes são tolerados pelo pipeline (viram None/0).
        """
        raise NotImplementedError

    def disponivel(self) -> bool:
        """Pode ser sobrescrito para checar credenciais/conectividade."""
        return True
