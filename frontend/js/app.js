// ===== Estado e utilidades =====
const state = { janela: "semanal", indexador: "todos", abertos: true, sortBy: "semestral", search: "", moversDir: "pos" };
let DATA = null;        // payload /api/dashboard
let sparkChart = null;
let timelineChart = null;

function fmtBRL(v) {
  const abs = Math.abs(v);
  const s = v < 0 ? "−" : (v > 0 ? "+" : "");
  if (abs >= 1e9) return `${s}R$ ${(abs / 1e9).toFixed(2)} bi`;
  if (abs >= 1e6) return `${s}R$ ${(abs / 1e6).toFixed(1)} mi`;
  if (abs >= 1e3) return `${s}R$ ${(abs / 1e3).toFixed(0)} mil`;
  return `${s}R$ ${abs.toFixed(0)}`;
}
function cls(v) { return v > 0 ? "num-pos" : (v < 0 ? "num-neg" : "num-neutral"); }
const bucketLabel = { ipca: ["badge-ipca", "IPCA+"], cdi: ["badge-cdi", "CDI+"], lf: ["badge-lf", "LF"], misto: ["badge-misto", "Misto"] };

// ===== Boot =====
async function init() {
  try {
    await refreshData();
    bindEvents();
  } catch (e) {
    document.body.insertAdjacentHTML("afterbegin",
      `<div class="bg-red-950 text-red-300 text-sm p-3 text-center">Não consegui falar com a API em ${API_BASE}. Suba o backend (uvicorn app.main:app --port 8000) e recarregue.</div>`);
    console.error(e);
  }
}

async function refreshData() {
  DATA = await API.dashboard(state);
  renderHeader();
  renderKpisFluxo();
  renderKpisMesa();
  renderBuckets();
  renderTimeline();
  renderTop();
  renderTable();
  await renderMovers();
  await renderStress();
}

function renderHeader() {
  document.getElementById("hdr-meta").innerHTML =
    `Atualizado ${DATA.data_referencia} · ${DATA.total_fundos.toLocaleString("pt-BR")} fundos · ${DATA.total_gestoras} gestoras · <span class="num-pos">${DATA.cobertura_pct.toFixed(1)}% cobertura</span>`;
}

function renderKpisFluxo() {
  const k = DATA.kpis_fluxo;
  const delta = k.fluxo_liquido - k.fluxo_liquido_anterior;
  document.getElementById("kpi-fluxo").innerHTML = `
    <p class="text-xs text-slate-500 uppercase tracking-wide">Fluxo líquido · janela</p>
    <p class="text-2xl font-semibold mt-1 ${cls(k.fluxo_liquido)}">${fmtBRL(k.fluxo_liquido)}</p>
    <p class="text-xs mt-1"><span class="${cls(delta)}">${delta >= 0 ? "▲" : "▼"} ${fmtBRL(Math.abs(delta)).replace("+", "")}</span> vs. período anterior</p>`;
  document.getElementById("kpi-capt").innerHTML = `
    <p class="text-xs text-slate-500 uppercase tracking-wide">Captação bruta</p>
    <p class="text-2xl font-semibold mt-1 num-pos">${fmtBRL(k.captacao_bruta)}</p>
    <p class="text-xs mt-1 text-slate-500">${k.fundos_entrada.toLocaleString("pt-BR")} fundos com entrada</p>`;
  document.getElementById("kpi-resg").innerHTML = `
    <p class="text-xs text-slate-500 uppercase tracking-wide">Resgates</p>
    <p class="text-2xl font-semibold mt-1 num-neg">${fmtBRL(k.resgates)}</p>
    <p class="text-xs mt-1 text-slate-500">${k.fundos_saida.toLocaleString("pt-BR")} fundos com saída</p>`;
  document.getElementById("kpi-pl").innerHTML = `
    <p class="text-xs text-slate-500 uppercase tracking-wide">PL total coberto</p>
    <p class="text-2xl font-semibold mt-1">${fmtBRL(k.pl_total).replace("+", "")}</p>
    <p class="text-xs mt-1"><span class="num-pos">▲ ${k.pl_var_30d_pct.toFixed(1)}%</span> nos últimos 30d</p>`;
}

function renderKpisMesa() {
  const k = DATA.kpis_mesa;
  document.getElementById("kpi-abertos").innerHTML = `
    <p class="text-[10px] text-emerald-400 uppercase tracking-wide">★ Fundos abertos p/ captação</p>
    <p class="text-2xl font-semibold mt-1">${k.fundos_abertos.toLocaleString("pt-BR")}</p>
    <p class="text-[10px] mt-0.5 text-slate-500">${(k.fundos_abertos_pct * 100).toFixed(0)}% do universo · ${fmtBRL(k.pl_investivel).replace("+", "")} investível</p>`;
}

