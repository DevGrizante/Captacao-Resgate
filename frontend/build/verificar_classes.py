"""
Confere se toda classe usada no painel existe no CSS gerado.

O build do Tailwind e ESTATICO: ele varre os arquivos e emite so as classes que
encontrou. Uma classe montada em tempo de execucao (`bg-${cor}-500`) nao seria
vista e o elemento apareceria sem estilo -- sem erro nenhum no console. Este
script e a rede de seguranca: rode depois de `gerar_css.bat`.

    python verificar_classes.py        # da pasta frontend/build

Saida 0 = tudo coberto. Saida 1 = lista o que faltou e de que arquivo veio.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]


def main() -> int:
    css_gerado = RAIZ / "css" / "tailwind.css"
    if not css_gerado.exists():
        print(f"ERRO: {css_gerado} nao existe. Rode gerar_css.bat antes.")
        return 1

    # O Tailwind escapa os caracteres especiais do seletor (.max-w-\[13rem\]).
    # Tirar as barras invertidas primeiro deixa o seletor identico ao nome que
    # aparece no HTML, e a comparacao vira uma busca direta.
    css = css_gerado.read_text(encoding="utf-8").replace("\\", "")

    usadas: dict[str, set[str]] = {}
    # login.html tem CSS proprio, inline, e nao usa Tailwind — conferi-lo
    # aqui acusaria falta de regras que nunca deveriam existir no painel.
    paginas = [p for p in RAIZ.glob("*.html") if p.name != "login.html"]
    for arq in paginas + list(RAIZ.glob("js/*.js")):
        txt = arq.read_text(encoding="utf-8")
        # class="..." sem $ dentro: com $ e template literal, e o pedaco
        # interpolado nao e classe.
        for m in re.finditer(r'class="([^"$]+)"', txt):
            for c in m.group(1).split():
                usadas.setdefault(c, set()).add(arq.name)
        for m in re.finditer(r'classList\.(?:add|remove|toggle)\(([^)]*)\)', txt):
            for literal in re.findall(r'"([^"]+)"', m.group(1)):
                for c in literal.split():
                    usadas.setdefault(c, set()).add(arq.name)

    # As classes do proprio projeto (card, pill, badge-*) vivem em styles.css.
    proprias = set(
        re.findall(r'\.([a-zA-Z][\w-]*)', (RAIZ / "css" / "styles.css").read_text(encoding="utf-8"))
    )

    def definida(classe: str) -> bool:
        # Precedida de ponto e seguida de delimitador de seletor -- assim
        # "text-xs" nao casa dentro de "text-xs-qualquer-coisa".
        return re.search(r"\." + re.escape(classe) + r"(?=[\s,{:>~+\[\]]|$)", css) is not None

    faltando = {c: v for c, v in usadas.items() if c not in proprias and not definida(c)}

    print(f"classes usadas ....... {len(usadas)}")
    print(f"proprias (styles.css)  {len(proprias)}")
    print(f"faltando no CSS ...... {len(faltando)}")
    if faltando:
        print()
        for classe, arquivos in sorted(faltando.items()):
            print(f"  {classe:30s} <- {', '.join(sorted(arquivos))}")
        print()
        print("Conserte escrevendo a classe inteira no codigo (nunca por")
        print("concatenacao) ou liste-a em `safelist` no tailwind.config.js.")
        return 1

    print("OK: o CSS gerado cobre todas as classes do painel.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
