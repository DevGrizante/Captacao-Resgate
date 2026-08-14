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
            nome, gestora,
            diaria, semanal, mensal, semestral (floats)
        E, quando a fonte tiver:
            cnpj, pl, pl_anterior,
            pct_lf, pct_ipca, pct_cdi (frações 0..1),
            duration, cotizacao_resgate, taxa_adm, aberto_captacao,
            resgate_pct_pl_semana,
            historico_semanal ({data_fim_iso: fluxo_liquido_da_semana})

        Campo que a fonte não tem deve vir `None` — nunca zero. O pipeline
        propaga o None até o front, que mostra "—". Zero significaria "medimos
        e deu zero", que é uma afirmação diferente.
        """
        raise NotImplementedError

    def metadados(self) -> dict:
        """Info sobre a extração, exposta no rodapé do dashboard.

        Chaves reconhecidas: arquivo, recebido_em, data_referencia e
        `janelas` — a lista de janelas semanais no formato
        {chave, inicio, fim, rotulo, curto}, em ordem cronológica, que o
        pipeline usa para montar a série temporal.
        """
        return {}

    def disponivel(self) -> bool:
        """Pode ser sobrescrito para checar credenciais/conectividade."""
        return True