function renderBuckets() {
  const cores = { ipca: "bg-purple-500", cdi: "bg-blue-500", lf: "bg-amber-500", misto: "bg-slate-500" };
  const nomes = { ipca: "IPCA+", cdi: "CDI+", lf: "Letras Financeiras", misto: "Misto" };
  const maxAbs = Math.max(...DATA.buckets.map(b => Math.abs(b.fluxo))) || 1;
  document.getElementById("buckets").innerHTML = DATA.buckets.map(b => {
    const pct = Math.abs(b.fluxo) / maxAbs * 100;
    const dur = b.duration_media ? `dur. ${b.duration_media.toFixed(1)}a · ` : "";
    const cot = b.cotizacao_media ? `cot. D+${b.cotizacao_media} · ` : "";
    return `<div>
      <div class="flex items-center justify-between text-xs mb-1">
        <span class="flex items-center gap-2"><span class="w-2 h-2 rounded-full ${cores[b.bucket]}"></span>${nomes[b.bucket]}</span>
        <span class="${cls(b.fluxo)} font-mono">${fmtBRL(b.fluxo)}</span>
      </div>
      <div class="h-1.5 bg-slate-800 rounded"><div class="h-full ${cores[b.bucket]} rounded" style="width:${pct}%"></div></div>
      <p class="text-[10px] text-slate-500 mt-1">${b.fundos.toLocaleString("pt-BR")} fundos · ${(b.pct_pl * 100).toFixed(0)}% do PL · ${dur}${cot}${(b.pct_abertos * 100).toFixed(0)}% abertos</p>
    </div>`;
  }).join("");
}

function renderTimeline() {
  const s = DATA.serie_temporal;
  const labels = s.map(p => p.semana);
  if (timelineChart) timelineChart.destroy();
  timelineChart = new Chart(document.getElementById("chart-timeline"), {
    type: "line",
    data: {
      labels, datasets: [
        { label: "IPCA+", data: s.map(p => p.ipca), borderColor: "#a855f7", backgroundColor: "rgba(168,85,247,.1)", tension: .35, borderWidth: 2, pointRadius: 0, pointHoverRadius: 4 },
        { label: "CDI+", data: s.map(p => p.cdi), borderColor: "#3b82f6", backgroundColor: "rgba(59,130,246,.1)", tension: .35, borderWidth: 2, pointRadius: 0, pointHoverRadius: 4 },
        { label: "LF", data: s.map(p => p.lf), borderColor: "#f59e0b", backgroundColor: "rgba(245,158,11,.1)", tension: .35, borderWidth: 2, pointRadius: 0, pointHoverRadius: 4 },
      ]
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: { display: false }, tooltip: { backgroundColor: "#1e293b", borderColor: "#334155", borderWidth: 1, callbacks: { label: c => `${c.dataset.label}: ${fmtBRL(c.parsed.y)}` } } },
      scales: {
        x: { ticks: { color: "#64748b", font: { size: 10 } }, grid: { color: "rgba(148,163,184,.05)" } },
        y: { ticks: { color: "#64748b", font: { size: 10 }, callback: v => fmtBRL(v) }, grid: { color: "rgba(148,163,184,.05)" } }
      }
    }
  });
}

function renderTop() {
  const w = state.janela;
  const sorted = [...DATA.gestoras].sort((a, b) => b[w] - a[w]);
  const maxAbs = Math.max(...DATA.gestoras.map(g => Math.abs(g[w]))) || 1;
  const bar = (g, color) => {
    const v = g[w]; const pct = Math.abs(v) / maxAbs * 100;
    return `<div class="group cursor-pointer" onclick="openDossie('${esc(g.nome)}')">
      <div class="flex items-center justify-between text-xs mb-1">
        <span class="truncate max-w-[55%] group-hover:text-blue-400">${g.nome}</span>
        <span class="font-mono ${cls(v)}">${fmtBRL(v)}</span>
      </div>
      <div class="h-1 bg-slate-800 rounded"><div class="h-full ${color} rounded" style="width:${pct}%"></div></div>
    </div>`;
  };
  document.getElementById("top-cap").innerHTML = sorted.slice(0, 5).map(g => bar(g, "bg-emerald-500")).join("");
  document.getElementById("top-res").innerHTML = sorted.slice(-5).reverse().map(g => bar(g, "bg-red-500")).join("");
}

