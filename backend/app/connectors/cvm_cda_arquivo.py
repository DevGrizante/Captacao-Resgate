"""
O arquivo do CDA, baixado uma vez e compartilhado.

Dois módulos leem o mesmo zip mensal da CVM por motivos diferentes:

    connectors/cvm_carteira.py   composição por indexador e hedge em DAP
    connectors/cvm_emissores.py  quem emitiu o papel bancário que o fundo carrega

Cada um tem o seu próprio parquet de saída, mas a origem é a mesma. Sem este
módulo, uma carga fria baixaria 24 MB duas vezes e gastaria o dobro do tempo
para chegar exatamente ao mesmo lugar.

O zip fica em `data/cache/`, com o mesmo TTL da composição (`CDA_TTL_HORAS`).
Guardar o bruto em disco também paga em desenvolvimento: reprocessar a leitura
deixa de depender da rede.

>>> A DEFASAGEM VIVE AQUI

`mes_alvo()` é o único lugar que decide de que mês é o CDA. Ela era privada em
`cvm_carteira`; subiu para cá quando o segundo leitor apareceu, porque os dois
precisam ler o MESMO mês — um mapa de emissores de um mês e uma composição de
outro se contradiriam na tela sem que ninguém percebesse.

O porquê da defasagem está documentado em `cvm_carteira.py`: no mês corrente
46% do PL do universo está sob sigilo, e o que sobra é amostra enviesada.
"""
from __future__ import annotations

import io
import logging
import time
import zipfile
from datetime import date

import requests

from app.config import CACHE_DIR, settings

logger = logging.getLogger("cvm_cda")

URL_CDA = "https://dados.cvm.gov.br/dados/FI/DOC/CDA/DADOS/cda_fi_{aaaamm}.zip"


def mes_alvo() -> str:
    """AAAAMM de `CDA_DEFASAGEM_MESES` atrás."""
    hoje = date.today()
    total = hoje.year * 12 + (hoje.month - 1) - settings.CDA_DEFASAGEM_MESES
    return f"{total // 12:04d}{total % 12 + 1:02d}"


def _caminho(mes: str):
    return CACHE_DIR / f"cda_fi_{mes}.zip"


def abrir(mes: str | None = None) -> zipfile.ZipFile | None:
    """Devolve o zip do CDA do mês, baixando se necessário. None se indisponível.

    Quem chama trata `None` como "não temos carteira deste mês" e segue sem —
    nunca como carteira vazia, que seria lida como "o fundo não tem nada".
    """
    mes = mes or mes_alvo()
    destino = _caminho(mes)

    if destino.exists():
        idade_h = (time.time() - destino.stat().st_mtime) / 3600
        if idade_h < settings.CDA_TTL_HORAS:
            try:
                return zipfile.ZipFile(destino)
            except zipfile.BadZipFile:
                # Download interrompido deixa um arquivo truncado. Apagar e
                # rebaixar é melhor que propagar o erro: o estado é recuperável.
                logger.warning("CDA %s em cache está corrompido — rebaixando.", mes)
                destino.unlink(missing_ok=True)

    try:
        resp = requests.get(URL_CDA.format(aaaamm=mes), timeout=settings.CVM_TIMEOUT_S)
        resp.raise_for_status()
        conteudo = resp.content
        z = zipfile.ZipFile(io.BytesIO(conteudo))  # valida antes de gravar
    except Exception as e:  # noqa: BLE001
        logger.warning("CDA %s indisponível (%s).", mes, e)
        return None

    try:
        # Grava em temporário e troca: uma queda no meio da escrita deixaria um
        # zip truncado que o próximo start leria como cache válido.
        tmp = destino.with_suffix(".zip.tmp")
        tmp.write_bytes(conteudo)
        tmp.replace(destino)
        logger.info("CDA %s baixado (%.1f MB).", mes, len(conteudo) / 1e6)
    except Exception as e:  # noqa: BLE001
        logger.debug("não consegui guardar o CDA em cache: %s", e)
    return z
