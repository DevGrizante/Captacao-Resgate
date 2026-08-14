"""
Perfil de indexador OBSERVADO — sem o Quantum.

>>> ISTO NÃO É O BUCKET. Leia antes de usar.

O bucket IPCA+/CDI+/LF/Misto do projeto mede a COMPOSIÇÃO da carteira: que
papel o fundo carrega. É o que o Quantum entrega e é o que a mesa de crédito
precisa para decidir o que originar.

O que este módulo entrega é a EXPOSIÇÃO DO COTISTA: a que o fundo é indexado,
ou como a cota dele se comporta. As duas coisas divergem com frequência em
crédito — comprar debênture IPCA+ e travar em CDI via swap é rotina, e nesse
fundo a composição é IPCA e o comportamento é CDI.

Por isso o resultado vive num campo próprio (`perfil_indexador`) e nunca
preenche `bucket`. Quando o Quantum chegar, os dois convivem e a diferença
entre eles é, ela mesma, informação.

DUAS ROTAS, nesta ordem:

  1. DECLARADO (exato, ~23% dos fundos)
     - `INDICE_REFER` da LAMINA — o benchmark do fundo.  (2,2%)
     - `PARAM_TAXA_PERFM` do EXTRATO — o índice sobre o qual ele cobra
       taxa de performance.                              (16,7%)
     - O nome do fundo, quando traz o índice explícito.   (5,9%)

  2. INFERIDO pelo comportamento da cota (~82%, acerto de 86,5%)
     Fundo pós-fixado acrua todo dia e quase não oscila; fundo indexado à
     inflação marca a mercado e oscila. Medido contra 661 fundos de benchmark
     conhecido, a volatilidade anualizada separa os dois:

         pós-fixado     p25 0,44%  |  mediana 1,30%  |  p75 2,42%
         inflação       p25 5,78%  |  mediana 6,65%  |  p75 6,73%

     Um corte único em 3,8% acerta 86,5%, contra 53,9% de chutar a classe
     maior. Combinar com o excesso sobre o CDI não melhorou (86,2%).

>>> LIMITES QUE O USUÁRIO PRECISA VER NA TELA:
    * o corte é de REGIME. A separação foi medida numa janela de juros em
      alta, em que os fundos de inflação apanharam. A volatilidade é mais
      estrutural que o retorno, mas não é imune a mudança de cenário.
    * dentro da zona cinzenta (2,5%–5,0% de volatilidade, ~10% dos fundos) o
      acerto cai para 77%. Ali devolvemos `None` em vez de chutar.
    * o gabarito é o benchmark declarado, que já é um proxy — logo a rota 2 é
      um proxy calibrado sobre outro proxy.
"""
from __future__ import annotations

import re
import unicodedata

from app.config import settings

DECLARADO = "declarado"
INFERIDO = "inferido"

POS = "pos"          # pós-fixado: CDI, Selic, DI
INFLACAO = "inflacao"  # IPCA, IMA-B, NTN-B, IGP-M

_RE_INFLACAO = re.compile(r"IPCA|IMA-?B|IMAB|NTN-?B|INFLAC|IGP-?M|\bIPC\b")
_RE_POS = re.compile(r"\bCDI\b|SELIC|\bDI\b|DI1|IRF-?M|CETIP")
# No nome do fundo é preciso ser mais restritivo: "DI" solto casa com muita
# coisa, então exigimos a forma como ela realmente aparece nos nomes.
_RE_NOME_INFLACAO = re.compile(r"\bIPCA\b|INFLAC|\bIMAB?\b|\bNTNB\b")
_RE_NOME_POS = re.compile(r"\bCDI\b|REFERENCIADO DI\b|\bSELIC\b|\bPOS[- ]?FIXAD")


def _sem_acento(texto) -> str:
    s = unicodedata.normalize("NFKD", str(texto or ""))
    return "".join(c for c in s if not unicodedata.combining(c)).upper()


def _do_indice(valor) -> str | None:
    """Traduz um rótulo de índice (CDI, IMA-B, IPCA…) para pós/inflação."""
    s = _sem_acento(valor)
    if not s or s == "NAN":
        return None
    if _RE_INFLACAO.search(s):
        return INFLACAO
    if _RE_POS.search(s):
        return POS
    return None  # IBOVESPA, IBX, dólar… não são eixo de crédito


def _do_nome(nome) -> str | None:
    s = _sem_acento(nome)
    tem_inf = bool(_RE_NOME_INFLACAO.search(s))
    tem_pos = bool(_RE_NOME_POS.search(s))
    if tem_inf and not tem_pos:
        return INFLACAO
    if tem_pos and not tem_inf:
        return POS
    return None  # os dois no nome, ou nenhum: não dá para afirmar


def avaliar(fundo: dict) -> tuple[str | None, str | None, str | None]:
    """Devolve (perfil, origem, detalhe) para o fundo.

    perfil : "pos" | "inflacao" | None
    origem : "declarado" | "inferido" | None
    detalhe: o rótulo que decidiu, ou a volatilidade que embasou a inferência
    """
    # --- 1) declarado: exato, tem prioridade sobre qualquer inferência ---
    for campo in ("indice_referencia", "indice_taxa_perfm"):
        perfil = _do_indice(fundo.get(campo))
        if perfil:
            return perfil, DECLARADO, str(fundo.get(campo)).strip()

    perfil = _do_nome(fundo.get("nome"))
    if perfil:
        return perfil, DECLARADO, "nome do fundo"

    if not settings.PERFIL_INDEXADOR_INFERIR:
        return None, None, None

    # --- 2) inferido pelo comportamento da cota ---
    vol = fundo.get("vol_pct")
    if vol is None:
        return None, None, None
    baixo = settings.PERFIL_VOL_ZONA_BAIXA
    alto = settings.PERFIL_VOL_ZONA_ALTA
    if vol < baixo:
        return POS, INFERIDO, f"vol. {vol:.2f}%"
    if vol > alto:
        return INFLACAO, INFERIDO, f"vol. {vol:.2f}%"
    # Zona cinzenta: o acerto cai de 87% para 77%. Preferimos não afirmar.
    return None, None, f"vol. {vol:.2f}% (zona indefinida)"
