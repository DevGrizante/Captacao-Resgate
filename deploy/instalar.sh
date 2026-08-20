#!/usr/bin/env bash
#
# Instala o painel numa máquina Linux recém-criada (testado em Ubuntu 24.04).
#
#     git clone git@github.com:DevGrizante/Captacao-Resgate.git ~/painel
#     cd ~/painel && bash deploy/instalar.sh
#
# O que ele faz:
#   1. instala python3-venv e git, se faltarem
#   2. monta o ambiente virtual e as dependências
#   3. cria o backend/.env a partir do exemplo, já com os valores de servidor
#   4. abre as portas 80 e 443 no iptables da imagem Oracle
#   5. instala e liga o serviço systemd
#
# O que ele NÃO faz, de propósito:
#   · não preenche PAINEL_SENHA nem INGESTAO_TOKEN — segredo não entra em
#     script; ele para e diz o que falta
#   · não configura o Caddy: isso depende de um domínio já apontado, e o
#     script não tem como saber qual é
#
# Rodar duas vezes não faz mal: cada passo confere antes de agir.
set -euo pipefail

RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
USUARIO="$(whoami)"

info()  { printf '\n\033[1;34m==>\033[0m %s\n' "$*"; }
aviso() { printf '\033[1;33m  !\033[0m %s\n' "$*"; }
erro()  { printf '\033[1;31m  x\033[0m %s\n' "$*" >&2; }

# ---------------------------------------------------------------------------
info "1/5  Pacotes do sistema"
# ---------------------------------------------------------------------------
FALTANDO=()
command -v git >/dev/null 2>&1 || FALTANDO+=(git)
python3 -c "import venv" >/dev/null 2>&1 || FALTANDO+=(python3-venv)

