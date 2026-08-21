"""
Entrypoint da API — Captação e Resgate · Crédito Privado.

Rodar (dentro de backend/):
    uvicorn app.main:app --reload --port 8000

Painel:          http://127.0.0.1:8000/
Docs interativas: http://127.0.0.1:8000/docs

>>> ESTE PROCESSO SERVE TAMBÉM O PAINEL (19/08/2026)

Antes eram dois: uvicorn na 8000 e `python -m http.server` na 5500 para os
arquivos estáticos. O http.server responde em HTTP/1.0 — ou seja, fecha a
conexão TCP a cada arquivo, não manda ETag nem Cache-Control e não comprime
nada. Com o painel montado aqui:

    · uma origem só, então nenhuma requisição gasta preflight de CORS;
    · HTTP/1.1 com keep-alive: uma conexão serve a página inteira;
    · ETag/304 e Cache-Control nos assets, que são versionados por ?v=;
    · gzip nas respostas JSON, que são o grosso do tráfego do painel;
    · um processo em vez de dois.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from urllib.parse import quote

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.responses import JSONResponse, RedirectResponse, Response

from app.config import BASE_DIR, settings
from app.routers import admin, api, auth, ingestao
from app.services import agendador, email_inbox, parametros, sessao

# Os parâmetros gravados pelo painel de controle precisam valer ANTES de
# qualquer classificação — e o pipeline classifica na primeira requisição.
# Aplicar aqui, no import do módulo, garante que não existe uma janela em que
# a API responde com o corte do .env enquanto o painel mostra outro.
logger = logging.getLogger("api")

parametros.carregar_do_disco()

@asynccontextmanager
async def ciclo_de_vida(_: FastAPI):
    """Sobe e desce as tarefas periódicas junto com o processo.

    As duas tarefas são de FUNDO por exigência de desempenho: nenhuma delas
    pode acontecer enquanto alguém espera uma resposta HTTP. Ver
    `services/agendador.py` para o porquê de não haver cron nem broker aqui.

    Registrar com intervalo 0 é o mesmo que não registrar, e é o padrão — quem
    não configurar nada continua com exatamente o comportamento de hoje.
    """
    if email_inbox.habilitado():
        agendador.registrar(
            "coleta-email", email_inbox.sincronizar, settings.EMAIL_INTERVALO_MIN
        )
    else:
        logger.info("Ingestão por e-mail desligada (EMAIL_MODO=%s).", settings.EMAIL_MODO)

    try:
        yield
    finally:
        await agendador.parar()


app = FastAPI(
    title="Captação e Resgate · Crédito Privado",
    description="Consolidação de captação/resgate de fundos de crédito privado, "
                "classificados em LF / Incentivada / Tradicional / Misto.",
    version="0.3.0",
    lifespan=ciclo_de_vida,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# O painel abre pedindo /api/dashboard (94 KB), /api/tesourarias (36 KB) e
# /api/carteira-bancaria (69 KB). É JSON: comprime perto de 90%. O corte de
# 1000 bytes evita gastar CPU em resposta pequena, onde o cabeçalho do gzip
# custaria mais que o que ele economiza.
app.add_middleware(GZipMiddleware, minimum_size=1000)

# =============================================================================
#  O portão
# =============================================================================

# Aberto sem provar nada. Cada item aqui é superfície exposta, então a lista é
# curta de propósito:
#
#   /login, /logout  o próprio portão — trancá-los seria um laço.
#   /health          precisa responder antes do login para o systemd, o
#                    Iniciar.bat e qualquer monitoramento saberem se o serviço
#                    subiu. Devolve menos coisa para quem não entrou (ver abaixo).
#   /favicon.ico     o navegador pede sozinho; negar só polui o log.
_ABERTOS = {"/login", "/logout", "/health", "/favicon.ico"}

# A ingestão tem a PRÓPRIA autenticação, por token no cabeçalho. Deixar o
# cookie mandar aqui obrigaria o coletor a fazer login como se fosse gente —
# e o router já recusa quem chega sem o token certo.
_COM_TOKEN_PROPRIO = ("/api/inbox",)


@app.middleware("http")
async def exigir_sessao(request: Request, call_next):
    """Ninguém passa sem cookie válido, exceto o que está listado acima.

    Fica em middleware, e não em dependência de cada rota, porque o painel é
    servido por um `mount` de arquivos estáticos: uma dependência protegeria os
    endpoints e deixaria o `index.html` — com toda a estrutura da tela — aberto
    a quem chegasse.

    Quem pede página é REDIRECIONADO para o login; quem pede dado leva 401 em
    JSON. A diferença importa: redirecionar um `fetch` faria o JavaScript
    receber o HTML da tela de login e quebrar tentando lê-lo como dados.
    """
    caminho = request.url.path

    if (
        not sessao.protegido()
        or caminho in _ABERTOS
        or caminho.startswith(_COM_TOKEN_PROPRIO)
        or sessao.cookie_valido(request.cookies.get(sessao.NOME_COOKIE))
    ):
        return await call_next(request)

    quer_html = "text/html" in request.headers.get("accept", "")
    if quer_html:
        destino = request.url.path
        if request.url.query:
            destino += "?" + request.url.query
        return RedirectResponse(
            f"/login?proximo={quote(destino, safe='/?=&')}",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    return JSONResponse(
        status_code=status.HTTP_401_UNAUTHORIZED,
        content={"detail": "Sessão expirada ou ausente. Entre de novo."},
    )


app.include_router(auth.router)
app.include_router(api.router)
app.include_router(admin.router)
# Ingestão da planilha pela rede. Responde 503 enquanto INGESTAO_TOKEN estiver
# vazio, então incluir aqui não abre nada por si só.
app.include_router(ingestao.router)


if not sessao.protegido():
    logger.warning(
        "PAINEL_SENHA vazio: o painel está SEM SENHA. Aceitável em 127.0.0.1; "
        "num servidor alcançável, preencha antes de divulgar o endereço."
    )


@app.exception_handler(FileNotFoundError)
def sem_planilha(request: Request, exc: FileNotFoundError):
    """Fonte sem arquivo não é bug: é estado, e precisa dizer o próprio nome.

    Sem isto, um servidor recém-subido responde "Internal Server Error" na
    primeira tela — o que manda quem instalou procurar defeito no código,
    quando o que falta é a planilha do dia. O texto que o conector levanta já
    explica o que fazer; o que faltava era ele chegar até a tela, com um status
    que diz "ainda não, tente depois" em vez de "quebrei".

    503 e não 404: o recurso existe e vai passar a funcionar assim que a
    planilha chegar. `Retry-After` diz de quanto em quanto vale insistir.
    """
    logger.warning("Requisição sem planilha disponível: %s", exc)
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content={"detail": str(exc)},
        headers={"Retry-After": "300"},
    )


@app.get("/health")
def health(request: Request):
    """Vivo? E lendo o quê? Não dispara carga — só reporta o que já está em pé.

    Fica aberto porque o systemd, o Iniciar.bat e qualquer monitoramento
    precisam saber se o serviço subiu ANTES de haver sessão. Mas aberto não
    quer dizer falante: para quem não entrou, responde só que está de pé e se
    já existe planilha. O nome do arquivo carrega a data do relatório da mesa,
    e o corte de classificação é parâmetro de negócio — nenhum dos dois precisa
    ser lido por quem passa na porta.
    """
    from app.services import outlook_inbox

    ultimo = outlook_inbox.arquivo_mais_recente()

    if sessao.protegido() and not sessao.cookie_valido(
        request.cookies.get(sessao.NOME_COOKIE)
    ):
        return {"status": "ok", "tem_planilha": ultimo is not None}

    return {
        "status": "ok",
        "data_source": settings.DATA_SOURCE,
        "quantum_enabled": settings.QUANTUM_ENABLED,
        "outlook_enabled": settings.OUTLOOK_ENABLED,
        "ultimo_arquivo": ultimo.name if ultimo else None,
        # O corte vigente entra no health de propósito: é o parâmetro que muda
        # o que a tela mostra, e o primeiro a se conferir quando alguém
        # estranha a classificação.
        "corte_classificacao_pct": round(settings.THRESHOLD_MAJORITARIO * 100, 2),
        "hedge_dap_minimo_pct": round(settings.HEDGE_DAP_MINIMO * 100, 2),
    }


# =============================================================================
#  O painel, servido pelo mesmo processo
# =============================================================================

FRONTEND_DIR = BASE_DIR / "frontend"

# Cache longo para o que é versionado, nenhum para o que não é.
#
# Todo asset é referenciado com ?v=NN nos .html — subir o número troca a URL e
# o navegador busca de novo. Por isso o JS/CSS pode ficar um ano em cache sem
# risco de o usuário ver versão velha, e `immutable` faz o navegador nem
# revalidar (some o 304 e a viagem de ida e volta que ele custa).
#
# O .html é a exceção: é ele que carrega o ?v= novo. Se ficasse em cache, subir
# a versão não adiantaria nada — o navegador continuaria lendo o HTML antigo,
# apontando para o JS antigo. `no-cache` aqui não quer dizer "não guarde", e sim
# "guarde, mas revalide sempre" — o servidor responde 304 e o custo é só o
# cabeçalho.
_UM_ANO = 60 * 60 * 24 * 365


class PainelEstatico(StaticFiles):
    """StaticFiles com política de cache — o de fábrica não manda Cache-Control."""

    def file_response(self, full_path, stat_result, scope, status_code=200) -> Response:
        resposta = super().file_response(full_path, stat_result, scope, status_code)
        caminho = str(full_path).lower()
        if caminho.endswith(".html"):
            resposta.headers["Cache-Control"] = "no-cache"
        elif caminho.endswith(("config.js",)):
            # config.js é gerado a cada `Iniciar.bat` com a porta do dia; não
            # tem ?v= que mude junto. Cachear isso é como fixar a porta velha.
            resposta.headers["Cache-Control"] = "no-cache"
        elif caminho.endswith((".js", ".css", ".svg", ".woff", ".woff2", ".png", ".ico")):
            resposta.headers["Cache-Control"] = f"public, max-age={_UM_ANO}, immutable"
        return resposta


if FRONTEND_DIR.is_dir():
    # Montado por ÚLTIMO, e só depois dos routers: um mount em "/" casa com
    # qualquer caminho, então ele precisa ser a última alternativa. /api,
    # /health e /docs continuam sendo resolvidos antes de chegar aqui.
    #
    # html=True faz "/" servir index.html e "/fundos.html" funcionar direto.
    app.mount("/", PainelEstatico(directory=FRONTEND_DIR, html=True), name="painel")
