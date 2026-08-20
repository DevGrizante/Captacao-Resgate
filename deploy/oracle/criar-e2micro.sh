#!/usr/bin/env bash
#
# Cria uma instancia VM.Standard.E2.1.Micro no Always Free da Oracle.
#
# RODAR NO CLOUD SHELL da Oracle (o icone de terminal no topo do console),
# nao na sua maquina - la o CLI ja vem autenticado.
#
#     bash criar-e2micro.sh captacao-app
#     bash criar-e2micro.sh cvm-app
#
# O nome e o unico argumento. Sem ele, usa "captacao-app".
#
# POR QUE ESTE SCRIPT E DIFERENTE DO DO A1.FLEX
#
#   1. NAO passa --shape-config. O E2.1.Micro e um shape FIXO: 1 OCPU
#      burstavel e 1 GB, sem opcao. Mandar shape-config faz o launch falhar
#      com "shape config not supported for this shape", erro que parece de
#      permissao e nao e.
#
#   2. Percorre TODAS as availability domains, e nao so a primeira. Capacidade
#      e por AD; parar na primeira e desistir cedo demais.
#
#   3. A imagem e filtrada pelo shape, o que garante x86-64. O E2 e AMD, nao
#      ARM - as mesmas rodas de pandas/pyarrow que voce ja usa no Windows
#      valem aqui, sem nada compilado.
#
#   4. Anexa o cloud-init que cria SWAP no primeiro boot. Numa maquina de
#      1 GB isso nao e refinamento: e o que separa "lento" de "o kernel matou
#      o processo".
set -uo pipefail

NOME="${1:-captacao-app}"
SHAPE="VM.Standard.E2.1.Micro"
BOOT_GB=50
ESPERA_S=60

# Ajuste se a sua subnet for outra. Este e o OCID que voce ja usou no A1.
SUBNET_ID="${SUBNET_ID:-ocid1.subnet.oc1.sa-saopaulo-1.aaaaaaaa7x7jc3ewp3fbayeup4utgq5okntisnj67o63agst5beyw7lus4wq}"
CHAVE_PUB="${CHAVE_PUB:-$HOME/oracle_painel.pub}"
CLOUD_INIT="${CLOUD_INIT:-$(dirname "$0")/cloud-init-e2micro.yaml}"

# ---------------------------------------------------------------------------
if [ ! -f "$CHAVE_PUB" ]; then
  echo "ERRO: nao encontrei a chave publica em $CHAVE_PUB" >&2
  echo "      Envie-a para o Cloud Shell ou aponte com CHAVE_PUB=/caminho/da/chave.pub" >&2
  exit 1
fi

USER_DATA_ARG=()
if [ -f "$CLOUD_INIT" ]; then
  USER_DATA_ARG=(--user-data-file "$CLOUD_INIT")
  echo "cloud-init: $CLOUD_INIT (cria swap no primeiro boot)"
else
  echo "AVISO: $CLOUD_INIT nao encontrado - a VM subira SEM swap."
  echo "       Em 1 GB, sem swap, uma reconstrucao de cache mata o servico."
fi

# --- Compartment ------------------------------------------------------------
COMPARTMENT_ID=$(awk -F'=' '/^tenancy=/{print $2; exit}' /etc/oci/config 2>/dev/null | tr -d '[:space:]')
if [ -z "$COMPARTMENT_ID" ]; then
  COMPARTMENT_ID=$(oci iam user list --query 'data[0]."compartment-id"' --raw-output 2>/dev/null)
fi
if [ -z "$COMPARTMENT_ID" ] || [ "$COMPARTMENT_ID" == "None" ]; then
  echo "ERRO: nao consegui descobrir o compartment/tenancy." >&2
  exit 1
fi
echo "Compartment: $COMPARTMENT_ID"

