// Papel bancário visto pelo FUNDO: LF, CDB e DPGE, papel a papel.
//
// A tela de Tesourarias agrega (prazo médio, spread médio, faixas). Aqui não se
// agrega nada: cada linha do detalhe é um emissor, uma data de vencimento e uma
// taxa — que é a granularidade em que a mesa negocia a rolagem.

const VAZIO = "—";
let DADOS = [];
let DETALHE = null;
const estado = { busca: "", ordenar: "valor" };
const filtro = { texto: "", tipo: "", ordem: "vencimento" };

const $ = (id) => document.getElementById(id);
// O nome da gestora vai dentro de um onclick com aspas simples; sem
// escapar, uma casa com apostrofo no nome quebraria o handler.
const esc = (s) => String(s).replaceAll("\\", "\\\\").replaceAll("'", "\\'");
const nInt = (v) => Number(v || 0).toLocaleString("pt-BR");

function fmtBRL(v) {
  const abs = Math.abs(v || 0);
  if (abs >= 1e12) return `R$ ${(abs / 1e12).toFixed(2)} tri`;
  if (abs >= 1e9) return `R$ ${(abs / 1e9).toFixed(2)} bi`;
  if (abs >= 1e6) return `R$ ${(abs / 1e6).toFixed(1)} mi`;
  if (abs >= 1e3) return `R$ ${(abs / 1e3).toFixed(0)} mil`;
  return `R$ ${abs.toFixed(0)}`;
}
const ou = (v, fmt) => (v === null || v === undefined) ? `<span class="text-slate-600">${VAZIO}</span>` : fmt(v);

const MESES = ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"];
function fmtMes(iso) {
  // Vazio é papel perpétuo (LFSC), não dado faltando — a distinção importa
  // numa agenda de vencimentos.
  if (!iso) return "perpétuo";
  const [a, m] = String(iso).split("-");
  const nome = MESES[Number(m) - 1];
  if (!nome || !a) return VAZIO;
  return `${nome}/${a}`;
}
function fmtPrazo(dias) {
  if (dias === null || dias === undefined) return VAZIO;
  return dias >= 365 ? `${(dias / 365).toFixed(1)}a` : `${Math.round(dias)}d`;
}

// A taxa vem do backend como UM número mais a forma que ele representa. As
// duas formas coexistem porque o mercado cota papel bancário das duas
// maneiras, e converter uma na outra dependeria do nível do CDI na data —
// produziria um número que ninguém negociou.
// Só três formas: prefixado entra na régua do corte junto com o pós-fixado,
// por decisão do negócio (ver `_taxa_e_forma` no backend).
const FORMA_TAXA = {
  cdi_spread: (t) => `CDI + ${t.toFixed(2)}%`,
  pct_di:     (t) => `${t.toFixed(1)}% do DI`,
  ipca:       (t) => `IPCA + ${t.toFixed(2)}%`,
};
function fmtTaxa(p) {
  const f = FORMA_TAXA[p.forma];
  if (!f || p.taxa === null || p.taxa === undefined) {
    return `<span class="text-slate-600">${VAZIO}</span>`;
  }
  return f(p.taxa);
}

const CORES_TIPO = { lf: "#34d399", cdb: "#60a5fa", dpge: "#fbbf24" };
function chipTipo(t) {
  const cor = CORES_TIPO[t] || "#64748b";
  return `<span class="px-1.5 py-0.5 rounded text-[10px] font-medium"
    style="background:${cor}22;color:${cor}">${t.toUpperCase()}</span>`;
}

async function init() {
  try {
    // Sem teto artificial: os KPIs somam sobre esta lista, e um recorte do
    // topo do ranking apresentado como total seria um erro silencioso. A busca
    // também é client-side, então um teto esconderia fundos da pesquisa.
    DADOS = await API.carteiraBancaria(20000);
    if (!DADOS.length) { semDados(); return; }
    renderCabecalho();
    renderKpis();
    renderTabela();
    ligarEventos();
  } catch (e) {
    const c = $("erro-api");
    c.classList.remove("hidden");
    c.innerHTML = `<strong>Não consegui falar com a API em ${API_BASE}.</strong> ` +
      `Suba o backend (o Iniciar.bat faz isso) e recarregue. ` +
      `<span class="font-mono text-red-400/70">${e.message}</span>`;
    $("hdr-meta").textContent = "API indisponível";
  }
}