function renderTable() {
  const q = state.search.toLowerCase();
  const filtered = DATA.gestoras.filter(g => g.nome.toLowerCase().includes(q));
  const key = state.sortBy;
  const sorted = [...filtered].sort((a, b) => Math.abs(b[key]) - Math.abs(a[key]));
  document.getElementById("table-body").innerHTML = sorted.map(g => {
    const mix = `<div class="flex h-1.5 w-32 rounded overflow-hidden mx-auto" title="IPCA+ ${(g.mix_ipca*100).toFixed(0)}% · CDI+ ${(g.mix_cdi*100).toFixed(0)}% · LF ${(g.mix_lf*100).toFixed(0)}% · Misto ${(g.mix_misto*100).toFixed(0)}%">
      <div style="width:${g.mix_ipca*100}%" class="bg-purple-500"></div>
      <div style="width:${g.mix_cdi*100}%" class="bg-blue-500"></div>
      <div style="width:${g.mix_lf*100}%" class="bg-amber-500"></div>
      <div style="width:${g.mix_misto*100}%" class="bg-slate-600"></div>
    </div>`;
    return `<tr class="row-hover border-t border-slate-800 cursor-pointer" onclick="openDossie('${esc(g.nome)}')">
      <td class="px-4 py-2.5 font-medium">${g.nome}</td>
      <td class="px-4 py-2.5 text-center text-slate-400">${g.fundos}</td>
      <td class="px-4 py-2.5 text-center"><span class="num-pos">${g.abertos}</span><span class="text-slate-600 text-[10px]"> /${g.fundos}</span></td>
      <td class="px-4 py-2.5">${mix}</td>
      <td class="px-4 py-2.5 text-right font-mono ${cls(g.diaria)}">${fmtBRL(g.diaria)}</td>
      <td class="px-4 py-2.5 text-right font-mono ${cls(g.semanal)}">${fmtBRL(g.semanal)}</td>
      <td class="px-4 py-2.5 text-right font-mono ${cls(g.mensal)}">${fmtBRL(g.mensal)}</td>
      <td class="px-4 py-2.5 text-right font-mono ${cls(g.semestral)}">${fmtBRL(g.semestral)}</td>
    </tr>`;
  }).join("");
}

async function renderMovers() {
  const movers = await API.movers(state.moversDir, 6, state);
  document.getElementById("movers-list").innerHTML = movers.map(g => {
    const v = g.var_pl_pct; const color = v > 0 ? "num-pos" : "num-neg";
    return `<tr class="row-hover border-t border-slate-800/50 cursor-pointer" onclick="openDossie('${esc(g.nome)}')">
      <td class="px-4 py-2 truncate max-w-[160px]">${g.nome}</td>
      <td class="px-4 py-2 text-center text-slate-400">${g.duration_media ? g.duration_media.toFixed(1) + "a" : "—"}</td>
      <td class="px-4 py-2 text-center text-slate-400">${g.cotizacao_media ? "D+" + g.cotizacao_media : "—"}</td>
      <td class="px-4 py-2 text-right font-mono ${color}">${v > 0 ? "+" : ""}${v.toFixed(1)}%</td>
    </tr>`;
  }).join("");
}

async function renderStress() {
  const stress = await API.stress(8, state);
  document.getElementById("stress-list").innerHTML = stress.map(f => `
    <tr class="row-hover border-t border-slate-800/50">
      <td class="px-4 py-2"><div class="truncate max-w-[150px]">${f.nome}</div><div class="text-[10px] text-slate-600">${f.gestora}</div></td>
      <td class="px-4 py-2 text-center text-slate-400">${f.duration ? f.duration.toFixed(1) + "a" : "—"}</td>
      <td class="px-4 py-2 text-center text-slate-400">${f.cotizacao_resgate ? "D+" + f.cotizacao_resgate : "—"}</td>
      <td class="px-4 py-2 text-right font-mono num-neg">${f.resgate_pct_pl.toFixed(1)}%</td>
    </tr>`).join("");
}

