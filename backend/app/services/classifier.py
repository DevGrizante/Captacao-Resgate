"""
Classificação de fundos: LF, Incentivada, Tradicional ou Misto.

REGRA HIERÁRQUICA (validada com o negócio)

    1) Se mais de THRESHOLD_MAJORITARIO da carteira está em Letras Financeiras
       -> LF. O fundo é de papel bancário, não de crédito corporativo.

    2) O que sobra é fundo de crédito, e aí a pergunta muda: o fundo entrega
       spread de crédito indexado à inflação ou ao CDI? A resposta tem DUAS
       verificações, e as duas precisam bater.

       INCENTIVADA  nome traz "Incentivada"/"Incentivado" ou "Infra"
                    E a carteira é majoritariamente IPCA+

       TRADICIONAL  nome NÃO traz essas palavras
                    E a carteira está atrelada a CDI+

    3) Nome e carteira discordando -> MISTO, com o motivo registrado.

O corte é o mesmo nos dois degraus e é editável em tempo de execução pelo
painel de controle (services/parametros.py). Ele vale sobre bases diferentes:
LF sobre a carteira de crédito inteira, indexador sobre a base EX-LF. Tirar LF
da conta antes é intencional — LF é instrumento, não indexador, e deixá-la no
denominador faria uma carteira metade LF / metade debênture IPCA+ parecer não
ter indexador dominante.

>>> O HEDGE EM DAP DEIXOU DE DECIDIR O BUCKET (18/08/2026)

Por decisão de negócio, nome + carteira IPCA+ bastam para Incentivada. A
cobertura de DAP continua medida, guardada e visível no dossiê — ela só não
separa mais os dois produtos. Eram 669 fundos com mandato de infra que
carregam juro real na cota e caíam em Misto por isso.

Para voltar a exigir a perna de hedge, basta `INCENTIVADA_EXIGE_HEDGE_DAP=true`
no .env: o código do teste continua aqui, apenas desligado por padrão.

>>> O QUE É "COMPORTAMENTO DE IPCA+", E POR QUE A PERNA DE HEDGE FOI MEDIDA

O fundo de debênture incentivada compra o papel em B + spread (NTN-B de
referência mais o prêmio de crédito) e vende cupom de IPCA no futuro de DAP.
O que sobra na cota é só o spread de crédito: a perna de inflação foi travada.
Um fundo que compra o mesmo papel e NÃO vende DAP está com outra tese — está
comprado em juro real, e o cotista carrega a marcação da NTN-B junto.

São produtos diferentes para a mesa, e é por isso que o nome sozinho não
classifica. Medido no CDA de 2026-04, entre os 1.281 fundos com "Incentivad*"
no nome e carteira legível, só 787 (61%) tinham posição em DAP — os outros 494
seriam vendidos como a mesma coisa sem esta verificação.

A recíproca também vale e está implementada: um fundo SEM "Incentivad*" no
nome que compra IPCA+ e trava tudo em DAP entrega, na prática, CDI+. Ele entra
como TRADICIONAL, porque é isso que o cotista recebe.

>>> DE ONDE VEM CADA NÚMERO
    pct_lf / pct_ipca / pct_cdi ... CDA da CVM + registro do SND, já como
                                    frações da carteira de crédito
    hedge_dap ..................... futuro de DAP no BLC_8 do mesmo CDA
    nome .......................... cadastro do fundo

    Ver connectors/cvm_carteira.py, que monta os três.
"""
from __future__ import annotations

import re
import unicodedata

from app.config import settings
from app.models.schemas import Bucket

# Duas famílias de nome declaram debênture incentivada:
#
#   INCENTIVAD[AO]  "Debêntures Incentivadas", "Incentivado"… o radical cobre
#                   as flexões e não há outro termo parecido em nome de fundo.
#   INFRA           "FI INFRA" é o sufixo padrão do Fundo de Investimento em
#                   Infraestrutura, que por construção carrega papel da lei
#                   12.431. São 1.291 fundos que o primeiro padrão não pegava —
#                   quase dez vezes o universo que tínhamos.
#
# O segundo casa apenas com a palavra inteira ou com as formas por extenso
# ("INFRAESTRUTURA", "INFRASTRUTTURA"). Sem isso, `\bINFRA` pegaria "INFRANET",
# que é nome de empresa na carteira e não o mandato do fundo.
_RE_INCENTIVADA = re.compile(r"INCENTIVAD[AO]|\bINFRA(\b|ESTRUTURA\b|STRUTTURA\b)")


def _sem_acento(texto) -> str:
    s = unicodedata.normalize("NFKD", str(texto or ""))
    return "".join(c for c in s if not unicodedata.combining(c)).upper()


def nome_incentivado(nome) -> bool:
    """O nome do fundo declara debênture incentivada?

    É a primeira metade da dupla verificação: sem isso no nome, o fundo não
    pode ser Incentivada por mais que a carteira pareça.
    """
    return bool(_RE_INCENTIVADA.search(_sem_acento(nome)))


