// Papel bancário — LF, CDB e DPGE — lido pelas DUAS PONTAS.
//
//   Gestora → Papel   escolho a casa e vejo o que ela tem: emissor, mês de
//                     vencimento e taxa, papel a papel.
//   Papel → Gestora   escolho o emissor e o mês, e vejo QUEM tem aquilo em
//                     carteira — que é a pergunta de quem vai ligar oferecendo
//                     a rolagem de um bloco específico.
//
// É a mesma matéria-prima e o mesmo total: muda por onde se entra. Por isso a
// tela é uma só, com uma chave no cabeçalho, e não duas páginas — duas telas
// com o mesmo número em lugares diferentes é o que faz a mesa desconfiar do
// dado.
//
// O detalhe também é um só. As duas visões têm exatamente o mesmo desenho
// (KPIs, agenda de vencimentos, concentração e a tabela de blocos); o que muda
// é qual ponta fica fixa. Cada payload é normalizado em `montarDetalhe` para
// uma forma comum, e daí para baixo existe um caminho só.

const VAZIO = "—";
let DADOS = null;        // gestoras   (visão 1)
let EMISSORES = null;    // emissores  (visão 2)
let DETALHE = null;      // o dossiê aberto, já normalizado
// O combo do dossiê precisa ser alcançado de fora (o clique na agenda e os
// chips também mexem no vencimento); o da lista se resolve sozinho.
let COMBO_DOSSIE = null;
const estado = { visao: "gestora", busca: "", ordenar: "valor", buscaE: "", ordenarE: "valor",
                 // Vencimento escolhido na LISTA de emissores: "2027-02", o
                 // sentinela "perpetuo", ou null para todos.
                 mesE: null };
const filtro = { texto: "", tipo: "", ordem: "vencimento", mes: null };

const $ = (id) => document.getElementById(id);
// O nome vai dentro de um onclick com aspas simples; sem escapar, uma casa com
// apostrofo no nome quebraria o handler.
const esc = (s) => String(s).replaceAll("\\", "\\\\").replaceAll("'", "\\'");
const nInt = (v) => Number(v || 0).toLocaleString("pt-BR");

// Buscar "ita" trazia Vinland Capital, Sueste Capital, DAO Capital e Quantitas
// junto com a Itaú: casar por SUBSTRING é fatal num universo em que quase todo
// nome de asset termina em "Capital" — cap-ita-l. Aqui o termo casa com o
// COMEÇO de uma palavra do nome, que é como se procura uma casa pelo nome.
//
// O acento sai da frente no mesmo movimento: quem digita "itau" está
// procurando "Itaú", e exigir o acento fazia a busca devolver vazio sem dizer
// por quê. Vários termos valem como E — "asset man" acha "Itaú Asset
// Management" e mais nada.
const semAcento = (s) =>
  String(s).normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase();

function casa(nome, consulta) {
  const termos = semAcento(consulta).split(/\s+/).filter(Boolean);
  if (!termos.length) return true;
  const palavras = semAcento(nome).split(/[^a-z0-9]+/).filter(Boolean);
  return termos.every(termo => palavras.some(palavra => palavra.startsWith(termo)));
}

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
// Papel perpétuo não tem mês, e no dado ele vem como string vazia — que é
// falsy e se confundiria com "nenhum filtro". Na tela ele vira um sentinela
// com nome.
const PERPETUO = "perpetuo";
const chaveMes = (m) => (m === PERPETUO ? "" : m);
const rotuloMes = (m) => (m === PERPETUO || m === "" ? "perpétuo" : fmtMes(m));

