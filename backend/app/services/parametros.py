"""
Parâmetros de classificação editáveis em tempo de execução.

POR QUE ISTO EXISTE

O corte que separa "LF" de "fundo de crédito" é uma régua de negócio, não uma
constante de engenharia: ela muda quando a mesa muda de leitura sobre o que é
uma carteira dominada por papel bancário. Deixá-la só no `.env` obrigaria a
editar arquivo e reiniciar o servidor a cada calibragem — e, na prática, a
régua ficaria congelada no valor que alguém escolheu uma vez.

Aqui os parâmetros ficam em três camadas, da mais fraca para a mais forte:

    1. default no código (app/config.py)
    2. variável de ambiente / .env
    3. data/parametros.json  <- o que o painel de controle grava

A camada 3 é aplicada sobre o objeto `settings` no import, de modo que todo
código que já lê `settings.THRESHOLD_MAJORITARIO` passa a enxergar o valor
editado sem saber que este módulo existe.

>>> ESCOPO: só entram aqui parâmetros cuja mudança é reversível relendo os
    dados que já estão em memória. O corte de classificação é assim — mexer
    nele reclassifica os fundos, e nada mais. Um parâmetro que exija rebaixar
    o CDA da CVM não pertence a esta lista: o painel prometeria um efeito
    imediato que ele não teria.
"""
from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass
from datetime import UTC

from app.config import DATA_DIR, settings

logger = logging.getLogger("parametros")

ARQUIVO = DATA_DIR / "parametros.json"

# O painel pode ser usado por mais de uma pessoa ao mesmo tempo; a escrita do
# arquivo e a mutação de `settings` andam juntas e precisam ser atômicas.
_LOCK = threading.Lock()


@dataclass(frozen=True)
class Definicao:
    """Um parâmetro editável, com a régua de validação junto.

    `atributo` é o nome em `Settings`; `chave` é o nome que trafega na API e
    no JSON. Os dois são separados de propósito: o front fala em percentual
    (20), o backend guarda fração (0.20), e a conversão fica num lugar só.
    """
    chave: str
    atributo: str
    rotulo: str
    descricao: str
    minimo: float          # em percentual, como o usuário digita
    maximo: float
    passo: float


DEFINICOES: tuple[Definicao, ...] = (
    Definicao(
        chave="threshold_majoritario",
        atributo="THRESHOLD_MAJORITARIO",
        rotulo="Corte de classificação",
        descricao=(
            "Fatia mínima da carteira para uma classe ser considerada "
            "majoritária. Acima deste corte em Letras Financeiras, o fundo é "
            "LF; abaixo, ele é fundo de crédito e passa pela verificação de "
            "Incentivada / Tradicional."
        ),
        minimo=1.0,
        maximo=99.0,
        passo=0.5,
    ),
    Definicao(
        chave="hedge_dap_minimo",
        atributo="HEDGE_DAP_MINIMO",
        rotulo="Cobertura mínima de hedge em DAP",
        descricao=(
            "Nocional em futuro de DAP sobre o valor da carteira indexada a "
            "IPCA. Acima deste piso, entendemos que o fundo trava o cupom de "
            "inflação e fica só com o spread de crédito."
        ),
        minimo=0.0,
        maximo=300.0,
        passo=5.0,
    ),
)

_POR_CHAVE = {d.chave: d for d in DEFINICOES}


def definicoes() -> list[dict]:
    """Metadados dos parâmetros — é o que o painel usa para montar os campos."""
    return [
        {
            "chave": d.chave,
            "rotulo": d.rotulo,
            "descricao": d.descricao,
            "minimo": d.minimo,
            "maximo": d.maximo,
            "passo": d.passo,
            "valor": _ler(d),
            "padrao_codigo": _PADROES[d.chave],
        }
        for d in DEFINICOES
    ]


def atual() -> dict[str, float]:
    """Valores correntes, em percentual."""
    return {d.chave: _ler(d) for d in DEFINICOES}