def tem_hedge_dap(dap_cobertura: float | None) -> bool:
    """A posição em DAP é grande o bastante para travar a carteira IPCA+?

    O conector mede a cobertura (nocional em DAP / R$ em papel IPCA+); a régua
    fica aqui, e não lá, porque `HEDGE_DAP_MINIMO` é editável pelo painel — o
    julgamento precisa ser refeito a cada reclassificação, sobre a medição que
    já está em memória.

    `None` é "não sabemos" (fundo sem carteira lida) e vira falso: um hedge que
    não foi observado não pode promover um fundo a Incentivada.

    A cobertura precisa ser estritamente positiva ANTES de passar pelo corte.
    Sem isso, baixar o corte a 0% pelo painel faria todo fundo sem posição
    nenhuma em DAP contar como travado — a verificação viraria vacuosa
    justamente no ajuste que parece só afrouxá-la. Com o corte em 0%, o que
    passa a valer é "qualquer posição em DAP conta", que é o que se espera.
    """
    if dap_cobertura is None:
        return False
    cobertura = float(dap_cobertura)
    return cobertura > 0 and cobertura >= settings.HEDGE_DAP_MINIMO


def classificar(
    pct_lf: float,
    pct_ipca: float,
    pct_cdi: float,
    nome: str = "",
    dap_cobertura: float | None = None,
) -> tuple[Bucket, str]:
    """Aplica a regra hierárquica e devolve (bucket, motivo).

    Args:
        pct_lf: fração da carteira de crédito em Letras Financeiras (0..1)
        pct_ipca: fração indexada a IPCA, sobre a base ex-LF (0..1)
        pct_cdi: fração indexada a CDI/DI, sobre a base ex-LF (0..1)
        nome: nome do fundo, para a verificação de "Incentivada/o"
        dap_cobertura: nocional em futuro de DAP sobre o R$ da carteira IPCA+.
            `None` significa que não houve leitura de carteira para este fundo.

    Returns:
        (Bucket, motivo). O motivo viaja até a tela: quando o nome diz uma
        coisa e a carteira diz outra, o usuário precisa ver qual das duas
        pontas falhou, em vez de encontrar o fundo em "Misto" sem explicação.
    """
    thr = settings.THRESHOLD_MAJORITARIO
    thr_txt = f"{thr:.0%}"
    hedge_dap = tem_hedge_dap(dap_cobertura)

    # --- 1) LF domina a carteira inteira? ---
    if pct_lf > thr:
        return Bucket.LF, (
            f"Letras Financeiras são {pct_lf:.0%} da carteira de crédito, "
            f"acima do corte de {thr_txt}."
        )

    # --- 2) É fundo de crédito. Reescala o restante removendo a LF. ---
    base_ex_lf = 1.0 - pct_lf
    if base_ex_lf <= 0:
        return Bucket.MISTO, "Carteira toda em LF — sem base para medir indexador."

    share_ipca = pct_ipca / base_ex_lf
    share_cdi = pct_cdi / base_ex_lf
    ipca_dominante = share_ipca > thr
    cdi_dominante = share_cdi > thr
    exige_hedge = settings.INCENTIVADA_EXIGE_HEDGE_DAP

    # --- 2a) Nome de incentivada: precisa da carteira confirmando IPCA+ ---
    if nome_incentivado(nome):
        if not ipca_dominante:
            return Bucket.MISTO, (
                f"Nome de incentivada, mas só {share_ipca:.0%} da carteira "
                f"ex-LF é IPCA+ (corte de {thr_txt})."
            )
        if exige_hedge and not hedge_dap:
            return Bucket.MISTO, (
                f"Nome de incentivada e carteira {share_ipca:.0%} IPCA+, mas "
                "sem hedge em DAP: o fundo carrega o cupom de inflação, não "
                "só o spread de crédito."
            )
        return Bucket.INCENTIVADA, (
            f"Nome de incentivada e carteira {share_ipca:.0%} IPCA+"
            + (" com hedge em DAP." if hedge_dap else ".")
        )

    # --- 2b) Sem o nome: é Tradicional se a carteira for atrelada a CDI+ ---
    if cdi_dominante:
        return Bucket.TRADICIONAL, (
            f"Carteira {share_cdi:.0%} atrelada a CDI+, acima do corte de {thr_txt}."
        )

    # Comprar IPCA+ e travar em DAP entrega spread de crédito puro — o mesmo
    # que CDI+ para quem tem a cota. Sem esta linha, a casa que faz o hedge
    # apareceria em Misto justamente por fazer o hedge.
    if ipca_dominante and hedge_dap:
        return Bucket.TRADICIONAL, (
            f"Carteira {share_ipca:.0%} IPCA+ travada em DAP: o cotista recebe "
            "spread de crédito, equivalente a CDI+."
        )

    # Carteira de inflação sem hedge e sem o nome: não é Tradicional (o cotista
    # carrega juro real) nem Incentivada (o nome não declara). Dizer isso é
    # melhor que a mensagem genérica de "nenhum indexador domina", que seria
    # falsa aqui — o indexador domina, o que não fecha é a regra.
    if ipca_dominante:
        return Bucket.MISTO, (
            f"Carteira {share_ipca:.0%} IPCA+ sem hedge em DAP e sem "
            "\"Incentivada/o\" no nome: carrega juro real, não é CDI+."
        )

    return Bucket.MISTO, (
        f"Nenhum indexador domina a carteira ex-LF: IPCA+ {share_ipca:.0%}, "
        f"CDI+ {share_cdi:.0%}, corte de {thr_txt}."
    )


