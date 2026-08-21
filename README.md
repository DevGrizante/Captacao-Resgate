# Captação e Resgate · Crédito Privado

Dashboard de captação/resgate de fundos de **crédito privado**, consolidado por
gestora — feito para mesa de crédito: além do fluxo, responde "quanto essa
gestora tem, quanto cobra, em quanto tempo devolve o dinheiro, com quanto meu
cliente entra e quem já compra dela".

Substitui o relatório manual por e-mail + PBI por um site com link fixo que
atualiza com um F5.

## De onde vem cada número

O fluxo vem da planilha do Quantum que chega por e-mail. Todo o resto vem de
casar o **CNPJ do fundo** com bases abertas da CVM e do SND:

| Fonte | O que entrega | Cobertura |
|---|---|---|
| Planilha (e-mail) | captação/resgate por janela, 40 semanas | 100% |
| `INF_DIARIO` | PL (D-1), rentabilidade, volatilidade, nº de cotistas | ~79-83% |
| `PERFIL_MENSAL` | % em crédito privado, prazo da carteira, concentração, perfil de cotistas | ~83% |
| `EXTRATO` | taxa de adm., cotização, aplicação mínima, público-alvo | ~69% |
| `registro_fundo_classe` | gestor, administrador, classificação ANBIMA | ~92% |
| `CDA` + SND | **composição da carteira e hedge em DAP → classificação LF / Incentivada / Tradicional / Misto**, mix por papel | ~65% |
| `CDA` BLC_5 | **quem emitiu o papel bancario**: tesouraria, preco (% CDI / spread), vencimento | 100% do BLC_5 |
| `LAMINA` | índice de referência declarado | ~2% |

Cada campo carrega a sua cobertura até a tela: o aviso do topo diz em quantos
fundos cada bloco existe, e o que não existe aparece como "—".

```
Captacao_Resgate/
├── backend/          FastAPI + conectores (vinculado, CVM, mock)
│   ├── app/
│   │   ├── connectors/   fontes de dados
│   │   ├── services/     ingestão do e-mail, pipeline, classificação
│   │   ├── routers/      endpoints da API
│   │   └── models/       schemas Pydantic
│   ├── scripts/          gerar_mock.py
│   └── requirements.txt
├── frontend/         site estático (HTML/CSS/JS) que consome a API
│   ├── index.html        dashboard de captacao/resgate
│   ├── tesourarias.html  mapa Tesouraria x Asset
│   ├── fundos.html       papel bancario nas duas pontas: gestora e emissor
│   └── admin.html        painel de controle
├── data/
│   ├── inbox/        anexos vinculado_*.xlsx baixados do e-mail
│   ├── cache/        parquets da CVM
│   └── mock_fundos.json
└── README.md
```

## Fonte de dados: a planilha que chega por e-mail

Enquanto a **API do Quantum Axis não é liberada**, a fonte de produção é o anexo
que chega todo dia útil de manhã:

| | |
|---|---|
| Pasta no Outlook | `Quantum` |
| Assunto | `FW: Captação e resgate` |
| Remetente | **qualquer um** |
| Anexo | `vinculado_<timestamp>.xlsx` (~1 MB) |

O app lê **sempre o e-mail mais recente** que casa com esses critérios. Não há
passo manual: `POST /api/admin/refresh` (ou o vencimento do cache) re-varre a
pasta, salva o anexo em `data/inbox/vinculado_AAAAMMDD_HHMM.xlsx` e recarrega.
Anexo já baixado não é baixado de novo, e o histórico fica todo em `data/inbox/`.

> **Quem manda não é critério, e isso é deliberado.** O relatório é encaminhado
> por mais de uma pessoa da mesa. Enquanto o filtro exigia um remetente fixo,
> um dia em que outra pessoa mandasse o arquivo o app não via nada de novo e
> seguia servindo o anexo da véspera — **sem erro nenhum na tela**, que é o pior
> tipo de falha. Foi exatamente o que aconteceu em 17/08/2026: o e-mail das
> 07:42 do remetente antigo trazia um export sem a coluna `CNPJ`, e o das 11:22
> de outra pessoa (com CNPJ) ficava invisível. Sem CNPJ não há casamento com a
> CVM, e o dashboard subia com **todos os fundos sem classificação**.
>
> O assunto casa por substring, sem acento e sem caixa, então pega tanto o
> `FW:` encaminhado quanto o original. O remetente do e-mail escolhido vai para
> o log, para a origem seguir auditável. `OUTLOOK_REMETENTE` no `.env` volta a
> restringir, se um dia a pasta passar a receber outra coisa parecida.

### O que a planilha traz

Aba única (`tarefa`), 4.683 fundos de 363 gestoras:

```
linha 1 | Nome | Gestão | CNPJ | CNPJ Gestão | Diária | Semanal | Mensal | Semestral | Captação Janela →
linha 3 |                                                                           | 06/08/2026 - 12/08/2026 | … (40 janelas)
linha 4+| …dados…
rodapé  | disclaimers da Quantum (descartados: são as linhas sem Gestão)
```

As 40 janelas semanais viram a série temporal real do dashboard. Célula vazia é
lida como "sem movimento". (Conferido: a coluna `Semanal` bate exatamente com a
janela semanal mais recente.)

> As colunas são localizadas **pelo nome no cabeçalho**, não por posição. As
> duas colunas de CNPJ foram inseridas no meio da planilha em 14/08/2026 —
> posição fixa teria feito os fluxos serem lidos das colunas erradas, sem erro.

**Subclasses:** o export lista a classe-mãe e cada `… SUBCLASSE X` como linhas
separadas com o mesmo CNPJ de fundo (42 CNPJs, 122 linhas). Os fluxos são de
cada subclasse e somam; o PL, não — é creditado uma vez só, à linha da
classe-mãe, senão o mesmo PL entraria 3-4 vezes no total.

### PL e cadastro: vêm da CVM

O CNPJ do fundo é casado com duas bases de dados abertos da CVM:

| Base | Entrega | Cobertura |
|---|---|---|
| `INF_DIARIO/inf_diario_fi_AAAAMM.zip` | **PL**, declarado diariamente (D-1) | 79,6% |
| `CAD/registro_fundo_classe.zip` | cadastro: gestor, administrador, ANBIMA, condomínio; PL de reserva | 73,3% |

O **informe diário vem primeiro** para o PL, e o registro só entra como
reserva. Três motivos: cobre mais, é mais fresco (declaração diária, contra um
`Data_Patrimonio_Liquido` que às vezes atrasa meses) e só fundo vivo declara,
então cancelado se resolve sozinho. Onde as duas bases têm valor, elas
concordam — diferença mediana de 0,08% em 3.345 fundos.

Vale a pena ter as duas: o informe sozinho deixaria de fora fundos que só o
registro conhece, e o registro é a única fonte do cadastro (gestor,
administrador, classificação).

**Cobertura de PL: 78,9%** (3.695 de 4.683 — 3.678 do informe, 17 do registro).
Os 988 restantes não constam em nenhuma das duas bases, ou têm PL abaixo do
piso de credibilidade.