function semDados() {
  $("hdr-meta").textContent = "sem carteira nesta carga";
  $("tabela").innerHTML = `<tr><td colspan="9" class="px-4 py-8 text-center text-slate-500">
    A carteira vem do CDA da CVM, que não está disponível nesta carga.<br>
    A tela aparece assim que o CDA for baixado — o dashboard segue funcionando sem ela.</td></tr>`;
}

function renderCabecalho() {
  const data = DADOS[0].carteira_data;
  $("hdr-meta").textContent =
    `${nInt(DADOS.length)} gestoras · carteira declarada à CVM em ${fmtMes(data)}`;
  $("aviso-resumo").textContent =
    `Posições de LF, CDB e DPGE declaradas no CDA de ${fmtMes(data)} — estoque na data-base, não emissão.`;
  $("rodape").textContent =
    `Captação e Resgate · Crédito Privado · carteira do CDA de ${fmtMes(data)}`;
}

function renderKpis() {
  const total = DADOS.reduce((s, f) => s + f.valor, 0);
  const venc = DADOS.reduce((s, f) => s + f.valor_venc_12m, 0);
  const papeis = DADOS.reduce((s, f) => s + f.posicoes, 0);
  const com = DADOS.filter(f => f.spread_cdi !== null);
  const peso = com.reduce((s, f) => s + f.valor, 0);
  const taxa = peso ? com.reduce((s, f) => s + f.spread_cdi * f.valor, 0) / peso : null;

  const kpi = (el, rot, v, sub) => {
    $(el).innerHTML = `<p class="text-xs text-slate-500 uppercase tracking-wide">${rot}</p>
      <p class="text-2xl font-semibold mt-1">${v}</p>
      <p class="text-xs mt-1 text-slate-500">${sub}</p>`;
  };
  kpi("kpi-total", "Estoque", fmtBRL(total), `${nInt(papeis)} papéis em carteira`);
  kpi("kpi-fundos", "Gestoras", nInt(DADOS.length), "com LF, CDB ou DPGE");
  kpi("kpi-taxa", "Taxa média", taxa ? `CDI + ${taxa.toFixed(2)}%` : VAZIO,
      "ponderada por volume, só pós-fixado");
  kpi("kpi-venc", "Vence em 12 meses", fmtBRL(venc),
      `${(venc / total * 100).toFixed(0)}% do estoque`);
}

function renderTabela() {
  const q = estado.busca.toLowerCase();
  const k = estado.ordenar;
  const peso = (f) => (f[k] === null || f[k] === undefined) ? -1 : f[k];
  const linhas = DADOS
    .filter(f => !q || f.gestora.toLowerCase().includes(q))
    .sort((a, b) => peso(b) - peso(a));

  if (!linhas.length) {
    $("tabela").innerHTML = `<tr><td colspan="9" class="px-4 py-6 text-center text-slate-600">
      Nenhuma gestora com esse nome.</td></tr>`;
    return;
  }

  $("tabela").innerHTML = linhas.map(f => `
    <tr class="row-hover border-t border-slate-800 cursor-pointer" onclick="abrirDossie('${esc(f.gestora)}')">
      <td class="px-4 py-2.5">
        <div class="truncate max-w-[320px] font-medium">${f.gestora}</div>
        <div class="text-[10px] text-slate-500">${nInt(f.fundos)} fundo${f.fundos > 1 ? "s" : ""}</div>
      </td>
      <td class="px-4 py-2.5 text-right font-mono">${fmtBRL(f.valor)}</td>
      <td class="px-4 py-2.5 text-center text-slate-400">${nInt(f.posicoes)}</td>
      <td class="px-4 py-2.5 text-center text-slate-400">${nInt(f.emissores)}</td>
      <td class="px-4 py-2.5 text-right font-mono">${ou(f.spread_cdi, v => `CDI + ${v.toFixed(2)}%`)}</td>
      <td class="px-4 py-2.5 text-center text-slate-400">${fmtPrazo(f.prazo_dias)}</td>
      <td class="px-4 py-2.5 text-right font-mono text-slate-400">${ou(f.pct_pl, v => v.toFixed(1) + "%")}</td>
      <td class="px-4 py-2.5 text-right font-mono text-slate-400">${fmtBRL(f.valor_venc_12m)}</td>
      <td class="px-4 py-2.5">${barraMix(f)}</td>
    </tr>`).join("");
}

