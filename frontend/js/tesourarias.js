// Tela de tesourarias: o mapa Tesouraria x Asset.
//
// A tela responde três perguntas em sequência, e a ordem é a da conversa de
// mesa: (1) quem são os emissores e a que preço o mercado carrega, (2) quem já
// compra de um deles e o que está vencendo, (3) quem tem apetite pela classe e
// ainda não compra daquele emissor. A terceira é a que gera ligação.

const VAZIO = "—";
let DADOS = [];
const estado = { busca: "", ordenar: "valor" };

const $ = (id) => document.getElementById(id);

function fmtBRL(v) {
  const abs = Math.abs(v || 0);
  const s = v < 0 ? "−" : "";
  if (abs >= 1e12) return `${s}R$ ${(abs / 1e12).toFixed(2)} tri`;
  if (abs >= 1e9) return `${s}R$ ${(abs / 1e9).toFixed(2)} bi`;
  if (abs >= 1e6) return `${s}R$ ${(abs / 1e6).toFixed(1)} mi`;
  if (abs >= 1e3) return `${s}R$ ${(abs / 1e3).toFixed(0)} mil`;
  return `${s}R$ ${abs.toFixed(0)}`;
}
function fmtFluxo(v) {
  const cor = v > 0 ? "num-pos" : (v < 0 ? "num-neg" : "num-neutral");
  const sinal = v > 0 ? "+" : (v < 0 ? "−" : "");
  return `<span class="${cor}">${sinal}${fmtBRL(Math.abs(v)).replace("R$ ", "")}</span>`;
}
const ou = (v, fmt) => (v === null || v === undefined) ? `<span class="text-slate-600">${VAZIO}</span>` : fmt(v);
const nInt = (v) => Number(v || 0).toLocaleString("pt-BR");

// "2026-04" -> "abr/2026"
const MESES = ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"];
function fmtMes(iso) {
  if (!iso) return VAZIO;
  const [a, m] = iso.split("-");
  return `${MESES[Number(m) - 1] || m}/${a}`;
}

// O papel bancário é cotado das duas formas, e o CDA guarda as duas. Mostramos
// a que o emissor de fato usa: spread quando existe, senão o % do CDI. Somar ou
// converter uma na outra produziria um número que ninguém negociou.
function fmtPreco(spread, pctCdi) {
  if (spread !== null && spread !== undefined && spread > 0) return `CDI + ${spread.toFixed(2)}%`;
  if (pctCdi !== null && pctCdi !== undefined) return `${pctCdi.toFixed(1)}% CDI`;
  return `<span class="text-slate-600">${VAZIO}</span>`;
}
function fmtPrazo(dias) {
  if (dias === null || dias === undefined) return `<span class="text-slate-600">${VAZIO}</span>`;
  return dias >= 365 ? `${(dias / 365).toFixed(1)}a` : `${Math.round(dias)}d`;
}

async function init() {
  try {
    // Todos, sem teto: os KPIs somam sobre esta lista, e a busca é
    // client-side — um recorte esconderia emissores das duas coisas.
    DADOS = await API.tesourarias(2000);
    if (!DADOS.length) {
      mostrarSemDados();
      return;
    }
    renderCabecalho();
    renderKpis();
    renderTabela();
    ligarEventos();
  } catch (e) {
    const caixa = $("erro-api");
    caixa.classList.remove("hidden");
    caixa.innerHTML = `<strong>Não consegui falar com a API em ${API_BASE}.</strong> ` +
      `Suba o backend (o Iniciar.bat faz isso) e recarregue. ` +
      `<span class="font-mono text-red-400/70">${e.message}</span>`;
    $("hdr-meta").textContent = "API indisponível";
  }
}