> O registro sozinho cobria 72%. Uma tentativa anterior de casar por **nome**,
> sem CNPJ, chegava a 48,5% com falsos positivos reais (fundos diferentes
> casando com score 0,86) — por isso a coluna de CNPJ no export importa tanto.

O dashboard **diz isso na cara**: um aviso no topo e o rodapé do KPI informam
que os valores em R$ de PL, %PL e PL investível cobrem só esse recorte,
enquanto os fluxos cobrem o universo inteiro. Um total parcial exibido como
total vira erro de leitura na mesa.

> `Forma_Condominio` (Aberto/Fechado) **não** é usada como "aberto para
> captação". São coisas diferentes: condomínio aberto é natureza jurídica;
> "aberto para captação" é o fundo estar aceitando aplicação hoje — e fundo de
> condomínio aberto em soft-close é comum. Quem responde isso é o Quantum.

### Universo: só crédito privado

O relatório é para mesa de crédito, então multimercado macro que veio no export
sai da conta. "É de crédito privado" não vem carimbado num campo só; combinamos
quatro sinais, em `services/credito_privado.py`:

| Sinal | Origem | Cobertura |
|---|---|---|
| `PR_ATIVO_CRED_PRIV` — % do PL declarado | PERFIL_MENSAL | 82,8% |
| `ATIVO_CRED_PRIV = S` — a política permite | EXTRATO | 68,9% |
| Classificação ANBIMA de crédito | registro | 40,0% |
| Nome (crédito privado, FI-Infra, debênture, CRI/CRA, FIDC) | planilha | 58,2% |

Entram em **OR** de propósito: num relatório de mesa, deixar de fora um fundo
de crédito que existe é pior que deixar entrar um multimercado que não é — o
primeiro é uma oportunidade invisível, o segundo é uma linha que o usuário
reconhece e ignora. Cada fundo carrega em `sinais_credito` quais dispararam,
então dá para auditar por que ele entrou.

`CREDITO_PRIVADO_MODO=estrito` no `.env` troca para só o sinal quantitativo.

### Perfil de indexador — a exposição do cotista

Campo adjacente ao bucket, e separado dele de propósito:

> **`perfil_indexador` não é `bucket`.** O bucket mede a **composição da
> carteira** — que papel o fundo carrega, que é o que a mesa precisa para
> decidir o que originar. O perfil mede a **exposição do cotista** — o
> benchmark que o fundo persegue, ou como a cota se comporta. Em crédito as
> duas divergem: comprar debênture IPCA+ e travar em CDI via swap é rotina, e
> nesse fundo a composição é IPCA e o comportamento é CDI.

Duas rotas, nesta ordem (`services/perfil_indexador.py`):

| Rota | Fonte | Cobertura |
|---|---|---|
| **Declarado** | `INDICE_REFER` (LAMINA), `PARAM_TAXA_PERFM` (EXTRATO), nome do fundo | 24% |
| **Inferido** | volatilidade anualizada da cota | 48% |
| | *sem perfil (zona cinzenta ou sem série)* | 28% |

A inferência sai de uma medição contra 661 fundos de benchmark conhecido:

```
pós-fixado   vol.  p25 0,44%  mediana 1,30%  p75 2,42%
inflação     vol.  p25 5,78%  mediana 6,65%  p75 6,73%
```

Corte único em 3,8% acerta **86,5%**, contra 53,9% de chutar a classe maior.
Combinar com o excesso sobre o CDI não melhorou (86,2%). Entre 2,5% e 5,0% de
volatilidade o acerto cai para 77%, então ali o campo fica vazio em vez de
chutar.

**Dois limites que aparecem na tela**, não só aqui: o corte é de regime — foi
calibrado numa janela de juros em alta, em que os fundos de inflação apanharam
(retorno mediano de 0,70% contra CDI de 7,40%) — e o gabarito é o benchmark
declarado, que já é um proxy. Cada fundo mostra se o perfil veio declarado ou
inferido, e a inferência leva "?" no rótulo.

`PERFIL_INDEXADOR_INFERIR=false` no `.env` desliga a rota 2 e deixa só o
declarado.

### A classificacao LF / Incentivada / Tradicional / Misto — sem o Quantum

Sai do **CDA** da CVM (a carteira que o fundo declara) cruzado com o **registro
de debêntures do SND**. Cobre **2.897 fundos (65%)**, R$ 2,79 tri de PL.

Uma primeira medição, feita sobre o CDA do mês corrente, tinha concluído que
não dava: 51,7% do PL vinha como posição confidencial e só 9,2% dos fundos
saíam classificados. **Essa conclusão estava errada por olhar o mês errado.**
O sigilo do CDA expira, e no nosso universo ele some entre o 3º e o 4º mês:

```
CDA de 202605 (3 meses atrás)   45,9% do PL sigiloso   R$   738 bi visíveis
CDA de 202603 (5 meses atrás)    0,0% do PL sigiloso   R$ 1.617 bi visíveis
```

Por isso lemos o CDA com `CDA_DEFASAGEM_MESES=4` de atraso. O preço é uma
carteira de ~4 meses atrás, aceitável para um bucket — fundo de crédito não
troca de mandato em um trimestre — e a data viaja até a tela em todo lugar onde
o bucket aparece.

**De onde sai o indexador de cada papel:**

| Bloco | Papel | Indexador vem de | Valor no universo |
|---|---|---|---|
| `BLC_4` | debêntures | código do papel → registro do SND | R$ 734 bi |
| `BLC_5` | LF, CDB, DPGE | `DS_INDEXADOR_POSFX`, declarado no arquivo | R$ 683 bi (LF) + 116 bi (CDB) |
| `BLC_6` | títulos IF no exterior | idem | R$ 10 bi |
| `BLC_8` | CRI, CRA, nota promissória | *não tem* — dilui o mix | R$ 16 bi |

`BLC_1` (títulos públicos) e `BLC_2` (cotas de fundos) ficam de fora do mix: são
caixa e alocação, não a tese de crédito. Entram só no denominador de "quanto do
PL é carteira de crédito".

A ponte do SND é quase total: das 231.508 linhas de debênture do `BLC_4`,
**99,7% casam** — 99,4% do valor. Sem ela, 45% da carteira de crédito ficaria
sem indexador.

**Guardas, todas por fundo.** Quem não passa fica *sem classificação*, nunca com
bucket adivinhado — *Misto* significa "olhamos a carteira e nenhum indexador é
majoritário", não "não conseguimos olhar":

- `CDA_SIGILO_MAXIMO_PCT=20` — sigilo alto significa amostra enviesada;
- `CDA_IDX_MINIMO_PCT=80` — indexador desconhecido em boa parte da carteira;
- `CDA_CREDITO_MINIMO_PCT=10` — carteira de crédito pequena demais perto do PL.

**Bucket e perfil da cota divergem, e a divergência é informação.** Cruzando os
dois campos nos fundos que têm ambos:

```
                     cota: inflação   cota: pós
carteira CDI+                  61         752     (92% concordam)
carteira IPCA+                713         349     (33% DIVERGEM)
carteira LF/bancário           42         605
```