# =============================================================================
#  Fallback: o fundo sem carteira lida também precisa de bucket
# =============================================================================
#
# >>> POR QUE ISTO PASSOU A EXISTIR (20/08/2026)
#
# A regra acima só decide quando há composição de carteira. Sem ela o fundo
# saía com `bucket = None`, e a intenção era boa: MISTO significa "olhamos a
# carteira e nada domina", não "não olhamos", e carimbar um bucket adivinhado
# apagaria essa diferença.
#
# O problema é que o relatório não é sobre a carteira — é sobre o FLUXO da
# gestora. Um fundo sem bucket some de toda visão que agrupa por bucket, e com
# ele some o fluxo dele. Medido no arquivo de 20/08/2026:
#
#     4.867 fundos no painel
#     3.108 com bucket        (63,9%)
#     1.759 sem bucket        R$ 5,6 bi de fluxo semanal (13,6% do total)
#                             R$ 423,9 bi de PL          (12,0% do total)
#
# Treze por cento do fluxo invisível numa tela cujo propósito é mostrar fluxo.
# O custo de errar um rótulo é menor que o custo de omitir o fluxo.
#
# >>> O QUE NÃO SE PERDE
#
# A distinção continua registrada, em dois lugares: `bucket_origem` diz se o
# bucket foi MEDIDO na carteira ou INFERIDO, e o motivo continua explicando de
# onde ele veio. Quem precisar do universo com carteira de verdade filtra por
# `bucket_origem == "carteira"`, e nada do que existia antes muda de sentido.
#
# >>> POR QUE ESTES SINAIS, E POR QUE NESTA ORDEM
#
# São os únicos que sobram quando não há CDA, e cada um espelha um degrau da
# regra principal:
#
#     nome de infra          -> mesma primeira metade da dupla verificação
#     perfil do cotista      -> o mais próximo de "indexador" que existe sem
#                               carteira (services/perfil_indexador.py)
#     nada                   -> TRADICIONAL
#
# TRADICIONAL é o padrão, e não MISTO, justamente para preservar o sentido de
# MISTO. Um fundo de crédito privado sem mandato de infra declarado é, por
# definição do negócio, um fundo de crédito tradicional — é o que a mesa
# assumiria olhando só o nome.

ORIGEM_CARTEIRA = "carteira"
ORIGEM_INFERIDO = "inferido"


def classificar_sem_carteira(
    nome: str = "",
    perfil_indexador: str | None = None,
    carteira_motivo: str | None = None,
) -> tuple[Bucket, str]:
    """Bucket para quem não tem composição lida. NUNCA devolve None.

    Args:
        nome: nome do fundo, para a verificação de "Incentivada/o" / "Infra".
        perfil_indexador: perfil do COTISTA ("pos" | "inflacao" | None), de
            `services/perfil_indexador.py`. Não é a carteira — é o que a cota
            entregou —, e por isso o motivo devolvido diz isso com todas as
            letras, para ninguém ler o rótulo como se fosse medição de carteira.
        carteira_motivo: por que a carteira não serviu, quando ela foi lida e
            descartada ("pouco crédito no PL", "indexador desconhecido",
            "carteira sob sigilo"). Entra no texto para a tela distinguir
            "não estava no CDA" de "estava e não deu para usar".

    Returns:
        (Bucket, motivo). O motivo SEMPRE começa dizendo que é inferido.
    """
    if carteira_motivo:
        porque = f"Carteira lida mas descartada ({carteira_motivo})"
    else:
        porque = "Fundo não consta no CDA do mês lido"

    if nome_incentivado(nome):
        return Bucket.INCENTIVADA, (
            f"{porque}. Classificado pelo NOME, que declara infraestrutura ou "
            "debênture incentivada — sem carteira para confirmar."
        )

    if perfil_indexador == "inflacao":
        return Bucket.MISTO, (
            f"{porque}. A cota se comporta como indexada à inflação, mas o nome "
            "não declara infraestrutura: sem carteira, não dá para dizer se é "
            "juro real ou spread travado."
        )

    if perfil_indexador == "pos":
        return Bucket.TRADICIONAL, (
            f"{porque}. A cota se comporta como pós-fixada (CDI), e o nome não "
            "declara infraestrutura."
        )

    return Bucket.TRADICIONAL, (
        f"{porque} e sem perfil de cota observável. Classificado como crédito "
        "tradicional por ausência de sinal de infraestrutura — é o padrão, "
        "não uma medição."
    )