function mostrarSemDados() {
  $("hdr-meta").textContent = "sem mapa de emissores nesta carga";
  $("tabela").innerHTML = `<tr><td colspan="9" class="px-4 py-8 text-center text-slate-500">
    O mapa de emissores vem do CDA da CVM, que não está disponível nesta carga.<br>
    Ele aparece assim que o CDA for baixado — o dashboard segue funcionando sem ele.</td></tr>`;
}

function renderCabecalho() {
  const data = DADOS[0].carteira_data;
  $("hdr-meta").textContent =
    `${nInt(DADOS.length)} tesourarias · carteira declarada à CVM em ${fmtMes(data)}`;
  $("aviso-resumo").textContent =
    `Posições de LF, CDB e DPGE declaradas no CDA de ${fmtMes(data)} — estoque, não emissão.`;
  $("rodape").textContent =
    `Captação e Resgate · Crédito Privado · mapa de emissores do CDA de ${fmtMes(data)}`;
}

function renderKpis() {
  const estoque = DADOS.reduce((s, t) => s + t.valor, 0);
  const venc = DADOS.reduce((s, t) => s + t.valor_venc_12m, 0);
  // Preço "do mercado": média ponderada pelo estoque de cada emissor. A média
  // simples daria o mesmo peso a um banco com R$ 136 bi e a um com R$ 200 mi.
  const comSpread = DADOS.filter(t => t.spread !== null && t.spread > 0);
  const pesoSpread = comSpread.reduce((s, t) => s + t.valor, 0);
  const spread = pesoSpread ? comSpread.reduce((s, t) => s + t.spread * t.valor, 0) / pesoSpread : null;
  const assets = Math.max(...DADOS.map(t => t.gestoras));

  const kpi = (el, rot, valor, sub) => {
    $(el).innerHTML = `<p class="text-xs text-slate-500 uppercase tracking-wide">${rot}</p>
      <p class="text-2xl font-semibold mt-1">${valor}</p>
      <p class="text-xs mt-1 text-slate-500">${sub}</p>`;
  };
  kpi("kpi-estoque", "Estoque bancário", fmtBRL(estoque),
      "LF, CDB e DPGE nos fundos do universo");
  kpi("kpi-emissores", "Tesourarias", nInt(DADOS.length),
      `a maior alcança ${nInt(assets)} assets`);
  kpi("kpi-venc", "Vence em 12 meses", fmtBRL(venc),
      `${(venc / estoque * 100).toFixed(0)}% do estoque — a agenda de rolagem`);
  kpi("kpi-preco", "Preço médio", spread ? `CDI + ${spread.toFixed(2)}%` : VAZIO,
      "ponderado pelo estoque de cada emissor");
}

function renderTabela() {
  const q = estado.busca.toLowerCase();
  const chave = estado.ordenar;
  const peso = (t) => (t[chave] === null || t[chave] === undefined) ? -1 : t[chave];
  const linhas = DADOS
    .filter(t => t.nome.toLowerCase().includes(q))
    .sort((a, b) => peso(b) - peso(a));

  if (!linhas.length) {
    $("tabela").innerHTML = `<tr><td colspan="9" class="px-4 py-6 text-center text-slate-600">
      Nenhuma tesouraria com esse nome.</td></tr>`;
    return;
  }

  $("tabela").innerHTML = linhas.map(t => `
    <tr class="row-hover border-t border-slate-800 cursor-pointer" onclick="abrirDossie('${t.raiz}')">
      <td class="px-4 py-2.5 font-medium">${t.nome}</td>
      <td class="px-4 py-2.5 text-right font-mono">${fmtBRL(t.valor)}</td>
      <td class="px-4 py-2.5 text-right font-mono text-slate-400">${ou(t.share_pct, v => v.toFixed(1) + "%")}</td>
      <td class="px-4 py-2.5 text-center text-slate-400">${nInt(t.gestoras)}</td>
      <td class="px-4 py-2.5 text-center text-slate-500">${nInt(t.fundos)}</td>
      <td class="px-4 py-2.5 text-right font-mono">${fmtPreco(t.spread, t.pct_cdi)}</td>
      <td class="px-4 py-2.5 text-center text-slate-400">${fmtPrazo(t.prazo_dias)}</td>
      <td class="px-4 py-2.5 text-right font-mono text-slate-400">
        ${fmtBRL(t.valor_venc_12m)}
        <span class="text-slate-600">${ou(t.pct_venc_12m, v => ` ${v.toFixed(0)}%`)}</span>
      </td>
      <td class="px-4 py-2.5 text-center">${barraLigado(t.pct_ligado)}</td>
    </tr>`).join("");
}

