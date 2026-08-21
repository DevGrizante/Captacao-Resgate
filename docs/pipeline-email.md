# Pipeline de ingestão do relatório da Quantum

Como sair de "o anexo chega no Outlook do Raphael às 7h" para "o dado do dia
existe como recurso de rede, sem depender de máquina nem de pessoa".

---

## Onde estamos e por que isso incomoda

Hoje há dois caminhos, e os dois passam por um notebook Windows:

```
  ┌──────────────┐   COM/pywin32   ┌─────────────┐
  │ Outlook local│ ───────────────▶│ data/inbox/ │   caminho 1 (hoje, local)
  └──────────────┘                 └─────────────┘

  ┌──────────────┐  coletar_       ┌─────────────┐
  │ Outlook local│  vinculado.py   │  POST       │   caminho 2 (hoje, servidor)
  │  (notebook)  │ ───────────────▶│  /api/inbox │
  └──────────────┘     HTTPS       └─────────────┘
```

O caminho 2 já tira o servidor do Windows. O que ele **não** tira é o notebook
do meio. E o modo de falha é o pior que existe: se a máquina estiver desligada,
`outlook_inbox.sincronizar()` cai em silêncio para o arquivo mais recente que já
tem, e **o painel serve a planilha da véspera sem nenhum erro na tela**.

## Para onde vamos

```
  ┌────────────────┐   Graph / IMAP   ┌──────────────────┐
  │ Caixa na nuvem │ ◀────────────────│  API (container) │
  │  (M365/IMAP)   │                  │  tarefa de fundo │
  └────────────────┘                  └────────┬─────────┘
                                               │ ingestao.receber()
                                               ▼
                                      ┌──────────────────┐
                                      │  data/inbox/     │
                                      │  + pipeline      │
                                      └──────────────────┘
```

Um componente a menos, e o servidor passa a ser o dono do próprio dado.

---

## Passo a passo

### Passo 1 — Criar uma caixa de serviço (não use uma caixa de pessoa)

Peça ao TI uma caixa compartilhada, algo como `painel-credito@bgcg.com`, e
configure uma **regra de encaminhamento** no Outlook de quem hoje recebe o
relatório, mandando cópia para ela.

Por que uma caixa própria, e não a caixa de alguém:

- quem sai da empresa não leva o pipeline junto;
- a caixa pode ser lida por uma aplicação sem que isso signifique ler o e-mail
  pessoal de ninguém;
- a política de acesso do passo 3 fica trivial de escrever e de auditar.

### Passo 2 — Registrar a aplicação no Entra ID (Microsoft 365)

No portal do Entra ID (antigo Azure AD):

1. **App registrations → New registration**. Nome: `painel-captacao-resgate`.
   Guarde o *Application (client) ID* e o *Directory (tenant) ID*.
2. **Certificates & secrets → New client secret**. Guarde o **valor** — ele só
   aparece uma vez.
3. **API permissions → Microsoft Graph → Application permissions → `Mail.Read`**,
   e depois **Grant admin consent**.

> Permissão de *aplicação*, não *delegada*. Delegada exige um usuário
> interativo fazendo login, o que um serviço que roda às 7h da manhã não tem.

### Passo 3 — Restringir a permissão a UMA caixa

Este passo não é opcional. Por padrão, `Mail.Read` de aplicação enxerga **todas
as caixas do tenant** — um vazamento do client secret viraria acesso de leitura
ao e-mail da empresa inteira.

No Exchange Online PowerShell:

```powershell
New-ApplicationAccessPolicy `
  -AppId <CLIENT_ID> `
  -PolicyScopeGroupId painel-credito@bgcg.com `
  -AccessRight RestrictAccess `
  -Description "Painel Captacao e Resgate: so a caixa do relatorio"

# Confira que a política pegou:
Test-ApplicationAccessPolicy -Identity outra.pessoa@bgcg.com -AppId <CLIENT_ID>
# deve responder: Denied
```

### Passo 4 — Configurar o servidor

No `backend/.env` do servidor:

