// Painel de controle. Uma tela, uma responsabilidade: mexer nos parâmetros de
// classificação e mostrar, no mesmo lugar, o que a mudança fez com a base.
//
// A decisão de desenho que importa aqui é não separar "salvar" de "ver o
// efeito". Uma régua de negócio ajustada às cegas vira tentativa e erro: o
// usuário mexe no número, olha o dashboard, volta, mexe de novo. Por isso o
// PUT devolve as transições e elas aparecem sem recarregar nada.

const BUCKETS_ADMIN = {
  incentivada:       { rotulo: "Incentivada", cor: "#a855f7" },
  tradicional:       { rotulo: "Tradicional", cor: "#3b82f6" },
  lf:                { rotulo: "LF",          cor: "#f59e0b" },
  misto:             { rotulo: "Misto",       cor: "#64748b" },
  sem_classificacao: { rotulo: "Sem classificação", cor: "#1e293b" },
};
const ORDEM = ["incentivada", "tradicional", "lf", "misto", "sem_classificacao"];

let DEFINICOES = [];

const $ = (id) => document.getElementById(id);
const nInt = (v) => Number(v || 0).toLocaleString("pt-BR");

async function init() {
  try {
    await carregar();
    ligarBotoes();
  } catch (e) {
    mostrarErroApi(e);
  }
}

async function carregar() {
  const estado = await API.parametros();
  DEFINICOES = estado.parametros;
  $("erro-api").classList.add("hidden");
  renderCabecalho(estado);
  renderDistribuicao(estado.distribuicao, estado.total_fundos);
  renderFormulario(estado.parametros);
}

function renderCabecalho(estado) {
  $("hdr-meta").textContent =
    `${nInt(estado.total_fundos)} fundos carregados · ` +
    `${nInt(estado.total_sem_classificacao)} sem classificação`;
}

// ===== Distribuição =====
function renderDistribuicao(dist, total, rotulo = "distribuição atual") {
  const barra = $("dist-bar");
  const legenda = $("dist-legend");
  $("dist-meta").textContent = `${rotulo} · ${nInt(total)} fundos`;

  if (!total) {
    barra.innerHTML = "";
    legenda.innerHTML = `<div class="col-span-5 text-slate-600 text-xs">Nenhum fundo carregado.</div>`;
    return;
  }

  barra.innerHTML = ORDEM.map(k => {
    const n = dist[k] || 0;
    if (!n) return "";
    return `<div style="width:${n / total * 100}%;background:${BUCKETS_ADMIN[k].cor}"
              title="${BUCKETS_ADMIN[k].rotulo}: ${nInt(n)}"></div>`;
  }).join("");

  legenda.innerHTML = ORDEM.map(k => {
    const n = dist[k] || 0;
    return `<div>
      <div class="flex items-center justify-center gap-1.5">
        <span class="w-2 h-2 rounded-full shrink-0" style="background:${BUCKETS_ADMIN[k].cor}"></span>
        <span class="text-[10px] text-slate-500 truncate">${BUCKETS_ADMIN[k].rotulo}</span>
      </div>
      <div class="text-sm font-semibold mt-0.5">${nInt(n)}</div>
      <div class="text-[10px] text-slate-600">${(n / total * 100).toFixed(1)}%</div>
    </div>`;
  }).join("");
}

// ===== Formulário =====
function renderFormulario(params) {
  $("form-parametros").innerHTML = params.map(p => {
    // O padrão do código aparece ao lado do valor só quando os dois divergem:
    // repeti-lo quando são iguais só polui a tela.
    const alterado = Math.abs(p.valor - p.padrao_codigo) > 1e-9;
    return `<div>
      <div class="flex items-baseline justify-between gap-3 flex-wrap">
        <label for="p-${p.chave}" class="text-xs font-medium">${p.rotulo}</label>
        ${alterado
          ? `<span class="text-[10px] text-amber-400/80">alterado · padrão ${p.padrao_codigo.toLocaleString("pt-BR")}%</span>`
          : `<span class="text-[10px] text-slate-600">no padrão</span>`}
      </div>
      <p class="text-[11px] text-slate-500 mt-1 mb-2">${p.descricao}</p>
      <div class="flex items-center gap-2">
        <input type="number" id="p-${p.chave}" data-chave="${p.chave}"
          value="${p.valor}" min="${p.minimo}" max="${p.maximo}" step="${p.passo}"
          class="w-32 bg-slate-900 border border-slate-800 rounded px-3 py-2 text-sm font-mono
                 focus:outline-none focus:border-blue-600">
        <span class="text-sm text-slate-500">%</span>
        <span class="text-[10px] text-slate-600 ml-2">
          aceita de ${p.minimo.toLocaleString("pt-BR")}% a ${p.maximo.toLocaleString("pt-BR")}%
        </span>
      </div>
    </div>`;
  }).join("");
}

