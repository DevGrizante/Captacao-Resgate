"""
A planilha do dia como recurso de rede.

TRÊS ENDPOINTS, UM PROPÓSITO

    POST /api/inbox                 empurra a planilha para dentro
    GET  /api/inbox/ultimo          o que temos, e de quando
    GET  /api/inbox/ultimo/arquivo  o .xlsx em si

Juntos eles transformam "o anexo que chega no Outlook do Raphael às 7h" em um
endereço fixo que qualquer sistema consulta — este painel, uma planilha, um
robô de mesa, o que vier depois. Quem produz o dado deixa de ser uma máquina
específica.

CORPO BINÁRIO, NÃO MULTIPART

    O upload aceita os bytes crus (`application/octet-stream`) em vez de
    formulário multipart. Multipart exigiria a dependência `python-multipart`
    e, do lado de quem chama, montar um formulário. Assim funciona com um
    comando só:

        curl -X POST http://SERVIDOR/api/inbox \\
             -H "Authorization: Bearer SEU_TOKEN" \\
             --data-binary @vinculado_20260819_0743.xlsx

AUTENTICAÇÃO

    Token único no cabeçalho `Authorization: Bearer ...`, conferido em tempo
    constante. É o bastante para seis pessoas conhecidas e um script agendado;
    não é o bastante para um endpoint exposto sem HTTPS, porque aí o token
    viaja em texto claro. Ver o README antes de publicar.

    Sem `INGESTAO_TOKEN` configurado, os três endpoints respondem 503. Fechado
    por omissão é a única opção defensável: quem esquecer de configurar fica
    sem o recurso, em vez de ficar com ele aberto.
"""
from __future__ import annotations

import logging
import secrets
from datetime import datetime

from fastapi import APIRouter, Header, HTTPException, Request, status
from fastapi.responses import FileResponse

from app.config import settings
from app.services import ingestao
from app.services.pipeline import pipeline

logger = logging.getLogger("ingestao")

router = APIRouter(prefix="/api/inbox", tags=["ingestao"])

_TIPO_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _exigir_token(autorizacao: str | None) -> None:
    """Porteiro dos três endpoints.

    `secrets.compare_digest` em vez de `==`: a comparação normal para no
    primeiro byte diferente, e o tempo que ela leva vaza quantos caracteres do
    token o atacante já acertou. É barato fechar essa porta.
    """
    if not settings.INGESTAO_TOKEN:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "Ingestão desligada: defina INGESTAO_TOKEN no .env do servidor "
                "para habilitar."
            ),
        )

    esperado = f"Bearer {settings.INGESTAO_TOKEN}"
    if not autorizacao or not secrets.compare_digest(autorizacao, esperado):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token de ingestão ausente ou incorreto.",
            headers={"WWW-Authenticate": "Bearer"},
        )


def _quando(cabecalho: str | None) -> datetime | None:
    """Lê o X-Recebido-Em, se veio.

    O coletor manda a hora em que o E-MAIL chegou, não a do upload. Importa
    porque é ela que nomeia o arquivo: se o script rodar atrasado às 11h com um
    e-mail das 7h43, o arquivo precisa continuar sendo o das 7h43 — senão o
    histórico da inbox passa a contar a hora do robô em vez da hora do dado.
    """
    if not cabecalho:
        return None
    try:
        quando = datetime.fromisoformat(cabecalho.replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"X-Recebido-Em não é uma data ISO 8601 válida: {cabecalho!r}",
        ) from None
    # O nome do arquivo é hora local; um valor com fuso vira local antes de virar nome.
    if quando.tzinfo is not None:
        quando = quando.astimezone().replace(tzinfo=None)
    return quando


def _descricao(recebido: ingestao.Recebido) -> dict:
    """Metadados de uma planilha, no formato que a API devolve."""
    data = ingestao.data_do_nome(recebido.nome)
    idade_h = None
    if data is not None:
        idade_h = round((datetime.now() - data).total_seconds() / 3600, 1)
    return {
        "arquivo": recebido.nome,
        "recebido_em": data.isoformat() if data else None,
        "idade_horas": idade_h,
        "bytes": recebido.bytes_,
        "sha256": recebido.sha256,
    }


@router.post("", status_code=status.HTTP_200_OK)
async def post_inbox(
    request: Request,
    authorization: str | None = Header(default=None),
    x_recebido_em: str | None = Header(default=None),
    recalcular: bool = True,
):
    """Recebe a planilha do dia e, por padrão, já recalcula o painel.

    `recalcular=false` grava e devolve na hora, sem recomputar — útil para
    reenviar um arquivo antigo sem sacudir o que a mesa está olhando.

    Com `recalcular=true` (padrão) a resposta só sai depois de o pipeline
    refazer as contas. Normalmente são segundos; se o cache da CVM tiver
    expirado no mesmo instante, pode passar de minutos, porque aí a carga
    pesada entra junto. O coletor usa timeout largo por causa disso.
    """
    _exigir_token(authorization)
    quando = _quando(x_recebido_em)

    corpo = await request.body()
    try:
        recebido = ingestao.receber(corpo, recebido_em=quando)
    except ingestao.IngestaoInvalida as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e)
        ) from e

    resposta = {
        "status": "ja_tinha" if recebido.ja_existia else "novo",
        **_descricao(recebido),
        "recalculado": False,
    }

    # Sem novidade não há o que recalcular: refazer o pipeline por um arquivo
    # idêntico seria gastar segundos para chegar exatamente no mesmo número.
    if recalcular and not recebido.ja_existia:
        try:
            pipeline.refresh()
            resposta["recalculado"] = True
            # Qual arquivo o painel passou a usar. Não é redundante com
            # `arquivo`: se chegar uma planilha com data ANTERIOR à que já
            # estava lá, ela é salva mas não vira a vigente. Devolver as duas
            # coisas é o que deixa isso visível para quem enviou, em vez de
            # dar "ok" para um upload que não mudou nada.
            resposta["painel_lendo"] = getattr(pipeline.fonte_info(), "arquivo", None)
        except Exception as e:  # noqa: BLE001
            # O arquivo ESTÁ salvo. Falhar o request agora faria o coletor
            # tentar de novo amanhã achando que não enviou nada.
            logger.exception("Planilha salva, mas o recálculo falhou.")
            resposta["erro_recalculo"] = str(e)

    return resposta


@router.get("/ultimo")
def get_ultimo(authorization: str | None = Header(default=None)):
    """O que o servidor tem hoje: nome, hora do e-mail, tamanho e hash.

    O `sha256` é o que permite ao coletor não reenviar o que já está lá — ele
    compara antes de gastar a subida.
    """
    _exigir_token(authorization)
    recebido = ingestao.ultimo()
    if recebido is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Nenhuma planilha recebida ainda.",
        )
    return _descricao(recebido)


@router.get("/ultimo/arquivo")
def get_ultimo_arquivo(authorization: str | None = Header(default=None)):
    """Baixa a planilha mais recente.

    É o que faz disto uma fonte, e não só um depósito: outro sistema consome o
    mesmo arquivo que o painel está usando, com a garantia de ser exatamente o
    mesmo — o hash de /ultimo confere.
    """
    _exigir_token(authorization)
    recebido = ingestao.ultimo()
    if recebido is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Nenhuma planilha recebida ainda.",
        )
    return FileResponse(
        recebido.caminho,
        media_type=_TIPO_XLSX,
        filename=recebido.nome,
        headers={"X-Sha256": recebido.sha256},
    )
