// Camada de acesso à API. Ajuste API_BASE se o backend rodar em outra porta/host.
const API_BASE = window.API_BASE || "http://localhost:8000";

async function apiGet(path) {
  const resp = await fetch(`${API_BASE}${path}`);
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
  const dados = await resp.json().catch(() => null);
  if (!resp.ok) {
    const detalhe = dados && dados.detail;
    throw new Error(typeof detalhe === "string" ? detalhe : `API ${path} -> ${resp.status}`);
  }
  return dados;
}

const API = {
  dashboard: (s) => apiGet(`/api/dashboard?janela=${s.janela}&indexador=${s.indexador}&abertos=${s.abertos}`),
  dossie: (gestora, s) => apiGet(`/api/dossie/${encodeURIComponent(gestora)}?janela=${s.janela}&indexador=${s.indexador}&abertos=${s.abertos}`),
  movers: (direcao, limite, s) => apiGet(`/api/movers?direcao=${direcao}&limite=${limite}&janela=${s.janela}&indexador=${s.indexador}&abertos=${s.abertos}`),
  stress: (limite, s) => apiGet(`/api/stress?limite=${limite}&janela=${s.janela}&indexador=${s.indexador}&abertos=${s.abertos}`),

  // --- mesa Tesouraria x Asset ---
  tesourarias: (limite = 60) => apiGet(`/api/tesourarias?limite=${limite}`),
  tesouraria: (raiz, limite = 40) => apiGet(`/api/tesourarias/${encodeURIComponent(raiz)}?limite=${limite}`),

  // --- carteira de papel bancario, por fundo ---
  carteiraBancaria: (limite = 300, busca = "") =>
    apiGet(`/api/carteira-bancaria?limite=${limite}&busca=${encodeURIComponent(busca)}`),
  carteiraBancariaGestora: (gestora) => apiGet(`/api/carteira-bancaria/${encodeURIComponent(gestora)}`),

  // --- painel de controle ---
  parametros: () => apiGet("/api/admin/parametros"),
  salvarParametros: (valores) => apiSend("PUT", "/api/admin/parametros", { valores }),
  restaurarParametros: () => apiSend("POST", "/api/admin/parametros/restaurar"),
  reclassificar: () => apiSend("POST", "/api/admin/reclassificar"),
  recarregarFonte: () => apiSend("POST", "/api/admin/refresh"),
};