if [ ${#FALTANDO[@]} -gt 0 ]; then
    echo "  instalando: ${FALTANDO[*]}"
    sudo apt-get update -qq
    sudo apt-get install -y -qq "${FALTANDO[@]}"
else
    echo "  já tem tudo"
fi
echo "  python: $(python3 --version)"

# ---------------------------------------------------------------------------
info "2/5  Ambiente virtual e dependências"
# ---------------------------------------------------------------------------
cd "$RAIZ/backend"
if [ ! -x ".venv/bin/python" ]; then
    python3 -m venv .venv
    echo "  ambiente criado"
fi

# Reinstala só quando o requirements muda — o marcador guarda a cópia usada.
# Sem isto, cada execução do script gastaria minutos reinstalando o pandas.
if ! cmp -s requirements.txt .venv/requirements.lock 2>/dev/null; then
    echo "  instalando dependências (alguns minutos na primeira vez)..."
    .venv/bin/pip install --quiet --upgrade pip
    # O pywin32 do requirements tem marcador `sys_platform == "win32"`: o pip
    # o ignora sozinho aqui. pandas, numpy e pyarrow têm pacotes prontos para
    # ARM, então nada é compilado do zero.
    .venv/bin/pip install --quiet -r requirements.txt
    cp requirements.txt .venv/requirements.lock
    echo "  pronto"
else
    echo "  dependências já instaladas"
fi

# ---------------------------------------------------------------------------
info "3/5  Configuração (backend/.env)"
# ---------------------------------------------------------------------------
if [ ! -f .env ]; then
    cp .env.example .env
    # Valores que são sempre assim em servidor. O resto fica com o padrão.
    sed -i 's/^OUTLOOK_ENABLED=.*/OUTLOOK_ENABLED=false/' .env
    sed -i 's/^PAINEL_COOKIE_SEGURO=.*/PAINEL_COOKIE_SEGURO=true/' .env
    # O segredo do cookie PRECISA ser único por instalação: reaproveitar o de
    # outra máquina faria um cookie válido lá valer aqui.
    SEGREDO="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
    sed -i "s|^PAINEL_SEGREDO=.*|PAINEL_SEGREDO=$SEGREDO|" .env
    echo "  .env criado, com PAINEL_SEGREDO sorteado só para esta máquina"

    # --- Máquina pequena: proibir a reconstrução do cache -------------------
    # Medido: ler os parquet prontos custa 366 MB de pico; RECONSTRUIR o cache
    # a partir da CVM custa 2.078 MB. Numa VM de 1 GB o segundo caminho não
    # cabe — o kernel mata o processo no meio, e o serviço morre sem deixar
    # erro na tela, só um "Killed" no journal.
    #
    # Os TTLs abaixo são o que decide qual dos dois caminhos o app toma: com
    # eles altos, ele nunca resolve baixar nada sozinho e se limita a ler o
    # que estiver em data/cache/. Quem atualiza o cache passa a ser você, de
    # fora (deploy/enviar_cache — veja o README).
    RAM_MB=$(awk '/MemTotal/{printf "%d", $2/1024}' /proc/meminfo)
    if [ "$RAM_MB" -lt 2048 ]; then
        for VAR in CVM_CADASTRO_TTL_HORAS CVM_INFORME_TTL_HORAS \
                   CVM_DOCUMENTOS_TTL_HORAS CDA_TTL_HORAS SND_TTL_HORAS; do
            sed -i "s|^${VAR}=.*|${VAR}=87600|" .env
        done
        echo "  máquina com ${RAM_MB} MB: TTLs travados em 10 anos"
        echo "     -> o servidor NUNCA vai reconstruir o cache sozinho"
        echo "     -> envie data/cache/ da sua máquina (deploy/enviar_cache)"
    fi
else
    echo "  .env já existe — não vou mexer"
fi

# ---------------------------------------------------------------------------
# Aviso de memória, mesmo quando o .env já existia
# ---------------------------------------------------------------------------
RAM_MB=$(awk '/MemTotal/{printf "%d", $2/1024}' /proc/meminfo)
SWAP_MB=$(awk '/SwapTotal/{printf "%d", $2/1024}' /proc/meminfo)
if [ "$RAM_MB" -lt 2048 ] && [ "$SWAP_MB" -lt 1024 ]; then
    aviso "Esta máquina tem ${RAM_MB} MB de RAM e ${SWAP_MB} MB de swap."
    aviso "  Sem swap, qualquer reconstrução de cache mata o serviço. Crie:"
    aviso "    sudo fallocate -l 4G /swapfile && sudo chmod 600 /swapfile"
    aviso "    sudo mkswap /swapfile && sudo swapon /swapfile"
    aviso "    echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab"
fi

PENDENTES=()
grep -q '^PAINEL_SENHA=.\+'   .env || PENDENTES+=("PAINEL_SENHA")
grep -q '^INGESTAO_TOKEN=.\+' .env || PENDENTES+=("INGESTAO_TOKEN")

# ---------------------------------------------------------------------------
info "4/5  Portas 80 e 443 no firewall da máquina"
# ---------------------------------------------------------------------------
# A Security List da Oracle é só metade: a imagem Ubuntu vem com regras de
# iptables que barram tudo além do SSH. Liberar só na console dá a impressão
# exata de servidor no ar que não responde.
if command -v iptables >/dev/null 2>&1; then
    for PORTA in 80 443; do
        if sudo iptables -C INPUT -p tcp --dport "$PORTA" -j ACCEPT 2>/dev/null; then
            echo "  porta $PORTA já liberada"
        else
            sudo iptables -I INPUT -p tcp --dport "$PORTA" -j ACCEPT
            echo "  porta $PORTA liberada"
        fi
    done
    # Sem salvar, as regras somem no próximo reinício e o painel "cai sozinho"
    # semanas depois, sem relação aparente com nada.
    if command -v netfilter-persistent >/dev/null 2>&1; then
        sudo netfilter-persistent save >/dev/null
        echo "  regras salvas (sobrevivem ao reinício)"
    else
        aviso "netfilter-persistent não encontrado: as regras se perdem ao reiniciar."
        aviso "  sudo apt install iptables-persistent"
    fi
else
    echo "  sem iptables nesta máquina — nada a fazer"
fi

# ---------------------------------------------------------------------------
info "5/5  Serviço systemd"
# ---------------------------------------------------------------------------
UNIDADE=/etc/systemd/system/painel.service
# O arquivo do repositório assume /home/ubuntu/painel e o usuário ubuntu.
# Aqui ele é ajustado para onde o projeto realmente está.
sudo sed -e "s|/home/ubuntu/painel|$RAIZ|g" \
         -e "s|^User=.*|User=$USUARIO|" \
         -e "s|^Group=.*|Group=$USUARIO|" \
         "$RAIZ/deploy/painel.service" | sudo tee "$UNIDADE" >/dev/null
sudo systemctl daemon-reload
sudo systemctl enable painel >/dev/null 2>&1
echo "  unidade instalada apontando para $RAIZ"

# ---------------------------------------------------------------------------
if [ ${#PENDENTES[@]} -gt 0 ]; then
    erro "FALTA PREENCHER: ${PENDENTES[*]}"
    echo
    echo "  Edite  $RAIZ/backend/.env  e depois:"
    echo "      sudo systemctl restart painel"
    echo
    echo "  PAINEL_SENHA vazio deixa o painel SEM SENHA."
    echo "  INGESTAO_TOKEN vazio impede o coletor de enviar a planilha."
    exit 1
fi

sudo systemctl restart painel
sleep 3

if systemctl is-active --quiet painel; then
    info "Pronto"
    echo "  serviço no ar:  systemctl status painel"
    echo "  log ao vivo:    sudo journalctl -u painel -f"
    echo
    echo "  Falta o HTTPS. Aponte o domínio para o IP desta máquina e:"
    echo "      sudo cp $RAIZ/deploy/Caddyfile /etc/caddy/Caddyfile"
    echo "      sudo nano /etc/caddy/Caddyfile     # trocar o domínio"
    echo "      sudo systemctl reload caddy"
else
    erro "O serviço não subiu. O motivo está em:"
    echo "      sudo journalctl -u painel -n 50"
    exit 1
fi