// ===== Dossiê lateral =====
async function openDossie(nome) {
  const d = await API.dossie(nome, state);
  const g = d.gestora;
  document.getElementById("d-name").textContent = g.nome;
  document.getElementById("d-meta").textContent =
    `${g.fundos} fundos · ${g.abertos} abertos p/ captação · classe majoritária ${d.classe_majoritaria}`;
  document.getElementById("d-sem").innerHTML = `<span class="${cls(g.semanal)}">${fmtBRL(g.semanal)}</span>`;
  document.getElementById("d-semt").innerHTML = `<span class="${cls(g.semestral)}">${fmtBRL(g.semestral)}</span>`;
  document.getElementById("d-pl").textContent = fmtBRL(g.pl).replace("+", "");
  document.getElementById("d-abertos").innerHTML = `${g.abertos} <span class="text-slate-500 text-xs">(${(g.abertos / g.fundos * 100).toFixed(0)}%)</span>`;
  document.getElementById("d-dur").textContent = g.duration_media ? `${g.duration_media.toFixed(1)} anos` : "—";
  document.getElementById("d-cot").textContent = g.cotizacao_media ? `D+${g.cotizacao_media}` : "—";
  document.getElementById("d-tx").textContent = g.taxa_adm_media ? `${g.taxa_adm_media.toFixed(2)}%` : "—";

  const parts = [["mix_ipca", "bg-purple-500", "IPCA+", "text-purple-400"], ["mix_cdi", "bg-blue-500", "CDI+", "text-blue-400"], ["mix_lf", "bg-amber-500", "LF", "text-amber-400"], ["mix_misto", "bg-slate-600", "Misto", "text-slate-400"]];
  document.getElementById("d-mix-bar").innerHTML = parts.map(([k, c]) => `<div class="${c}" style="width:${g[k] * 100}%"></div>`).join("");
  document.getElementById("d-mix-legend").innerHTML = parts.map(([k, c, label, tc]) => `<div><div class="font-semibold ${tc}">${(g[k] * 100).toFixed(0)}%</div><div class="text-slate-600">${label}</div></div>`).join("");

  if (sparkChart) sparkChart.destroy();
  sparkChart = new Chart(document.getElementById("d-spark"), {
    type: "bar",
    data: { labels: d.sparkline.map((_, i) => `S-${12 - i}`), datasets: [{ data: d.sparkline, backgroundColor: d.sparkline.map(v => v >= 0 ? "#10b981" : "#ef4444"), borderRadius: 2 }] },
    options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false }, tooltip: { callbacks: { label: c => fmtBRL(c.parsed.y) } } }, scales: { x: { ticks: { color: "#475569", font: { size: 8 } }, grid: { display: false } }, y: { ticks: { display: false }, grid: { display: false } } } }
  });

  document.getElementById("d-fundos").innerHTML = d.fundos.map(f => {
    const b = bucketLabel[f.bucket];
    const st = f.aberto_captacao ? "badge-ok" : "badge-misto";
    return `<tr class="border-t border-slate-800/50">
      <td class="px-3 py-2">${f.nome}</td>
      <td class="px-3 py-2 text-center"><span class="${b[0]} px-1.5 py-0.5 rounded text-[9px]">${b[1]}</span></td>
      <td class="px-3 py-2 text-center text-slate-400">${f.cotizacao_resgate ? "D+" + f.cotizacao_resgate : "—"}</td>
      <td class="px-3 py-2 text-center"><span class="${st} px-1.5 py-0.5 rounded text-[9px]">${f.aberto_captacao ? "Aberto" : "Fech."}</span></td>
      <td class="px-3 py-2 text-right font-mono ${cls(f.semanal)}">${fmtBRL(f.semanal)}</td>
    </tr>`;
  }).join("");

  document.getElementById("dossie-overlay").classList.remove("hidden");
  requestAnimationFrame(() => {
    document.getElementById("dossie-overlay").style.opacity = "1";
    document.getElementById("dossie").classList.remove("dossie-closed");
    document.getElementById("dossie").classList.add("dossie-open");
  });
}
function closeDossie() {
  document.getElementById("dossie-overlay").style.opacity = "0";
  document.getElementById("dossie").classList.add("dossie-closed");
  document.getElementById("dossie").classList.remove("dossie-open");
  setTimeout(() => document.getElementById("dossie-overlay").classList.add("hidden"), 250);
}

// ===== Eventos =====
function bindEvents() {
  bindTabs("janela-tabs", "janela", () => { document.getElementById("idx-window-label").textContent = state.janela; refreshData(); });
  bindTabs("idx-tabs", "indexador", () => { refreshData(); });
  document.getElementById("abertos-checkbox").addEventListener("change", e => { state.abertos = e.target.checked; refreshData(); });
  document.getElementById("search").addEventListener("input", e => { state.search = e.target.value; renderTable(); });
  document.getElementById("sort-by").addEventListener("change", e => { state.sortBy = e.target.value; renderTable(); });
  document.getElementById("movers-dir").addEventListener("change", e => { state.moversDir = e.target.value; renderMovers(); });
  document.addEventListener("keydown", e => { if (e.key === "Escape") closeDossie(); });
}
function bindTabs(id, key, cb) {
  document.querySelectorAll(`#${id} button`).forEach(b => b.addEventListener("click", () => {
    document.querySelectorAll(`#${id} button`).forEach(x => { x.classList.remove("pill-active"); x.classList.add("bg-slate-800"); });
    b.classList.add("pill-active"); b.classList.remove("bg-slate-800");
    state[key] = b.dataset.w || b.dataset.i; cb();
  }));
}
function esc(s) { return s.replace(/'/g, "\\'"); }

init();
