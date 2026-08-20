# =============================================================================
#  Captacao e Resgate - API (FastAPI + Uvicorn)
#  Build multi-stage: o estagio "builder" carrega compilador e headers para
#  montar as wheels (pandas/pyarrow/openpyxl trazem extensoes em C); o estagio
#  final recebe SO a virtualenv pronta. Resultado: imagem sem gcc, sem cache de
#  pip e sem headers de dev -- tipicamente ~55% menor que um build single-stage.
# =============================================================================

# -----------------------------------------------------------------------------
# Estagio 1: builder -- compila e instala as dependencias em uma venv isolada
# -----------------------------------------------------------------------------
FROM python:3.12-slim-bookworm AS builder

# build-essential so existe aqui; nao vai para a imagem final.
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

# A venv em /opt/venv e copiada inteira para o estagio final.
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

WORKDIR /build

# requirements.txt e copiado sozinho, ANTES do codigo: enquanto ele nao mudar,
# o Docker reaproveita esta camada e pula a reinstalacao a cada build.
COPY requirements.txt .

# --no-cache-dir: o cache do pip nao serve para nada dentro da imagem e pesa.
# O marcador `pywin32; sys_platform == "win32"` do requirements e ignorado
# automaticamente aqui (o container e Linux) -- nao precisa editar o arquivo.
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

# -----------------------------------------------------------------------------
# Estagio 2: runtime -- so o interpretador, a venv e o codigo da aplicacao
# -----------------------------------------------------------------------------
FROM python:3.12-slim-bookworm AS runtime

# PYTHONDONTWRITEBYTECODE: nao gera .pyc (o container e efemero).
# PYTHONUNBUFFERED: log sai na hora, sem ficar preso no buffer -- essencial
#   para `docker compose logs` mostrar o progresso da carga de dados.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH"

# Usuario sem privilegios: se a aplicacao for comprometida, o atacante nao
# herda root dentro do container.
RUN useradd --create-home --shell /usr/sbin/nologin appuser

COPY --from=builder /opt/venv /opt/venv

WORKDIR /app

# O codigo entra em /app/backend porque config.py resolve BASE_DIR como
# `Path(__file__).resolve().parents[2]` -- ou seja, dois niveis acima de
# app/config.py. Mantendo backend/ como subpasta, BASE_DIR vira /app e
# /app/data continua sendo o diretorio de dados esperado.
COPY --chown=appuser:appuser backend/ /app/backend/

# data/ e volume no compose, mas as pastas precisam existir com o dono certo
# antes de o processo (nao-root) tentar criar cache/ e inbox/.
RUN mkdir -p /app/data/cache /app/data/inbox /app/data/relatorios \
    && chown -R appuser:appuser /app/data

USER appuser
WORKDIR /app/backend

EXPOSE 8000

# Healthcheck bate no /health, que responde sem disparar carga de dados.
HEALTHCHECK --interval=30s --timeout=5s --start-period=90s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4).status == 200 else 1)"

# Sem --reload em producao: o watcher de arquivos custa CPU e RAM a toa.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
