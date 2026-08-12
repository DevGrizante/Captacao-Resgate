# Captação e Resgate · Crédito Privado

Dashboard web de captação/resgate de fundos de crédito privado, consolidado por
gestora e classificado por indexador majoritário (**IPCA+ / CDI+ / LF / Misto**).

Substitui o relatório manual por e-mail + PBI por um site com link fixo que
atualiza com um F5.

```
Captacao_Resgate/
├── backend/          FastAPI + conectores (CVM real, Quantum stub, mock)
│   ├── app/
│   │   ├── connectors/   fontes de dados
│   │   ├── services/     pipeline + classificação por indexador
│   │   ├── routers/      endpoints da API
│   │   └── models/       schemas Pydantic
│   ├── scripts/          gerar_mock.py
│   └── requirements.txt
├── frontend/         site estático (HTML/CSS/JS) que consome a API
├── data/             cache + mock_fundos.json
└── README.md
```

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

Por padrão usa `DATA_SOURCE=mock` (4.693 fundos reais do seu Excel, offline).

### 2. Frontend (site)

O front é estático. Sirva a pasta `frontend/` com qualquer servidor. O mais
simples:

```bat
cd frontend
python -m http.server 5500
```

Abra `http://localhost:5500`. Se o backend estiver em outra porta, edite
`API_BASE` no topo de `frontend/js/api.js` (ou defina `window.API_BASE` antes
de carregar o script).

> Atalho: dê duplo clique em `Iniciar.bat` na raiz — ele sobe API e front juntos.

## Trocar mock → CVM (dados reais)

No `.env`:

```
DATA_SOURCE=cvm
CVM_MESES_INFORME=7
```

Reinicie a API. O `CVMConnector` baixa o informe diário e o cadastro dos
dados abertos da CVM, monta as janelas por CNPJ e agrega. A primeira carga é
lenta (baixa vários ZIPs) — o resultado fica em `data/cache/`.

> Sem composição de carteira, os fundos entram como **Misto** até o Quantum
> enriquecer. É o comportamento esperado: a CVM dá o fluxo, o Quantum dá o
> indexador.

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
   documenta os endpoints, ex. "Consolidação de Carteiras"). O restante do
   pipeline já consome o que esse método devolver.

Enquanto `QUANTUM_ENABLED=false`, o enriquecimento é um no-op — o app roda
normalmente só com CVM/mock.

## Regra de classificação por indexador

Hierárquica, aplicada em `services/classifier.py`:

1. `> 50%` em **Letras Financeiras** → **LF**
2. senão, `> 50%` do restante (ex-LF) em ativos **IPCA+** → **IPCA**
3. senão, `> 50%` do restante (ex-LF) em ativos **CDI+/DI+** → **CDI**
4. senão → **Misto**

LF é tratada como instrumento (não indexador): sai da conta primeiro; só então
mede-se IPCA vs CDI sobre a base ex-LF.

## Endpoints

| Método | Rota | Descrição |
|---|---|---|
| GET | `/api/dashboard` | KPIs, buckets, série temporal, ranking de gestoras |
| GET | `/api/dossie/{gestora}` | Painel lateral: resumo, mix, métricas, fundos |
| GET | `/api/movers?direcao=pos\|neg` | Gestoras por variação % de PL |
| GET | `/api/stress?limite=N` | Fundos com resgate acima do limiar |
| POST | `/api/admin/refresh` | Recalcula o pipeline |
| GET | `/health` | Status e fonte de dados ativa |

## O que ainda é placeholder (aguardando dados reais)

Alguns campos usam valores fixos até termos série histórica / Quantum:
`var_pl_pct` (precisa de série de PL semanal), `premio_ipca`/`premio_cdi`
(vêm do Quantum), `pl_var_30d_pct`, `cobertura_pct`, e a série temporal por
bucket. Estão isolados e marcados com `# placeholder` no `services/pipeline.py`.