# --- Availability domains ---------------------------------------------------
# Com retry: a primeira chamada do CLI numa sessao nova do Cloud Shell as vezes
# falha com o token ainda "esquentando", e seguir com a lista vazia quebraria o
# launch mais adiante com uma mensagem que nao explica nada.
ADS=""
for i in 1 2 3 4 5; do
  ADS=$(oci iam availability-domain list --compartment-id "$COMPARTMENT_ID" \
        --query 'data[].name' --raw-output 2>/dev/null | tr -d '[]",' | tr -s ' \n' '\n' | grep -v '^$')
  [ -n "$ADS" ] && break
  echo "  (tentativa $i de listar availability domains falhou; nova em 5s)"
  sleep 5
done
if [ -z "$ADS" ]; then
  echo "ERRO: nao consegui listar as availability domains." >&2
  exit 1
fi
echo "Availability domains:"; echo "$ADS" | sed 's/^/  - /'

# --- Imagem -----------------------------------------------------------------
IMAGE_ID=$(oci compute image list --compartment-id "$COMPARTMENT_ID" \
  --operating-system "Canonical Ubuntu" \
  --operating-system-version "24.04" \
  --shape "$SHAPE" \
  --sort-by TIMECREATED --sort-order DESC \
  --query 'data[0].id' --raw-output 2>/dev/null)
if [ -z "$IMAGE_ID" ] || [ "$IMAGE_ID" == "None" ]; then
  echo "ERRO: nao achei imagem Ubuntu 24.04 compativel com $SHAPE." >&2
  exit 1
fi
echo "Imagem: $IMAGE_ID"

# --- Ja existe? -------------------------------------------------------------
EXISTENTE=$(oci compute instance list --compartment-id "$COMPARTMENT_ID" \
  --display-name "$NOME" --lifecycle-state RUNNING \
  --query 'data[0].id' --raw-output 2>/dev/null || true)
if [ -n "$EXISTENTE" ] && [ "$EXISTENTE" != "None" ]; then
  echo
  echo "Ja existe uma instancia '$NOME' rodando: $EXISTENTE"
  IP=$(oci compute instance list-vnics --instance-id "$EXISTENTE" \
       --query 'data[0]."public-ip"' --raw-output 2>/dev/null)
  echo "IP publico: $IP"
  exit 0
fi

# --- Criar ------------------------------------------------------------------
TENTATIVA=0
while true; do
  TENTATIVA=$((TENTATIVA + 1))
  while IFS= read -r AD; do
    [ -z "$AD" ] && continue
    echo "[$(date '+%H:%M:%S')] tentativa $TENTATIVA em $AD ..."

    SAIDA=$(oci compute instance launch \
      --compartment-id "$COMPARTMENT_ID" \
      --availability-domain "$AD" \
      --shape "$SHAPE" \
      --subnet-id "$SUBNET_ID" \
      --image-id "$IMAGE_ID" \
      --display-name "$NOME" \
      --boot-volume-size-in-gbs "$BOOT_GB" \
      --assign-public-ip true \
      --ssh-authorized-keys-file "$CHAVE_PUB" \
      "${USER_DATA_ARG[@]}" \
      --wait-for-state RUNNING \
      --max-wait-seconds 300 2>&1)
    CODIGO=$?

    if [ $CODIGO -eq 0 ]; then
      ID=$(echo "$SAIDA" | jq -r '.data.id')
      IP=$(oci compute instance list-vnics --instance-id "$ID" \
           --query 'data[0]."public-ip"' --raw-output)
      echo
      echo "=================================================="
      echo " Instancia criada: $NOME"
      echo " OCID ......: $ID"
      echo " IP publico : $IP"
      echo
      echo " Conecte do PowerShell da sua maquina:"
      echo "   ssh -i \$env:USERPROFILE\\.ssh\\oracle_painel ubuntu@$IP"
      echo
      echo " O cloud-init cria o swap no primeiro boot. Confira com:"
      echo "   free -h        # deve mostrar 4,0Gi de Swap"
      echo "=================================================="
      exit 0
    fi

    if echo "$SAIDA" | grep -qi "capacity"; then
      echo "  sem capacidade nesta AD"
      continue
    fi

    echo
    echo "Erro que nao e de capacidade - parando para voce ler:"
    echo "$SAIDA"
    exit 1
  done <<< "$ADS"

  echo "  nenhuma AD com capacidade; nova rodada em ${ESPERA_S}s"
  sleep "$ESPERA_S"
done
