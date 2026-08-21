# =============================================================================
#  Captacao e Resgate - Frontend (HTML/CSS/JS estatico)
#  O front nao tem etapa de build (nao ha bundler): o "estagio 1" existe apenas
#  para preparar os arquivos -- gerar o config.js e descartar o que nao deve ser
#  publicado -- sem sujar a imagem final com ferramentas.
#  Imagem final: nginx:alpine, ~50 MB, servindo arquivos ja comprimidos.
# =============================================================================

# -----------------------------------------------------------------------------
# Estagio 1: preparacao dos assets
# -----------------------------------------------------------------------------
FROM alpine:3.20 AS assets

WORKDIR /src
COPY frontend/ ./

# config.js e gerado (o Iniciar.bat faz isso no Windows, e ele esta no
# .gitignore). Dentro do Docker o nginx faz proxy de /api para a API, entao o
# front fala com a propria origem -- API_BASE vazio. Isso elimina o CORS e uma
# resolucao de DNS a cada chamada.
RUN printf '%s\n' \
    '// Gerado no build da imagem Docker.' \
    '// Vazio = mesma origem: o nginx faz proxy de /api para o container da API.' \
    'window.API_BASE = "";' \
    > js/config.js \
    # Pre-compressao: o gzip_static do nginx entrega o .gz pronto, sem gastar
    # CPU comprimindo a cada requisicao. app.js cai de ~48 KB para ~11 KB.
    && find . -type f \( -name '*.js' -o -name '*.css' -o -name '*.html' \) \
        -exec gzip -9 -k {} \;

# -----------------------------------------------------------------------------
# Estagio 2: runtime -- nginx alpine
# -----------------------------------------------------------------------------
FROM nginx:1.27-alpine AS runtime

# Remove a pagina padrao do nginx e a configuracao de exemplo.
RUN rm -rf /usr/share/nginx/html/* /etc/nginx/conf.d/default.conf

COPY --from=assets /src /usr/share/nginx/html
COPY deploy/nginx.conf /etc/nginx/conf.d/app.conf

# nginx:alpine ja roda os workers como usuario `nginx`; o master precisa de
# root apenas para abrir a porta 80 dentro do container.
EXPOSE 80

HEALTHCHECK --interval=30s --timeout=4s --start-period=10s --retries=3 \
    CMD wget -q --spider http://127.0.0.1/ || exit 1

CMD ["nginx", "-g", "daemon off;"]