Um terço dos fundos com carteira IPCA+ entrega ao cotista retorno de
pós-fixado: são as casas que compram debênture IPCA+ e travam em CDI via swap.
É por isso que a inferência por volatilidade sozinha dizia "95% pós", e é por
isso que os dois campos existem lado a lado em vez de um substituir o outro.

> Armadilha do CDA: `TP_TITPUB = "LETRAS FINANCEIRAS DO TESOURO"` é **LFT,
> título público**, não Letra Financeira de banco. São 13.218 posições, e todas
> estão no `BLC_1`, que não entra no mix. Uma heurística de texto que procure
> "LETRA FINANCEIRA" classificaria todas como LF — errado.

> Fragilidade conhecida: o arquivo do SND abre com um aviso de que a consulta
> migrou para `data.anbima.com.br`. O endpoint segue servindo dado do dia
> (conferido em 14/08/2026), mas pode ser desligado. Se cair, as debêntures
> ficam sem indexador, a cobertura despenca abaixo da guarda e os fundos
> afetados voltam a "sem classificação" — nunca com bucket chutado.

### O que ainda falta (só o Quantum entrega)

**Duration**, **cotização** e **status de captação**. A CVM não publica duration
nem cotização (o prazo médio da carteira, do `PERFIL_MENSAL`, entra como proxy e
é rotulado como tal).

Esses campos vêm **vazios** — a API devolve `null` e o front mostra "—", com um
aviso no topo listando o que falta. Nenhum número é estimado. Em particular, um
fundo sem composição fica **sem bucket**, e não "Misto" — *Misto* significa
"olhamos a carteira e nenhum indexador é majoritário", não "não olhamos".

## Como rodar (Windows)

### Caminho normal: duplo clique em `Iniciar.bat`

A única exigência é **Python 3.10+ instalado**. O `Iniciar.bat` cuida do resto:
acha o interpretador (inclusive via `py -3`, e rejeitando o atalho da Microsoft
Store, que não executa nada), cria o ambiente virtual, instala as dependências,
copia o `.env` do exemplo, escolhe a porta, sobe o servidor, espera a API
responder, **pré-carrega as bases** e abre o navegador.

| | |
|---|---|
| Dashboard | `http://127.0.0.1:8000/` |
| Tesourarias | `http://127.0.0.1:8000/tesourarias.html` |
| Papel bancário | `http://127.0.0.1:8000/fundos.html` |
| Painel de controle | `http://127.0.0.1:8000/admin.html` |
| API / docs | `http://127.0.0.1:8000/docs` |

**Uma porta, uma janela.** Até 18/08/2026 subiam dois servidores: o uvicorn na
8000 e um `python -m http.server` na 5500 para os arquivos do painel. O
`http.server` fala HTTP/1.0 — fecha a conexão TCP a cada arquivo, não manda
`ETag` nem `Cache-Control` e não comprime nada. Hoje a própria API serve o
painel (`app.mount("/")` no `main.py`), o que resolve tudo isso de uma vez e
ainda dispensa CORS, porque passa a ser a mesma origem.

**Por que ele pré-carrega antes de abrir a tela.** A primeira chamada de dados
lê a planilha, cruza as bases da CVM e classifica os fundos — medido nesta
máquina, **199 s** com o cache do dia ainda frio. Sem o aquecimento, esse tempo
era pago pelo usuário olhando um "Carregando…" sem explicação. As chamadas
seguintes ficam em ~25 ms.

O `pip install` só roda quando o `requirements.txt` muda: o script guarda uma
cópia dele dentro do venv e compara. Cliques seguintes sobem em segundos.

**Rodando junto com o `cvm-monitor-pro`:** os dois convivem. As portas padrão
não se cruzam (aqui 8000, lá 8080), cada projeto tem o seu próprio venv dentro
da própria pasta, e se a porta estiver ocupada o script anda para a próxima
livre em vez de subir por cima de um servidor alheio.

### O painel não sai da máquina

Nenhum arquivo do painel vem da internet. Antes, cada carga de página buscava
`cdn.tailwindcss.com` (407 KB, que ainda compilava o CSS dentro do navegador) e
o Chart.js no jsDelivr (205 KB). Hoje:

| | antes | agora |
|---|---|---|
| Tailwind | 407 KB do CDN, compilado no navegador | `css/tailwind.css`, **17 KB** (4 KB gzip) |
| Chart.js | 205 KB do jsDelivr | `js/vendor/chart.umd.min.js`, local |
| JSON da API | sem compressão | gzip: 94 KB → **13 KB** |
| Página inteira (index) | ~778 KB na rede | **107 KB**, em ~52 ms |

O CSS agora é **gerado** a partir das classes que o projeto realmente usa, em
vez de vir o framework inteiro. Quem só quer rodar o painel não precisa de
nada: o `frontend/css/tailwind.css` está versionado. Quem **mexer nas classes**
do HTML/JS precisa regerá-lo:

```bat
frontend\build\gerar_css.bat
```

O script instala o Tailwind na primeira vez, gera o CSS e roda
`verificar_classes.py`, que confere se toda classe usada no painel ganhou
regra. Essa conferência existe por um motivo específico: o build é estático,
então uma classe montada em tempo de execução (`bg-${cor}-500`) não seria vista
pelo scanner e o elemento apareceria **sem estilo, sem erro nenhum no console**.
Hoje as 250 classes do painel são strings completas e todas estão cobertas.

### Caminho manual

<details>
<summary>Se preferir subir na mão</summary>

```bat
cd backend
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
uvicorn app.main:app --reload --port 8000
```

A API sobe em `http://localhost:8000`. Docs interativas em `/docs`.

Para a leitura do Outlook funcionar, o **Outlook clássico precisa estar aberto**
na mesma sessão do Windows. Se não estiver, o app não quebra: usa o último
arquivo já baixado em `data/inbox/` e diz qual é no `/health`.

> **A primeira carga demora alguns minutos**: baixa 7 meses de informe diário
> (~3,3 milhões de linhas), 5 anos de EXTRATO e 6 meses de PERFIL. Tudo fica em
> `data/cache/` como parquet, com TTL de 12-24h — as cargas seguintes são
> instantâneas. Para subir rápido em desenvolvimento, use `CVM_ENRIQUECER=false`
> ou `DATA_SOURCE=mock`.

### 2. Frontend (site)

Não precisa subir nada: **o `uvicorn` acima já serve o painel** em
`http://127.0.0.1:8000/`. O `main.py` monta `frontend/` na raiz, depois dos
routers — `/api`, `/health` e `/docs` continuam sendo resolvidos antes.

O `frontend/js/config.js` (gerado pelo `Iniciar.bat`, fora do git) traz
`window.API_BASE = ""`, ou seja, mesma origem. Preencha-o apenas para apontar
o painel para uma API em **outra** máquina.

Servir a pasta por fora ainda funciona, mas aí o `API_BASE` tem de apontar para
a porta da API e o `CORS_ORIGINS` do backend precisa liberar a origem do front.