function valoresDoFormulario() {
  const valores = {};
  for (const p of DEFINICOES) {
    const campo = $(`p-${p.chave}`);
    if (campo && campo.value !== "") valores[p.chave] = Number(campo.value);
  }
  return valores;
}

// ===== Ações =====
function ligarBotoes() {
  $("btn-salvar").addEventListener("click", () =>
    executar(() => API.salvarParametros(valoresDoFormulario()), "Salvando e reclassificando…"));

  $("btn-restaurar").addEventListener("click", () =>
    executar(() => API.restaurarParametros(), "Restaurando padrões…"));

  $("btn-reclassificar").addEventListener("click", () =>
    executar(() => API.reclassificar(), "Reclassificando…"));

  // Enter em qualquer campo salva: é o gesto natural num formulário de um campo.
  $("form-parametros").addEventListener("keydown", e => {
    if (e.key === "Enter") { e.preventDefault(); $("btn-salvar").click(); }
  });
}

async function executar(acao, mensagem) {
  const botoes = document.querySelectorAll("#btn-salvar, #btn-restaurar, #btn-reclassificar");
  botoes.forEach(b => { b.disabled = true; b.classList.add("loading"); });
  $("status-form").className = "text-[11px] text-slate-500 ml-auto";
  $("status-form").textContent = mensagem;

  try {
    const r = await acao();
    renderResultado(r);
    // Recarrega para o formulário refletir o que o backend de fato gravou —
    // e não o que o usuário digitou, que pode ter sido normalizado.
    await carregar();
    renderDistribuicao(r.distribuicao_depois, r.total_fundos, "depois da reclassificação");
    $("status-form").className = "text-[11px] text-emerald-400 ml-auto";
    $("status-form").textContent = r.status === "ok"
      ? `Pronto em ${r.duracao_s.toFixed(2)}s.`
      : `Nada a fazer: ${r.status}.`;
  } catch (e) {
    $("status-form").className = "text-[11px] text-red-400 ml-auto";
    $("status-form").textContent = e.message;
  } finally {
    botoes.forEach(b => { b.disabled = false; b.classList.remove("loading"); });
  }
}

function renderResultado(r) {
  $("resultado").classList.remove("hidden");

  const mudancas = Object.entries(r.mudancas || {});
  const kpis = [
    ["Fundos reclassificados", nInt(r.fundos_reclassificados),
     `de ${nInt(r.total_fundos)} na base`],
    ["Parâmetros alterados",
     mudancas.length ? String(mudancas.length) : "—",
     mudancas.map(([k, v]) => {
       const def = DEFINICOES.find(d => d.chave === k);
       return `${def ? def.rotulo : k}: ${v.de}% → ${v.para}%`;
     }).join(" · ") || "nenhum"],
    ["Tempo", `${r.duracao_s.toFixed(2)}s`, "sem rebaixar dado da CVM"],
  ];
  $("res-kpis").innerHTML = kpis.map(([rot, valor, sub]) =>
    `<div class="card rounded-lg p-3">
      <p class="text-[10px] text-slate-500 uppercase tracking-wide">${rot}</p>
      <p class="text-xl font-semibold mt-1">${valor}</p>
      <p class="text-[10px] text-slate-500 mt-0.5">${sub}</p>
    </div>`).join("");

  const transicoes = Object.entries(r.alteracoes || {});
  const box = $("res-transicoes-box");
  if (!transicoes.length) {
    box.classList.add("hidden");
    return;
  }
  box.classList.remove("hidden");
  $("res-transicoes").innerHTML = transicoes.map(([par, n]) => {
    const [de, para] = par.split(" -> ");
    const chip = (k) => {
      const b = BUCKETS_ADMIN[k] || { rotulo: k, cor: "#475569" };
      return `<span class="px-1.5 py-0.5 rounded text-[10px]"
        style="background:${b.cor}26;color:${b.cor}">${b.rotulo}</span>`;
    };
    return `<tr class="border-t border-slate-800/50">
      <td class="px-3 py-2">${chip(de)} <span class="text-slate-600">→</span> ${chip(para)}</td>
      <td class="px-3 py-2 text-right font-mono">${nInt(n)}</td>
    </tr>`;
  }).join("");
}

function mostrarErroApi(e) {
  const caixa = $("erro-api");
  caixa.classList.remove("hidden");
  caixa.innerHTML =
    `<strong>Não consegui falar com a API em ${API_BASE}.</strong><br>` +
    `Suba o backend (o Iniciar.bat faz isso) e recarregue esta página.<br>` +
    `<span class="text-red-400/70 font-mono text-xs">${e.message}</span>`;
  $("hdr-meta").textContent = "API indisponível";
}

init();
