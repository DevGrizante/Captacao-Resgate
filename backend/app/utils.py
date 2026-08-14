"""Helpers pequenos usados por mais de um módulo."""
from __future__ import annotations

import re

import pandas as pd


def so_digitos(valor) -> str | None:
    """'33.149.272/0001-07' -> '33149272000107'. None se não houver dígito.

    Cada base da CVM formata CNPJ de um jeito: o informe diário vem pontuado,
    o registro vem só com dígitos, a planilha do Quantum vem pontuada. Todo
    casamento passa por aqui antes de comparar.
    """
    if valor is None or (isinstance(valor, float) and pd.isna(valor)):
        return None
    d = re.sub(r"\D", "", str(valor))
    return d or None