</details>

## Senha do painel

Uma senha só, igual para todo mundo, guardada em `PAINEL_SENHA` no
`backend/.env` (que não vai para o git). É o mínimo para publicar o painel num
endereço alcançável: sem ela, quem descobrir a URL vê fluxo de captação e
resgate por gestora, e o `/admin.html` deixa alterar o corte de classificação
para todos.

**Isto não é controle de acesso por pessoa.** O log não sabe quem fez o quê, e
tirar o acesso de alguém significa trocar a senha de todos. É aceitável para
seis pessoas conhecidas numa mesa; a etapa seguinte é usuário por usuário com
banco.

### O que fica aberto

Só três coisas, e cada uma por um motivo:

| | |
|---|---|
| `/login`, `/logout` | é o próprio portão — trancá-lo seria um laço |
| `/health` | o systemd, o `Iniciar.bat` e qualquer monitoramento precisam saber se o serviço subiu **antes** de haver sessão |
| `/api/inbox*` | tem autenticação própria, por token — é a porta das máquinas, não das pessoas |

O `/health` responde **menos** para quem não entrou: só `status` e
`tem_planilha`. O nome do arquivo carrega a data do relatório da mesa e o corte
de classificação é parâmetro de negócio; nenhum dos dois precisa ser lido por
quem passa na porta.

Todo o resto — as quatro telas, o `/docs`, o `/openapi.json`, o JS e o CSS —
exige sessão.

### Como funciona

Cookie assinado com HMAC-SHA256, `HttpOnly` e `SameSite=lax`. O cookie **não
guarda segredo nenhum**: só a hora em que expira, mais a assinatura dessa hora.
Esticar a validade invalida a assinatura.

Escolhemos cookie em vez de HTTP Basic por um motivo concreto: o Basic ocupa o
cabeçalho `Authorization`, que aqui já é do token de ingestão. O navegador
passaria a mandar `Basic …` para o `POST /api/inbox`, que espera `Bearer …`, e
as duas autenticações brigariam. Com cookie, gente usa cookie e máquina usa
token, sem se atrapalhar.

A assinatura usa `hmac` da biblioteca padrão, e não o `SessionMiddleware` do
Starlette, porque este depende de `itsdangerous` — pacote que o projeto não
tem. Mesma escolha feita na ingestão, que lê corpo binário em vez de multipart
para não puxar `python-multipart`.

### Configuração

```ini
PAINEL_SENHA=a-senha-da-mesa
# Assina o cookie. Sem valor, é sorteado a cada partida e todo mundo cai no
# reinício do serviço. Nunca reaproveite o valor de outra instalação:
#   python -c "import secrets; print(secrets.token_urlsafe(32))"
PAINEL_SEGREDO=...
PAINEL_SESSAO_HORAS=12
# false em http://127.0.0.1 · true no servidor com HTTPS.
# Com true em http, o navegador descarta o cookie e o login entra em laço.
PAINEL_COOKIE_SEGURO=false
```

**`PAINEL_SENHA` vazio desliga a senha.** É o padrão de propósito: quem roda em
`127.0.0.1` na própria máquina não deveria ter de digitar senha para ver o
próprio painel. Em servidor é obrigatório — o app avisa no log ao subir se
estiver vazio.

### Freio de força bruta

Oito senhas erradas do mesmo IP em cinco minutos travam aquele IP pelo resto da
janela, inclusive para a senha certa. Não impede um ataque determinado; impede
o script que dispara milhares de tentativas por minuto, que é o que de fato
acontece com qualquer coisa exposta na internet.

> **Atrás de NAT corporativo, o freio é compartilhado.** Se as seis pessoas
> saem pelo mesmo IP público, oito erros de uma travam as outras por até cinco
> minutos. O contador olha o `X-Forwarded-For`, então atrás do Caddy ele
> distingue clientes reais — mas não distingue pessoas que dividem a mesma
> saída de internet.

### O que muda para quem já usava

O `Iniciar.bat` faz login sozinho antes de pré-carregar as bases
(`backend/scripts/aquecer.py` lê a senha do próprio `.env`, então ela não
aparece na linha de comando nem no log). O `Coletar_e_Enviar.bat` não é afetado:
ele usa o token de ingestão, não o cookie.

Nas telas, um botão **Sair** no cabeçalho encerra a sessão. Se o cookie vencer
com o painel aberto, a primeira chamada que levar 401 manda para o login e traz
de volta para a mesma tela depois.

## A planilha do dia como recurso de rede

Até agora a planilha só existia dentro da máquina que tem Outlook: o app lia o
anexo por COM, tecnologia que só existe no Windows. Isso amarrava o projeto a
seis instalações locais e impedia qualquer servidor.

A direção agora se inverte. A máquina com Outlook **empurra** o arquivo para o
servidor, e o servidor o publica como recurso HTTP:

| | |
|---|---|
| `POST /api/inbox` | recebe a planilha e recalcula o painel |
| `GET /api/inbox/ultimo` | nome, hora do e-mail, tamanho e `sha256` |
| `GET /api/inbox/ultimo/arquivo` | o `.xlsx` em si |

Os três exigem `Authorization: Bearer <INGESTAO_TOKEN>`. **Sem token
configurado, respondem 503** — desligado, e não aberto. Um endpoint de upload
sem autenticação é pior que endpoint nenhum: quem alcançasse a URL passaria a
decidir que números a mesa vê.

Com isso, qualquer sistema passa a consumir o mesmo arquivo que o painel usa, e
o `sha256` prova que é o mesmo:

```bash
curl -H "Authorization: Bearer SEU_TOKEN" https://SERVIDOR/api/inbox/ultimo
curl -H "Authorization: Bearer SEU_TOKEN" -o hoje.xlsx \
     https://SERVIDOR/api/inbox/ultimo/arquivo
```

### O coletor

`Coletar_e_Enviar.bat` é a única peça que ainda precisa de Windows. Ele lê o
Outlook, salva o anexo e publica:

```bat
Coletar_e_Enviar.bat                 rem duplo clique, espera ENTER no fim
Coletar_e_Enviar.bat /agendado       rem para o Agendador de Tarefas
Coletar_e_Enviar.bat --destino https://painel.suaempresa.com
Coletar_e_Enviar.bat --sem-outlook   rem publica o que já está em data/inbox/
```

Antes de enviar, ele pergunta ao servidor qual planilha está lá e **compara o
hash**. Se for a mesma, não sobe nem recalcula — o que torna seguro agendar
várias tentativas por dia.

**Agendamento sugerido** (Agendador de Tarefas do Windows): diariamente às
08:15, repetindo a cada 1 h por 3 h, com o argumento `/agendado`. A repetição
cobre o e-mail que atrasa; reenviar o mesmo arquivo não custa nada.

Não marque *"Executar estando o usuário conectado ou não"*. O Outlook precisa
da sessão aberta para responder ao COM; numa sessão desconectada a leitura
falha e o script acabaria publicando a planilha da véspera.

O log fica em `data/logs/coletor_AAAA-MM.log`, um por mês, em UTF-8.

