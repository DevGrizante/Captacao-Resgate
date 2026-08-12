"""
Schemas Pydantic — contratos de dados da API.

Estes modelos definem o formato exato que o front recebe. Se você mudar
um campo aqui, o front (js/api.js) precisa acompanhar.
"""
from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class Bucket(str, Enum):
    """Classificação por indexador majoritário da carteira."""
    IPCA = "ipca"
    CDI = "cdi"
    LF = "lf"
    MISTO = "misto"


class Janela(str, Enum):
    DIARIA = "diaria"
    SEMANAL = "semanal"
    MENSAL = "mensal"
    SEMESTRAL = "semestral"


class Fundo(BaseModel):
    """Um fundo individual, já enriquecido e classificado."""
    cnpj: str
    nome: str
    gestora: str
    bucket: Bucket
    # Fluxos por janela (R$)
    diaria: float = 0.0
    semanal: float = 0.0
    mensal: float = 0.0
    semestral: float = 0.0
    # Enriquecimento (Quantum / CDA)
    pl: float = 0.0
    duration: Optional[float] = None       # anos
    cotizacao_resgate: Optional[int] = None  # D+X
    taxa_adm: Optional[float] = None        # % a.a.
    aberto_captacao: bool = True
    resgate_pct_pl_semana: Optional[float] = None  # p/ sinal de estresse
    # Percentuais de composição que geraram o bucket
    pct_ipca: float = 0.0
    pct_cdi: float = 0.0
    pct_lf: float = 0.0


class GestoraResumo(BaseModel):
    """Consolidado de uma gestora — a linha da tabela principal."""
    nome: str
    fundos: int
    abertos: int
    diaria: float
    semanal: float
    mensal: float
    semestral: float
    pl: float
    var_pl_pct: float = 0.0
    duration_media: Optional[float] = None
    cotizacao_media: Optional[int] = None
    taxa_adm_media: Optional[float] = None
    # Mix de composição (fração 0..1)
    mix_ipca: float = 0.0
    mix_cdi: float = 0.0
    mix_lf: float = 0.0
    mix_misto: float = 0.0


class BucketResumo(BaseModel):
    bucket: Bucket
    fluxo: float
    fundos: int
    pct_pl: float
    duration_media: Optional[float] = None
    cotizacao_media: Optional[int] = None
    pct_abertos: float = 0.0


class KpisFluxo(BaseModel):
    fluxo_liquido: float
    fluxo_liquido_anterior: float
    captacao_bruta: float
    resgates: float
    fundos_entrada: int
    fundos_saida: int
    pl_total: float
    pl_var_30d_pct: float


class KpisMesa(BaseModel):
    fundos_abertos: int
    fundos_abertos_pct: float
    pl_investivel: float
    duration_media: float
    cotizacao_media: int
    fundos_cot_curta: int          # D+ < 15
    premio_ipca: Optional[float] = None
    premio_cdi: Optional[float] = None
    taxa_adm_media: float
    stress_fundos: int             # resgate > 5% PL
    stress_valor: float


class SerieTemporalPonto(BaseModel):
    semana: str
    ipca: float
    cdi: float
    lf: float
    misto: float


class DashboardResponse(BaseModel):
    """Payload principal consumido pela home do front."""
    data_referencia: str
    total_fundos: int
    total_gestoras: int
    cobertura_pct: float
    kpis_fluxo: KpisFluxo
    kpis_mesa: KpisMesa
    buckets: list[BucketResumo]
    serie_temporal: list[SerieTemporalPonto]
    gestoras: list[GestoraResumo]


class DossieResponse(BaseModel):
    """Payload do painel lateral de uma gestora."""
    gestora: GestoraResumo
    classe_majoritaria: str
    sparkline: list[float]           # 13 semanas de fluxo
    fundos: list[Fundo]


class StressFundo(BaseModel):
    nome: str
    gestora: str
    duration: Optional[float] = None
    cotizacao_resgate: Optional[int] = None
    resgate_pct_pl: float


class MoverGestora(BaseModel):
    nome: str
    duration_media: Optional[float] = None
    cotizacao_media: Optional[int] = None
    var_pl_pct: float
