// ===== Estado e utilidades =====
const state = { janela: "semanal", indexador: "todos", abertos: true, sortBy: "semestral", search: "", moversDir: "pos", view: "fluxo" };
let DATA = null;        // payload /api/dashboard
let sparkChart = null;
let timelineChart = null;

function fmtBRL(v) {
  const abs = Math.abs(v);
  const s = v < 0 ? "−" : (v > 0 ? "+" : "");
  // O PL total do universo passa de R$ 2 tri — sem esta faixa vira "2334 bi".
  if (abs >= 1e12) return `${s}R$ ${(abs / 1e12).toFixed(2)} tri`;
  if (abs >= 1e9) return `${s}R$ ${(abs / 1e9).toFixed(2)} bi`;
  if (abs >= 1e6) return `${s}R$ ${(abs / 1e6).toFixed(1)} mi`;
  if (abs >= 1e3) return `${s}R$ ${(abs / 1e3).toFixed(0)} mil`;
  return `${s}R$ ${abs.toFixed(0)}`;
}
function cls(v) { return v > 0 ? "num-pos" : (v < 0 ? "num-neg" : "num-neutral"); }

// `null` da API significa "não temos esse dado" — nunca zero. Tudo que possa
// vir vazio passa por aqui, para o dashboard nunca exibir um zero inventado.
const VAZIO = "—";
function ou(v, fmt) { return (v === null || v === undefined) ? VAZIO : fmt(v); }
function pct(n) {
  return `${n.toLocaleString("pt-BR")} de ${DATA.total_fundos.toLocaleString("pt-BR")} (${(n / DATA.total_fundos * 100).toFixed(0)}%)`;
}
function fmtData(iso) {
  if (!iso) return VAZIO;
  const [a, m, d] = iso.slice(0, 10).split("-");
  return `${d}/${m}/${a}`;
}
// "2026-04" -> "abr/2026". A carteira do CDA é mensal e vem defasada; mostrar
// o mês por extenso deixa a defasagem óbvia na leitura.
const MESES_CURTOS = ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"];
function fmtMes(iso) {
  if (!iso) return VAZIO;
  const [a, m] = iso.split("-");
  return `${MESES_CURTOS[Number(m) - 1] || m}/${a}`;
}

// Vocabulário único dos buckets: rótulo, cor, classe de badge e o nome do
// campo agregado correspondente. Antes isso estava espalhado por cinco pontos
// da tela, e renomear "IPCA+" para "Incentivada" exigia acertar os cinco.
const BUCKETS = {
  incentivada: { rotulo: "Incentivada", curto: "Incentivada", cor: "#a855f7", bg: "bg-purple-500", badge: "badge-incentivada", texto: "text-purple-400" },
  tradicional: { rotulo: "Tradicional", curto: "Tradicional", cor: "#3b82f6", bg: "bg-blue-500", badge: "badge-tradicional", texto: "text-blue-400" },
  lf:          { rotulo: "Letras Financeiras", curto: "LF", cor: "#f59e0b", bg: "bg-amber-500", badge: "badge-lf", texto: "text-amber-400" },
  misto:       { rotulo: "Misto", curto: "Misto", cor: "#64748b", bg: "bg-slate-500", badge: "badge-misto", texto: "text-slate-400" },
};
const ORDEM_BUCKETS = ["incentivada", "tradicional", "lf", "misto"];

const nomesFonte = { vinculado: "planilha do e-mail (Quantum Axis)", cvm: "dados abertos da CVM", mock: "MOCK — valores sintéticos" };

// Nome amigável dos campos que a fonte não entrega, para o aviso do topo.
const rotuloCampo = {
  pl: "PL", indexador: "classificação por indexador", duration: "duration",
  cotizacao_resgate: "cotização", taxa_adm: "taxa de adm.",
  aberto_captacao: "status de captação", premio_ipca: "prêmio IPCA+",
  premio_cdi: "prêmio CDI+",
};

// Há classificação por indexador nesta carga? Governa tabs, buckets e mix.
function temIndexador() { return DATA.total_sem_classificacao < DATA.total_fundos; }

// ===== Boot =====
async function init() {
  try {
    await refreshData();
    bindEvents();
  } catch (e) {
    document.body.insertAdjacentHTML("afterbegin",
      `<div class="bg-red-950 text-red-300 text-sm p-3 text-center">
        Não consegui falar com a API em ${API_BASE}. Suba o backend (uvicorn app.main:app --port 8000) e recarregue.<br>
        <span style="color: yellow; font-family: monospace;">ERRO JS: ${e.message}</span>
      </div>`);
    console.error(e);
  }
}