### Códigos de saída

O Agendador enxerga o código de retorno, então dá para configurar alerta:

| | |
|---|---|
| `0` | publicou, ou o servidor já tinha esta mesma planilha |
| `1` | erro de configuração — destino ou token faltando |
| `2` | não achei planilha nenhuma para enviar |
| `3` | o servidor recusou ou não respondeu |

### O que o servidor recusa

O conteúdo é conferido de verdade, não pela extensão: precisa começar com a
assinatura de ZIP e conter `xl/workbook.xml`. Um `.xlsx` que na verdade é a
página de erro do proxy — que acontece em rede corporativa — é rejeitado com
`422` e a explicação, em vez de quebrar três passos depois dentro do pandas.

O **nome do arquivo é reescrito pelo servidor** a partir do cabeçalho
`X-Recebido-Em` (a hora do e-mail, não a do upload). Nome vindo do cliente
nunca toca no disco: é o caminho clássico para escrever fora da pasta.

`INGESTAO_MANTER_ARQUIVOS` (padrão 60) poda as planilhas antigas. Uma por dia
útil são ~1,2 MB/dia, o que encheria 300 MB de disco em um ano sem ninguém
perceber.

### Antes de expor isso na internet

O token viaja em texto claro sobre HTTP. **Só publique atrás de HTTPS** — Caddy
ou Cloudflare Tunnel resolvem com pouca configuração. E note que o painel em si
(incluindo o `/admin.html`, que altera o corte de classificação para todo
mundo) continua sem autenticação: isso é aceitável enquanto o servidor escuta
só em `127.0.0.1`, e deixa de ser no minuto em que existir um link público.

## Tesourarias — o mapa Tesouraria ↔ Asset

`http://127.0.0.1:8000/tesourarias.html`, ou o botão **🏛 Tesourarias**.

O dashboard responde "quem está captando". Esta tela responde a camada onde o
negócio de fato acontece, e a diferença é concreta: *"a Asset X compra LF"* não
é uma ligação. *"A Asset X tem R$ 26,6 bi do Banco Y a CDI + 1,28%, prazo médio
de 2,7 anos, com R$ 4,3 bi vencendo em 12 meses, e captou R$ 1,8 bi na semana"*
é.

Sai do bloco **BLC_5 do CDA** (Letra Financeira, CDB/RDB, DPGE), que é o mais
bem preenchido de todo o CDA: `CNPJ_EMISSOR` em 100% das linhas, `DT_VENC` em
100%, e o preço em 100% do papel indexado a CDI. São 90.327 posições e
R$ 877,7 bi em abr/2026.

### As três perguntas da tela

| | |
|---|---|
| **Ranking** | quais tesourarias o mercado carrega, quanto, a que preço, a que prazo, e quanto vence em 12 meses |
| **Quem já compra** | por asset: posição, preço que ela paga, prazo, o que está vencendo e quanto do papel bancário dela é daquele emissor |
| **Quem ainda não compra** | assets com apetite comprovado pela classe — já compram de outros bancos, já têm limite montado — e a taxa que pagam hoje, que é o número a bater |

O dossiê de cada gestora, no dashboard principal, ganhou o lado inverso: **de
quais tesourarias aquela casa compra**, com preço e vencimento. É a pergunta que
antecede a ligação.

### Decisões que mudam o número na tela

**Agrupamento pela raiz do CNPJ, nunca pelo nome.** O campo `EMISSOR` é texto
livre digitado pelo administrador: **795 grafias no arquivo são 179 emissores de
verdade**. O Bradesco aparece como "BRADESCO", "BANCO BRADESCO S.A.", "BCO
BRADESCO SA" e mais sete variações, somando R$ 136 bi. Um ranking por nome
quebraria a maior posição do mercado em dez pedaços e nenhum apareceria no topo.

**Preço são dois campos, e não convertemos um no outro.** Papel bancário é
cotado como percentual do CDI (`103,5% do CDI`) *ou* como CDI mais spread
(`CDI + 0,9%`). O CDA guarda os dois; a mediana do mercado é a segunda forma. A
conversão dependeria do nível do CDI na data e produziria um número que ninguém
negociou. Ambos vêm **ponderados por valor** — uma ponta de R$ 1 mi a CDI+3% não
move o custo de quem tem R$ 200 mi a CDI+0,8%.

**Posição intragrupo vem marcada.** `EMISSOR_LIGADO` no CDA identifica a asset
do próprio banco carregando papel da casa. Não é negócio disputável, e sem a
marca ela lideraria a lista de clientes como se fosse conquista comercial — a
Bradesco Asset carrega 28% do papel do Bradesco, e na Caixa a fatia intragrupo é
de 46%.

**O nocional vem do vencimento declarado, faixa a faixa.** A curva de rolagem é
montada posição a posição e só depois somada. Derivá-la do prazo médio perderia
o que interessa: um par com metade em 60 dias e metade em 5 anos tem média de
2,6 anos e nada vencendo nela.

> **São posições, não emissões.** Descrevem o estoque que os fundos carregavam
> na data-base do CDA, com a mesma defasagem proposital do resto do projeto.
> Apresentar como "o banco emitiu X" seria errado, e a tela diz isso no topo.

## Papel bancário — LF, CDB e DPGE, nas duas pontas

`http://127.0.0.1:8000/fundos.html`, ou o botão **📄 Papel bancário**.

Mesma matéria-prima da aba Tesourarias, sem agregar em médias. A tela tem uma
**chave de visão no cabeçalho** que inverte a leitura sem trocar de página:

| Visão | A pergunta | A lista | O detalhe |
|---|---|---|---|
| **Gestora → Papel** | o que esta casa tem na carteira? | gestoras que carregam papel bancário | blocos por emissor + tipo + mês de vencimento |
| **Papel → Gestora** | quem tem o meu papel, e vencendo quando? | emissores cujo papel está nos fundos | blocos por gestora + tipo + mês de vencimento |

São a mesma carteira somada por chaves diferentes: o **estoque total é idêntico
nas duas** (R$ 662,9 bi no CDA de abr/2026), muda só por onde se entra. Uma tela
só, e não duas páginas, justamente porque duas telas mostrando o mesmo número em
lugares diferentes é o que faz a mesa desconfiar do dado.

A distinção com a aba Tesourarias continua valendo, e é ela que justifica a
segunda visão existir: lá tudo é prazo médio, spread médio e faixa de
vencimento, que respondem "como está o meu funding no mercado". Não respondem
"quem tem o bloco que vence em fev/27 e a quanto ele comprou", que é o que se
precisa para ligar oferecendo a rolagem.

