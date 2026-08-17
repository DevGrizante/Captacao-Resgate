"""
Painel de controle — parâmetros de classificação.

O que este router entrega é uma coisa só, e é preciso ser explícito sobre ela:
mudar o corte percentual e ver a base inteira se reclassificar na mesma
resposta. Por isso o PUT não devolve "ok": ele devolve quantos fundos mudaram
de bucket e para onde foram. Um painel que altera uma régua de negócio sem
mostrar o efeito convida o usuário a mexer no número até parecer certo.

>>> O QUE A RECLASSIFICAÇÃO NÃO FAZ

Ela não rebaixa dado da CVM nem revarre o Outlook. A composição da carteira, o
nome do fundo e a cobertura de DAP — os três insumos da regra — já estão em
memória. Refazer a carga demoraria minutos e traria variáveis que ninguém
pediu para mudar, tornando impossível dizer se o antes/depois veio do
parâmetro ou de um CDA novo. Para recarregar a fonte existe
`POST /api/admin/refresh`, que é outra operação e está em routers/api.py.

>>> SEGURANÇA

Não há autenticação aqui, pelo mesmo motivo que não há no resto da API: isto
roda em localhost, na máquina do analista, com CORS restrito às origens do
front. Se um dia subir para um servidor compartilhado, este é o primeiro
router a ganhar autenticação — ele escreve em disco e muda o que todo mundo vê.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException

from app.models.schemas import (
    ParametrosResponse,
    ParametrosUpdate,
    ReclassificacaoResponse,
)
from app.services import parametros
from app.services.pipeline import pipeline

logger = logging.getLogger("admin")

router = APIRouter(prefix="/api/admin", tags=["admin"])


@router.get("/parametros", response_model=ParametrosResponse)
def get_parametros():
    """Parâmetros editáveis e o retrato atual da classificação."""
    return _estado()


@router.put("/parametros", response_model=ReclassificacaoResponse)
def put_parametros(corpo: ParametrosUpdate):
    """Grava os parâmetros e reclassifica a base inteira.

    Os dois passos são um só de propósito: um PUT que só gravasse deixaria o
    banco em desacordo com o parâmetro até alguém lembrar de apertar outro
    botão, e o painel estaria dizendo 20% enquanto a tela mostra fundos
    classificados a 50%.
    """
    try:
        mudancas = parametros.aplicar(corpo.valores)
    except ValueError as e:
        # 422: a requisição está bem-formada, o valor é que não serve. O texto
        # do erro é o que aparece no painel, então ele diz a faixa aceita.
        raise HTTPException(status_code=422, detail=str(e)) from None

    if not mudancas:
        # Nada mudou: reclassificar daria o mesmo resultado e custaria o mesmo.
        # Responder com o estado atual é mais honesto que fingir trabalho.
        resultado = _resultado_vazio()
        resultado["status"] = "sem alteração"
        return ReclassificacaoResponse(**resultado)

    logger.info("Painel de controle alterou parâmetros: %s", mudancas)
    resultado = pipeline.reclassificar()
    return ReclassificacaoResponse(status="ok", mudancas=mudancas, **resultado)


@router.post("/parametros/restaurar", response_model=ReclassificacaoResponse)
def post_restaurar():
    """Volta aos valores de `.env`/código e reclassifica."""
    mudancas = parametros.restaurar_padroes()
    if not mudancas:
        resultado = _resultado_vazio()
        resultado["status"] = "já estava no padrão"
        return ReclassificacaoResponse(**resultado)
    resultado = pipeline.reclassificar()
    return ReclassificacaoResponse(status="ok", mudancas=mudancas, **resultado)


@router.post("/reclassificar", response_model=ReclassificacaoResponse)
def post_reclassificar():
    """Reaplica a regra sem mexer em parâmetro.

    Útil depois de um `refresh` da fonte, ou para conferir que a base está de
    acordo com o parâmetro corrente sem ter que alterá-lo e desalterá-lo.
    """
    return ReclassificacaoResponse(status="ok", **pipeline.reclassificar())


def _estado() -> ParametrosResponse:
    distribuicao = pipeline.distribuicao()
    total = pipeline.total_fundos()
    return ParametrosResponse(
        parametros=parametros.definicoes(),
        distribuicao=distribuicao,
        total_fundos=total,
        total_sem_classificacao=distribuicao.get("sem_classificacao", 0),
    )


def _resultado_vazio() -> dict:
    """Resposta para o caso em que nada mudou — antes e depois são o mesmo."""
    distribuicao = pipeline.distribuicao()
    return {
        "status": "sem alteração",
        "mudancas": {},
        "total_fundos": pipeline.total_fundos(),
        "fundos_reclassificados": 0,
        "distribuicao_antes": distribuicao,
        "distribuicao_depois": distribuicao,
        "alteracoes": {},
        "duracao_s": 0.0,
    }