def validar(valores: dict) -> dict[str, float]:
    """Normaliza e valida a entrada do painel. Levanta ValueError com o motivo.

    Chave desconhecida é erro, e não algo a ignorar em silêncio: um painel que
    aceita `thresold_majoritario` e não faz nada é pior que um que recusa.
    """
    if not valores:
        raise ValueError("Nenhum parâmetro informado.")

    desconhecidas = set(valores) - set(_POR_CHAVE)
    if desconhecidas:
        raise ValueError(
            f"Parâmetro desconhecido: {', '.join(sorted(desconhecidas))}. "
            f"Aceitos: {', '.join(_POR_CHAVE)}."
        )

    limpos: dict[str, float] = {}
    for chave, bruto in valores.items():
        d = _POR_CHAVE[chave]
        try:
            v = float(bruto)
        except (TypeError, ValueError):
            raise ValueError(f"{d.rotulo}: '{bruto}' não é um número.") from None
        if v != v or v in (float("inf"), float("-inf")):
            raise ValueError(f"{d.rotulo}: valor inválido.")
        if not (d.minimo <= v <= d.maximo):
            raise ValueError(
                f"{d.rotulo}: {v:g}% está fora da faixa aceita "
                f"({d.minimo:g}% a {d.maximo:g}%)."
            )
        limpos[chave] = v
    return limpos


def aplicar(valores: dict) -> dict[str, dict[str, float]]:
    """Valida, grava em disco e passa a valer. Devolve o que mudou.

    O retorno é {chave: {"de": x, "para": y}} só para o que realmente mudou —
    é o que o painel mostra de volta e o que vai para o log. Um PUT que não
    mexe em nada devolve `{}`, e quem chama decide se vale reclassificar.
    """
    limpos = validar(valores)
    with _LOCK:
        mudancas = {}
        for chave, v in limpos.items():
            d = _POR_CHAVE[chave]
            antes = _ler(d)
            if abs(antes - v) < 1e-9:
                continue
            _escrever(d, v)
            mudancas[chave] = {"de": antes, "para": v}
        if mudancas:
            _persistir()
            logger.info("Parâmetros alterados: %s", mudancas)
    return mudancas


def restaurar_padroes() -> dict[str, dict[str, float]]:
    """Volta aos valores do código/.env e apaga o arquivo de overrides."""
    with _LOCK:
        mudancas = {}
        for d in DEFINICOES:
            antes = _ler(d)
            padrao = _PADROES[d.chave]
            if abs(antes - padrao) >= 1e-9:
                _escrever(d, padrao)
                mudancas[d.chave] = {"de": antes, "para": padrao}
        ARQUIVO.unlink(missing_ok=True)
        if mudancas:
            logger.info("Parâmetros restaurados ao padrão: %s", mudancas)
    return mudancas


def carregar_do_disco() -> None:
    """Aplica `data/parametros.json` sobre `settings`. Chamado no start da API.

    Arquivo ausente é o caso normal (nunca se editou nada pelo painel). Arquivo
    corrompido ou com valor fora da faixa é logado e ignorado: subir com o
    default é melhor que não subir, e o painel mostra o valor que valeu.
    """
    if not ARQUIVO.exists():
        return
    try:
        dados = json.loads(ARQUIVO.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        logger.warning("%s ilegível (%s) — seguindo com os padrões.", ARQUIVO.name, e)
        return

    salvos = dados.get("valores", dados) if isinstance(dados, dict) else {}
    for chave, valor in (salvos or {}).items():
        d = _POR_CHAVE.get(chave)
        if d is None:
            logger.warning("%s: parâmetro '%s' não existe mais — ignorado.",
                           ARQUIVO.name, chave)
            continue
        try:
            _escrever(d, validar({chave: valor})[chave])
        except ValueError as e:
            logger.warning("%s: %s — mantendo o padrão.", ARQUIVO.name, e)
    logger.info("Parâmetros carregados de %s: %s", ARQUIVO.name, atual())


# ---------- internos ----------
def _ler(d: Definicao) -> float:
    """Fração 0..1 em `settings` -> percentual, arredondado ao passo do campo."""
    return round(float(getattr(settings, d.atributo)) * 100, 4)


def _escrever(d: Definicao, valor_pct: float) -> None:
    setattr(settings, d.atributo, valor_pct / 100.0)


def _persistir() -> None:
    ARQUIVO.parent.mkdir(parents=True, exist_ok=True)
    from datetime import datetime

    conteudo = {
        "atualizado_em": datetime.now(UTC).isoformat(timespec="seconds"),
        "valores": {d.chave: _ler(d) for d in DEFINICOES},
    }
    # Grava em arquivo temporário e troca: uma queda no meio da escrita deixaria
    # um JSON pela metade, e o próximo start subiria sem os parâmetros.
    tmp = ARQUIVO.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(conteudo, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(ARQUIVO)


# Fotografia dos defaults ANTES de qualquer override — é para cá que o botão
# "restaurar padrão" volta, e é o que o painel mostra ao lado do valor atual.
_PADROES: dict[str, float] = {d.chave: _ler(d) for d in DEFINICOES}
