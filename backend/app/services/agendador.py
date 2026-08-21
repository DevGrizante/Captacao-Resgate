"""
Tarefas periódicas dentro do próprio processo da API.

>>> POR QUE NÃO CRON, CELERY OU APSCHEDULER

    cron        exigiria uma segunda unidade de deploy (container ou timer do
                systemd) para chamar um endpoint que já existe. Em nuvem isso
                é mais uma coisa para provisionar, monitorar e explicar.
    Celery      exigiria broker (Redis/RabbitMQ) — infra e custo mensal para
                duas tarefas que rodam a cada quinze minutos.
    APScheduler uma dependência a mais para o que `asyncio` já faz em vinte
                linhas, num app que tem exatamente um processo.

    O critério é o tamanho do problema: são duas tarefas, num processo só, sem
    necessidade de garantia de entrega. Trazer um broker para isso seria
    resolver um problema que não temos e ganhar dois que não tínhamos.

>>> O QUE ESTE MÓDULO GARANTE

    · A tarefa roda em THREAD, nunca no event loop. Buscar e-mail e reprocessar
      o CDA são operações de bloqueio: rodá-las no loop congelaria TODA
      requisição HTTP do painel pelo tempo que durassem. É exatamente o tipo de
      lentidão que ninguém consegue diagnosticar depois.
    · Exceção em tarefa NÃO mata o laço. Uma caixa de e-mail fora do ar por uma
      hora não pode desligar a coleta para sempre — que é o que acontece quando
      uma exceção escapa de um `while True`.
    · O desligamento é limpo: o `stop()` cancela as tarefas e o processo morre
      sem deixar thread pendurada.
    · Cada execução deixa rastro no log com o tempo que levou, porque "a coleta
      automática está rodando?" precisa ter resposta sem ligar um depurador.

>>> A PRIMEIRA EXECUÇÃO É ADIADA

Toda tarefa espera um intervalo antes da primeira rodada. Sem isso, subir o
processo dispararia a coleta junto com a primeira carga de dados — as duas
disputando CPU e rede no minuto em que o painel mais precisa responder.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable

logger = logging.getLogger("agendador")

_tarefas: list[asyncio.Task] = []


async def _laco(nome: str, fn: Callable[[], object], intervalo_s: int) -> None:
    """Roda `fn` a cada `intervalo_s`, para sempre, sem nunca deixar escapar."""
    while True:
        try:
            await asyncio.sleep(intervalo_s)
        except asyncio.CancelledError:
            logger.info("Tarefa %r encerrada.", nome)
            raise

        inicio = time.monotonic()
        try:
            # `to_thread` e não `await fn()`: as tarefas são código síncrono de
            # bloqueio (requests, imaplib, pandas). Chamá-las direto no loop
            # travaria todas as requisições HTTP enquanto durassem.
            await asyncio.to_thread(fn)
            logger.info(
                "Tarefa %r concluída em %.1fs.", nome, time.monotonic() - inicio
            )
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            # Deixar a exceção subir encerraria o laço em definitivo, e a
            # tarefa nunca mais rodaria — sem nada indicando isso além da
            # ausência de dado novo. Log e segue para a próxima rodada.
            logger.exception(
                "Tarefa %r falhou; próxima tentativa em %ds.", nome, intervalo_s
            )


def registrar(nome: str, fn: Callable[[], object], intervalo_min: int) -> bool:
    """Agenda `fn` a cada `intervalo_min` minutos. False se ficou desligada."""
    if intervalo_min <= 0:
        logger.info("Tarefa %r não agendada (intervalo = 0).", nome)
        return False

    tarefa = asyncio.create_task(_laco(nome, fn, intervalo_min * 60), name=nome)
    _tarefas.append(tarefa)
    logger.info("Tarefa %r agendada a cada %d min.", nome, intervalo_min)
    return True


async def parar() -> None:
    """Cancela tudo e espera terminar. Chamado no desligamento da API."""
    for tarefa in _tarefas:
        tarefa.cancel()
    if _tarefas:
        await asyncio.gather(*_tarefas, return_exceptions=True)
    _tarefas.clear()
