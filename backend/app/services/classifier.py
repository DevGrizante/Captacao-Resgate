"""
Classificação de fundos por indexador majoritário.

Regra hierárquica (validada com o negócio):
    1) Se > 50% da carteira em Letras Financeiras  -> LF
    2) Senão, se > 50% do RESTANTE (ex-LF) em ativos IPCA+ -> IPCA
    3) Senão, se > 50% do RESTANTE (ex-LF) em ativos CDI+/DI+ -> CDI
    4) Senão -> MISTO

O "restante ex-LF" é intencional: LF é um instrumento, não um indexador.
Primeiro tiramos LF da conta; só então olhamos o mix de indexador do que sobra.

A entrada é um dicionário de pesos por classe de ativo, tipicamente vindo
do CDA da CVM ou do Quantum. As chaves esperadas (todas frações 0..1 do PL):
    pct_lf    : peso em Letras Financeiras
    pct_ipca  : peso em ativos indexados a IPCA (deb. IPCA+, CRI, CRA, NTN-B)
    pct_cdi   : peso em ativos indexados a CDI/DI (deb. CDI+, FIDC, CDB %CDI)
    (o que não for nenhum dos três é tratado como "outros" e dilui o mix)
"""
from __future__ import annotations

from app.config import settings
from app.models.schemas import Bucket


def classificar(pct_lf: float, pct_ipca: float, pct_cdi: float) -> Bucket:
    """Aplica a regra hierárquica e devolve o bucket.

    Args:
        pct_lf: fração do PL em Letras Financeiras (0..1)
        pct_ipca: fração do PL em ativos IPCA+ (0..1)
        pct_cdi: fração do PL em ativos CDI+/DI+ (0..1)

    Returns:
        Bucket correspondente.
    """
    thr = settings.THRESHOLD_MAJORITARIO

    # 1) LF domina a carteira inteira?
    if pct_lf > thr:
        return Bucket.LF

    # 2/3) Reescala o restante removendo LF, e mede IPCA vs CDI sobre a base ex-LF.
    base_ex_lf = 1.0 - pct_lf
    if base_ex_lf <= 0:
        return Bucket.MISTO

    share_ipca = pct_ipca / base_ex_lf
    share_cdi = pct_cdi / base_ex_lf

    if share_ipca > thr:
        return Bucket.IPCA
    if share_cdi > thr:
        return Bucket.CDI

    # 4) Nenhum majoritário claro.
    return Bucket.MISTO


def classificar_dict(composicao: dict) -> Bucket:
    """Versão que aceita o dict de composição direto, tolerante a chaves ausentes."""
    return classificar(
        pct_lf=float(composicao.get("pct_lf", 0.0) or 0.0),
        pct_ipca=float(composicao.get("pct_ipca", 0.0) or 0.0),
        pct_cdi=float(composicao.get("pct_cdi", 0.0) or 0.0),
    )