async function refreshData() {
  DATA = await API.dashboard(state);
  renderFonte();
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

// ===== Fonte dos dados =====
function renderFonte() {
  const f = DATA.fonte;
  const nome = nomesFonte[f.fonte] || f.fonte;

  const rodape = document.getElementById("rodape");
  if (rodape) {
    rodape.textContent = [
      "Captação e Resgate · Crédito Privado",
      `fluxo: ${nome}${f.recebido_em ? ", recebido em " + fmtData(f.recebido_em) : ""}`,
      f.cvm_ativo ? `PL: CVM${f.pl_data_max ? " em " + fmtData(f.pl_data_max) : ""}` : null,
    ].filter(Boolean).join(" · ");
  }

  const aviso = document.getElementById("fonte-aviso");
  const linhas = [];

  if (f.campos_indisponiveis.length) {
    const faltando = f.campos_indisponiveis.map(c => rotuloCampo[c] || c).join(", ");
    linhas.push(
      `<strong>Sem a API do Quantum Axis</strong>, esta carga não traz: ${faltando}. ` +
      `Esses campos aparecem como “${VAZIO}” — nenhum número foi estimado.`);
  }
  // Cada base da CVM tem a sua cobertura e a sua defasagem. Dizer isso junto
  // evita que o usuário leia "taxa adm 0,45%" como se valesse para todos.
  const parciais = [
    f.fundos_com_extrato && `taxa de adm., cotização, aplicação mínima e público-alvo em ${pct(f.fundos_com_extrato)}`,
    f.fundos_com_perfil && `% em crédito privado, prazo da carteira e perfil de cotistas em ${pct(f.fundos_com_perfil)}`,
    f.fundos_com_rentab && `rentabilidade e volatilidade em ${pct(f.fundos_com_rentab)}`,
  ].filter(Boolean);
  if (parciais.length) {
    linhas.push(
      `<strong>Cobertura parcial por campo:</strong> ${parciais.join("; ")}. ` +
      `A declaração de taxa e cotização é anual — pode ser de um ano anterior, ` +
      `e cada fundo mostra a data no dossiê.`);
  }
  // O PL vem do casamento por CNPJ com a CVM, que não cobre todo mundo. Um
  // total parcial apresentado como total é erro de leitura garantido.
  if (f.cvm_ativo && f.pl_cobertura_pct !== null && f.pl_cobertura_pct < 99) {
    linhas.push(
      `<strong>PL da CVM cobre ${f.pl_cobertura_pct.toFixed(0)}% dos fundos</strong> ` +
      `(${f.fundos_com_pl.toLocaleString("pt-BR")} de ${DATA.total_fundos.toLocaleString("pt-BR")}; ` +
      `${(f.fundos_sem_pl ?? 0).toLocaleString("pt-BR")} não constam nas bases da CVM). ` +
      `Todo número em R$ de PL, %PL e PL investível se refere só a esse recorte; ` +
      `os fluxos cobrem o universo inteiro.`);
  }
  // A carteira é o bucket. Duas coisas precisam ser ditas: de que mês ela é
  // (vem defasada de propósito) e que não é o mesmo eixo do perfil da cota.
  if (f.fundos_com_carteira) {
    linhas.push(
      `<strong>A classificação (LF / Incentivada / Tradicional / Misto) vem da carteira ` +
      `declarada à CVM</strong> no CDA de ${fmtMes(f.carteira_data)}, cruzada com o registro ` +
      `de debêntures do SND e com as posições em futuro de DAP. ` +
      `${pct(f.fundos_com_carteira)}. A defasagem é proposital: no mês corrente ` +
      `46% do PL fica sob sigilo e a carteira visível seria uma amostra enviesada. ` +
      `<em>Incentivada</em> exige duas coisas ao mesmo tempo: o nome do fundo trazer ` +
      `“Incentivada/o” e a carteira operar IPCA+ de fato — comprar em B + spread e ` +
      `travar o cupom de inflação em DAP. Nome sem carteira que confirme cai em ` +
      `<em>Misto</em>, com o motivo visível no dossiê. ` +
      `Fundo com muito sigilo, indexador desconhecido em boa parte da carteira ou ` +
      `pouco crédito perto do PL fica <em>sem classificação</em> — nunca com bucket ` +
      `adivinhado.`);
  }
  if (f.fundos_com_perfil_indexador) {
    linhas.push(
      `<strong>Perfil de indexador não é a classificação por bucket.</strong> ` +
      `Ele mede a exposição do cotista — o benchmark que o fundo persegue, ou ` +
      `como a cota se comporta — e não a composição da carteira. Em crédito as ` +
      `duas divergem (comprar IPCA+ e travar em CDI via swap é rotina). ` +
      `${pct(f.fundos_com_perfil_indexador)}, sendo ` +
      `${(f.perfil_declarado ?? 0).toLocaleString("pt-BR")} por benchmark declarado ` +
      `e ${(f.perfil_inferido ?? 0).toLocaleString("pt-BR")} inferidos pela ` +
      `volatilidade (~87% de acerto contra benchmark conhecido).`);
  }

  if (!linhas.length) { 
    if (aviso) aviso.classList.add("hidden"); 
    return; 
  }
  // Resumo de uma linha; o texto completo fica atrás do "ver detalhes". O
  // banner precisa ser lido todo dia — três parágrafos deixam de ser lidos.
  const resumo = [
    "Fluxo 100%",
    f.pl_cobertura_pct !== null ? `PL ${f.pl_cobertura_pct.toFixed(0)}%` : null,
    f.fundos_com_extrato ? `taxa/cotização ${Math.round(f.fundos_com_extrato / DATA.total_fundos * 100)}%` : null,
    f.fundos_com_perfil_indexador ? `perfil de indexador ${Math.round(f.fundos_com_perfil_indexador / DATA.total_fundos * 100)}%` : null,
    f.fundos_com_carteira
      ? `bucket ${Math.round(f.fundos_com_carteira / DATA.total_fundos * 100)}% (carteira de ${fmtMes(f.carteira_data)})`
      : (temIndexador() ? null : "bucket indisponível"),
  ].filter(Boolean).join(" · ");

  aviso.className = "bg-amber-950/40 border-b border-amber-900/50 text-amber-200/90 text-xs px-6 py-2";
  aviso.innerHTML = `<div class="max-w-[1400px] mx-auto">
      <div class="flex items-baseline gap-2 flex-wrap">
        <span>Cobertura dos dados: ${resumo}.</span>
        <button type="button" class="underline underline-offset-2 hover:text-amber-100" id="aviso-toggle">ver detalhes</button>
      </div>
      <div class="hidden space-y-1 mt-2 pt-2 border-t border-amber-900/40" id="aviso-detalhe">
        ${linhas.map(l => `<div>${l}</div>`).join("")}
      </div>
    </div>`;
  aviso.classList.remove("hidden");

  const botao = document.getElementById("aviso-toggle");
  const detalhe = document.getElementById("aviso-detalhe");
  if (botao && detalhe) {
    botao.addEventListener("click", () => {
      const aberto = !detalhe.classList.toggle("hidden");
      botao.textContent = aberto ? "ocultar detalhes" : "ver detalhes";
    });
  }
}

function renderHeader() {
  const f = DATA.fonte;
  const partes = [
    `Referência ${fmtData(DATA.data_referencia)}`,
    `${DATA.total_fundos.toLocaleString("pt-BR")} fundos de crédito privado`,
    `${DATA.total_gestoras} gestoras`,
  ];
  if (f.descartados_nao_credito)
    partes.push(`<span class="text-slate-600">${f.descartados_nao_credito} fora do universo</span>`);
  document.getElementById("hdr-meta").innerHTML = partes.join(" · ");
}

function renderKpisFluxo() {
  const k = DATA.kpis_fluxo;

  let comparacao = `<span class="text-slate-600">sem período anterior para comparar</span>`;
  if (k.fluxo_liquido_anterior !== null && k.fluxo_liquido_anterior !== undefined) {
    const delta = k.fluxo_liquido - k.fluxo_liquido_anterior;
    comparacao = `<span class="${cls(delta)}">${delta >= 0 ? "▲" : "▼"} ${fmtBRL(Math.abs(delta)).replace("+", "")}</span> vs. semana anterior`;
  }
  document.getElementById("kpi-fluxo").innerHTML = `
    <p class="text-xs text-slate-500 uppercase tracking-wide">Fluxo líquido · janela</p>
    <p class="text-2xl font-semibold mt-1 ${cls(k.fluxo_liquido)}">${fmtBRL(k.fluxo_liquido)}</p>
    <p class="text-xs mt-1">${comparacao}</p>`;

  document.getElementById("kpi-capt").innerHTML = `
    <p class="text-xs text-slate-500 uppercase tracking-wide">Captação bruta</p>
    <p class="text-2xl font-semibold mt-1 num-pos">${fmtBRL(k.captacao_bruta)}</p>
    <p class="text-xs mt-1 text-slate-500">${k.fundos_entrada.toLocaleString("pt-BR")} fundos com entrada</p>`;

  document.getElementById("kpi-resg").innerHTML = `
    <p class="text-xs text-slate-500 uppercase tracking-wide">Resgates</p>
    <p class="text-2xl font-semibold mt-1 num-neg">${fmtBRL(k.resgates)}</p>
    <p class="text-xs mt-1 text-slate-500">${k.fundos_saida.toLocaleString("pt-BR")} fundos com saída</p>`;

  // "PL total coberto" é literal: cobre só os fundos que casaram com a CVM.
  const f = DATA.fonte;
  let rodapePl = "aguardando PL";
  if (k.pl_total !== null) {
    rodapePl = k.pl_var_30d_pct !== null
      ? `<span class="${cls(k.pl_var_30d_pct)}">${k.pl_var_30d_pct > 0 ? "▲" : "▼"} ${Math.abs(k.pl_var_30d_pct).toFixed(1)}%</span> nos últimos 30d`
      : `${f.fundos_com_pl.toLocaleString("pt-BR")} de ${DATA.total_fundos.toLocaleString("pt-BR")} fundos (${(f.pl_cobertura_pct ?? 0).toFixed(0)}%)`;
  }
  document.getElementById("kpi-pl").innerHTML = `
    <p class="text-xs text-slate-500 uppercase tracking-wide">PL total coberto</p>
    <p class="text-2xl font-semibold mt-1 ${k.pl_total === null ? "text-slate-600" : ""}">${ou(k.pl_total, v => fmtBRL(v).replace("+", ""))}</p>
    <p class="text-xs mt-1 text-slate-500">${rodapePl}</p>`;
}

function renderKpisMesa() {
  const k = DATA.kpis_mesa;
  if (k.fundos_abertos === null || k.fundos_abertos === undefined) {
    document.getElementById("kpi-abertos").innerHTML = `
      <p class="text-[10px] text-slate-500 uppercase tracking-wide">★ Fundos abertos p/ captação</p>
      <p class="text-2xl font-semibold mt-1 text-slate-600">${VAZIO}</p>
      <p class="text-[10px] mt-0.5 text-slate-500">status de captação e PL investível vêm do Quantum Axis</p>`;
    return;
  }
  document.getElementById("kpi-abertos").innerHTML = `
    <p class="text-[10px] text-emerald-400 uppercase tracking-wide">★ Fundos abertos p/ captação</p>
    <p class="text-2xl font-semibold mt-1">${k.fundos_abertos.toLocaleString("pt-BR")}</p>
    <p class="text-[10px] mt-0.5 text-slate-500">${(k.fundos_abertos_pct * 100).toFixed(0)}% do universo · ${ou(k.pl_investivel, v => fmtBRL(v).replace("+", "") + " investível")}</p>`;
}

function renderBuckets() {
  const box = document.getElementById("buckets");
  const vazio = document.getElementById("buckets-vazio");

  if (!temIndexador()) {
    box.innerHTML = "";
    vazio.textContent =
      `Os ${DATA.total_fundos.toLocaleString("pt-BR")} fundos desta carga estão sem classificação: ` +
      `a planilha do e-mail traz o fluxo, mas não a composição da carteira. A quebra ` +
      `LF / Incentivada / Tradicional / Misto volta assim que a API do Quantum Axis for liberada.`;
    vazio.classList.remove("hidden");
    return;
  }

  vazio.classList.add("hidden");
  const maxAbs = Math.max(...DATA.buckets.map(b => Math.abs(b.fluxo))) || 1;
  box.innerHTML = DATA.buckets.map(b => {
    const pct = Math.abs(b.fluxo) / maxAbs * 100;
    const detalhes = [
      `${b.fundos.toLocaleString("pt-BR")} fundos`,
      b.pct_pl !== null ? `${(b.pct_pl * 100).toFixed(0)}% do PL` : null,
      b.duration_media !== null ? `dur. ${b.duration_media.toFixed(1)}a` : null,
      b.cotizacao_media !== null ? `cot. D+${b.cotizacao_media}` : null,
      b.pct_abertos !== null ? `${(b.pct_abertos * 100).toFixed(0)}% abertos` : null,
    ].filter(Boolean).join(" · ");
    const meta = BUCKETS[b.bucket] || { rotulo: b.bucket, bg: "bg-slate-500" };
    return `<div class="group cursor-pointer hover:bg-slate-900/50 p-2 -mx-2 rounded transition-colors" onclick="openIdxDossie('${b.bucket}')">
      <div class="flex items-center justify-between text-xs mb-1">
        <span class="flex items-center gap-2 group-hover:text-blue-400"><span class="w-2 h-2 rounded-full ${meta.bg}"></span>${meta.rotulo}</span>
        <span class="${cls(b.fluxo)} font-mono">${fmtBRL(b.fluxo)}</span>
      </div>
      <div class="h-1.5 bg-slate-800 rounded"><div class="h-full ${meta.bg} rounded" style="width:${pct}%"></div></div>
      <p class="text-[10px] text-slate-500 mt-1">${detalhes}</p>
    </div>`;
  }).join("");
}

function renderTimeline() {
  const s = DATA.serie_temporal;
  const legenda = document.getElementById("tl-legend");
  const titulo = document.getElementById("tl-title");

  if (!s.length) {
    titulo.textContent = "Fluxo líquido semanal";
    legenda.innerHTML = `<span class="text-slate-600">esta fonte não traz série histórica</span>`;
    if (timelineChart) { timelineChart.destroy(); timelineChart = null; }
    return;
  }

  titulo.textContent = `Fluxo líquido semanal · últimas ${s.length} semanas`;

  // Quebra por indexador só existe quando há composição de carteira. Sem ela,
  // a série vira uma linha só: o fluxo líquido total.
  const porIndexador = s[0].incentivada !== null && s[0].incentivada !== undefined;
  const series = porIndexador
    ? ORDEM_BUCKETS.map(k => ({ label: BUCKETS[k].curto, key: k, cor: BUCKETS[k].cor }))
    : [{ label: "Fluxo líquido", key: "total", cor: "#3b82f6" }];

  legenda.innerHTML = series.map(d =>
    `<label class="flex items-center gap-1"><span class="w-2 h-2 rounded-full" style="background:${d.cor}"></span>${d.label}</label>`
  ).join("");

  if (timelineChart) timelineChart.destroy();
  timelineChart = new Chart(document.getElementById("chart-timeline"), {
    type: "line",
    data: {
      labels: s.map(p => p.curto),
      datasets: series.map(d => ({
        label: d.label,
        data: s.map(p => p[d.key]),
        borderColor: d.cor,
        backgroundColor: d.cor + "1a",
        fill: !porIndexador,
        tension: .35, borderWidth: 2, pointRadius: 0, pointHoverRadius: 4,
      })),
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: {
        legend: { display: false },
        tooltip: {
          backgroundColor: "#1e293b", borderColor: "#334155", borderWidth: 1,
          callbacks: {
            title: itens => s[itens[0].dataIndex].rotulo,
            label: c => `${c.dataset.label}: ${fmtBRL(c.parsed.y)}`,
          },
        },
      },
      scales: {
        x: { ticks: { color: "#64748b", font: { size: 10 }, maxTicksLimit: 14 }, grid: { color: "rgba(148,163,184,.05)" } },
        y: { ticks: { color: "#64748b", font: { size: 10 }, callback: v => fmtBRL(v) }, grid: { color: "rgba(148,163,184,.05)" } },
      },
    },
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
  document.getElementById("top-cap").innerHTML = sorted.slice(0, 10).map(g => bar(g, "bg-emerald-500")).join("");
  document.getElementById("top-res").innerHTML = sorted.slice(-10).reverse().map(g => bar(g, "bg-red-500")).join("");
}

// Colunas por visão. Uma tabela única com tudo teria 16 colunas e nenhuma
// legível; o broker escolhe a lente conforme a pergunta que está fazendo.
const COLUNAS = {
  fluxo: [
    ["Fundos", "c", g => g.fundos],
    ["PL", "d", g => ou(g.pl, v => fmtBRL(v).replace("+", ""))],
    ["Mix da classificação", "c", g => barraMix(g)],
    ["Diária", "d", g => num(g.diaria)],
    ["Semanal", "d", g => num(g.semanal)],
    ["Mensal", "d", g => num(g.mensal)],
    ["Semestral", "d", g => num(g.semestral)],
  ],
  mesa: [
    ["Fundos", "c", g => g.fundos],
    ["PL", "d", g => ou(g.pl, v => fmtBRL(v).replace("+", ""))],
    ["Share", "d", g => ou(g.share_pl_pct, v => v.toFixed(2) + "%")],
    ["Taxa adm", "d", g => ou(g.taxa_adm_media, v => v.toFixed(2) + "%")],
    ["Cotização", "c", g => ou(g.cotizacao_media, v => "D+" + v)],
    ["Prazo cart.", "c", g => ou(g.prazo_carteira_dias, v => Math.round(v) + "d")],
    // Em que instrumento a casa entra — é o que decide se ela é cliente de
    // debênture ou de papel bancário, e isso não sai do bucket.
    ["Papel", "c", g => barraPapel(g)],
    // Zero aqui é informação, não ausência: gestora cujo fundo típico não tem
    // aplicação mínima é exatamente o que se procura para o cliente pequeno.
    ["Aplic. típica", "d", g => ou(g.aplicacao_minima, v =>
      v === 0 ? `<span class="num-pos">sem mín.</span>` : fmtBRL(v).replace("+", ""))],
    ["Semestral", "d", g => num(g.semestral)],
  ],
  perf: [
    ["Fundos", "c", g => g.fundos],
    ["PL", "d", g => ou(g.pl, v => fmtBRL(v).replace("+", ""))],
    ["Rentab.", "d", g => ou(g.rentab_media_pct, v => `<span class="${cls(v)}">${v.toFixed(2)}%</span>`)],
    ["Vol.", "d", g => ou(g.vol_media_pct, v => v.toFixed(2) + "%")],
    ["Pós / inflação", "c", g => barraPerfilIdx(g)],
    ["% cred. priv.", "d", g => ou(g.pct_credito_privado, v => v.toFixed(0) + "%")],
    ["Prazo cart.", "c", g => ou(g.prazo_carteira_dias, v => Math.round(v) + "d")],
    ["Semestral", "d", g => num(g.semestral)],
  ],
  dist: [
    ["Fundos", "c", g => g.fundos],
    ["PL", "d", g => ou(g.pl, v => fmtBRL(v).replace("+", ""))],
    ["Cotistas", "d", g => ou(g.cotistas, v => v.toLocaleString("pt-BR"))],
    ["Δ cotistas", "d", g => ou(g.cotistas_var_pct, v => `<span class="${cls(v)}">${v > 0 ? "+" : ""}${v.toFixed(1)}%</span>`)],
    ["Público-alvo", "e", g => `<span class="text-slate-400">${g.publico_alvo || VAZIO}</span>`],
    ["Quem compra", "e", g => barraPerfil(g.perfil_cotistas)],
    ["Semestral", "d", g => num(g.semestral)],
  ],
};

// Cores por tipo de investidor, para a barra de "quem compra".
const CORES_PERFIL = {
  pf_private: ["#8b5cf6", "PF private"], pf_varejo: ["#a78bfa", "PF varejo"],
  pj_private: ["#3b82f6", "PJ private"], pj_varejo: ["#60a5fa", "PJ varejo"],
  fundo_pensao: ["#10b981", "Fundo de pensão"], rpps: ["#34d399", "RPPS"],
  previdencia_aberta: ["#14b8a6", "Prev. aberta"], seguradora: ["#0ea5e9", "Seguradora"],
  banco: ["#f59e0b", "Banco"], corretora: ["#fbbf24", "Corretora"],
  distribuidor: ["#fcd34d", "Distribuidor"], nao_residente: ["#ef4444", "Não residente"],
};

function num(v) { return `<span class="${cls(v)}">${fmtBRL(v)}</span>`; }

function barraMix(g) {
  if (g.mix_incentivada === null || g.mix_incentivada === undefined) {
    return `<span class="text-slate-600">${VAZIO}</span>`;
  }
  const titulo = ORDEM_BUCKETS
    .map(k => `${BUCKETS[k].curto} ${((g[`mix_${k}`] || 0) * 100).toFixed(0)}%`).join(" · ");
  return `<div class="flex h-1.5 w-28 rounded overflow-hidden mx-auto" title="${titulo}">
    ${ORDEM_BUCKETS.map(k => `<div style="width:${(g[`mix_${k}`] || 0) * 100}%" class="${BUCKETS[k].bg}"></div>`).join("")}</div>`;
}

// Mix por instrumento. O trecho cinza ao fim é o que não é debênture/LF/CDB/
// CRI — fica visível de propósito, para a barra não sugerir que a leitura da
// carteira foi completa quando não foi.
const PAPEIS = [["papel_debenture", "#a78bfa", "Debênture"], ["papel_lf", "#fbbf24", "LF"],
                ["papel_cdb", "#60a5fa", "CDB/DPGE"], ["papel_cri_cra", "#34d399", "CRI/CRA/NP"]];
function barraPapel(g) {
  if (g.papel_debenture === null || g.papel_debenture === undefined) {
    return `<span class="text-slate-600">${VAZIO}</span>`;
  }
  const titulo = PAPEIS.map(([k, , rot]) => `${rot} ${((g[k] || 0) * 100).toFixed(0)}%`).join(" · ");
  return `<div class="flex h-1.5 w-28 rounded overflow-hidden mx-auto" title="${titulo}">
    ${PAPEIS.map(([k, c]) => `<div style="width:${(g[k] || 0) * 100}%;background:${c}"></div>`).join("")}
    <div class="flex-1" style="background:#1e293b"></div></div>`;
}

// Perfil de indexador observado. Cores propositalmente diferentes das do mix
// por bucket (roxo/azul/âmbar): são medidas diferentes e não podem ser lidas
// como a mesma coisa.
function barraPerfilIdx(g) {
  const pos = g.perfil_pos_pct, inf = g.perfil_inflacao_pct;
  if (pos === null || pos === undefined) return `<span class="text-slate-600">${VAZIO}</span>`;
  return `<div class="flex h-1.5 w-24 rounded overflow-hidden mx-auto"
      title="Pós-fixado ${pos.toFixed(0)}% · Inflação ${inf.toFixed(0)}%${g.perfil_indefinido ? ` · ${g.perfil_indefinido} sem perfil` : ""}">
    <div style="width:${pos}%;background:#22d3ee"></div>
    <div style="width:${inf}%;background:#f472b6"></div>
  </div>`;
}

function barraPerfil(perfil) {
  const itens = Object.entries(perfil || {}).sort((a, b) => b[1] - a[1]);
  if (!itens.length) return `<span class="text-slate-600">${VAZIO}</span>`;
  const titulo = itens.map(([k, v]) => `${(CORES_PERFIL[k] || [, k])[1]} ${v.toFixed(0)}%`).join(" · ");
  return `<div class="flex h-1.5 w-32 rounded overflow-hidden" title="${titulo}">
    ${itens.map(([k, v]) => `<div style="width:${v}%;background:${(CORES_PERFIL[k] || ["#475569"])[0]}"></div>`).join("")}
  </div>`;
}

function renderTable() {
  const cols = COLUNAS[state.view] || COLUNAS.fluxo;
  const alinha = { c: "text-center", d: "text-right font-mono", e: "text-left" };

  document.getElementById("table-head").innerHTML =
    `<th class="px-4 py-2 font-medium">Gestora</th>` +
    cols.map(([rot, a]) => `<th class="px-4 py-2 font-medium ${alinha[a]}">${rot}</th>`).join("");

  const q = state.search.toLowerCase();
  const key = state.sortBy;
  // Ordena por magnitude (um resgate de 5 bi importa tanto quanto uma captação
  // de 5 bi). Gestora sem o campo vai para o fim, nunca para o topo.
  const peso = g => (g[key] === null || g[key] === undefined) ? -1 : Math.abs(g[key]);
  const sorted = DATA.gestoras
    .filter(g => g.nome.toLowerCase().includes(q))
    .sort((a, b) => peso(b) - peso(a));

  document.getElementById("table-body").innerHTML = sorted.map(g =>
    `<tr class="row-hover border-t border-slate-800 cursor-pointer" onclick="openDossie('${esc(g.nome)}')">
      <td class="px-4 py-2.5 font-medium">${g.nome}</td>
      ${cols.map(([, a, fn]) => `<td class="px-4 py-2.5 ${alinha[a]}">${fn(g)}</td>`).join("")}
    </tr>`).join("");
}

async function renderMovers() {
  const movers = await API.movers(state.moversDir, 6, state);
  // Sem PL não há "variação de PL": a coluna passa a mostrar o fluxo da janela,
  // que é o dado real disponível, e o título acompanha.
  const porPL = movers.length > 0 && movers[0].var_pl_pct !== null;

  document.getElementById("movers-title").textContent =
    porPL ? "Top movers · variação % de PL" : "Top movers · fluxo da janela";
  document.getElementById("movers-sub").textContent =
    porPL ? "Gestoras com maior variação relativa na janela"
          : "Gestoras com maior fluxo líquido na janela (variação de PL exige Quantum Axis)";
  document.getElementById("movers-col").textContent = porPL ? "Var. PL" : "Fluxo";

  document.getElementById("movers-list").innerHTML = movers.map(g => {
    const valor = porPL
      ? `<span class="${cls(g.var_pl_pct)}">${g.var_pl_pct > 0 ? "+" : ""}${g.var_pl_pct.toFixed(1)}%</span>`
      : `<span class="${cls(g.fluxo)}">${fmtBRL(g.fluxo)}</span>`;
    return `<tr class="row-hover border-t border-slate-800/50 cursor-pointer" onclick="openDossie('${esc(g.nome)}')">
      <td class="px-4 py-2 truncate max-w-[160px]">${g.nome}</td>
      <td class="px-4 py-2 text-center text-slate-400">${ou(g.duration_media, v => v.toFixed(1) + "a")}</td>
      <td class="px-4 py-2 text-center text-slate-400">${ou(g.cotizacao_media, v => "D+" + v)}</td>
      <td class="px-4 py-2 text-right font-mono">${valor}</td>
    </tr>`;
  }).join("");
}

async function renderStress() {
  const stress = await API.stress(8, state);

  // A lista mistura as duas réguas: fundo com PL entra por "resgate ≥ 5% do
  // PL"; fundo sem PL (não casou com a CVM) entra por "resgate ≫ movimento
  // típico dele". Cada linha mostra o sinal que a qualificou, com o rótulo
  // junto — senão o número perde o significado.
  document.getElementById("stress-sub").textContent =
    "Resgate ≥ 5% do PL, ou ≫ movimento típico do fundo quando não há PL";
  document.getElementById("stress-c1").textContent = "PL";
  document.getElementById("stress-c2").textContent = "Sinal";
  document.getElementById("stress-c3").textContent = "Resgate";

  if (!stress.length) {
    document.getElementById("stress-list").innerHTML =
      `<tr><td colspan="4" class="px-4 py-4 text-center text-slate-600">Nenhum fundo acima do limiar nesta janela.</td></tr>`;
    return;
  }

  document.getElementById("stress-list").innerHTML = stress.map(f => {
    const sinal = f.resgate_pct_pl !== null
      ? `<span class="num-neg">${f.resgate_pct_pl.toFixed(1)}%</span><span class="text-slate-600"> do PL</span>`
      : ou(f.severidade, v => `<span class="num-neg">${v.toFixed(1)}×</span><span class="text-slate-600"> típico</span>`);
    return `<tr class="row-hover border-t border-slate-800/50">
      <td class="px-4 py-2"><div class="truncate max-w-[150px]">${f.nome}</div><div class="text-[10px] text-slate-600">${f.gestora}</div></td>
      <td class="px-4 py-2 text-center text-slate-400">${ou(f.pl, v => fmtBRL(v).replace("+", ""))}</td>
      <td class="px-4 py-2 text-center">${sinal}</td>
      <td class="px-4 py-2 text-right font-mono num-neg">${fmtBRL(f.resgate)}</td>
    </tr>`;
  }).join("");
}

// ===== Dossiê lateral =====
async function openDossie(nome) {
  const d = await API.dossie(nome, state);
  // A API responde 404 se a gestora sumiu do recorte atual (filtro de
  // indexador, por exemplo). Sem esta guarda o painel abre pela metade.
  if (!d || !d.gestora) {
    console.warn(`Gestora "${nome}" não está no recorte atual.`);
    return;
  }
  const g = d.gestora;
  document.getElementById("d-name").textContent = g.nome;
  document.getElementById("d-meta").textContent = [
    `${g.fundos} fundos`,
    g.abertos !== null ? `${g.abertos} abertos p/ captação` : null,
    d.classe_majoritaria ? `classe majoritária ${d.classe_majoritaria}` : null,
  ].filter(Boolean).join(" · ");

  document.getElementById("d-sem").innerHTML = `<span class="${cls(g.semanal)}">${fmtBRL(g.semanal)}</span>`;
  document.getElementById("d-semt").innerHTML = `<span class="${cls(g.semestral)}">${fmtBRL(g.semestral)}</span>`;
  document.getElementById("d-pl").textContent = ou(g.pl, v => fmtBRL(v).replace("+", ""));
  document.getElementById("d-abertos").innerHTML =
    ou(g.abertos, v => `${v} <span class="text-slate-500 text-xs">(${(v / g.fundos * 100).toFixed(0)}%)</span>`);
  // Duration de verdade só o Quantum dá; o prazo médio da carteira declarado
  // à CVM é o proxy disponível, e é rotulado como tal para não se confundirem.
  document.getElementById("d-dur").textContent =
    g.duration_media !== null ? `${g.duration_media.toFixed(1)} anos`
                              : ou(g.prazo_carteira_dias, v => `${Math.round(v)} dias`);
  document.getElementById("d-cot").textContent = ou(g.cotizacao_media, v => `D+${v}`);
  document.getElementById("d-tx").textContent = ou(g.taxa_adm_media, v => `${v.toFixed(2)}%`);
  document.getElementById("d-rent").innerHTML =
    ou(g.rentab_media_pct, v => `<span class="${cls(v)}">${v.toFixed(2)}%</span>`);
  document.getElementById("d-vol").textContent = ou(g.vol_media_pct, v => `${v.toFixed(2)}%`);
  document.getElementById("d-cred").textContent = ou(g.pct_credito_privado, v => `${v.toFixed(0)}%`);


  const perfil = Object.entries(g.perfil_cotistas || {}).sort((a, b) => b[1] - a[1]);
  const caixaPerfil = document.getElementById("d-perfil-box");
  if (!perfil.length) {
    caixaPerfil.classList.add("hidden");
  } else {
    caixaPerfil.classList.remove("hidden");
    document.getElementById("d-perfil-bar").innerHTML = perfil
      .map(([k, v]) => `<div style="width:${v}%;background:${(CORES_PERFIL[k] || ["#475569"])[0]}"></div>`).join("");
    document.getElementById("d-perfil-legend").innerHTML = perfil.map(([k, v]) => {
      const [cor, rot] = CORES_PERFIL[k] || ["#475569", k];
      return `<div class="flex items-center gap-1.5">
        <span class="w-2 h-2 rounded-full shrink-0" style="background:${cor}"></span>
        <span class="text-slate-400 truncate">${rot}</span>
        <span class="text-slate-500 ml-auto">${v.toFixed(0)}%</span></div>`;
    }).join("");
  }

  const barra = document.getElementById("d-mix-bar");
  const legenda = document.getElementById("d-mix-legend");
  // De quando é a carteira. O CDA vem defasado de propósito, e um bucket lido
  // como "hoje" quando é de quatro meses atrás é erro de leitura na mesa.
  document.getElementById("d-mix-fonte").textContent =
    g.carteira_data ? `· carteira declarada à CVM em ${fmtMes(g.carteira_data)}` : "";
  if (g.mix_incentivada === null || g.mix_incentivada === undefined) {
    barra.innerHTML = `<div class="bg-slate-800 w-full"></div>`;
    legenda.innerHTML = `<div class="col-span-4 text-slate-600">Composição da carteira não disponível nesta fonte</div>`;
  } else {
    barra.innerHTML = ORDEM_BUCKETS
      .map(k => `<div class="${BUCKETS[k].bg}" style="width:${(g[`mix_${k}`] || 0) * 100}%"></div>`).join("");
    legenda.innerHTML = ORDEM_BUCKETS.map(k =>
      `<div><div class="font-semibold ${BUCKETS[k].texto}">${((g[`mix_${k}`] || 0) * 100).toFixed(0)}%</div>
       <div class="text-slate-600">${BUCKETS[k].curto}</div></div>`).join("");
  }

  // Mix por INSTRUMENTO — eixo diferente do bucket. As fatias não fecham 100%
  // de propósito: o que não é debênture/LF/CDB/CRI fica de fora em vez de ser
  // empurrado para uma categoria "outros" que não diria nada ao broker.
  const papeis = PAPEIS;
  const caixaPapel = document.getElementById("d-papel-box");
  if (g.papel_debenture === null || g.papel_debenture === undefined) {
    caixaPapel.classList.add("hidden");
  } else {
    caixaPapel.classList.remove("hidden");
    document.getElementById("d-papel-bar").innerHTML =
      papeis.map(([k, c]) => `<div style="width:${(g[k] || 0) * 100}%;background:${c}"></div>`).join("") +
      `<div class="flex-1" style="background:#1e293b"></div>`;
    document.getElementById("d-papel-legend").innerHTML =
      papeis.filter(([k]) => (g[k] || 0) >= 0.01)
        .map(([k, c, rot]) => `<span style="color:${c}">■</span> ${rot} ${((g[k] || 0) * 100).toFixed(0)}%`)
        .join(" · ") +
      (g.carteira_credito ? ` <span class="text-slate-600">· sobre ${fmtBRL(g.carteira_credito).replace("+", "")} em crédito</span>` : "");
  }

  if (sparkChart) sparkChart.destroy();
  const ds = [];
  for (const k of ORDEM_BUCKETS) {
    const serie = d[`sparkline_${k}`];
    if (serie && serie.some(v => v !== 0)) {
      ds.push({ label: BUCKETS[k].curto, data: serie, backgroundColor: BUCKETS[k].cor, borderRadius: 2 });
    }
  }
  if (!ds.length) {
    ds.push({ label: "Fluxo total", data: d.sparkline, backgroundColor: d.sparkline.map(v => v >= 0 ? "#10b981" : "#ef4444"), borderRadius: 2 });
  }

  sparkChart = new Chart(document.getElementById("d-spark"), {
    type: "bar",
    data: {
      labels: d.sparkline_labels,
      datasets: ds,
    },
    options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false }, tooltip: { mode: 'index', intersect: false, itemSort: (a, b) => b.parsed.y - a.parsed.y, callbacks: { label: c => `${c.dataset.label}: ${fmtBRL(c.parsed.y)}` } } }, scales: { x: { stacked: true, ticks: { color: "#475569", font: { size: 8 } }, grid: { display: false } }, y: { stacked: true, ticks: { display: false }, grid: { display: false } } } },
  });

  document.getElementById("d-fundos").innerHTML = d.fundos.map(f => {
    const b = BUCKETS[f.bucket];
    // Subtítulo: bucket quando houver, e a data da declaração de taxa/cotização
    // — sem ela o broker não sabe se a taxa é de ontem ou de 2022.
    // Perfil observado vem com a origem colada: "CDI" declarado e "CDI"
    // deduzido de volatilidade não valem a mesma coisa, e o leitor precisa
    // conseguir distinguir sem abrir documentação.
    const perfilTxt = { pos: "Pós-fixado", inflacao: "Inflação" }[f.perfil_indexador];
    const perfilBadge = perfilTxt
      ? `<span class="px-1 rounded" style="background:${f.perfil_indexador === "pos" ? "#164e63" : "#831843"};color:#e2e8f0"
           title="${f.perfil_indexador_origem === "declarado" ? "Benchmark declarado" : "Inferido pela volatilidade"}: ${f.perfil_indexador_detalhe || ""}">${perfilTxt}${f.perfil_indexador_origem === "inferido" ? "?" : ""}</span>`
      : null;
    // O badge leva no title a regra que o gerou E a carteira que a sustenta:
    // sem isso o usuário vê "Incentivada" e não tem como conferir de onde
    // saiu, de quando é, nem por que o fundo vizinho de nome parecido não é.
    const pctTxt = v => `${((v || 0) * 100).toFixed(0)}%`;
    const bucketTitle = [
      f.bucket_motivo,
      f.carteira_data
        ? `Carteira de ${fmtMes(f.carteira_data)}: LF ${pctTxt(f.pct_lf)} · IPCA ${pctTxt(f.pct_ipca)} · ` +
          `CDI ${pctTxt(f.pct_cdi)} · pré ${pctTxt(f.pct_pre)}` +
          (f.carteira_pct_pl ? ` (crédito = ${f.carteira_pct_pl.toFixed(0)}% do PL)` : "")
        : null,
      // A cobertura de DAP é o que separa "compra IPCA+" de "compra IPCA+ e
      // fica só com o spread". Só aparece quando há o que mostrar.
      f.dap_cobertura ? `Hedge em DAP: ${(f.dap_cobertura * 100).toFixed(0)}% da carteira IPCA+` : null,
    ].filter(Boolean).join(" | ").replace(/"/g, "'");
    const detalhe = [
      b ? `<span class="${b.badge} px-1 rounded" title="${bucketTitle}">${b.curto}</span>` : null,
      // Sem bucket, dizer POR QUE vale mais que deixar a lacuna sem explicação
      // — o usuário precisa saber se é falta de declaração ou dado insuficiente.
      (!b && f.carteira_motivo)
        ? `<span class="text-slate-600" title="Apareceu no CDA, mas não passou nas guardas de qualidade">sem bucket: ${f.carteira_motivo}</span>`
        : null,
      // Nome diz "incentivada" e o bucket não: é o caso que mais gera dúvida
      // na mesa, e o motivo precisa estar visível sem passar o mouse.
      (f.nome_incentivado && f.bucket !== "incentivada" && f.bucket_motivo)
        ? `<span class="badge-warn px-1 rounded" title="${bucketTitle}">nome incentivada, carteira não confirma</span>`
        : null,
      perfilBadge,
      f.classificacao_anbima,
      f.extrato_data ? `decl. ${fmtData(f.extrato_data)}` : null,
    ].filter(Boolean).join(" · ");
    return `<tr class="border-t border-slate-800/50">
      <td class="px-3 py-2">
        <div>${f.nome}</div>
        ${detalhe ? `<div class="text-[9px] text-slate-600 mt-0.5">${detalhe}</div>` : ""}
      </td>
      <td class="px-3 py-2 text-right font-mono text-slate-400">${ou(f.pl, v => fmtBRL(v).replace("+", ""))}</td>
      <td class="px-3 py-2 text-center text-slate-400">${ou(f.taxa_adm, v => v.toFixed(2) + "%")}</td>
      <td class="px-3 py-2 text-center text-slate-400">${ou(f.cotizacao_resgate, v => "D+" + v)}</td>
      <td class="px-3 py-2 text-right font-mono text-slate-400">${ou(f.aplicacao_minima, v => fmtBRL(v).replace("+", ""))}</td>
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
  document.getElementById("view-mode").addEventListener("change", e => { state.view = e.target.value; renderTable(); });
  document.getElementById("movers-dir").addEventListener("change", e => { state.moversDir = e.target.value; renderMovers(); });
  document.addEventListener("keydown", e => { if (e.key === "Escape") closeDossie(); });

  // Filtros que dependem de dado que a fonte não tem só confundiriam: filtrar
  // por IPCA+ sem classificação devolveria um dashboard zerado.
  if (!temIndexador()) desabilitar("#idx-tabs button:not([data-i='todos'])", "sem classificação por indexador nesta fonte");
  if (DATA.kpis_mesa.fundos_abertos === null) {
    const chk = document.getElementById("abertos-checkbox");
    chk.checked = false; chk.disabled = true;
    state.abertos = false;
    chk.closest("label").classList.add("opacity-40");
    chk.closest("label").title = "status de captação vem do Quantum Axis";
  }
}
function desabilitar(seletor, motivo) {
  document.querySelectorAll(seletor).forEach(b => {
    b.disabled = true;
    b.classList.add("opacity-40", "cursor-not-allowed");
    b.title = motivo;
  });
}
function bindTabs(id, key, cb) {
  document.querySelectorAll(`#${id} button`).forEach(b => b.addEventListener("click", () => {
    if (b.disabled) return;
    document.querySelectorAll(`#${id} button`).forEach(x => { x.classList.remove("pill-active"); x.classList.add("bg-slate-800"); });
    b.classList.add("pill-active"); b.classList.remove("bg-slate-800");
    state[key] = b.dataset.w || b.dataset.i; cb();
  }));
}
function esc(s) { return s.replace(/'/g, "\\'"); }

async function openIdxDossie(bucket) {
  const meta = BUCKETS[bucket] || { rotulo: bucket, bg: "bg-slate-500" };

  // Buscar os dados do dashboard filtrados para este indexador
  const res = await API.dashboard({ ...state, indexador: bucket });
  const w = state.janela;
  const sorted = [...res.gestoras].sort((a, b) => b[w] - a[w]);
  
  const maxAbs = Math.max(...res.gestoras.map(g => Math.abs(g[w]))) || 1;
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

  document.getElementById("idx-d-name").innerHTML = `<span class="w-3 h-3 rounded-full ${meta.bg}"></span> ${meta.rotulo}`;
  document.getElementById("idx-d-window-cap").textContent = `janela ${state.janela}`;
  document.getElementById("idx-d-window-res").textContent = `janela ${state.janela}`;

  document.getElementById("idx-top-cap").innerHTML = sorted.slice(0, 10).map(g => bar(g, "bg-emerald-500")).join("");
  document.getElementById("idx-top-res").innerHTML = sorted.slice(-10).reverse().map(g => bar(g, "bg-red-500")).join("");

  document.getElementById("idx-dossie-overlay").classList.remove("hidden");
  // Animação de abertura
  setTimeout(() => {
    document.getElementById("idx-dossie-overlay").classList.remove("opacity-0");
    document.getElementById("idx-dossie").classList.remove("dossie-closed");
  }, 10);
}

function closeIdxDossie() {
  document.getElementById("idx-dossie-overlay").classList.add("opacity-0");
  document.getElementById("idx-dossie").classList.add("dossie-closed");
  setTimeout(() => document.getElementById("idx-dossie-overlay").classList.add("hidden"), 300);
}

init();
