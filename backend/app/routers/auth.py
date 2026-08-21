"""
Entrar e sair do painel.

    GET  /login   a página
    POST /login   confere a senha e devolve o cookie
    POST /logout  apaga o cookie

O `?proximo=` carrega para onde a pessoa queria ir antes de esbarrar no
portão. Sem isso, quem clicasse num link direto do dossiê de uma gestora seria
jogado na home depois de entrar, e teria de refazer o caminho.
"""
from __future__ import annotations

import logging
from urllib.parse import parse_qs, urlparse

from fastapi import APIRouter, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse

from app.config import BASE_DIR, settings
from app.services import sessao

logger = logging.getLogger("auth")

router = APIRouter(tags=["acesso"])

_PAGINA = BASE_DIR / "frontend" / "login.html"


def _ip(request: Request) -> str:
    """IP de quem está chamando, respeitando o proxy reverso.

    Atrás do Caddy todo mundo chega como 127.0.0.1; sem olhar o
    X-Forwarded-For, o freio de tentativas trataria o servidor inteiro como um
    único cliente e um atacante travaria o acesso de todos os outros.
    """
    encaminhado = request.headers.get("x-forwarded-for", "")
    if encaminhado:
        return encaminhado.split(",")[0].strip()
    return request.client.host if request.client else "desconhecido"


def _destino_seguro(proximo: str | None) -> str:
    """Só aceita caminho interno.

    `?proximo=https://site-malicioso/` transformaria a nossa página de login
    num trampolim de redirecionamento — o usuário digita a senha no domínio
    certo e cai em outro. Aceitar apenas caminhos que começam com "/" e não
    são "//" (que o navegador lê como outro host) fecha isso.
    """
    if not proximo or not proximo.startswith("/") or proximo.startswith("//"):
        return "/"
    if urlparse(proximo).netloc:
        return "/"
    return proximo


@router.get("/login", response_class=HTMLResponse)
def get_login(request: Request, proximo: str = "/", erro: str = ""):
    # Sem senha configurada não existe login: mandar de volta evita a tela
    # sem função e a impressão de que o painel está protegido quando não está.
    if not sessao.protegido():
        return RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)

    if sessao.cookie_valido(request.cookies.get(sessao.NOME_COOKIE)):
        return RedirectResponse(_destino_seguro(proximo), status_code=status.HTTP_303_SEE_OTHER)

    html = _PAGINA.read_text(encoding="utf-8")
    aviso = ""
    if erro == "senha":
        aviso = "Senha incorreta."
    elif erro == "travado":
        aviso = ("Muitas tentativas seguidas. Espere alguns minutos antes de "
                 "tentar de novo.")
    return HTMLResponse(
        html.replace("{{ERRO}}", aviso).replace("{{PROXIMO}}", _destino_seguro(proximo))
    )


async def _campos(request: Request) -> dict[str, str]:
    """Lê um formulário urlencoded sem depender de `python-multipart`.

    O `Form(...)` do FastAPI puxaria essa dependência só para ler dois campos
    de texto. `parse_qs` da biblioteca padrão faz o mesmo — e o formulário do
    login é `application/x-www-form-urlencoded`, não multipart, porque não há
    arquivo nenhum a enviar.
    """
    corpo = (await request.body()).decode("utf-8", errors="replace")
    return {k: v[0] for k, v in parse_qs(corpo, keep_blank_values=True).items()}


@router.post("/login")
async def post_login(request: Request):
    campos = await _campos(request)
    senha = campos.get("senha", "")
    destino = _destino_seguro(campos.get("proximo", "/"))
    ip = _ip(request)

    faltam = sessao.bloqueado(ip)
    if faltam:
        logger.warning("Login recusado por excesso de tentativas: %s", ip)
        return RedirectResponse(f"/login?erro=travado&proximo={destino}",
                                status_code=status.HTTP_303_SEE_OTHER)

    if not sessao.senha_confere(senha):
        sessao.registrar_erro(ip)
        return RedirectResponse(f"/login?erro=senha&proximo={destino}",
                                status_code=status.HTTP_303_SEE_OTHER)

    sessao.registrar_acerto(ip)
    valor, duracao = sessao.criar_cookie()
    resposta = RedirectResponse(destino, status_code=status.HTTP_303_SEE_OTHER)
    resposta.set_cookie(
        sessao.NOME_COOKIE,
        valor,
        max_age=duracao,
        # httponly: JavaScript não lê o cookie, então um XSS não leva a sessão.
        httponly=True,
        # lax: o cookie acompanha navegação normal, mas não requisição vinda de
        # outro site — o que barra CSRF nas ações do painel de controle.
        samesite="lax",
        secure=settings.PAINEL_COOKIE_SEGURO,
        path="/",
    )
    return resposta


@router.get("/login.html")
def get_login_html():
    """O arquivo cru tem `{{ERRO}}` e `{{PROXIMO}}` sem preencher.

    Quem chegasse nele pelo `mount` de estáticos veria os marcadores na tela.
    Registrado aqui, antes do mount, para que /login.html caia sempre na rota
    que sabe montar a página.
    """
    return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/logout")
def post_logout():
    resposta = RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    resposta.delete_cookie(sessao.NOME_COOKIE, path="/")
    return resposta