// Quanto do estoque está na asset do próprio grupo. Barra em vez de número
// solto porque a leitura é comparativa: o olho precisa achar rápido o emissor
// cujo "cliente" é ele mesmo.
function barraLigado(pct) {
  if (pct === null || pct === undefined) return `<span class="text-slate-600">${VAZIO}</span>`;
  const cor = pct >= 50 ? "#ef4444" : (pct >= 20 ? "#f59e0b" : "#475569");
  return `<div class="flex items-center gap-1.5 justify-center"
      title="${pct.toFixed(1)}% do estoque está na asset do próprio grupo — não é negócio disputável">
    <div class="h-1.5 w-10 rounded overflow-hidden bg-slate-800">
      <div style="width:${Math.min(pct, 100)}%;background:${cor}" class="h-full"></div>
    </div>
    <span class="text-slate-500 text-[10px] w-7 text-right">${pct.toFixed(0)}%</span>
  </div>`;
}

// ===== Dossiê =====
const CORES_CURVA = ["#ef4444", "#f59e0b", "#eab308", "#3b82f6", "#475569"];

async function abrirDossie(raiz) {
  const d = await API.tesouraria(raiz, 40).catch(() => null);
  if (!d) return;

  $("d-nome").textContent = d.resumo.nome;
  $("d-meta").textContent =
    `${nInt(d.resumo.gestoras)} assets · ${nInt(d.resumo.fundos)} fundos · ` +
    `carteira de ${fmtMes(d.resumo.carteira_data)}`;

  const r = d.resumo;
  const kpis = [
    ["Estoque", fmtBRL(r.valor), r.share_pct !== null ? `${r.share_pct.toFixed(1)}% do bancário` : ""],
    ["Preço médio", fmtPreco(r.spread, r.pct_cdi), "ponderado por valor"],
    ["Prazo médio", fmtPrazo(r.prazo_dias), ""],
    ["Vence em 12m", fmtBRL(r.valor_venc_12m),
     r.pct_venc_12m !== null ? `${r.pct_venc_12m.toFixed(0)}% do estoque` : ""],
  ];
  $("d-kpis").innerHTML = kpis.map(([rot, v, sub]) =>
    `<div class="card rounded-lg p-3">
      <p class="text-[10px] text-slate-500 uppercase">${rot}</p>
      <p class="text-base font-semibold mt-0.5">${v}</p>
      <p class="text-[10px] text-slate-600 mt-0.5">${sub}</p>
    </div>`).join("");

  const total = d.curva_vencimento.reduce((s, f) => s + f.valor, 0);
  $("d-curva-bar").innerHTML = total
    ? d.curva_vencimento.map((f, i) =>
        `<div style="width:${f.valor / total * 100}%;background:${CORES_CURVA[i]}"
           title="${f.rotulo}: ${fmtBRL(f.valor)}"></div>`).join("")
    : "";
  $("d-curva-legend").innerHTML = d.curva_vencimento.map((f, i) =>
    `<div>
      <div class="flex items-center justify-center gap-1">
        <span class="w-2 h-2 rounded-full" style="background:${CORES_CURVA[i]}"></span>
        <span class="text-slate-500">${f.rotulo}</span>
      </div>
      <div class="font-semibold mt-0.5">${fmtBRL(f.valor)}</div>
      <div class="text-slate-600">${f.pct !== null ? f.pct.toFixed(0) + "%" : VAZIO}</div>
    </div>`).join("");

  const intragrupo = d.compradores.filter(c => c.ligado).length;
  $("d-compradores-n").textContent =
    `· ${nInt(d.compradores.length)} assets` + (intragrupo ? `, ${intragrupo} intragrupo` : "");

  $("d-compradores").innerHTML = d.compradores.map(c => `
    <tr class="border-t border-slate-800/50">
      <td class="px-3 py-2">
        <div class="truncate max-w-[200px]">${c.gestora}</div>
        <div class="text-[9px] text-slate-600">
          ${nInt(c.fundos)} fundos
          ${c.pct_do_bancario !== null ? ` · ${c.pct_do_bancario.toFixed(0)}% do bancário dela` : ""}
          ${c.ligado ? ` · <span class="badge-warn px-1 rounded">intragrupo</span>` : ""}
        </div>
      </td>
      <td class="px-3 py-2 text-right font-mono">${fmtBRL(c.valor)}</td>
      <td class="px-3 py-2 text-right font-mono text-slate-400">${fmtPreco(c.spread, c.pct_cdi)}</td>
      <td class="px-3 py-2 text-center text-slate-400">${fmtPrazo(c.prazo_dias)}</td>
      <td class="px-3 py-2 text-right font-mono text-slate-400">${fmtBRL(c.valor_venc_12m)}</td>
      <td class="px-3 py-2 text-right font-mono">${fmtFluxo(c.fluxo_semanal)}</td>
    </tr>`).join("");

  $("d-oportunidades").innerHTML = d.oportunidades.length
    ? d.oportunidades.map(o => `
      <tr class="border-t border-slate-800/50">
        <td class="px-3 py-2">
          <div class="truncate max-w-[200px]">${o.gestora}</div>
          <div class="text-[9px] text-slate-600">${nInt(o.fundos)} fundos${o.pl ? ` · PL ${fmtBRL(o.pl)}` : ""}</div>
        </td>
        <td class="px-3 py-2 text-right font-mono">${fmtBRL(o.valor_bancario)}</td>
        <td class="px-3 py-2 text-center text-slate-400">${nInt(o.emissores)}</td>
        <td class="px-3 py-2 text-right font-mono text-slate-400">${fmtPreco(o.spread_medio, null)}</td>
        <td class="px-3 py-2 text-right font-mono">${fmtFluxo(o.fluxo_semanal)}</td>
      </tr>`).join("")
    : `<tr><td colspan="5" class="px-3 py-4 text-center text-slate-600">
        Todos os assets que compram papel bancário já compram desta tesouraria.</td></tr>`;

  $("dossie-overlay").classList.remove("hidden");
  $("d-corpo").scrollTop = 0;
  requestAnimationFrame(() => {
    $("dossie-overlay").style.opacity = "1";
    $("dossie").classList.remove("dossie-closed");
    $("dossie").classList.add("dossie-open");
  });
}

function fecharDossie() {
  $("dossie-overlay").style.opacity = "0";
  $("dossie").classList.add("dossie-closed");
  $("dossie").classList.remove("dossie-open");
  setTimeout(() => $("dossie-overlay").classList.add("hidden"), 250);
}

function ligarEventos() {
  $("busca").addEventListener("input", e => { estado.busca = e.target.value; renderTabela(); });
  $("ordenar").addEventListener("change", e => { estado.ordenar = e.target.value; renderTabela(); });
  document.addEventListener("keydown", e => { if (e.key === "Escape") fecharDossie(); });
  $("aviso-toggle").addEventListener("click", () => {
    const aberto = !$("aviso-detalhe").classList.toggle("hidden");
    $("aviso-toggle").textContent = aberto ? "ocultar detalhes" : "ver detalhes";
  });
}

init();
