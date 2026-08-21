// Camada de acesso à API.
//
// Vazio = MESMA ORIGEM, e é o padrão certo: quem serve estas telas é a própria
// API, então `/api/...` já chega no lugar. Preencher `window.API_BASE` no
// config.js só faz sentido para apontar o painel para uma API em outra máquina.
//
// O padrão antigo era "http://localhost:8000", e isso quebrava o servidor de um
// jeito difícil de diagnosticar: no navegador de quem acessa o painel remoto,
// "localhost" é a máquina DELE, não o servidor. Todas as chamadas de dados
// falhavam enquanto as telas carregavam normalmente.
const API_BASE = window.API_BASE || "";

// A sessão dura 12 horas. Quem deixa o painel aberto de um dia para o outro
// volta com o cookie vencido, e aí TODA chamada passa a devolver 401 — a tela
// encheria de "erro ao carregar" sem dizer o motivo de verdade.
//
// Mandar para o login é o certo, mas só uma vez: o painel dispara várias
// chamadas em paralelo, e sem esta trava cada uma delas chamaria o redirect,
// atropelando a anterior. O `?proximo=` traz a pessoa de volta para a tela em
// que ela estava.
let redirecionando = false;
function sessaoExpirada() {
  if (redirecionando) return;
  redirecionando = true;
  const daqui = location.pathname + location.search;
  location.href = `/login?proximo=${encodeURIComponent(daqui)}`;
}

async function apiGet(path) {
  const resp = await fetch(`${API_BASE}${path}`);
  if (resp.status === 401) {
    sessaoExpirada();
    throw new Error("Sessão expirada.");
  }
  if (!resp.ok) throw new Error(`API ${path} -> ${resp.status}`);
  return resp.json();
}

// O backend responde 422 com o motivo em `detail` quando um parâmetro está
// fora da faixa. Propagar esse texto é o que permite ao painel dizer "o corte
// aceita de 1% a 99%" em vez de "erro 422".
async function apiSend(metodo, path, corpo) {
  const resp = await fetch(`${API_BASE}${path}`, {
    method: metodo,
    headers: { "Content-Type": "application/json" },
    body: corpo === undefined ? undefined : JSON.stringify(corpo),
  });
  if (resp.status === 401) {
    sessaoExpirada();
    throw new Error("Sessão expirada.");
  }
  const dados = await resp.json().catch(() => null);
  if (!resp.ok) {
    const detalhe = dados && dados.detail;
    throw new Error(typeof detalhe === "string" ? detalhe : `API ${path} -> ${resp.status}`);
  }
  return dados;
}

const API = {
  dashboard: (s) => apiGet(`/api/dashboard?janela=${s.janela}&indexador=${s.indexador}`),
  dossie: (gestora, s) => apiGet(`/api/dossie/${encodeURIComponent(gestora)}?janela=${s.janela}&indexador=${s.indexador}`),
  movers: (direcao, limite, s) => apiGet(`/api/movers?direcao=${direcao}&limite=${limite}&janela=${s.janela}&indexador=${s.indexador}`),
  stress: (limite, s) => apiGet(`/api/stress?limite=${limite}&janela=${s.janela}&indexador=${s.indexador}`),

  // --- mesa Tesouraria x Asset ---
  tesourarias: (limite = 60) => apiGet(`/api/tesourarias?limite=${limite}`),
  tesouraria: (raiz, limite = 40) => apiGet(`/api/tesourarias/${encodeURIComponent(raiz)}?limite=${limite}`),

  // --- carteira de papel bancario, por fundo ---
  carteiraBancaria: (limite = 300, busca = "") =>
    apiGet(`/api/carteira-bancaria?limite=${limite}&busca=${encodeURIComponent(busca)}`),
  carteiraBancariaGestora: (gestora) => apiGet(`/api/carteira-bancaria/${encodeURIComponent(gestora)}`),

  // --- a mesma carteira lida pela ponta do emissor: quem tem o meu papel ---
  papelPorEmissor: (limite = 1000, busca = "") =>
    apiGet(`/api/papel-por-emissor?limite=${limite}&busca=${encodeURIComponent(busca)}`),
  papelPorEmissorDetalhe: (raiz) => apiGet(`/api/papel-por-emissor/${encodeURIComponent(raiz)}`),

  // --- painel de controle ---
  parametros: () => apiGet("/api/admin/parametros"),
  salvarParametros: (valores) => apiSend("PUT", "/api/admin/parametros", { valores }),
  restaurarParametros: () => apiSend("POST", "/api/admin/parametros/restaurar"),
  reclassificar: () => apiSend("POST", "/api/admin/reclassificar"),
  recarregarFonte: () => apiSend("POST", "/api/admin/refresh"),
};
