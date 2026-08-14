// Camada de acesso à API. Ajuste API_BASE se o backend rodar em outra porta/host.
const API_BASE = window.API_BASE || "http://localhost:8001";

async function apiGet(path) {
  const resp = await fetch(`${API_BASE}${path}`);
  if (!resp.ok) throw new Error(`API ${path} -> ${resp.status}`);
  return resp.json();
}

const API = {
  dashboard: (s) => apiGet(`/api/dashboard?janela=${s.janela}&indexador=${s.indexador}&abertos=${s.abertos}`),
  dossie: (gestora, s) => apiGet(`/api/dossie/${encodeURIComponent(gestora)}?janela=${s.janela}&indexador=${s.indexador}&abertos=${s.abertos}`),
  movers: (direcao, limite, s) => apiGet(`/api/movers?direcao=${direcao}&limite=${limite}&janela=${s.janela}&indexador=${s.indexador}&abertos=${s.abertos}`),
  stress: (limite, s) => apiGet(`/api/stress?limite=${limite}&janela=${s.janela}&indexador=${s.indexador}&abertos=${s.abertos}`),
};