// Lê o que a pessoa digitou no campo de vencimento. Aceita as formas em que se
// escreve um mês de verdade — "fev/27", "fev/2027", "fevereiro 2027",
// "2027-02" — porque exigir um formato único num campo livre é transformar
// digitação em adivinhação.
//
// Três respostas, e as três importam: `null` = todos os vencimentos,
// uma string = o mês, `undefined` = não entendi. Sem a terceira, um erro de
// digitação viraria "nenhum resultado" sem dizer por quê.
function mesDoTexto(txt) {
  const s = semAcento(txt).trim();
  if (!s) return null;
  if (s.startsWith("perp")) return PERPETUO;
  let m = s.match(/^(\d{4})[-/](\d{1,2})$/);
  if (m) return `${m[1]}-${String(m[2]).padStart(2, "0")}`;
  m = s.match(/^([a-z]{3,})[\s./-]+(\d{2,4})$/);
  if (!m) return undefined;
  const i = MESES.findIndex(mes => m[1].startsWith(mes));
  if (i < 0) return undefined;
  const ano = m[2].length <= 2 ? `20${m[2].padStart(2, "0")}` : m[2];
  return `${ano}-${String(i + 1).padStart(2, "0")}`;
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

const marcaGrupo = (pct) =>
  `<span class="badge-warn px-1 rounded text-[9px] shrink-0"
     title="${(pct ?? 100).toFixed(0)}% desta posição é intragrupo (a asset é do próprio grupo do emissor)">grupo</span>`;

// =============================================================================
//  Combo de vencimento
// =============================================================================
// Um campo que ABRE a lista num clique e filtra as opções enquanto se digita.
//
// O <datalist> nativo não dá conta: o navegador decide sozinho quando mostrar
// a lista, não existe jeito de abri-la programaticamente, e ela não mostra o
// volume de cada mês — que é metade da informação, porque é ele que diz onde
// está o muro de vencimento.
//
// A lista é `position: fixed` e vive fora do card: o cabeçalho da tabela mora
// dentro de um `overflow-hidden`, e no dossiê dentro de um painel que rola —
// posicionamento absoluto seria recortado pelo pai nos dois casos.
function criarCombo({ id, opcoes, aoEscolher }) {
  const campo = $(id);
  const lista = $(`${id}-lista`);
  const botao = $(`${id}-botao`);
  let visiveis = [];
  let marcado = -1;

  const aberto = () => !lista.classList.contains("hidden");

  function posicionar() {
    const r = campo.getBoundingClientRect();
    const abaixo = window.innerHeight - r.bottom - 12;
    const paraCima = abaixo < 140 && r.top > abaixo;
    lista.style.left = `${r.left}px`;
    lista.style.width = `${Math.max(r.width, 190)}px`;
    lista.style.maxHeight = `${Math.min(280, (paraCima ? r.top : abaixo) - 8)}px`;
    lista.style.top = paraCima ? "" : `${r.bottom + 4}px`;
    lista.style.bottom = paraCima ? `${window.innerHeight - r.top + 4}px` : "";
  }

  function desenhar(termo) {
    const t = semAcento(termo || "").trim();
    // O texto é casado de três jeitos, e os três precisam existir: pelo rótulo
    // ("dez" acha todos os dezembros), pela chave ("2027" acha o ano inteiro)
    // e pelo MÊS que ele significa — sem este, "dez/26" esvaziaria a lista,
    // porque não é substring de "dez/2026". Escrever a data por extenso é
    // justamente o que o campo promete aceitar.
    const alvo = mesDoTexto(termo);
    visiveis = opcoes().filter(o =>
      !t || o.valor === alvo || semAcento(o.rotulo).includes(t) || o.valor.includes(t));
    lista.innerHTML = visiveis.length
      ? visiveis.map((o, i) => `<li role="option" data-i="${i}" aria-selected="${i === marcado}"
           class="combo-opcao${i === marcado ? " combo-marcado" : ""}">
           <span>${o.rotulo}</span><span class="combo-extra">${o.extra || ""}</span></li>`).join("")
      : `<li class="combo-vazio">nenhum vencimento com "${termo}"</li>`;
  }

  function abrir() {
    marcado = -1;
    desenhar(campo.value);
    posicionar();
    lista.classList.remove("hidden");
    campo.setAttribute("aria-expanded", "true");
  }

  function fechar() {
    lista.classList.add("hidden");
    campo.setAttribute("aria-expanded", "false");
    marcado = -1;
  }

  function marcar(passo) {
    if (!aberto()) { abrir(); return; }
    if (!visiveis.length) return;
    marcado = (marcado + passo + visiveis.length) % visiveis.length;
    desenhar(campo.value);
    lista.querySelector(".combo-marcado")?.scrollIntoView({ block: "nearest" });
  }

  function escolher(opcao) {
    campo.value = opcao.rotulo;
    campo.classList.remove("borda-erro");
    fechar();
    atualizarBotao();
    aoEscolher(opcao.valor);
  }

  function limpar() {
    campo.value = "";
    campo.classList.remove("borda-erro");
    fechar();
    atualizarBotao();
    aoEscolher(null);
  }

  // O botão é um só e troca de papel: sem valor ele abre a lista, com valor ele
  // limpa. Dois botões dentro de um campo de 8rem só serviriam para a pessoa
  // errar o alvo — e a lista continua a um clique no próprio campo.
  function atualizarBotao() {
    const cheio = Boolean(campo.value);
    botao.textContent = cheio ? "×" : "▾";
    botao.setAttribute("aria-label", cheio ? "limpar o vencimento" : "abrir a lista de vencimentos");
  }
  atualizarBotao();

  campo.addEventListener("focus", abrir);
  campo.addEventListener("click", () => { if (!aberto()) abrir(); });
  campo.addEventListener("input", () => {
    if (!aberto()) abrir(); else { marcado = -1; desenhar(campo.value); }
    atualizarBotao();
    // Texto que já é um mês vale na hora; o que ainda não é só filtra a lista.
    const mes = mesDoTexto(campo.value);
    if (mes !== undefined) { campo.classList.remove("borda-erro"); aoEscolher(mes); }
  });
  campo.addEventListener("keydown", e => {
    if (e.key === "ArrowDown") { e.preventDefault(); marcar(1); }
    else if (e.key === "ArrowUp") { e.preventDefault(); marcar(-1); }
    else if (e.key === "Escape") { if (aberto()) { e.stopPropagation(); fechar(); } }
    else if (e.key === "Enter") {
      e.preventDefault();
      if (marcado >= 0) escolher(visiveis[marcado]);
      else if (visiveis.length === 1) escolher(visiveis[0]);
      else validarTexto();
    }
  });
  campo.addEventListener("blur", () => {
    // Sem o atraso, o clique numa opção nunca chega: o blur fecharia a lista
    // antes do mouseup. O `mousedown` da opção resolve o caso comum; este
    // atraso cobre o resto (rolagem da lista, clique fora).
    setTimeout(() => { if (aberto()) fechar(); validarTexto(); }, 120);
  });

  function validarTexto() {
    const mes = mesDoTexto(campo.value);
    if (mes === undefined) { campo.classList.add("borda-erro"); return; }
    campo.classList.remove("borda-erro");
    aoEscolher(mes);
  }

  // `mousedown` e não `click`: o click só acontece depois do blur do campo, e
  // aí a lista já teria sido fechada debaixo do cursor.
  lista.addEventListener("mousedown", e => {
    e.preventDefault();
    const li = e.target.closest("[data-i]");
    if (li) escolher(visiveis[Number(li.dataset.i)]);
  });
  botao.addEventListener("mousedown", e => {
    e.preventDefault();
    if (campo.value) { limpar(); campo.focus(); return; }
    if (aberto()) fechar(); else { campo.focus(); abrir(); }
  });
  // Rolar a PÁGINA move o campo e deixaria a lista flutuando sozinha, então
  // ela fecha. Rolar a própria lista, não: ela tem 280px de altura e 147 meses
  // dentro, e fechar no primeiro giro da roda tornaria as opções de baixo
  // inalcançáveis. O evento de rolagem sobe em fase de captura vindo do
  // elemento que rolou, e é isso que separa um caso do outro.
  document.addEventListener("scroll", e => {
    if (aberto() && !lista.contains(e.target)) fechar();
  }, true);
  window.addEventListener("resize", () => { if (aberto()) posicionar(); });

  return {
    valor: () => campo.value,
    definir: (valorMes) => {
      campo.value = valorMes ? rotuloMes(valorMes) : "";
      campo.classList.remove("borda-erro");
      fechar();
      atualizarBotao();
    },
  };
}

// =============================================================================
//  Carga
// =============================================================================
// Cada lista é buscada UMA vez e guardada: alternar a visão é um gesto que se
// repete, e refazer o download a cada troca faria a régua parecer lenta. A
// promessa é o que fica em cache — se duas trocas rápidas caírem na mesma
// carga, as duas esperam o mesmo request. Falhou, o cache se apaga, para a
// próxima tentativa poder acontecer de verdade.
let pGestoras = null;
let pEmissores = null;

function carregarGestoras() {
  if (!pGestoras) {
    // Sem teto artificial: os KPIs somam sobre esta lista, e um recorte do
    // topo do ranking apresentado como total seria um erro silencioso. A busca
    // também é client-side, então um teto esconderia gestoras da pesquisa.
    pGestoras = API.carteiraBancaria(20000).catch(e => { pGestoras = null; throw e; });
  }
  return pGestoras;
}
function carregarEmissores() {
  if (!pEmissores) {
    pEmissores = API.papelPorEmissor(5000).catch(e => { pEmissores = null; throw e; });
  }
  return pEmissores;
}

async function init() {
  // Os eventos entram antes da rede: se a API estiver fora, a chave de visão e
  // o "ver detalhes" continuam respondendo em vez de parecerem quebrados.
  ligarEventos();
  await aplicarVisao(visaoInicial());
}

// A visão escolhida sobrevive ao F5: primeiro o que veio na URL (para o link
// colado no chat abrir na leitura certa), depois a última usada nesta máquina.
function visaoInicial() {
  const naUrl = new URLSearchParams(location.search).get("visao");
  if (naUrl === "gestora" || naUrl === "emissor") return naUrl;
  try {
    const salva = localStorage.getItem("papel_visao");
    if (salva === "gestora" || salva === "emissor") return salva;
  } catch { /* navegador com storage bloqueado: segue no padrão */ }
  return "gestora";
}

async function aplicarVisao(visao) {
  estado.visao = visao;
  $("visao-slider").dataset.ativo = visao;
  document.querySelectorAll(".visao-opcao").forEach(b =>
    b.setAttribute("aria-selected", String(b.dataset.visao === visao)));
  // A comparação sai de dentro do `classList.toggle`: o verificador de classes
  // (build/verificar_classes.py) lê os literais que aparecem lá dentro como se
  // fossem nomes de classe, e "gestora" viraria um falso alarme a cada build.
  const naGestora = visao === "gestora";
  $("secao-gestora").classList.toggle("hidden", !naGestora);
  $("secao-emissor").classList.toggle("hidden", naGestora);
  $("hdr-titulo").textContent = visao === "gestora"
    ? "Papel bancário por gestora · LF, CDB e DPGE"
    : "Papel bancário por emissor · quem tem em carteira";
  $("aviso-bloco").innerHTML = visao === "gestora"
    ? `<strong>Cada linha do detalhe é um bloco consolidado:</strong> mesmo emissor, mesmo tipo e
       mesmo mês de vencimento viram uma linha só, com o volume somado e a taxa média ponderada pelo
       volume. A coluna <em>Papéis</em> diz quantos registros entraram na linha.`
    : `<strong>Cada linha do detalhe é um bloco consolidado:</strong> mesma gestora, mesmo tipo e
       mesmo mês de vencimento viram uma linha só, com o volume somado e a taxa média ponderada pelo
       volume. A casa entra inteira, somando os fundos dela — quem decide alocação é a gestora, e a
       mesa liga uma vez.`;

  try {
    localStorage.setItem("papel_visao", visao);
  } catch { /* idem */ }
  const url = new URL(location.href);
  url.searchParams.set("visao", visao);
  history.replaceState(null, "", url);

  await carregarVisao();
}

async function carregarVisao() {
  const v = estado.visao;
  const corpo = v === "gestora" ? $("tabela") : $("tabela-e");
  if (!(v === "gestora" ? DADOS : EMISSORES)) {
    corpo.innerHTML = `<tr><td colspan="9" class="px-4 py-8 text-center text-slate-500">Carregando…</td></tr>`;
  }
  try {
    if (v === "gestora") DADOS = await carregarGestoras();
    else EMISSORES = await carregarEmissores();
  } catch (e) {
    erroDeApi(e, corpo);
    return;
  }
  // Trocou de visão enquanto a resposta vinha: quem manda é a escolha atual.
  if (estado.visao !== v) return;

  const lista = v === "gestora" ? DADOS : EMISSORES;
  if (!lista.length) { semDados(corpo); return; }
  renderCabecalho();
  renderKpis();
  renderTabela();
}

function erroDeApi(e, corpo) {
  const c = $("erro-api");
  c.classList.remove("hidden");
  c.innerHTML = `<strong>Não consegui falar com a API em ${API_BASE || location.origin}.</strong> ` +
    `Suba o backend (o Iniciar.bat faz isso) e recarregue. ` +
    `<span class="font-mono text-red-400/70">${e.message}</span>`;
  $("hdr-meta").textContent = "API indisponível";
  corpo.innerHTML = `<tr><td colspan="9" class="px-4 py-8 text-center text-slate-500">
    Sem resposta da API.</td></tr>`;
}

function semDados(corpo) {
  $("hdr-meta").textContent = "sem carteira nesta carga";
  corpo.innerHTML = `<tr><td colspan="9" class="px-4 py-8 text-center text-slate-500">
    A carteira vem do CDA da CVM, que não está disponível nesta carga.<br>
    A tela aparece assim que o CDA for baixado — o dashboard segue funcionando sem ela.</td></tr>`;
}

// =============================================================================
//  Cabeçalho e KPIs
// =============================================================================
function renderCabecalho() {
  const lista = estado.visao === "gestora" ? DADOS : EMISSORES;
  const data = lista[0].carteira_data;
  $("hdr-meta").textContent = estado.visao === "gestora"
    ? `${nInt(DADOS.length)} gestoras · carteira declarada à CVM em ${fmtMes(data)}`
    : `${nInt(EMISSORES.length)} emissores · carteira declarada à CVM em ${fmtMes(data)}`;
  $("aviso-resumo").textContent =
    `Posições de LF, CDB e DPGE declaradas no CDA de ${fmtMes(data)} — estoque na data-base, não emissão.`;
  $("rodape").textContent =
    `Captação e Resgate · Crédito Privado · carteira do CDA de ${fmtMes(data)}`;
}

const kpi = (el, rot, v, sub) => {
  $(el).innerHTML = `<p class="text-xs text-slate-500 uppercase tracking-wide">${rot}</p>
    <p class="text-2xl font-semibold mt-1">${v}</p>
    <p class="text-xs mt-1 text-slate-500">${sub}</p>`;
};

function renderKpis() {
  // O estoque, o que vence e a taxa média são os MESMOS números nas duas
  // visões — é a mesma carteira somada por chaves diferentes. Só o segundo
  // cartão troca, porque é ele que conta a ponta escolhida.
  const lista = estado.visao === "gestora" ? DADOS : EMISSORES;
  const total = lista.reduce((s, f) => s + f.valor, 0);
  const venc = lista.reduce((s, f) => s + f.valor_venc_12m, 0);
  const papeis = lista.reduce((s, f) => s + f.posicoes, 0);
  const com = lista.filter(f => f.spread_cdi !== null);
  const peso = com.reduce((s, f) => s + f.valor, 0);
  const taxa = peso ? com.reduce((s, f) => s + f.spread_cdi * f.valor, 0) / peso : null;

  // Com um vencimento escolhido, os cartões passam a falar dele. Manter o
  // estoque do universo em cima de uma tabela que mostra um mês só seria
  // convidar a ler um número pelo outro.
  if (estado.visao === "emissor" && estado.mesE) {
    kpisDoMes(total);
    return;
  }

  kpi("kpi-total", "Estoque", fmtBRL(total), `${nInt(papeis)} papéis em carteira`);
  if (estado.visao === "gestora") {
    kpi("kpi-fundos", "Gestoras", nInt(DADOS.length), "com LF, CDB ou DPGE");
  } else {
    const top5 = [...EMISSORES].sort((a, b) => b.valor - a.valor).slice(0, 5)
      .reduce((s, e) => s + e.valor, 0);
    kpi("kpi-fundos", "Emissores", nInt(EMISSORES.length),
        total ? `top 5 = ${(top5 / total * 100).toFixed(0)}% do estoque` : "");
  }
  kpi("kpi-taxa", "Taxa média", taxa ? `CDI + ${taxa.toFixed(2)}%` : VAZIO,
      "ponderada por volume, só pós-fixado");
  kpi("kpi-venc", "Vence em 12 meses", fmtBRL(venc),
      total ? `${(venc / total * 100).toFixed(0)}% do estoque` : "");
}

function kpisDoMes(estoqueTotal) {
  const chave = chaveMes(estado.mesE);
  const fatias = EMISSORES.map(e => e.meses.find(m => m.mes === chave)).filter(Boolean);
  const total = fatias.reduce((s, m) => s + m.valor, 0);
  const papeis = fatias.reduce((s, m) => s + m.posicoes, 0);
  const com = fatias.filter(m => m.taxa !== null);
  const peso = com.reduce((s, m) => s + m.valor, 0);
  const taxa = peso ? com.reduce((s, m) => s + m.taxa * m.valor, 0) / peso : null;

  kpi("kpi-total", `Vence em ${rotuloMes(estado.mesE)}`, fmtBRL(total),
      `${nInt(papeis)} papéis` + (estoqueTotal ? ` · ${(total / estoqueTotal * 100).toFixed(1)}% do estoque` : ""));
  kpi("kpi-fundos", "Emissores", nInt(fatias.length),
      `de ${nInt(EMISSORES.length)} com papel em carteira`);
  kpi("kpi-taxa", "Taxa média", taxa ? `CDI + ${taxa.toFixed(2)}%` : VAZIO,
      "no mês, ponderada por volume");

  // O acumulado até o mês é a pergunta seguinte de quem olha um vencimento:
  // não só "quanto vence em dez/26", mas "quanto já venceu até lá". Papel
  // perpétuo não entra em acumulado nenhum — ele não vence.
  if (estado.mesE === PERPETUO) {
    kpi("kpi-venc", "Estoque total", fmtBRL(estoqueTotal), "todos os vencimentos");
    return;
  }
  const ate = EMISSORES.reduce((s, e) => s + e.meses.reduce(
    (x, m) => x + (m.mes && m.mes <= chave ? m.valor : 0), 0), 0);
  kpi("kpi-venc", `Vence até ${rotuloMes(estado.mesE)}`, fmtBRL(ate),
      estoqueTotal ? `${(ate / estoqueTotal * 100).toFixed(0)}% do estoque, acumulado` : "");
}

// =============================================================================
//  Tabela principal
// =============================================================================
function renderTabela() {
  if (estado.visao === "gestora") renderTabelaGestoras();
  else renderTabelaEmissores();
}

// Ordenar por campo ausente não pode jogar a linha para o topo: `null` vira -1
// e cai no fim, que é onde "não declarado" pertence num ranking.
const peso = (o, k) => (o[k] === null || o[k] === undefined) ? -1 : o[k];

function renderTabelaGestoras() {
  // Digitar na busca antes da lista chegar (ou depois de a API falhar) não
  // pode explodir no console: sem dado, não há o que ordenar.
  if (!DADOS) return;
  const k = estado.ordenar;
  const linhas = DADOS
    .filter(f => casa(f.gestora, estado.busca))
    .sort((a, b) => peso(b, k) - peso(a, k));

  if (!linhas.length) {
    $("tabela").innerHTML = `<tr><td colspan="9" class="px-4 py-6 text-center text-slate-600">
      Nenhuma gestora com esse nome.</td></tr>`;
    return;
  }

  $("tabela").innerHTML = linhas.map(f => `
    <tr class="row-hover border-t border-slate-800 cursor-pointer" onclick="abrirDossieGestora('${esc(f.gestora)}')">
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

// Com um vencimento escolhido, o emissor passa a ser lido pela FATIA daquele
// mês: volume, papéis, gestoras, taxa e mix viram os do mês. Filtrar as linhas
// e deixar as colunas descrevendo o estoque inteiro seria o pior dos mundos —
// a tela responderia "quem tem papel vencendo em dez/26" com o número de tudo
// o que o emissor carrega, e a mesa ligaria com o valor errado na mão.
//
// `null` quando o emissor não tem nada vencendo no mês: a linha some.
function projetarNoMes(e, mes) {
  const m = e.meses.find(x => x.mes === chaveMes(mes));
  if (!m) return null;
  return {
    ...e,
    valor: m.valor, posicoes: m.posicoes, gestoras: m.gestoras,
    spread_cdi: m.taxa, pct_lf: m.pct_lf, pct_cdb: m.pct_cdb, pct_dpge: m.pct_dpge,
    estoque: e.valor,
    pct_do_estoque: e.valor ? m.valor / e.valor * 100 : null,
  };
}

// Prazo, "vence 3m" e "vence 12m" são janelas do estoque inteiro e não
// significam nada dentro de um mês só; no lugar delas entra o que só faz
// sentido filtrado: quanto daquele emissor vence ali.
function colunasEmissor() {
  const th = (rot, alin = "", extra = "") =>
    `<th class="px-4 py-2 font-medium${alin}"${extra}>${rot}</th>`;
  if (!estado.mesE) {
    return [th("Emissor"), th("Volume", " text-right"), th("Papéis", " text-center"),
            th("Gestoras", " text-center"), th("Taxa média", " text-right"),
            th("Prazo", " text-center"), th("Vence 3m", " text-right"),
            th("Vence 12m", " text-right"), th("LF / CDB / DPGE", " text-center")];
  }
  return [
    th("Emissor"),
    th(`Vence em ${rotuloMes(estado.mesE)}`, " text-right"),
    th("Papéis", " text-center", ' title="Registros do CDA vencendo neste mês"'),
    th("Gestoras", " text-center", ' title="Casas com papel deste emissor vencendo neste mês"'),
    th("Taxa média", " text-right", ' title="Ponderada por volume, só do que vence neste mês"'),
    th("% do estoque", " text-right", ' title="Quanto do papel deste emissor vence neste mês"'),
    th("LF / CDB / DPGE", " text-center"),
  ];
}

function renderTabelaEmissores() {
  if (!EMISSORES) return;
  const colunas = colunasEmissor();
  $("thead-e").innerHTML = colunas.join("");

  const k = estado.ordenarE;
  let linhas = EMISSORES.filter(e => casa(e.emissor, estado.buscaE));
  if (estado.mesE) linhas = linhas.map(e => projetarNoMes(e, estado.mesE)).filter(Boolean);
  linhas = linhas.sort((a, b) => peso(b, k) - peso(a, k));

  const totalFiltrado = linhas.reduce((s, e) => s + e.valor, 0);
  $("secao-emissor-sub").textContent = estado.mesE
    ? `${nInt(linhas.length)} emissor${linhas.length === 1 ? "" : "es"} com papel vencendo em ` +
      `${rotuloMes(estado.mesE)} · ${fmtBRL(totalFiltrado)} — clique para ver quem tem em carteira`
    : "Clique num emissor para ver, mês a mês, quem tem o papel em carteira, quanto e a que taxa";

  if (!linhas.length) {
    $("tabela-e").innerHTML = `<tr><td colspan="${colunas.length}" class="px-4 py-6 text-center text-slate-600">
      ${estado.mesE
        ? `Nenhum emissor com papel vencendo em ${rotuloMes(estado.mesE)}${estado.buscaE ? " com esse nome" : ""}.`
        : "Nenhum emissor com esse nome."}</td></tr>`;
    return;
  }

  // O mês escolhido viaja para o dossiê: quem filtrou a lista por dez/26 quer
  // abrir o emissor já em dez/26, não recomeçar a mira lá dentro.
  const abrir = (e) => `abrirDossieEmissor('${esc(e.raiz)}', '${estado.mesE || ""}')`;
  const nome = (e) => `
      <td class="px-4 py-2.5">
        <div class="flex items-center gap-1.5">
          <span class="truncate max-w-[300px] font-medium">${e.emissor}</span>
          ${e.pct_ligado >= 50 ? marcaGrupo(e.pct_ligado) : ""}
        </div>
        <div class="text-[10px] text-slate-500">${
          estado.mesE
            ? `estoque ${fmtBRL(e.estoque)}${e.share_pct !== null ? ` · ${e.share_pct.toFixed(1)}% do mercado` : ""}`
            : `${nInt(e.fundos)} fundo${e.fundos > 1 ? "s" : ""}${e.share_pct !== null ? ` · ${e.share_pct.toFixed(1)}% do estoque` : ""}`
        }</div>
      </td>`;

  $("tabela-e").innerHTML = linhas.map(e => estado.mesE ? `
    <tr class="row-hover border-t border-slate-800 cursor-pointer" onclick="${abrir(e)}">
      ${nome(e)}
      <td class="px-4 py-2.5 text-right font-mono">${fmtBRL(e.valor)}</td>
      <td class="px-4 py-2.5 text-center text-slate-400">${nInt(e.posicoes)}</td>
      <td class="px-4 py-2.5 text-center text-slate-400">${nInt(e.gestoras)}</td>
      <td class="px-4 py-2.5 text-right font-mono">${ou(e.spread_cdi, v => `CDI + ${v.toFixed(2)}%`)}</td>
      <td class="px-4 py-2.5 text-right font-mono text-slate-400">${ou(e.pct_do_estoque, v => v.toFixed(1) + "%")}</td>
      <td class="px-4 py-2.5">${barraMix(e)}</td>
    </tr>` : `
    <tr class="row-hover border-t border-slate-800 cursor-pointer" onclick="${abrir(e)}">
      ${nome(e)}
      <td class="px-4 py-2.5 text-right font-mono">${fmtBRL(e.valor)}</td>
      <td class="px-4 py-2.5 text-center text-slate-400">${nInt(e.posicoes)}</td>
      <td class="px-4 py-2.5 text-center text-slate-400">${nInt(e.gestoras)}</td>
      <td class="px-4 py-2.5 text-right font-mono">${ou(e.spread_cdi, v => `CDI + ${v.toFixed(2)}%`)}</td>
      <td class="px-4 py-2.5 text-center text-slate-400">${fmtPrazo(e.prazo_dias)}</td>
      <td class="px-4 py-2.5 text-right font-mono text-slate-400">${fmtBRL(e.valor_venc_3m)}</td>
      <td class="px-4 py-2.5 text-right font-mono text-slate-400">${fmtBRL(e.valor_venc_12m)}</td>
      <td class="px-4 py-2.5">${barraMix(e)}</td>
    </tr>`).join("");
}

// A lista que o campo oferece é a união das agendas de todos os emissores, com
// o volume do mercado em cada mês ao lado — assim a escolha já mostra onde
// está o muro de vencimento antes mesmo de filtrar.
function opcoesDeVencimento() {
  if (!EMISSORES) return [];
  const soma = new Map();
  for (const e of EMISSORES) {
    for (const m of e.meses) soma.set(m.mes, (soma.get(m.mes) || 0) + m.valor);
  }
  return [...soma.keys()]
    .sort((a, b) => (a === "") - (b === "") || a.localeCompare(b))
    .map(k => ({ valor: k || PERPETUO, rotulo: rotuloMes(k), extra: fmtBRL(soma.get(k)) }));
}

// As do dossiê são a agenda daquele emissor (ou daquela gestora), e por isso
// saem do detalhe aberto — não da lista.
function opcoesDoDossie() {
  if (!DETALHE) return [];
  const opcoes = DETALHE.por_mes.map(m => ({
    valor: m.mes, rotulo: fmtMes(m.mes), extra: fmtBRL(m.valor),
  }));
  if (DETALHE.blocos.some(b => !b.mes_venc)) {
    opcoes.push({ valor: PERPETUO, rotulo: "perpétuo", extra: "" });
  }
  return opcoes;
}

// Ordenar por "vence 3m/12m" com um mês escolhido não quer dizer nada: são
// janelas do estoque inteiro. Em vez de deixar a opção mentir, ela sai da
// régua enquanto o filtro está ligado.
const ORDENS_DE_JANELA = ["valor_venc_3m", "valor_venc_12m"];

function aplicarVencimentoNaLista(mes) {
  // Redesenhar sem nada ter mudado NÃO é só desperdício: o campo dispara
  // `blur` quando a pessoa clica numa linha, e a tabela era reconstruída entre
  // o mousedown e o mouseup — o primeiro clique depois de digitar não abria o
  // dossiê. Handler idempotente, clique preservado.
  if (mes === estado.mesE) return;
  estado.mesE = mes;
  for (const opcao of $("ordenar-e").options) {
    if (ORDENS_DE_JANELA.includes(opcao.value)) opcao.disabled = Boolean(mes);
  }
  if (mes && ORDENS_DE_JANELA.includes(estado.ordenarE)) {
    estado.ordenarE = "valor";
    $("ordenar-e").value = "valor";
  }
  renderKpis();
  renderTabelaEmissores();
}

function barraMix(f) {
  const partes = [["pct_lf", "lf"], ["pct_cdb", "cdb"], ["pct_dpge", "dpge"]];
  const titulo = partes.map(([k, t]) => `${t.toUpperCase()} ${((f[k] || 0) * 100).toFixed(0)}%`).join(" · ");
  return `<div class="flex h-1.5 w-24 rounded overflow-hidden mx-auto" title="${titulo}">
    ${partes.map(([k, t]) => `<div style="width:${(f[k] || 0) * 100}%;background:${CORES_TIPO[t]}"></div>`).join("")}
  </div>`;
}

// =============================================================================
//  Detalhe — um painel, duas leituras
// =============================================================================
// Normaliza os dois payloads numa forma só. Daqui para baixo o código não
// sabe (nem precisa saber) qual ponta está fixa: cada bloco tem um `nome`, um
// `sub`, um valor, e um `pivot` — a chave da OUTRA ponta, que é o que permite
// pular de "quem carrega este papel" para "o que essa casa carrega" sem
// voltar para a lista.
function montarDetalhe(tipo, d) {
  if (tipo === "gestora") {
    const r = d.gestora;
    return {
      tipo,
      resumo: r,
      nome: r.gestora,
      rotulo: "Carteira de papel bancário",
      meta: `${nInt(r.fundos)} fundo${r.fundos > 1 ? "s" : ""} · ${nInt(r.posicoes)} papéis ` +
            `de ${nInt(r.emissores)} emissores · carteira de ${fmtMes(r.carteira_data)}`,
      colunaRotulo: "Emissor",
      concRotulo: "Concentração por emissor",
      papeisRotulo: "Papéis em carteira",
      filtroDica: "filtrar emissor...",
      pivotDica: "ver quem mais carrega este papel",
      por_mes: d.por_mes,
      total: r.valor,
      blocos: d.posicoes.map(p => ({
        nome: p.emissor, sub: "", pivot: p.raiz_emissor,
        instrumento: p.instrumento, mes_venc: p.mes_venc,
        taxa: p.taxa, forma: p.forma, valor: p.valor, papeis: p.papeis,
        ligado: p.ligado, pct_ligado: p.pct_ligado,
      })),
      conc: d.por_emissor.map(e => ({
        nome: e.nome, valor: e.valor, pct: e.pct_do_bancario, spread: e.spread, ligado: false,
      })),
    };
  }
  const r = d.emissor;
  return {
    tipo,
    resumo: r,
    nome: r.emissor,
    rotulo: "Quem tem este papel em carteira",
    meta: `${nInt(r.gestoras)} gestora${r.gestoras > 1 ? "s" : ""} · ${nInt(r.fundos)} fundos · ` +
          `${nInt(r.posicoes)} papéis · carteira de ${fmtMes(r.carteira_data)}`,
    colunaRotulo: "Gestora",
    concRotulo: "Concentração por gestora",
    papeisRotulo: "Blocos em carteira",
    filtroDica: "filtrar gestora...",
    pivotDica: "ver a carteira desta casa",
    por_mes: d.por_mes,
    total: r.valor,
    blocos: d.posicoes.map(p => ({
      nome: p.gestora,
      sub: p.fundos > 1 ? `${nInt(p.fundos)} fundos` : "",
      pivot: p.gestora,
      instrumento: p.instrumento, mes_venc: p.mes_venc,
      taxa: p.taxa, forma: p.forma, valor: p.valor, papeis: p.papeis,
      ligado: p.ligado, pct_ligado: p.pct_ligado,
    })),
    conc: d.por_gestora.map(g => ({
      nome: g.gestora, valor: g.valor, pct: g.pct_do_emissor, spread: g.spread, ligado: g.ligado,
    })),
  };
}

async function abrirDossieGestora(gestora) {
  const d = await API.carteiraBancariaGestora(gestora).catch(() => null);
  if (!d) return;
  DETALHE = montarDetalhe("gestora", d);
  renderDossie();
}

async function abrirDossieEmissor(raiz, mes) {
  const d = await API.papelPorEmissorDetalhe(raiz).catch(() => null);
  if (!d) return;
  DETALHE = montarDetalhe("emissor", d);
  renderDossie();
  // `renderDossie` zera os filtros; o mês herdado da lista entra depois, para
  // não depender da ordem em que as duas coisas acontecem.
  if (mes) filtrarMes(mes);
}

// O pulo entre as pontas. Além de trocar o conteúdo do painel, troca a visão
// atrás dele: quem fechar o dossiê tem que cair na lista coerente com o que
// estava lendo, e não na lista de onde partiu três cliques atrás.
async function pivotar(pivot) {
  const destino = DETALHE.tipo === "gestora" ? "emissor" : "gestora";
  await aplicarVisao(destino);
  if (destino === "emissor") await abrirDossieEmissor(pivot);
  else await abrirDossieGestora(pivot);
}

function renderDossie() {
  const d = DETALHE;
  const r = d.resumo;

  $("d-rotulo").textContent = d.rotulo;
  $("d-nome").textContent = d.nome;
  $("d-meta").textContent = d.meta;
  $("d-conc-rotulo").textContent = d.concRotulo;
  $("d-papeis-rotulo").textContent = d.papeisRotulo;
  $("d-filtro").placeholder = d.filtroDica;

  // O terceiro cartão é o único que difere: a gestora tem PL, e "% do PL" diz
  // o tamanho da aposta dela; o emissor não tem PL nesta base, e o que
  // dimensiona é a fatia dele no papel bancário do universo.
  const primeiro = d.tipo === "gestora"
    ? ["Estoque", fmtBRL(r.valor), r.pct_pl !== null ? `${r.pct_pl.toFixed(1)}% do PL` : ""]
    : ["Estoque", fmtBRL(r.valor), r.share_pct !== null ? `${r.share_pct.toFixed(1)}% do papel bancário` : ""];
  const kpis = [
    primeiro,
    ["Taxa média", r.spread_cdi !== null ? `CDI + ${r.spread_cdi.toFixed(2)}%` : VAZIO, "só pós-fixado"],
    ["Prazo médio", fmtPrazo(r.prazo_dias), "a partir de hoje"],
    // As duas janelas de rolagem, lado a lado: 3 meses é o que precisa de
    // conversa agora, 12 meses é o horizonte do ano.
    ["Vence em 3m", fmtBRL(r.valor_venc_3m),
     r.valor ? `${(r.valor_venc_3m / r.valor * 100).toFixed(0)}% do estoque` : ""],
    ["Vence em 12m", fmtBRL(r.valor_venc_12m),
     r.valor ? `${(r.valor_venc_12m / r.valor * 100).toFixed(0)}% do estoque` : ""],
  ];
  $("d-kpis").innerHTML = kpis.map(([rot, v, s]) =>
    `<div class="card rounded-lg p-3">
      <p class="text-[10px] text-slate-500 uppercase">${rot}</p>
      <p class="text-base font-semibold mt-0.5">${v}</p>
      <p class="text-[10px] text-slate-600 mt-0.5">${s}</p>
    </div>`).join("");

  $("d-thead").innerHTML = `
    <th class="px-3 py-2 font-medium text-center">Tipo</th>
    <th class="px-3 py-2 font-medium">${d.colunaRotulo}</th>
    <th class="px-3 py-2 font-medium text-center">Mês/ano</th>
    <th class="px-3 py-2 font-medium text-center" title="Quantos papéis foram somados nesta linha">Papéis</th>
    <th class="px-3 py-2 font-medium text-right">Taxa</th>
    <th class="px-3 py-2 font-medium text-right">Volume</th>
    <th class="px-3 py-2 font-medium text-right">%</th>`;

  filtro.texto = ""; filtro.tipo = ""; filtro.ordem = "vencimento"; filtro.mes = null;
  $("d-filtro").value = ""; $("d-tipo").value = ""; $("d-ordem").value = "vencimento";
  // Perpétuo só entra na lista quando existe: a LFSC não vence, e oferecer um
  // vencimento vazio em toda carteira faria parecer que falta dado.
  COMBO_DOSSIE.definir(null);

  renderAgenda();
  renderConcentracao();
  renderBlocos();

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
  const cor = DETALHE.tipo === "gestora" ? "#34d399" : "#38bdf8";
  $("d-agenda").innerHTML = meses.map(m => `
    <div class="w-8 flex flex-col justify-end h-24 cursor-pointer" onclick="filtrarMes('${m.mes}')"
         title="${fmtMes(m.mes)}: ${fmtBRL(m.valor)} em ${m.posicoes} papéis">
      <div style="height:${Math.max(m.valor / max * 100, 2)}%;background:${cor};opacity:${!filtro.mes || filtro.mes === m.mes ? 1 : .28}" class="rounded-t"></div>
    </div>`).join("");
  $("d-agenda-labels").innerHTML = meses.map(m => {
    const [a, mm] = m.mes.split("-");
    return `<div class="w-8 text-center text-[8px] text-slate-500 leading-tight">
      ${MESES[Number(mm) - 1]}<br><span class="text-slate-600">${a.slice(2)}</span></div>`;
  }).join("");
}

// O mês pode ser escolhido de dois jeitos — clicando a barra ou no seletor — e
// os dois têm que contar a mesma história. Tudo passa por aqui, e daqui a
// agenda, o seletor e a tabela voltam a concordar.
function filtrarMes(mes) {
  filtro.texto = "";
  $("d-filtro").value = "";
  filtro.mes = (filtro.mes === mes || !mes) ? null : mes;
  COMBO_DOSSIE.definir(filtro.mes);
  renderAgenda();
  renderBlocos();
}

function renderConcentracao() {
  $("d-emissores").innerHTML = DETALHE.conc.slice(0, 14).map(c => `
    <button type="button" onclick="filtrarNome('${esc(c.nome)}')"
      class="pill px-2 py-1 rounded bg-slate-900 border border-slate-800 hover:border-emerald-700 text-[10px]"
      title="${c.spread ? `CDI + ${c.spread.toFixed(2)}%` : "sem taxa declarada"}">
      <span class="text-slate-300">${c.nome}</span>
      ${c.ligado ? `<span class="text-red-400 ml-1">·grupo</span>` : ""}
      <span class="text-slate-500 ml-1">${fmtBRL(c.valor)}</span>
      <span class="text-slate-600 ml-1">${c.pct !== null && c.pct !== undefined ? c.pct.toFixed(0) + "%" : ""}</span>
    </button>`).join("");
}

function filtrarNome(nome) {
  filtro.mes = null;
  COMBO_DOSSIE.definir(null);
  filtro.texto = filtro.texto === nome ? "" : nome;
  $("d-filtro").value = filtro.texto;
  renderAgenda();
  renderBlocos();
}

function renderBlocos() {
  const d = DETALHE;
  let lista = d.blocos;
  if (filtro.texto) lista = lista.filter(p => casa(p.nome, filtro.texto));
  if (filtro.tipo) lista = lista.filter(p => p.instrumento === filtro.tipo);
  if (filtro.mes === "perpetuo") lista = lista.filter(p => !p.mes_venc);
  else if (filtro.mes) lista = lista.filter(p => p.mes_venc === filtro.mes);

  const ordens = {
    vencimento: (a, b) => a.mes_venc.localeCompare(b.mes_venc) || b.valor - a.valor,
    valor: (a, b) => b.valor - a.valor,
    // Ordenar por taxa só compara dentro da mesma forma: "102% do DI" e
    // "CDI + 1,35%" não são grandezas comparáveis, e misturá-las na ordenação
    // jogaria todo o percentual do DI para o topo como se fosse o mais caro.
    spread: (a, b) => (a.forma || "").localeCompare(b.forma || "")
                      || (b.taxa ?? -1) - (a.taxa ?? -1),
    emissor: (a, b) => a.nome.localeCompare(b.nome) || a.mes_venc.localeCompare(b.mes_venc),
  };
  lista = [...lista].sort(ordens[filtro.ordem] || ordens.vencimento);

  const total = lista.reduce((s, p) => s + p.valor, 0);
  $("d-papeis-n").textContent =
    `· ${nInt(lista.length)} de ${nInt(d.blocos.length)} · ${fmtBRL(total)}` +
    (filtro.mes ? ` · ${filtro.mes === "perpetuo" ? "sem vencimento" : `vencendo em ${fmtMes(filtro.mes)}`}` : "");

  if (!lista.length) {
    $("d-papeis").innerHTML = `<tr><td colspan="7" class="px-3 py-4 text-center text-slate-600">
      Nenhum papel com esse filtro.</td></tr>`;
    return;
  }

  const base = d.total || 1;
  $("d-papeis").innerHTML = lista.map(p => `
    <tr class="border-t border-slate-800/50">
      <td class="px-3 py-1.5 text-center">${chipTipo(p.instrumento)}</td>
      <td class="px-3 py-1.5">
        <div class="flex items-center gap-1 max-w-[15rem]">
          <button type="button" onclick="pivotar('${esc(p.pivot)}')" title="${d.pivotDica}"
                  class="truncate text-left hover:text-emerald-400 hover:underline">${p.nome}</button>
          ${p.ligado ? marcaGrupo(p.pct_ligado) : ""}
        </div>
        ${p.sub ? `<div class="text-[9px] text-slate-600">${p.sub}</div>` : ""}
      </td>
      <td class="px-3 py-1.5 text-center font-mono text-slate-400 whitespace-nowrap">${fmtMes(p.mes_venc)}</td>
      <td class="px-3 py-1.5 text-center text-slate-600">${p.papeis > 1 ? p.papeis : ""}</td>
      <td class="px-3 py-1.5 text-right font-mono whitespace-nowrap">${fmtTaxa(p)}</td>
      <td class="px-3 py-1.5 text-right font-mono whitespace-nowrap">${fmtBRL(p.valor)}</td>
      <td class="px-3 py-1.5 text-right font-mono text-slate-600 whitespace-nowrap">${(p.valor / base * 100).toFixed(1)}%</td>
    </tr>`).join("");
}

function fecharDossie() {
  $("dossie-overlay").style.opacity = "0";
  $("dossie").classList.add("dossie-closed");
  $("dossie").classList.remove("dossie-open");
  setTimeout(() => $("dossie-overlay").classList.add("hidden"), 250);
}

function ligarEventos() {
  document.querySelectorAll(".visao-opcao").forEach(botao => {
    botao.addEventListener("click", () => {
      if (botao.dataset.visao !== estado.visao) aplicarVisao(botao.dataset.visao);
    });
    // Seta esquerda/direita anda na régua, como em qualquer segmented control.
    botao.addEventListener("keydown", e => {
      if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
      e.preventDefault();
      const outra = estado.visao === "gestora" ? "emissor" : "gestora";
      aplicarVisao(outra).then(() =>
        document.querySelector(`.visao-opcao[data-visao="${outra}"]`).focus());
    });
  });

  $("busca").addEventListener("input", e => { estado.busca = e.target.value; renderTabelaGestoras(); });
  $("ordenar").addEventListener("change", e => { estado.ordenar = e.target.value; renderTabelaGestoras(); });
  $("busca-e").addEventListener("input", e => { estado.buscaE = e.target.value; renderTabelaEmissores(); });
  $("ordenar-e").addEventListener("change", e => { estado.ordenarE = e.target.value; renderTabelaEmissores(); });

  $("d-filtro").addEventListener("input", e => { filtro.texto = e.target.value; filtro.mes = null; renderBlocos(); });
  criarCombo({
    id: "venc-e",
    opcoes: opcoesDeVencimento,
    aoEscolher: aplicarVencimentoNaLista,
  });
  COMBO_DOSSIE = criarCombo({
    id: "d-mes",
    opcoes: opcoesDoDossie,
    aoEscolher: mes => {
      // Mesma razão de `aplicarVencimentoNaLista`: sem esta saída, o combo
      // reaplicaria o mesmo mês no blur e redesenharia a tabela embaixo do
      // clique que causou o blur.
      if ((mes || null) === (filtro.mes || null)) return;
      // O `filtrarMes` alterna quando recebe o mês que já está ligado; aqui a
      // escolha é explícita, então o estado é zerado antes de aplicar.
      filtro.mes = null;
      filtrarMes(mes);
    },
  });
  $("d-tipo").addEventListener("change", e => { filtro.tipo = e.target.value; renderBlocos(); });
  $("d-ordem").addEventListener("change", e => { filtro.ordem = e.target.value; renderBlocos(); });
  document.addEventListener("keydown", e => { if (e.key === "Escape") fecharDossie(); });
  $("aviso-toggle").addEventListener("click", () => {
    const aberto = !$("aviso-detalhe").classList.toggle("hidden");
    $("aviso-toggle").textContent = aberto ? "ocultar detalhes" : "ver detalhes";
  });
}

init();