```bash
EMAIL_MODO=graph
EMAIL_INTERVALO_MIN=15
GRAPH_TENANT_ID=...
GRAPH_CLIENT_ID=...
GRAPH_CLIENT_SECRET=...
GRAPH_CAIXA=painel-credito@bgcg.com

# Reaproveitados do caminho antigo — mesmo critério, um lugar só:
OUTLOOK_PASTA=Quantum          # vazio = a caixa inteira
OUTLOOK_ASSUNTO=Captação e resgate
OUTLOOK_REMETENTE=             # vazio = qualquer remetente
```

**Não é Microsoft 365?** Troque o bloco por `EMAIL_MODO=imap` e preencha
`IMAP_HOST`, `IMAP_PORT`, `IMAP_USUARIO` e `IMAP_SENHA` (ou
`IMAP_OAUTH_TOKEN`). O resto do pipeline é idêntico.

### Passo 5 — Testar antes de confiar

```bash
# Força uma coleta agora, sem esperar o ciclo de 15 min:
curl -X POST https://painel.suaempresa.com/api/admin/coletar-email \
     -b cookies.txt

# Respostas possíveis:
#   {"status":"novo","arquivo":"vinculado_20260820_0727.xlsx","recalculado":true}
#   {"status":"ja_tinha", ...}
#   {"status":"sem_novidade", ...}
```

E acompanhe o log:

```bash
docker compose -f docker-compose.prod.yml logs -f api | grep -E "email_inbox|agendador"
```

### Passo 6 — Aposentar o coletor Windows

Só depois de **uma semana** de coleta automática funcionando:

1. desative a tarefa no Agendador de Tarefas do Windows;
2. mantenha `INGESTAO_TOKEN` preenchido por mais um mês — o `POST /api/inbox`
   vira o plano B manual para o dia em que a caixa estiver fora do ar;
3. só então apague `Coletar_e_Enviar.bat` e `backend/scripts/coletar_vinculado.py`.

---

## O que já está garantido, e por quem

O módulo novo (`services/email_inbox.py`) faz **só** a parte do e-mail. Toda a
regra de o que pode entrar continua em `services/ingestao.py`, que já existia e
já era boa:

| Garantia | Onde vive |
|---|---|
| O nome do arquivo nunca vem de fora | `ingestao._nome_do_arquivo` |
| O conteúdo é conferido de verdade (é um `.xlsx`?) | `ingestao._conferir_planilha` |
| O mesmo arquivo duas vezes não vira dois arquivos | `ingestao._procurar_igual` (SHA-256) |
| A pasta não cresce sem limite | `ingestao._podar` |
| A escrita é atômica (`.parcial` + rename) | `ingestao.receber` |
| A data do arquivo é a do **e-mail**, não a do robô | `ingestao.receber` (`os.utime`) |

Reimplementar isso no módulo de e-mail teria criado um segundo conjunto de
regras para o mesmo arquivo — e a hora em que os dois discordassem seria a hora
em que ninguém saberia qual está certo.

## Formato final no banco: por que continua sendo o `.xlsx` cru + parquet

A pergunta original era como armazenar "para facilitar ao máximo o consumo pelo
código atual". A resposta é que **o formato certo já é o que está lá**:

1. **`data/inbox/vinculado_AAAAMMDD_HHMM.xlsx`** — o original, intocado. É a
   fonte de auditoria: quando alguém questionar um número, é para cá que se
   volta. O nome carrega a data, então "o mais recente" é ordem alfabética.

2. **`data/cache/*.parquet`** — o derivado, colunar e comprimido. É o que o
   pipeline lê de verdade, e é por isso que uma carga quente leva 3,8 s.

Migrar isso para tabelas em MySQL **pioraria** o consumo pelo código atual: o
pipeline trabalha com `DataFrame` do pandas, e ler parquet do disco local é uma
ordem de grandeza mais rápido que montar o mesmo `DataFrame` a partir de linhas
de SQL pela rede. O banco entra quando houver a necessidade que ele resolve —
usuário por usuário, histórico versionado, consulta ad hoc por terceiros — e não
antes.

O que muda com este pipeline é **quem coloca o arquivo lá**, não o formato.
