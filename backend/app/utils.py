"""Helpers pequenos usados por mais de um módulo."""
from __future__ import annotations

import re

import pandas as pd

# Um CNPJ tem 14 dígitos; um CPF, 11. Comprimento ENTRE os dois não é nem um
# nem outro — só pode ser um CNPJ que perdeu zeros à esquerda no caminho. Ver
# a nota em `so_digitos`.
_CNPJ_DIGITOS = 14
_MENOR_CNPJ_TRUNCADO = 12


def so_digitos(valor) -> str | None:
    """'33.149.272/0001-07' -> '33149272000107'. None se não houver dígito.

    Cada base da CVM formata CNPJ de um jeito: o informe diário vem pontuado,
    o registro vem só com dígitos, a planilha do Quantum vem pontuada. Todo
    casamento passa por aqui antes de comparar.

    >>> O ZERO À ESQUERDA (corrigido em 20/08/2026)

    Quando a coluna vem SEM pontuação, o pandas a lê como int64 e o zero
    inicial desaparece: `07.661.541/0001-00` vira `7661541000100`, com 13
    dígitos. O CNPJ continua parecendo um CNPJ, o `.map` não reclama, e o
    casamento simplesmente não acontece — o fundo perde gestor, administrador,
    classificação e PL sem nenhum erro no log.

    Medido no relatório de 20/08/2026: 358 dos 4.603 fundos (7,8%) não casavam
    com o cadastro da CVM só por isso. Com o preenchimento, 4.602 casam.

    A conta só é feita entre 12 e 13 dígitos porque aí não há ambiguidade: CPF
    tem 11 e CNPJ tem 14, então nada legítimo cai nessa faixa. Um valor de 11
    dígitos fica intocado — pode ser um CPF de verdade, e transformá-lo num
    CNPJ inventado seria pior que não casar.
    """
    if valor is None or (isinstance(valor, float) and pd.isna(valor)):
        return None

    # >>> O `.0` DO FLOAT PRECISA MORRER ANTES DA EXPRESSÃO REGULAR
    #
    # Um join do pandas que introduz NaN promove a coluna inteira a float, e
    # `07661541000100` volta como `7661541000100.0`. Jogar isso direto na regex
    # produz `76615410001000` — quatorze dígitos, comprimento de CNPJ válido,
    # com um zero A MAIS no fim. O valor não parece errado em lugar nenhum: ele
    # só nunca casa, e o fundo some do enriquecimento sem uma linha de log.
    #
    # Era o que corrompia `cnpj_classe` no cadastro da CVM antes de 20/08/2026.
    if isinstance(valor, float) and valor.is_integer():
        valor = int(valor)
    else:
        texto = str(valor).strip()
        if texto.endswith(".0") and texto[:-2].replace("-", "").isdigit():
            valor = texto[:-2]

    d = re.sub(r"\D", "", str(valor))
    if not d:
        return None
    if _MENOR_CNPJ_TRUNCADO <= len(d) < _CNPJ_DIGITOS:
        return d.zfill(_CNPJ_DIGITOS)
    return d


# "Management" não distingue uma gestora de outra: numa lista em que quase toda
# casa é "Asset Management" ou "Wealth Management", a palavra só consome largura
# de coluna. Sai do nome exibido — a identidade continua no que vem antes dela.
#
# Medido no arquivo de 20/08/2026: 52 das 352 gestoras traziam a palavra, e
# removê-la produz ZERO colisão (nenhuma dupla de gestoras vira o mesmo nome).
# Se um dia produzir, é sinal de que duas casas distintas só se diferenciavam
# por ela — e aí a remoção passa a ser uma perda de informação, não uma limpeza.
_RE_MANAGEMENT = re.compile(r"\bMANAGEMENT\b", re.IGNORECASE)


def limpar_gestora(nome) -> str:
    """'BTG Pactual Asset Management' -> 'BTG Pactual Asset'.

    Aplicada no PONTO DE ENTRADA do nome (o conector), e não na tela: o nome da
    gestora é a CHAVE de agrupamento do painel inteiro — dossiê, ranking, série
    temporal e a tela de pressão somam por ele. Limpar só na exibição faria a
    mesma casa contar como duas quando um caminho passasse pelo nome limpo e
    outro pelo cru.

    Preserva a caixa original: o painel mostra "BB Asset", não "BB ASSET".
    """
    if nome is None:
        return ""
    limpo = _RE_MANAGEMENT.sub("", str(nome))
    # Espaço duplo sobra no meio quando a palavra estava entre outras duas;
    # pontuação sobra na ponta quando ela era o último termo ("Vinci, Management").
    limpo = re.sub(r"\s+", " ", limpo).strip()
    return limpo.strip(" -,.")