function barraMix(f) {
  const partes = [["pct_lf", "lf"], ["pct_cdb", "cdb"], ["pct_dpge", "dpge"]];
  const titulo = partes.map(([k, t]) => `${t.toUpperCase()} ${((f[k] || 0) * 100).toFixed(0)}%`).join(" · ");
  return `<div class="flex h-1.5 w-24 rounded overflow-hidden mx-auto" title="${titulo}">
    ${partes.map(([k, t]) => `<div style="width:${(f[k] || 0) * 100}%;background:${CORES_TIPO[t]}"></div>`).join("")}
  </div>`;
}

// ===== Detalhe =====
async function abrirDossie(gestora) {
  DETALHE = await API.carteiraBancariaGestora(gestora).catch(() => null);
  if (!DETALHE) return;
  const f = DETALHE.gestora;

  $("d-nome").textContent = f.gestora;
  $("d-meta").textContent =
    `${nInt(f.fundos)} fundo${f.fundos > 1 ? "s" : ""} · ${nInt(f.posicoes)} papéis ` +
    `de ${nInt(f.emissores)} emissores · carteira de ${fmtMes(f.carteira_data)}`;

  const kpis = [
    ["Estoque", fmtBRL(f.valor), f.pct_pl !== null ? `${f.pct_pl.toFixed(1)}% do PL` : ""],
    ["Taxa média", f.spread_cdi !== null ? `CDI + ${f.spread_cdi.toFixed(2)}%` : VAZIO, "só pós-fixado"],
    ["Prazo médio", fmtPrazo(f.prazo_dias), "a partir de hoje"],
    // As duas janelas de rolagem, lado a lado: 3 meses é o que precisa de
    // conversa agora, 12 meses é o horizonte do ano.
    ["Vence em 3m", fmtBRL(f.valor_venc_3m),
     f.valor ? `${(f.valor_venc_3m / f.valor * 100).toFixed(0)}% do estoque` : ""],
    ["Vence em 12m", fmtBRL(f.valor_venc_12m),
     f.valor ? `${(f.valor_venc_12m / f.valor * 100).toFixed(0)}% do estoque` : ""],
  ];
  $("d-kpis").innerHTML = kpis.map(([r, v, s]) =>
    `<div class="card rounded-lg p-3">
      <p class="text-[10px] text-slate-500 uppercase">${r}</p>
      <p class="text-base font-semibold mt-0.5">${v}</p>
      <p class="text-[10px] text-slate-600 mt-0.5">${s}</p>
    </div>`).join("");

  renderAgenda();
  renderEmissores();
  filtro.texto = ""; filtro.tipo = ""; filtro.ordem = "vencimento";
  $("d-filtro").value = ""; $("d-tipo").value = ""; $("d-ordem").value = "vencimento";
  renderPapeis();

  $("dossie-overlay").classList.remove("hidden");
  $("d-corpo").scrollTop = 0;
  requestAnimationFrame(() => {
    $("dossie-overlay").style.opacity = "1";
    $("dossie").classList.remove("dossie-closed");
    $("dossie").classList.add("dossie-open");
  });
}

// A agenda é o eixo que a mesa usa: quanto vence em cada mês/ano. Barras em
// vez de tabela porque a pergunta é comparativa — onde está a concentração.
function renderAgenda() {
  const meses = DETALHE.por_mes;
  const max = Math.max(...meses.map(m => m.valor)) || 1;
  $("d-agenda").innerHTML = meses.map(m => `
    <div class="w-8 flex flex-col justify-end h-24 cursor-pointer" onclick="filtrarMes('${m.mes}')"
         title="${fmtMes(m.mes)}: ${fmtBRL(m.valor)} em ${m.posicoes} papéis">
      <div style="height:${Math.max(m.valor / max * 100, 2)}%;background:#34d399" class="rounded-t"></div>
    </div>`).join("");
  $("d-agenda-labels").innerHTML = meses.map(m => {
    const [a, mm] = m.mes.split("-");
    return `<div class="w-8 text-center text-[8px] text-slate-500 leading-tight">
      ${MESES[Number(mm) - 1]}<br><span class="text-slate-600">${a.slice(2)}</span></div>`;
  }).join("");
}

function filtrarMes(mes) {
  filtro.texto = "";
  $("d-filtro").value = "";
  filtro.mes = filtro.mes === mes ? null : mes;
  renderPapeis();
}

function renderEmissores() {
  $("d-emissores").innerHTML = DETALHE.por_emissor.slice(0, 14).map(e => `
    <button type="button" onclick="filtrarEmissor('${e.nome.replace(/'/g, "\\'")}')"
      class="pill px-2 py-1 rounded bg-slate-900 border border-slate-800 hover:border-emerald-700 text-[10px]"
      title="${e.spread ? `CDI + ${e.spread.toFixed(2)}%` : "sem taxa declarada"}">
      <span class="text-slate-300">${e.nome}</span>
      <span class="text-slate-500 ml-1">${fmtBRL(e.valor)}</span>
      <span class="text-slate-600 ml-1">${e.pct_do_bancario !== null ? e.pct_do_bancario.toFixed(0) + "%" : ""}</span>
    </button>`).join("");
}

