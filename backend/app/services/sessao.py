"""
Sessão do painel: uma senha, compartilhada, com cookie assinado.

O QUE ISTO É E O QUE NÃO É

    É o mínimo defensável para colocar o painel num endereço público: sem ele,
    quem descobrisse a URL veria fluxo de captação e resgate por gestora e
    poderia alterar o corte de classificação para todo mundo.

    NÃO é controle de acesso por pessoa. Todos entram com a mesma senha, então
    o log não sabe quem fez o quê e tirar o acesso de alguém significa trocar a
    senha de todos. Isso é aceitável para seis pessoas conhecidas numa mesa; a
    etapa seguinte, já prevista, é usuário por usuário com banco.

POR QUE COOKIE ASSINADO E NÃO HTTP BASIC

    Basic Auth seria menos código, mas reenvia a credencial em toda requisição
    e não tem como "sair" sem fechar o navegador. Pior: ele ocupa o cabeçalho
    `Authorization`, que aqui já é do token de ingestão — o navegador passaria a
    mandar `Basic ...` para o `POST /api/inbox`, que espera `Bearer ...`, e as
    duas autenticações brigariam.

    Com cookie, as duas convivem: gente usa cookie, máquina usa token.

POR QUE HMAC NA MÃO

    O caminho idiomático seria o `SessionMiddleware` do Starlette, que depende
    de `itsdangerous` — pacote que este projeto não tem. Assinar a validade com
    HMAC-SHA256 da biblioteca padrão resolve o mesmo problema em vinte linhas e
    sem dependência nova, seguindo a mesma escolha feita na ingestão (corpo
    binário em vez de multipart).

    O cookie NÃO guarda segredo nenhum: só a hora em que expira, mais a
    assinatura dessa hora. Quem alterar a validade invalida a assinatura.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
import time
from dataclasses import dataclass, field

from app.config import settings

logger = logging.getLogger("sessao")

NOME_COOKIE = "painel_sessao"

# Segredo que assina os cookies. Vindo do .env, as sessões sobrevivem a um
# reinício do servidor; sorteado aqui, todo mundo é deslogado quando o serviço
# reinicia — chato, mas seguro. O que NÃO se faz é ter um valor padrão fixo no
# código: seria a mesma chave em toda instalação, e qualquer um poderia forjar
# um cookie válido para o painel de qualquer empresa.
_SEGREDO = settings.PAINEL_SEGREDO or secrets.token_urlsafe(32)
if not settings.PAINEL_SEGREDO:
    logger.warning(
        "PAINEL_SEGREDO não configurado: usando um segredo sorteado. As sessões "
        "abertas vão cair no próximo reinício. Defina no .env para evitar isso."
    )


def protegido() -> bool:
    """A senha está configurada?

    Sem `PAINEL_SENHA` o portão fica aberto, e é assim de propósito: quem roda
    em `127.0.0.1` na própria máquina não deveria ser obrigado a digitar senha
    para ver o próprio painel. O que não pode é isso passar despercebido em
    servidor — por isso o aviso no log ao subir e o alerta no README.
    """
    return bool(settings.PAINEL_SENHA)


def _assinar(ate: int) -> str:
    return hmac.new(_SEGREDO.encode(), str(ate).encode(), hashlib.sha256).hexdigest()


def criar_cookie() -> tuple[str, int]:
    """Devolve (valor do cookie, validade em segundos)."""
    duracao = settings.PAINEL_SESSAO_HORAS * 3600
    ate = int(time.time()) + duracao
    return f"{ate}.{_assinar(ate)}", duracao


def cookie_valido(valor: str | None) -> bool:
    """O cookie foi assinado por nós e ainda está no prazo?"""
    if not valor or "." not in valor:
        return False
    ate_txt, _, assinatura = valor.partition(".")
    try:
        ate = int(ate_txt)
    except ValueError:
        return False

    # Assinatura primeiro, prazo depois: conferir o prazo de um valor que
    # ninguém assinou seria dar informação sobre um dado que não é nosso.
    if not hmac.compare_digest(assinatura, _assinar(ate)):
        return False
    return ate > time.time()


def senha_confere(tentativa: str) -> bool:
    """Compara em tempo constante.

    `==` para no primeiro caractere diferente, e o tempo gasto vaza quantos
    caracteres o atacante já acertou. Custa nada fechar essa porta.
    """
    if not settings.PAINEL_SENHA:
        return False
    return hmac.compare_digest(tentativa or "", settings.PAINEL_SENHA)


# ---------------------------------------------------------------------------
#  Freio para tentativa em massa
# ---------------------------------------------------------------------------
# Uma senha única e curta é adivinhável por força bruta se o atacante puder
# tentar à vontade. O freio não impede um ataque determinado — impede o script
# bobo que dispara milhares de tentativas por minuto, que é o que de fato
# acontece com qualquer coisa exposta na internet.


@dataclass
class _Tentativas:
    marcas: list[float] = field(default_factory=list)


_JANELA_S = 300      # 5 minutos
_LIMITE = 8          # erros na janela antes de travar
_tentativas: dict[str, _Tentativas] = {}


def _limpar(t: _Tentativas, agora: float) -> None:
    t.marcas = [m for m in t.marcas if agora - m < _JANELA_S]


def bloqueado(ip: str) -> int:
    """Segundos que faltam para este IP poder tentar de novo. 0 = liberado."""
    t = _tentativas.get(ip)
    if t is None:
        return 0
    agora = time.time()
    _limpar(t, agora)
    if len(t.marcas) < _LIMITE:
        return 0
    return max(1, int(_JANELA_S - (agora - min(t.marcas))))


def registrar_erro(ip: str) -> None:
    t = _tentativas.setdefault(ip, _Tentativas())
    agora = time.time()
    _limpar(t, agora)
    t.marcas.append(agora)
    if len(t.marcas) >= _LIMITE:
        logger.warning("IP %s travado: %d senhas erradas em %d min.",
                       ip, len(t.marcas), _JANELA_S // 60)


def registrar_acerto(ip: str) -> None:
    """Acertou: zera o histórico.

    Sem isto, quem erra sete vezes e acerta na oitava continuaria a um erro do
    bloqueio pelo resto da janela.
    """
    _tentativas.pop(ip, None)
