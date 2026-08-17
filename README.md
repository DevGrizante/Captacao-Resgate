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
copia o `.env` do exemplo, escolhe as portas, sobe API e front, espera a API
responder e abre o navegador.

| | |
|---|---|
| Dashboard | `http://localhost:5500` |
| Painel de controle | `http://localhost:5500/admin.html` |
| API / docs | `http://localhost:8000/docs` |

O `pip install` só roda quando o `requirements.txt` muda: o script guarda uma
cópia dele dentro do venv e compara. Cliques seguintes sobem em segundos.

**Rodando junto com o `cvm-monitor-pro`:** os dois convivem. As portas padrão
não se cruzam (aqui 8000/5500, lá 8080), cada projeto tem o seu próprio venv
dentro da própria pasta, e se alguma porta estiver ocupada o script anda para a
próxima livre em vez de subir por cima de um servidor alheio. Quando a porta da
API muda, o script reescreve `frontend/js/config.js` e ajusta o `CORS_ORIGINS`
do backend, então as duas pontas continuam se enxergando.

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

O front é estático. Sirva a pasta `frontend/` com qualquer servidor:

```bat
cd frontend
python -m http.server 5500
```

Abra `http://localhost:5500`. Se o backend estiver em outra porta, ajuste
`window.API_BASE` em `frontend/js/config.js` (é o arquivo que o `Iniciar.bat`
gera; sem ele, `js/api.js` cai no padrão `http://localhost:8000`).

</details>

## Painel de controle

`http://localhost:5500/admin.html`, ou o botão **⚙ Painel** no cabeçalho.

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
python scripts\gerar_mock.py
```

## Relatório de cobertura

```bat
cd backend
python scripts\relatorio_cobertura.py
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

## A tabela de gestoras tem quatro lentes

Juntar tudo daria uma tabela de 16 colunas e nenhuma legível. O seletor
**Visão** troca o conjunto de colunas conforme a pergunta:

| Visão | Colunas | Responde |
|---|---|---|
| **Fluxo** | PL, mix, diária/semanal/mensal/semestral | quem está captando e quem está sangrando |
| **Mesa** | PL, share, taxa adm, cotização, prazo, **papel**, aplicação mínima | quanto custa, em quanto tempo sai, em que papel entra, com quanto entra |
| **Performance** | PL, rentabilidade, volatilidade, pós/inflação, % crédito privado, prazo | quem entrega retorno, e correndo quanto risco |
| **Distribuição** | cotistas, Δ cotistas, público-alvo, quem compra | onde essa gestora já está vendida |

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
| GET | `/api/fonte` | Arquivo em uso e campos indisponíveis |
| POST | `/api/admin/refresh` | Re-varre o Outlook e recalcula o pipeline (recarrega a fonte) |
| GET | `/api/admin/parametros` | Parametros editaveis e o retrato atual da classificacao |
| PUT | `/api/admin/parametros` | Grava os parametros **e reclassifica a base**, devolvendo as transicoes |
| POST | `/api/admin/parametros/restaurar` | Volta aos valores de `.env` e reclassifica |
| POST | `/api/admin/reclassificar` | Reaplica a regra sem mexer em parametro |
| GET | `/health` | Status, fonte ativa e último arquivo baixado |
