"""
Registro de debêntures do SND — o indexador de cada papel.

POR QUE ISTO EXISTE

O CDA da CVM entrega a carteira fundo a fundo, mas o bloco das debêntures
(BLC_4) identifica o papel só pelo código (`CSAN33`, `TFLE19`) e não diz a que
ele é indexado. Debênture é 45% da carteira de crédito do nosso universo — sem
essa ponte, quase metade do dinheiro fica sem indexador e o bucket não fecha.

O SND (Sistema Nacional de Debêntures) publica o arquivo de características de
todas as debêntures registradas, e nele vem a coluna `indice`. O casamento é
pelo código do ativo e é praticamente total:

    debêntures no BLC_4 ....... 231.508 linhas / R$ 792,9 bi
    casadas no SND ............ 230.761 linhas (99,7%) / R$ 788,3 bi (99,4%)

    DI R$ 482,7 bi | IPCA R$ 290,3 bi | pré R$ 12,2 bi | IGP-M R$ 2,8 bi

>>> FRAGILIDADE CONHECIDA
    O arquivo abre com um aviso de que a consulta migrou para data.anbima.com.br.
    O endpoint continua servindo dado do dia (conferido em 14/08/2026), mas é um
    caminho que a ANBIMA pode desligar. Se ele cair, `carregar()` devolve um mapa
    vazio: as debêntures ficam sem indexador, a cobertura da carteira despenca
    abaixo da guarda e os fundos afetados voltam a aparecer como "sem
    classificação" — nunca com um bucket adivinhado.
"""
from __future__ import annotations

import io
import logging
import time
import unicodedata

import pandas as pd
import requests

from app.config import CACHE_DIR, settings

logger = logging.getLogger("snd_debentures")

URL_SND = (
    "http://www.debentures.com.br/exploreosnd/consultaadados/emissoesdedebentures"
    "/caracteristicas_e.asp?tip_deb=publicas&op_exc=Nada"
)

_CACHE = CACHE_DIR / "snd_debentures_v1.parquet"

# O rótulo do SND -> o eixo que interessa ao crédito.
# IGP-M/IGP-DI/INPC entram em "ipca" de propósito: para a mesa o que importa é
# o papel ser corrigido por inflação, não qual índice de preços específico.
INDICE_PARA_EIXO = {
    "DI": "cdi",
    "ANBID": "cdi",
    "SELIC": "cdi",
    "IPCA": "ipca",
    "INPC": "ipca",
    "IGP-M": "ipca",
    "IGP-DI": "ipca",
    "IGPM": "ipca",
    "PRE": "pre",
    "SEM-INDICE": "pre",
    "TJLP": "outro",
    "TR": "outro",
    "TR-REAL": "outro",
    "DOLAR": "outro",
}


def _sem_acento(v) -> str:
    s = unicodedata.normalize("NFKD", str(v or ""))
    return "".join(c for c in s if not unicodedata.combining(c)).upper().strip()


def _fresco() -> bool:
    return (
        _CACHE.exists()
        and (time.time() - _CACHE.stat().st_mtime) / 3600 < settings.SND_TTL_HORAS
    )


def carregar() -> dict[str, str]:
    """Mapa {código do ativo: eixo} — 'cdi', 'ipca', 'pre' ou 'outro'.

    Devolve `{}` se a fonte estiver fora do ar; quem chama trata isso como
    "não sei o indexador destas debêntures", não como zero.
    """
    if _fresco():
        try:
            df = pd.read_parquet(_CACHE)
            logger.info("SND: usando cache local (%d papéis).", len(df))
            return dict(zip(df["cod"], df["eixo"]))
        except Exception as e:  # noqa: BLE001
            logger.debug("cache do SND ilegível (%s), rebaixando.", e)

    try:
        resp = requests.get(
            URL_SND,
            timeout=settings.CVM_TIMEOUT_S,
            # Sem User-Agent de navegador o servidor do SND devolve página de erro.
            headers={"User-Agent": "Mozilla/5.0"},
        )
        resp.raise_for_status()
    except Exception as e:  # noqa: BLE001
        logger.warning("SND indisponível (%s) — debêntures ficarão sem indexador.", e)
        return {}

    # O arquivo é TSV precedido de um cabeçalho de texto livre (aviso da ANBIMA
    # + data de geração). Localizamos a linha de títulos em vez de pular um
    # número fixo de linhas, que quebraria se o aviso mudasse de tamanho.
    linhas = resp.content.decode("latin-1").split("\r\n")
    inicio = next((i for i, l in enumerate(linhas) if l.startswith("Codigo do Ativo")), None)
    if inicio is None:
        logger.warning("SND: cabeçalho não encontrado — formato mudou.")
        return {}

    try:
        df = pd.read_csv(io.StringIO("\n".join(linhas[inicio:])), sep="\t", low_memory=False)
    except Exception as e:  # noqa: BLE001
        logger.warning("SND: arquivo ilegível (%s).", e)
        return {}

    df.columns = [c.strip() for c in df.columns]
    if "indice" not in df.columns or "Codigo do Ativo" not in df.columns:
        logger.warning("SND: colunas esperadas ausentes — formato mudou.")
        return {}

    df["cod"] = df["Codigo do Ativo"].astype(str).str.strip()
    df["eixo"] = df["indice"].map(lambda v: INDICE_PARA_EIXO.get(_sem_acento(v), "outro"))
    df = df[df["cod"].str.len() > 0].drop_duplicates("cod")[["cod", "eixo"]]

    try:
        df.to_parquet(_CACHE)
    except Exception as e:  # noqa: BLE001
        logger.debug("cache do SND: %s", e)
    logger.info("SND: %d debêntures com indexador.", len(df))
    return dict(zip(df["cod"], df["eixo"]))