| Nível | O que mostra |
|---|---|
| Lista | volume, nº de papéis, taxa média ponderada, prazo, o que vence em 3m e 12m e o mix LF/CDB/DPGE — por gestora ou por emissor |
| Vencimento | combo na lista de emissores: abre a lista completa num clique, com o volume do mercado em cada mês, e filtra enquanto se digita — `fev`, `2027`, `fev/27`, `fevereiro 2027` ou `2027-02` levam ao mesmo lugar; setas e Enter escolhem sem tirar a mão do teclado |
| Detalhe | um bloco por linha: a outra ponta, tipo, mês/ano de vencimento, quantos papéis entraram, taxa e volume |
| Agenda | quanto vence em cada mês/ano — clicável, e espelhada no mesmo combo de vencimento dentro do dossiê, que ali lista só os meses daquele emissor |
| Concentração | chips da outra ponta, clicáveis, que filtram a tabela |
| Pivô | clicar o nome dentro do detalhe **vira a tela do avesso**: do bloco do Bradesco na carteira da Itaú Asset para todos os emissores da Itaú Asset, e vice-versa — sem passar pela lista |

### O filtro de vencimento reescreve as colunas

Escolher um mês na lista de emissores não filtra só as linhas: **as colunas
passam a falar daquele mês**. Volume, papéis, gestoras, taxa e mix viram os do
mês; prazo e as janelas de 3m/12m saem (são recortes do estoque inteiro e não
significam nada dentro de um mês só) e entra "% do estoque" — quanto do papel
daquele emissor vence ali. Os quatro KPIs do topo acompanham, e o quarto vira o
acumulado: quanto já venceu **até** aquele mês.

Filtrar as linhas e deixar as colunas descrevendo o estoque inteiro seria o
pior dos mundos: a tela responderia "quem tem papel vencendo em dez/26" com o
volume de tudo o que o emissor carrega, e a mesa ligaria com o número errado na
mão. Em dez/2026 são R$ 27,44 bi em 54 emissores — e o Banco do Brasil, que tem
R$ 45,50 bi de estoque, aparece com os R$ 13,53 bi que vencem no mês, 29,7% do
que ele tem.

Clicar num emissor com o filtro ligado abre o dossiê **já naquele mês**. A
agenda por emissor viaja no mesmo payload da lista (2.070 pares emissor × mês,
46 KB comprimidos), então trocar de mês não custa uma ida ao servidor.

O combo é escrito à mão, e não com `<datalist>`: o nativo não abre num clique,
cada navegador decide sozinho quando mostrá-lo, e ele não teria onde exibir o
volume de cada mês — que é metade da informação, porque é o que mostra onde
está o muro de vencimento antes mesmo de filtrar.

Leitura real na visão invertida: `BRADESCO · dez/2026` mostra R$ 4,79 bi em 36
blocos, começando por `Banco Bradesco (grupo) · LF · 7 papéis · CDI + 1,12% ·
R$ 2,70 bi` e `BTG Pactual Asset Management · LF · 12 papéis · CDI + 1,02% ·
R$ 836,5 mi`.

**Posição intragrupo vem marcada.** A asset do próprio banco costuma ser a maior
carregadora do papel dele — Banco Bradesco tem 26% do papel Bradesco que está
nos fundos — e isso não é negócio disputável. As linhas continuam no total, mas
levam a marca `grupo`, senão a lista de contatos começaria errada.

### Decisões

**O bloco é a unidade, e ele não é uma média.** Uma linha do detalhe é o que a
mesa negocia: "o bloco do Safra que vence em fev/27". Papéis do mesmo emissor,
mesmo tipo e mesmo mês somam o volume e trazem a taxa ponderada por ele;
vencimentos, tipos ou **formas de taxa** diferentes permanecem separados,
porque juntá-los destruiria exatamente a informação procurada — mediar
"CDI + 1,35%" com "102% do DI" daria ~51, que a tela mostraria como "51,7% do
DI": um papel que ninguém emitiu. A coluna *Papéis* diz quantos registros do
CDA entraram em cada linha.

A única média do resumo é o `spread_cdi`, restrito ao pós-fixado em CDI/Selic —
misturar CDI (mediana +0,90%), IPCA (+7,30%) e prefixado (12,80%) numa média só
produziria um número que não descreve nada.

**A taxa aparece na forma em que foi declarada.** `CDI + 0,50%`, `103,5% do
CDI`, `IPCA + 7,30%` ou a taxa cheia no prefixado. Converter uma na outra
dependeria do nível do CDI na data e produziria um número que ninguém negociou.

**Escopo:** LF, CDB/RDB e DPGE. Letra de câmbio/hipotecária/imobiliária e o
"Outros" do arquivo ficam fora — 18 mil linhas sem tipo declarado que só
sujariam o total.

> **O CDA não separa LF de LFSC e LFSN.** O campo de tipo do arquivo público diz
> apenas "Letra Financeira", então a subordinação não aparece. Essa quebra
> existe na base do Quantum e importa — LFSC e LFSN são outro risco e outro
> preço. O campo `instrumento` em `connectors/cvm_emissores.py` é o ponto de
> enxerto: refiná-lo pelo código CETIP ou ISIN do papel faz a quebra aparecer
> em toda a tela sem mexer em mais nada.

Medido no CDA de abr/2026: **71.559 papéis de LF/CDB/DPGE, R$ 847,8 bi, em
3.172 fundos**, com taxa declarada em 93,6% das posições.

## Painel de controle

`http://127.0.0.1:8000/admin.html`, ou o botão **⚙ Painel** no cabeçalho.

Ele expõe os parâmetros que governam a classificação e, **no mesmo gesto**,
reclassifica a base inteira:

| Parâmetro | O que governa | Padrão |
|---|---|---|
| Corte de classificação | fatia mínima para uma classe ser majoritária: acima dele em LF o fundo é LF, abaixo ele vai para a dupla verificação Incentivada/Tradicional | 20% |
| Cobertura mínima de hedge em DAP | nocional em DAP sobre o R$ da carteira IPCA+ a partir do qual entendemos que o fundo travou o cupom de inflação | 20% |

Salvar dispara a reclassificação e a resposta traz **quantos fundos mudaram de
bucket e para onde foram**, não um "ok". Uma régua de negócio ajustada às cegas
vira tentativa e erro: sem ver o efeito, o usuário mexe no número, vai olhar o
dashboard, volta e mexe de novo. Reclassificar 4.477 fundos leva ~0,03s.

> **A reclassificação não rebaixa nada da CVM.** A composição da carteira, o
> nome e a cobertura de DAP — os três insumos da regra — já estão em memória.
> Refazer a carga levaria minutos e traria variáveis que ninguém pediu para
> mudar, tornando impossível dizer se o antes/depois veio do parâmetro ou de um
> CDA novo que chegou no meio. Para recarregar a fonte existe
> `POST /api/admin/refresh`, que é outra operação.

O que o painel grava vai para `data/parametros.json` e passa a valer sobre o
`.env` no próximo start. É estado local da máquina (fora do git); o valor que o
projeto entrega continua em `backend/.env.example`.

> **Sem autenticação**, pelo mesmo motivo que o resto da API: isto roda em
> localhost, na máquina do analista, com CORS restrito à origem do front. Se um
> dia subir para servidor compartilhado, `routers/admin.py` é o primeiro a
> ganhar autenticação — ele escreve em disco e muda o que todo mundo vê.

## Fontes alternativas

No `.env`, `DATA_SOURCE`:

