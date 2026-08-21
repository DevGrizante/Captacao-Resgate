# Deploy em nuvem

Escrito para **Oracle Cloud**, mas nada aqui é específico dela além do nome do
registry. Trocar para AWS ou Azure é trocar os segredos e a URL da imagem.

---

## O que roda onde

```
                    Internet
                       │  443 (TLS)
                       ▼
              ┌─────────────────┐
              │ Caddy (no host) │  certificado automático
              └────────┬────────┘
                       │ 127.0.0.1:8080
                       ▼
              ┌─────────────────┐
              │  web (nginx)    │  painel estático, pré-comprimido
              └────────┬────────┘
                       │ /api → rede interna
                       ▼
              ┌─────────────────┐      ┌──────────────────┐
              │  api (uvicorn)  │─────▶│ volume: dados    │
              │  + agendador    │      │ cache/ inbox/    │
              └────────┬────────┘      └──────────────────┘
                       │
         ┌─────────────┼──────────────┬──────────────┐
         ▼             ▼              ▼              ▼
    dados.cvm     debentures      api.bcb        Graph/IMAP
     .gov.br       .com.br        .gov.br        (e-mail)
```

Nenhuma porta da API fica exposta: quem fala com a internet é o Caddy, e o
uvicorn escuta só na rede interna do Compose. Sem isso daria para dar a volta no
HTTPS e o cookie de sessão viajaria em texto claro.

## Dimensionamento

A primeira carga é o pico: baixa ~24 MB do CDA, ~7 meses de informe diário e o
registro de fundos, e monta os parquets. Depois disso o regime permanente é
leitura de disco.

| | Primeira carga | Regime |
|---|---|---|
| Tempo | 9–12 min | 3,8 s (carga fria de cache) |
| RAM | ~2 GB de pico | ~400 MB |
| Disco | — | ~35 MB de cache + 1,2 MB/dia de inbox |

**A `e2.micro` do free tier (1 GB) não aguenta a primeira carga.** Duas saídas:

- `VM.Standard.A1.Flex` com 2 OCPU / 12 GB — também é *always free* na Oracle, e
  é ARM (por isso o `deploy.yml` constrói para `linux/arm64` também);
- ou subir a `e2.micro` com swap e aceitar que a primeira carga demore.

O `docker-compose.prod.yml` põe teto de 900 MB no container da API justamente
para que, quando faltar memória, morra o container (que reinicia sozinho) e não
o `sshd` — o OOM killer do Linux escolhe a vítima, e ela costuma ser a errada.

---

## Passo a passo

### 1. Provisionar a máquina

O repositório já traz o que faz isso:

```bash
bash deploy/oracle/criar-e2micro.sh        # cria a VM e as regras de rede
# deploy/oracle/cloud-init-e2micro.yaml    # instala Docker e Caddy no boot
```

### 2. Preparar o host

```bash
ssh ubuntu@<IP>
mkdir -p ~/painel && cd ~/painel

# Só três arquivos precisam existir no servidor — o resto vem da imagem:
scp docker-compose.prod.yml       ubuntu@<IP>:~/painel/
scp backend/.env.example          ubuntu@<IP>:~/painel/backend/.env
scp deploy/Caddyfile              ubuntu@<IP>:/tmp/
```

Edite `backend/.env` e preencha, no mínimo:

```bash
PAINEL_SENHA=<a senha da mesa>
PAINEL_SEGREDO=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")
PAINEL_COOKIE_SEGURO=true
OUTLOOK_ENABLED=false
INGESTAO_TOKEN=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")
```

E o `.env` da raiz, que só diz de onde puxar a imagem:

```bash
cat > ~/painel/.env <<EOF
REGISTRY=gru.ocir.io
NAMESPACE=<seu-namespace>
TAG=latest
EOF
```

### 3. Configurar o Caddy

```bash
sudo cp /tmp/Caddyfile /etc/caddy/Caddyfile
sudo nano /etc/caddy/Caddyfile     # trocar painel.suaempresa.com
sudo systemctl reload caddy
```

O Caddy emite e renova o certificado sozinho, **mas só se o domínio já apontar
para o IP público**. Aponte o registro A e espere propagar antes de recarregar.

Um ajuste: o `Caddyfile` do repositório faz proxy para `127.0.0.1:8000`
(instalação sem Docker). Com o Compose, o alvo é `127.0.0.1:8080` — a porta que
o serviço `web` publica no loopback.

### 4. Configurar o GitHub

Em **Settings → Secrets and variables → Actions**:

| Secrets | Variables |
|---|---|
| `OCIR_USUARIO` — `<tenancy>/<usuário>` | `OCIR_REGIAO` — `gru.ocir.io` |
| `OCIR_TOKEN` — auth token do OCI | `OCIR_NAMESPACE` — seu namespace |
| `SSH_CHAVE` — chave privada de deploy | `SERVIDOR_HOST` — o domínio |
| | `SERVIDOR_USUARIO` — `ubuntu` |
| | `SERVIDOR_CAMINHO` — `/home/ubuntu/painel` |

Em **Settings → Environments**, crie `producao`. Se quiser aprovação humana antes
de cada deploy, é aqui que se liga "required reviewers".

### 5. Publicar

```bash
git tag v1.0.0
git push origin v1.0.0
```

O `deploy.yml` constrói as duas imagens para amd64 **e** arm64, publica no OCIR,
entra por SSH, faz `pull` + `up -d` e **confere o `/health`** antes de terminar
verde. Um deploy que sobe container quebrado falha o workflow em vez de passar
despercebido.

### Rollback

```bash
ssh ubuntu@<IP> 'cd ~/painel && TAG=v0.9.0 docker compose -f docker-compose.prod.yml up -d'
```

As imagens antigas continuam no registry e no disco (o `image prune -f` do
deploy só remove o que ficou órfão, nunca o que tem tag). É por isso que o
rollback é um comando e não um novo build.

---

## O que ficou de fora, e por quê

**Banco de dados.** O `docker-compose.yml` de desenvolvimento tem variáveis
`DB_*` desde antes; elas continuam inertes (`grep -r DATABASE_URL backend/app`
não devolve nada). A persistência é parquet + JSON em `data/`, e isso é uma
escolha, não uma dívida: o pipeline trabalha com `DataFrame`, e ler parquet do
disco local é uma ordem de grandeza mais rápido que montar o mesmo `DataFrame` a
partir de linhas de SQL. O banco entra quando houver a necessidade que só ele
resolve — usuário por usuário, histórico versionado, consulta ad hoc por
terceiros.

**Kubernetes.** São dois containers, num host, sem necessidade de escala
horizontal (o estado é um cache local em disco). K8s aqui seria três camadas de
abstração para gerenciar o que um `docker compose up -d` gerencia.

**Redis / Celery.** As duas tarefas periódicas rodam em `asyncio.to_thread`
dentro do próprio processo (`services/agendador.py`). Um broker seria infra e
custo mensal para duas tarefas que rodam a cada quinze minutos, sem necessidade
de garantia de entrega.

**Os `.bat` do Windows.** `Iniciar.bat`, `Coletar_e_Enviar.bat` e
`Enviar_Cache.bat` **não** foram removidos. Eles são o caminho de ingestão que
está em produção hoje, e apagá-los antes de a coleta por e-mail rodar por uma
semana quebraria o fluxo diário da mesa. Estão excluídos da imagem Docker
(`.dockerignore`), então não pesam em nuvem. O passo 6 de
[pipeline-email.md](pipeline-email.md) diz quando aposentá-los.
