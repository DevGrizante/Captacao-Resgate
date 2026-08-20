/**
 * Config do build local do Tailwind.
 *
 * O painel usava https://cdn.tailwindcss.com, que baixa 407 KB e compila o CSS
 * no navegador a cada carga de página. Aqui o CSS sai pronto (~poucos KB) e é
 * servido pela própria API, sem sair da máquina.
 *
 * `content` precisa cobrir o JS: 48 classes do painel vivem em strings dentro
 * de app.js/fundos.js/tesourarias.js (badges, cores de variação). Elas são
 * strings COMPLETAS — nenhuma é montada por concatenação —, então o scanner
 * estático do Tailwind as encontra. Se algum dia alguém escrever
 * `bg-${cor}-500`, a classe some do CSS: nesse caso, escreva a string inteira
 * nos dois ramos do if, ou liste-a em `safelist`.
 */
module.exports = {
  content: [
    // O login.html fica de fora: ele e autocontido, com CSS inline, porque
    // precisa ser servido ANTES de haver sessao — e cada arquivo liberado
    // antes do login e superficie exposta a quem nao provou nada. Varrer as
    // classes dele aqui so incharia o CSS do painel com regras que ninguem usa.
    "../*.html",
    "!../login.html",
    "../js/*.js",
  ],
  theme: { extend: {} },
  plugins: [],
};