| Valor | O que faz |
|---|---|
| `vinculado` | **padrão** — planilha do e-mail |
| `cvm` | baixa informe diário + cadastro + CDA dos dados abertos da CVM |
| `mock` | `data/mock_fundos.json` — fluxo real, mas PL/composição/duration **sintéticos** |

> `mock` é só para desenvolver o front offline. Não use para decisão de mesa: os
> campos sintéticos são gerados com `random.seed(42)`.

O `CVMConnector` (`DATA_SOURCE=cvm`) reconstrói o fluxo pelo informe diário e
tira a composição aproximada do CDA, mas com defasagem de meses e sem
duration/cotização. A primeira carga é lenta (baixa vários ZIPs); o resultado
fica em `data/cache/`.

> Ele também lia o cadastro do `cad_fi.csv`, que pós-Resolução 175 virou acervo
> legado — 21 fundos ativos contra 46.572 cancelados — e por isso praticamente
> nenhum fundo saía com nome e gestora. Agora usa o mesmo
> `registro_fundo_classe.zip` da camada de PL (`connectors/cvm_cadastro.py`).

Para regenerar o mock a partir do último anexo:

```bat
cd backend
python backend/scripts/analise/gerar_mock.py
```

## Relatório de cobertura

```bat
cd backend
python backend/scripts/analise/relatorio_cobertura.py
```

Gera dois CSVs em `data/relatorios/`, para conferir o recorte do export:

* `cnpjs_sem_match_cvm.csv` — fundos da planilha que ficam sem PL, com o motivo
  (encerrado na CVM, PL zero, ausente das bases, PL abaixo do piso);
* `cnpjs_ausentes_no_quantum.csv` — fundos que a CVM tem no mesmo universo mas
  que não vêm no export, candidatos a incluir no filtro do Quantum.

O "mesmo universo" é inferido das classificações ANBIMA que os fundos já
presentes na planilha têm, então o relatório acompanha se o recorte mudar.

## Plugar o Quantum Axis (enriquecimento)

O Quantum entra como **enriquecedor**: dado um CNPJ, devolve composição por
indexador, duration, cotização, taxa de adm. e status de captação.

1. No `.env`:
   ```
   QUANTUM_ENABLED=true
   QUANTUM_BASE_URL=https://... (endpoint real do seu contrato)
   QUANTUM_TOKEN=seu_token
   ```
2. Implemente `_chamar_api` em `backend/app/connectors/quantum_connector.py`
   conforme o formato do seu contrato (o portal `developers.quantumaxis.com.br`
   documenta os endpoints, ex. "Consolidação de Carteiras").

Nada mais precisa mudar: os campos hoje `null` passam a vir preenchidos, e o
front reativa sozinho os buckets, o filtro por indexador, o mix por gestora e as
métricas de carteira. O aviso do topo some quando não sobrar campo faltando.

A ponte por CNPJ já está pronta — a planilha traz `CNPJ` (do fundo) e
`CNPJ Gestão` desde 14/08/2026, e o `CVMCadastroEnricher` roda no mesmo ponto
do pipeline onde o `QuantumEnricher` vai entrar.

## Regra de classificação: LF / Incentivada / Tradicional / Misto