function filtrarEmissor(nome) {
  filtro.mes = null;
  filtro.texto = filtro.texto === nome ? "" : nome;
  $("d-filtro").value = filtro.texto;
  renderPapeis();
}

function renderPapeis() {
  let lista = DETALHE.posicoes;
  const q = filtro.texto.toLowerCase();
  if (q) lista = lista.filter(p => p.emissor.toLowerCase().includes(q));
  if (filtro.tipo) lista = lista.filter(p => p.instrumento === filtro.tipo);
  if (filtro.mes) lista = lista.filter(p => p.mes_venc === filtro.mes);

  const ordens = {
    vencimento: (a, b) => a.mes_venc.localeCompare(b.mes_venc) || b.valor - a.valor,
    valor: (a, b) => b.valor - a.valor,
    // Ordenar por taxa só compara dentro da mesma forma: "102% do DI" e
    // "CDI + 1,35%" não são grandezas comparáveis, e misturá-las na ordenação
    // jogaria todo o percentual do DI para o topo como se fosse o mais caro.
    spread: (a, b) => (a.forma || "").localeCompare(b.forma || "")
                      || (b.taxa ?? -1) - (a.taxa ?? -1),
    emissor: (a, b) => a.emissor.localeCompare(b.emissor) || a.mes_venc.localeCompare(b.mes_venc),
  };
  lista = [...lista].sort(ordens[filtro.ordem] || ordens.vencimento);

  const total = lista.reduce((s, p) => s + p.valor, 0);
  $("d-papeis-n").textContent =
    `· ${nInt(lista.length)} de ${nInt(DETALHE.posicoes.length)} · ${fmtBRL(total)}` +
    (filtro.mes ? ` · vencendo em ${fmtMes(filtro.mes)}` : "");

  if (!lista.length) {
    $("d-papeis").innerHTML = `<tr><td colspan="7" class="px-3 py-4 text-center text-slate-600">
      Nenhum papel com esse filtro.</td></tr>`;
    return;
  }

  const totalGestora = DETALHE.gestora.valor || 1;
  $("d-papeis").innerHTML = lista.map(p => `
    <tr class="border-t border-slate-800/50">
      <td class="px-3 py-1.5 text-center">${chipTipo(p.instrumento)}</td>
      <td class="px-3 py-1.5">
        <div class="flex items-center gap-1 max-w-[13rem]">
          <span class="truncate" title="${p.emissor}">${p.emissor}</span>
          ${p.ligado ? `<span class="badge-warn px-1 rounded text-[9px] shrink-0"
               title="${(p.pct_ligado ?? 100).toFixed(0)}% deste bloco é posição intragrupo (emissor ligado à casa)">grupo</span>` : ""}
        </div>
      </td>
      <td class="px-3 py-1.5 text-center font-mono text-slate-400 whitespace-nowrap">${fmtMes(p.mes_venc)}</td>
      <td class="px-3 py-1.5 text-center text-slate-600">${p.papeis > 1 ? p.papeis : ""}</td>
      <td class="px-3 py-1.5 text-right font-mono whitespace-nowrap">${fmtTaxa(p)}</td>
      <td class="px-3 py-1.5 text-right font-mono whitespace-nowrap">${fmtBRL(p.valor)}</td>
      <td class="px-3 py-1.5 text-right font-mono text-slate-600 whitespace-nowrap">${(p.valor / totalGestora * 100).toFixed(1)}%</td>
    </tr>`).join("");
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
  $("d-filtro").addEventListener("input", e => { filtro.texto = e.target.value; filtro.mes = null; renderPapeis(); });
  $("d-tipo").addEventListener("change", e => { filtro.tipo = e.target.value; renderPapeis(); });
  $("d-ordem").addEventListener("change", e => { filtro.ordem = e.target.value; renderPapeis(); });
  document.addEventListener("keydown", e => { if (e.key === "Escape") fecharDossie(); });
  $("aviso-toggle").addEventListener("click", () => {
    const aberto = !$("aviso-detalhe").classList.toggle("hidden");
    $("aviso-toggle").textContent = aberto ? "ocultar detalhes" : "ver detalhes";
  });
}

init();
