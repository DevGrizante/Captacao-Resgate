"""
Decide se um fundo é de crédito privado.

O relatório é para broker de crédito: fundo que não é de crédito privado só
polui a mesa. Mas "é de crédito privado" não vem carimbado num único campo —
cada fonte responde uma parte, com cobertura diferente. Combinamos quatro
sinais, do mais forte para o mais fraco:

    1. PERFIL_MENSAL, `PR_ATIVO_CRED_PRIV`  — QUANTITATIVO, o melhor sinal:
       é o % do PL que o próprio fundo declara em crédito privado.  (82,8%)
    2. EXTRATO, `ATIVO_CRED_PRIV = S`       — a política do fundo permite
       crédito privado. Booleano, não diz quanto.                    (68,9%)
    3. Classificação ANBIMA do registro     — "Crédito Livre" / "Grau de
       Invest." são as categorias de crédito.                        (40,0%)
    4. Nome do fundo                        — "CRÉDITO PRIVADO", ou infra /
       debênture incentivada, que é crédito por natureza.            (58,2%)

Sozinho, nenhum resolve; juntos cobrem 92,6% do arquivo. Os sinais entram em
OR de propósito: num relatório de mesa, deixar de fora um fundo de crédito
que existe é pior que deixar entrar um multimercado que não é — o primeiro é
uma oportunidade invisível, o segundo é uma linha a mais que o usuário
reconhece e ignora.

`MODO` controla o rigor:
    todos    — não filtra nada (comportamento anterior)
    sinal    — qualquer um dos quatro sinais (padrão)
    estrito  — só o sinal quantitativo, acima do limiar de % do PL
"""
from __future__ import annotations

import re
import unicodedata

# Categorias ANBIMA que são, por definição, crédito.
_ANBIMA_CREDITO = ("crédito livre", "credito livre", "grau de invest")

# Nome que denuncia crédito privado. Debênture incentivada / FI-Infra é
# crédito privado por natureza, mesmo sem a expressão no nome.
_RE_NOME_CREDITO = re.compile(
    r"CREDITO PRIVADO|CRED PRIV|\bFI INFRA\b|INCENTIVAD|DEBENTURE|\bCRA\b|\bCRI\b|FIDC",
)


def _sem_acento(texto: str) -> str:
    s = unicodedata.normalize("NFKD", str(texto or ""))
    return "".join(c for c in s if not unicodedata.combining(c)).upper()


def sinais(fundo: dict, limiar_pct: float) -> dict:
    """Quais sinais apontam crédito privado neste fundo, e por quê."""
    pct = fundo.get("pct_credito_privado")
    return {
        "perfil_cvm": pct is not None and pct >= limiar_pct,
        "extrato_cvm": bool(fundo.get("credito_privado")),
        "anbima": any(
            t in str(fundo.get("classificacao_anbima") or "").lower()
            for t in _ANBIMA_CREDITO
        ),
        "nome": bool(_RE_NOME_CREDITO.search(_sem_acento(fundo.get("nome")))),
    }


def avaliar(fundo: dict, modo: str, limiar_pct: float) -> tuple[bool, list[str]]:
    """Devolve (é crédito privado?, quais sinais dispararam).

    A lista de sinais viaja junto até o front: quando o usuário estranhar um
    fundo na lista, ele consegue ver por que ele entrou, em vez de ter que
    confiar num filtro opaco.
    """
    if modo == "todos":
        return True, []

    marcados = sinais(fundo, limiar_pct)
    quais = [k for k, v in marcados.items() if v]

    if modo == "estrito":
        return marcados["perfil_cvm"], quais
    return bool(quais), quais
