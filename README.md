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
| `CDA` + SND | **composição da carteira → bucket IPCA+/CDI+/LF/Misto**, mix por papel | ~65% |
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
| Remetente | `ottavio.lucca@bgcg.com` |
| Assunto | `FW: Captação e resgate` |
| Anexo | `vinculado_<timestamp>.xlsx` (~1 MB) |

O app lê **sempre o e-mail mais recente** que casa com esses três critérios. Não
há passo manual: `POST /api/admin/refresh` (ou o vencimento do cache) re-varre a
pasta, salva o anexo em `data/inbox/vinculado_AAAAMMDD_HHMM.xlsx` e recarrega.
Anexo já baixado não é baixado de novo, e o histórico fica todo em `data/inbox/`.

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

### O bucket IPCA+ / CDI+ / LF / Misto — sem o Quantum

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

### 1. Backend (API)

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

Abra `http://localhost:5500`. Se o backend estiver em outra porta, edite
`API_BASE` no topo de `frontend/js/api.js` (ou defina `window.API_BASE` antes
de carregar o script).

> Atalho: dê duplo clique em `Iniciar.bat` na raiz — ele sobe API e front juntos.

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

## Regra de classificação por indexador

Hierárquica, aplicada em `services/classifier.py`:

1. `> 50%` em **Letras Financeiras** → **LF**
2. senão, `> 50%` do restante (ex-LF) em ativos **IPCA+** → **IPCA**
3. senão, `> 50%` do restante (ex-LF) em ativos **CDI+/DI+** → **CDI**
4. senão → **Misto**

LF é tratada como instrumento (não indexador): sai da conta primeiro; só então
mede-se IPCA vs CDI sobre a base ex-LF. As frações de entrada são sobre a
**carteira de crédito** do fundo, não sobre o PL — a pergunta é "deste dinheiro
em crédito, a que ele é indexado", e caixa em LFT não deve diluir a resposta.

Com os dados de abr/2026 a distribuição fica:

| Bucket | Fundos | % do PL |
|---|---|---|
| LF | 742 | 50,8% |
| CDI+ | 782 | 29,0% |
| IPCA+ | 1.358 | 8,5% |
| Misto | 15 | 0,1% |

IPCA+ é o bucket mais numeroso e o menor em PL: são muitos fundos de debênture
incentivada, todos pequenos. E **Misto quase não existe** (0,4% dos fundos)
porque a dominância é real — a mediana do maior indexador é 98,3% da carteira, e
o p10 ainda é 82%. Subir `THRESHOLD_MAJORITARIO` para 0,8 levaria Misto a 7%; o
limiar de 0,5 é o que estava validado com o negócio e ficou como está.

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
| POST | `/api/admin/refresh` | Re-varre o Outlook e recalcula o pipeline |
| GET | `/health` | Status, fonte ativa e último arquivo baixado |