Hierárquica, aplicada em `services/classifier.py`. O corte é `THRESHOLD_MAJORITARIO`,
hoje **20%** — e é editável em tempo de execução pelo [painel de controle](#painel-de-controle).

1. `>` corte em **Letras Financeiras** → **LF**.
   Papel bancário; o fundo sai da conta de crédito corporativo antes de tudo.
2. Senão é fundo de crédito, e vem a **dupla verificação**:
   * **Incentivada** — o nome traz `Incentivada`/`Incentivado`
     **E** a carteira opera IPCA+ de verdade: mais que o corte da base ex-LF em
     papel indexado a IPCA **e** hedge do cupom no futuro de **DAP**.
   * **Tradicional** — o nome **não** traz essas palavras
     **E** a carteira está atrelada a CDI+. Entra aqui também quem compra IPCA+
     e trava tudo em DAP: na ponta do cotista isso é CDI+.
3. Senão → **Misto**, com o motivo registrado em `bucket_motivo`.

LF é tratada como instrumento (não indexador): sai da conta primeiro; só então
mede-se o indexador sobre a base ex-LF. As frações de entrada são sobre a
**carteira de crédito** do fundo, não sobre o PL — a pergunta é "deste dinheiro
em crédito, a que ele é indexado", e caixa em LFT não deve diluir a resposta.

### Por que o nome sozinho não classifica

O fundo de debênture incentivada compra o papel em **B + spread** (NTN-B de
referência mais o prêmio de crédito) e **vende cupom de IPCA no futuro de DAP**.
O que sobra na cota é só o spread de crédito: a perna de inflação foi travada.
Um fundo que compra o mesmo papel e **não** vende DAP está com outra tese — está
comprado em juro real, e o cotista carrega a marcação da NTN-B junto.

São produtos diferentes para a mesa, e a diferença não aparece no nome. Medido
sobre o CDA de abr/2026 (4.477 fundos do universo, 2.894 com carteira legível):

| | Fundos |
|---|---|
| Nome traz `Incentivad*` | 154 |
| …e a carteira confirma IPCA+ **com** hedge em DAP → **Incentivada** | 80 |
| …e a carteira é IPCA+ **sem** hedge em DAP → **Misto** | 55 |
| …e a carteira é dominada por LF → **LF** | 2 |
| …sem carteira legível → sem classificação | 17 |

Ou seja: **mais de um terço dos fundos que se chamam "incentivada" não operam o
comportamento de IPCA+** descrito acima. Sem a segunda verificação eles seriam
vendidos como a mesma coisa. O sinal vem do bloco de derivativos do próprio CDA
(`Futuro de DAP:Cupom de DI x IPCA`, no BLC_8), e a régua é a cobertura —
nocional em DAP sobre o R$ em papel IPCA+. Entre os fundos com posição em DAP a
cobertura tem **mediana 0,65**; o piso de `HEDGE_DAP_MINIMO` (20%) descarta a
posição residual que não trava carteira nenhuma.

O nocional do DAP é **reconstruído**, não lido: o CDA informa contratos e ajuste
a mercado, nunca o tamanho da posição. Ver `_posicao_dap` em
`connectors/cvm_carteira.py`, que documenta a aproximação e o seu limite.

### Distribuição medida (carteira de abr/2026)

| Bucket | Fundos | % do PL classificado |
|---|---|---|
| LF | 1.181 | 79,4% |
| Tradicional | 930 | 13,7% |
| Misto | 703 | 4,6% |
| Incentivada | 80 | 2,2% |
| *sem classificação* | 1.583 | — |

**Incentivada é o menor bucket em PL e não é acidente**: são fundos de debênture
incentivada, todos pequenos, e o corte de 20% para LF joga muita carteira mista
com papel bancário para o bucket LF antes de a pergunta do indexador ser feita.
Misto cresceu em relação à regra anterior — 703 fundos contra 15 — porque agora
ele carrega os casos em que **nome e carteira discordam**, que antes eram
classificados só pelo indexador dominante. Isso é o efeito pretendido: são
exatamente os fundos que exigem um olhar antes de virarem argumento de venda.

Além do bucket, a carteira também responde **em que papel a casa entra**
(debênture / LF / CDB / CRI-CRA). É outro eixo: uma casa de LF é cliente de um
produto diferente de uma casa de debênture, e isso não sai do bucket. Aparece na
visão **Mesa** e no dossiê.

## Guardas de qualidade nos dados da CVM

As bases abertas têm campos preenchidos à mão, e alguns valores não são o que
o nome sugere. Cada guarda abaixo existe porque o número errado apareceu na
tela durante a construção:

| Campo | O que aparece | Guarda |
|---|---|---|
| `TAXA_ADM` | chega a **40.000** — valor em reais no lugar do % | descarta acima de `CVM_TAXA_ADM_TETO` (10% a.a.) |
| `APLIC_MIN` | mediana **1,0** — "sem mínimo" virou R$ 1 | ≤ 1 vira 0, exibido como "sem mínimo" |
| `PRAZO_CARTEIRA_TITULO` | **6.512** fundos com 0 | zero = não declarado, não "zero dias" |
| `PR_PATRIM_LIQ_MAIOR_COTST` | ~90% zerado | idem |
| `Patrimonio_Liquido` (registro) | placeholders de R$ 1 e PL negativo | piso de `CVM_PL_MINIMO` |
| Série de cota curta | retorno de 3 dias anualizado vira ficção | mínimo de `CVM_RENTAB_DIAS_MIN` dias |

Taxa de administração e cotização são agregadas por **mediana**, não por média
ponderada por PL. A ponderação é dominada pelos fundos master, que não cobram
taxa (ela é cobrada no feeder), e fazia uma casa grande aparecer cobrando
0,01% a.a. A mediana responde a pergunta certa: quanto essa gestora
tipicamente cobra.

## Sinais de estresse

Duas réguas, escolhidas **por fundo** conforme ele tenha PL ou não:

* **Com PL**: resgate na semana acima de `THRESHOLD_STRESS` (5%) do PL.
* **Sem PL**: resgate acima de `STRESS_MULTIPLO` (2×) o movimento típico do
  próprio fundo nas últimas `STRESS_SEMANAS_BASE` (13) semanas, com piso de
  `STRESS_MINIMO_ABS` (R$ 10 mi) para não encher a lista de fundo pequeno.
  Separa "fundo grande movimentando o de sempre" de "fundo saindo pela porta"
  sem precisar do PL.

A escolha é por fundo, e não pelo conjunto, de propósito: como a CVM cobre 79%,
aplicar a régua de %PL a todos faria os 21% sem PL sumirem da tela sem aviso —
justamente aqueles sobre os quais menos se sabe. Cada linha da tabela mostra
qual régua a qualificou (`-9,4% do PL` ou `3,4× típico`).

## A tabela de gestoras

Uma lente só: **fluxo**. PL, mix da classificação e as quatro janelas
(diária, semanal, mensal, semestral).

Havia um seletor **Visão** com mais três conjuntos de colunas — *Mesa* (taxa de
administração, cotização, aplicação mínima), *Performance* (rentabilidade e
volatilidade) e *Distribuição* (cotistas, público-alvo). Saíram em 21/08/2026,
junto com o filtro "Só fundos abertos p/ captação".

O motivo é de negócio, não de tela: os três descreviam o produto para o
**investidor final**, e esta mesa é B2B — fala com tesouraria de um lado e asset
do outro. Taxa de administração e rentabilidade não mudam com quem a mesa
conversa; fluxo, sim.

O dossiê lateral de cada gestora agrega o mesmo, e mais três barras que medem
coisas diferentes e por isso têm paletas diferentes:

* **Composição por bucket** (roxo/azul/âmbar) — o indexador da carteira, com o
  mês do CDA ao lado;
* **Em que papel entra** — o instrumento. O trecho cinza ao fim da barra é o que
  não é debênture/LF/CDB/CRI, deixado visível para não sugerir leitura completa;
* **Perfil de indexador** (ciano/rosa) — a exposição do cotista, não a carteira;
* **Quem já compra** — fração do PL por tipo de investidor (PF private, PF
  varejo, fundo de pensão, RPPS, seguradora, banco, distribuidor, não
  residente), do `PERFIL_MENSAL`.

Cada fundo da lista mostra o bucket com a carteira que o gerou no *tooltip*
(`LF 100% · IPCA 0% · CDI 0% · pré 0%`, com o mês e o peso do crédito no PL).

## Endpoints

| Método | Rota | Descrição |
|---|---|---|
| GET | `/api/dashboard` | KPIs, buckets, série semanal, ranking de gestoras com métricas de mesa |
| GET | `/api/dossie/{gestora}` | Painel lateral: resumo, mix, métricas, fundos |
| GET | `/api/movers?direcao=pos\|neg` | Gestoras por variação de PL em 30d (ou por fluxo, sem PL) |
| GET | `/api/stress?limite=N` | Fundos com resgate anormal |
| GET | `/api/tesourarias` | Ranking de tesourarias emissoras: estoque, preco, prazo, rolagem |
| GET | `/api/tesourarias/{raiz}` | Dossie: quem compra, curva de vencimento e quem ainda nao compra |
| GET | `/api/carteira-bancaria` | Gestoras que carregam LF/CDB/DPGE, da maior a menor |
| GET | `/api/carteira-bancaria/{gestora}` | Os papeis de uma gestora: emissor, mes de vencimento, volume e taxa |
| GET | `/api/papel-por-emissor` | A mesma carteira pela ponta do emissor: quem carrega o papel dele |
| GET | `/api/papel-por-emissor/{raiz}` | Quem tem o papel deste emissor em carteira, por gestora, tipo e mes |
| GET | `/api/fonte` | Arquivo em uso e campos indisponíveis |
| GET | `/api/pressao` | Pressão de compra/venda por gestora: direção do fluxo, perfil da carteira e agenda de vencimento em 3/6/12 meses por eixo |
| POST | `/api/inbox` | Recebe a planilha do dia pela rede (token próprio) |
| GET | `/api/inbox/ultimo` | Nome, hora, tamanho e hash da planilha em uso |
| GET | `/api/inbox/ultimo/arquivo` | Baixa a planilha que o painel está lendo |
| POST | `/api/admin/coletar-email` | Busca o anexo na caixa agora (requer `EMAIL_MODO`) |
| POST | `/api/admin/refresh` | Re-varre o Outlook e recalcula o pipeline (recarrega a fonte) |
| GET | `/api/admin/parametros` | Parametros editaveis e o retrato atual da classificacao |
| PUT | `/api/admin/parametros` | Grava os parametros **e reclassifica a base**, devolvendo as transicoes |
| POST | `/api/admin/parametros/restaurar` | Volta aos valores de `.env` e reclassifica |
| POST | `/api/admin/reclassificar` | Reaplica a regra sem mexer em parametro |
| GET | `/health` | Status, fonte ativa e último arquivo baixado |
