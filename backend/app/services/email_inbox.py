"""
Captura do anexo da Quantum direto da caixa de e-mail — sem Outlook, sem Windows.

>>> O PROBLEMA QUE ISTO RESOLVE

`outlook_inbox.py` lê o Outlook instalado via COM. Funciona, e é o que roda
hoje, mas amarra a ingestão a três coisas que a nuvem não tem:

    · Windows        COM não existe em Linux;
    · uma MÁQUINA    se o notebook estiver desligado, o dado do dia não chega;
    · uma PESSOA     o e-mail precisa cair na caixa DAQUELE usuário.

O contorno atual é o `coletar_vinculado.py`: a máquina com Outlook empurra o
arquivo por HTTP. Isso tira o servidor do Windows, mas mantém o notebook como
ponto único de falha — e o pior modo de falha continua sendo o silencioso, em
que o painel serve a planilha da véspera sem erro na tela.

Aqui a direção se inverte de vez: o SERVIDOR busca o e-mail, na nuvem, sem
intermediário. O notebook deixa de fazer parte do caminho do dado.

>>> DOIS MODOS, PORQUE SÃO DUAS REALIDADES DIFERENTES

    EMAIL_MODO=graph   Microsoft Graph com client credentials. É o caminho
                       certo para caixa em Microsoft 365 (o caso desta casa).
                       Autentica como APLICAÇÃO, não como pessoa: não há senha
                       de usuário guardada, não quebra quando alguém troca a
                       própria senha, e o acesso pode ser limitado a UMA caixa
                       por Application Access Policy no Exchange Online.

    EMAIL_MODO=imap    IMAP puro (imaplib, biblioteca padrão). Para provedor
                       que não é Microsoft, ou para uma caixa de serviço com
                       senha de aplicativo. Suporta senha simples e XOAUTH2.

    EMAIL_MODO=off     desligado (padrão). Quem não configurar não passa a
                       falar com servidor de e-mail nenhum por acidente.

>>> O QUE ESTE MÓDULO **NÃO** FAZ, DE PROPÓSITO

Ele não valida a planilha, não escolhe o nome do arquivo, não deduplica e não
poda a pasta. Tudo isso já existe em `services/ingestao.py` e é chamado daqui.
Reimplementar seria criar um segundo conjunto de regras para o mesmo arquivo —
e a hora em que os dois discordassem seria a hora em que ninguém saberia qual
está certo.

O que ele faz é só: achar o e-mail, extrair o anexo, entregar os bytes e a
hora de recebimento para `ingestao.receber`.

>>> IDEMPOTÊNCIA

Rodar duas vezes no mesmo minuto não cria dois arquivos: `ingestao.receber`
compara o SHA-256 do conteúdo com o que já está na pasta. Por isso este módulo
pode ser chamado por um agendador burro, sem estado, quantas vezes quiser.
"""
from __future__ import annotations

import base64
import email
import imaplib
import logging
import re
import unicodedata
from datetime import datetime, timedelta
from email.header import decode_header, make_header
from email.utils import parsedate_to_datetime

import requests

from app.config import settings
from app.services import ingestao

logger = logging.getLogger("email_inbox")

# Extensões que valem a pena baixar. A conferência de verdade (é mesmo um
# .xlsx?) é do `ingestao.receber`, que olha o conteúdo; aqui o filtro é só para
# não gastar rede baixando a assinatura de imagem de todo mundo.
_EXTENSOES = (".xlsx", ".xlsm")

_GRAPH = "https://graph.microsoft.com/v1.0"
_LOGIN = "https://login.microsoftonline.com"


class SemNovidade(Exception):
    """Não havia e-mail novo que casasse. Não é erro — é o estado normal."""


def _normalizar(texto) -> str:
    """Minúsculas, sem acento, sem espaço duplicado — para comparar assunto."""
    texto = unicodedata.normalize("NFKD", str(texto or ""))
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", texto).strip().lower()


def _assunto_casa(assunto: str) -> bool:
    """Mesmo critério do `outlook_inbox`: SUBSTRING, sem acento e sem caixa.

    Casa "FW: Captação e resgate" e o original sem o prefixo. Exigir o "FW:"
    deixaria de fora o e-mail direto — foi um erro já cometido uma vez.
    """
    return _normalizar(settings.OUTLOOK_ASSUNTO) in _normalizar(assunto)


def _remetente_casa(remetente: str) -> bool:
    """Vazio em `OUTLOOK_REMETENTE` = qualquer remetente.

    O padrão é vazio porque o relatório é encaminhado por mais de uma pessoa da
    mesa; travar num endereço já fez o painel servir o arquivo da véspera sem
    erro nenhum na tela.
    """
    alvo = settings.OUTLOOK_REMETENTE.strip().lower()
    return not alvo or alvo in (remetente or "").lower()


# =============================================================================
#  Microsoft Graph — o caminho para Microsoft 365
# =============================================================================

def _token_graph() -> str:
    """Token de aplicação (client credentials). Sem usuário, sem senha de gente.

    O escopo é fixo em `.default`: as permissões vêm do registro do app no
    Entra ID, não do código. É assim que o time de segurança consegue auditar o
    que este serviço pode fazer sem ler o nosso repositório.
    """
    resp = requests.post(
        f"{_LOGIN}/{settings.GRAPH_TENANT_ID}/oauth2/v2.0/token",
        data={
            "client_id": settings.GRAPH_CLIENT_ID,
            "client_secret": settings.GRAPH_CLIENT_SECRET,
            "scope": "https://graph.microsoft.com/.default",
            "grant_type": "client_credentials",
        },
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def _buscar_graph() -> tuple[bytes, datetime, str]:
    """Anexo mais recente que casa, pela API do Graph.

    A filtragem acontece no SERVIDOR (`$filter` + `$top` + `$orderby`), não
    aqui: baixar a caixa inteira para procurar um assunto seria lento, caro em
    throttling e desnecessário.

    `hasAttachments eq true` corta a maior parte antes do assunto porque é o
    filtro mais barato para o Exchange resolver.
    """
    token = _token_graph()
    cabecalhos = {"Authorization": f"Bearer {token}"}

    caixa = settings.GRAPH_CAIXA
    pasta = settings.OUTLOOK_PASTA.strip()

    # A pasta é opcional: sem ela, procura na caixa inteira. Com ela, resolve o
    # id primeiro — o Graph não aceita nome de pasta na URL de mensagens.
    base = f"{_GRAPH}/users/{caixa}"
    if pasta:
        id_pasta = _id_pasta_graph(base, cabecalhos, pasta)
        if id_pasta is None:
            raise SemNovidade(f"Pasta '{pasta}' não existe na caixa {caixa}.")
        base = f"{base}/mailFolders/{id_pasta}"

    resp = requests.get(
        f"{base}/messages",
        headers=cabecalhos,
        params={
            "$select": "id,subject,receivedDateTime,from,hasAttachments",
            "$filter": "hasAttachments eq true",
            "$orderby": "receivedDateTime desc",
            "$top": str(settings.OUTLOOK_MAX_ITENS),
        },
        timeout=60,
    )
    resp.raise_for_status()

    for msg in resp.json().get("value", []):
        remetente = (
            msg.get("from", {}).get("emailAddress", {}).get("address", "")
        )
        if not _assunto_casa(msg.get("subject", "")) or not _remetente_casa(remetente):
            continue

        recebido = datetime.fromisoformat(
            msg["receivedDateTime"].replace("Z", "+00:00")
        ).astimezone().replace(tzinfo=None)

        anexos = requests.get(
            f"{base}/messages/{msg['id']}/attachments",
            headers=cabecalhos,
            # `contentBytes` só existe em fileAttachment; pedir explicitamente
            # evita trazer o corpo de anexo de item (e-mail encaminhado como
            # anexo), que viria como MIME e não como arquivo.
            params={"$select": "name,contentType,size,contentBytes"},
            timeout=120,
        )
        anexos.raise_for_status()

        for anexo in anexos.json().get("value", []):
            nome = str(anexo.get("name", ""))
            if not nome.lower().endswith(_EXTENSOES):
                continue
            conteudo = anexo.get("contentBytes")
            if not conteudo:
                continue
            logger.info(
                "Graph: anexo %r do e-mail %r (de %s, %s).",
                nome, msg.get("subject"), remetente or "?", recebido,
            )
            return base64.b64decode(conteudo), recebido, remetente

    raise SemNovidade(
        f"Nenhum e-mail com assunto contendo {settings.OUTLOOK_ASSUNTO!r} e "
        f"anexo {'/'.join(_EXTENSOES)} nos {settings.OUTLOOK_MAX_ITENS} mais "
        f"recentes da caixa {caixa}."
    )


def _id_pasta_graph(base: str, cabecalhos: dict, nome: str) -> str | None:
    """Resolve o nome da pasta para o id que o Graph exige.

    Desce até dois níveis, como o `outlook_inbox` faz: a pasta que interessa é
    filha da caixa de entrada ou da raiz, e varrer a árvore inteira custaria
    uma chamada por pasta numa caixa que pode ter centenas.
    """
    alvo = _normalizar(nome)

    def procurar(url: str, profundidade: int) -> str | None:
        if profundidade > 2:
            return None
        r = requests.get(
            url, headers=cabecalhos,
            params={"$select": "id,displayName", "$top": "200"}, timeout=30,
        )
        r.raise_for_status()
        filhas = r.json().get("value", [])
        for f in filhas:
            if _normalizar(f.get("displayName")) == alvo:
                return f["id"]
        for f in filhas:
            achado = procurar(
                f"{base}/mailFolders/{f['id']}/childFolders", profundidade + 1
            )
            if achado:
                return achado
        return None

    return procurar(f"{base}/mailFolders", 0)


# =============================================================================
#  IMAP — o caminho genérico
# =============================================================================

def _buscar_imap() -> tuple[bytes, datetime, str]:
    """Anexo mais recente que casa, por IMAP.

    A busca no servidor é por DATA, não por assunto: o `SEARCH SUBJECT` do IMAP
    compara bytes crus e não tem a menor chance com "Captação" — o assunto
    chega codificado em RFC 2047 (`=?UTF-8?Q?Capta=C3=A7=C3=A3o?=`) e a grafia
    depende do cliente que enviou. Filtramos por janela de dias no servidor,
    que é barato e confiável, e decodificamos o assunto aqui.
    """
    conexao = imaplib.IMAP4_SSL(settings.IMAP_HOST, settings.IMAP_PORT)
    try:
        if settings.IMAP_OAUTH_TOKEN:
            # XOAUTH2: mesma caixa, mesmo protocolo, credencial moderna. Usado
            # quando o provedor já desligou autenticação básica mas o modo
            # Graph não se aplica (Gmail, por exemplo).
            cadeia = (
                f"user={settings.IMAP_USUARIO}\x01"
                f"auth=Bearer {settings.IMAP_OAUTH_TOKEN}\x01\x01"
            )
            conexao.authenticate("XOAUTH2", lambda _: cadeia.encode())
        else:
            conexao.login(settings.IMAP_USUARIO, settings.IMAP_SENHA)

        conexao.select(settings.IMAP_PASTA, readonly=True)

        desde = (datetime.now() - timedelta(days=settings.EMAIL_DIAS)).strftime("%d-%b-%Y")
        _, dados = conexao.search(None, f'(SINCE "{desde}")')
        ids = dados[0].split()
        if not ids:
            raise SemNovidade(f"Nenhum e-mail em {settings.IMAP_PASTA} desde {desde}.")

        # Do mais recente para o mais antigo, limitado como no Outlook.
        for uid in reversed(ids[-settings.OUTLOOK_MAX_ITENS:]):
            _, bruto = conexao.fetch(uid, "(RFC822)")
            if not bruto or not isinstance(bruto[0], tuple):
                continue
            msg = email.message_from_bytes(bruto[0][1])

            assunto = str(make_header(decode_header(msg.get("Subject", ""))))
            remetente = email.utils.parseaddr(msg.get("From", ""))[1]
            if not _assunto_casa(assunto) or not _remetente_casa(remetente):
                continue

            try:
                recebido = parsedate_to_datetime(msg.get("Date"))
                if recebido.tzinfo is not None:
                    recebido = recebido.astimezone().replace(tzinfo=None)
            except (TypeError, ValueError):
                recebido = datetime.now()

            for parte in msg.walk():
                nome = parte.get_filename()
                if not nome:
                    continue
                nome = str(make_header(decode_header(nome)))
                if not nome.lower().endswith(_EXTENSOES):
                    continue
                conteudo = parte.get_payload(decode=True)
                if not conteudo:
                    continue
                logger.info(
                    "IMAP: anexo %r do e-mail %r (de %s, %s).",
                    nome, assunto, remetente or "?", recebido,
                )
                return conteudo, recebido, remetente

        raise SemNovidade(
            f"Nenhum e-mail com assunto contendo {settings.OUTLOOK_ASSUNTO!r} e "
            f"anexo nos {settings.OUTLOOK_MAX_ITENS} mais recentes."
        )
    finally:
        try:
            conexao.logout()
        except Exception:  # noqa: BLE001 — logout que falha não invalida o que já foi lido
            pass


# =============================================================================
#  Porta de entrada
# =============================================================================

def habilitado() -> bool:
    return settings.EMAIL_MODO in ("graph", "imap")


def sincronizar() -> ingestao.Recebido | None:
    """Busca o anexo do dia e o entrega à ingestão. None se não havia novidade.

    NUNCA levanta por causa do servidor de e-mail. Uma caixa fora do ar não
    pode derrubar o painel — ele continua servindo a última planilha que tem,
    e o log diz o que aconteceu. O que NÃO acontece é o silêncio: toda falha
    sai no log em nível WARNING, com o motivo.
    """
    if not habilitado():
        return None

    try:
        conteudo, recebido_em, remetente = (
            _buscar_graph() if settings.EMAIL_MODO == "graph" else _buscar_imap()
        )
    except SemNovidade as e:
        logger.info("Ingestão por e-mail: %s", e)
        return None
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "Falha ao ler a caixa de e-mail no modo %r (%s) — o painel segue "
            "com a última planilha recebida.", settings.EMAIL_MODO, e,
        )
        return None

    try:
        resultado = ingestao.receber(conteudo, recebido_em=recebido_em)
    except ingestao.IngestaoInvalida as e:
        # Anexo que não é planilha é achado, não ruído: alguém mandou outra
        # coisa com o mesmo assunto, e quem opera precisa saber.
        logger.warning("Anexo recusado (de %s): %s", remetente or "?", e)
        return None

    if resultado.ja_existia:
        logger.info("E-mail já ingerido antes: %s.", resultado.nome)
    else:
        logger.info("Planilha nova ingerida do e-mail: %s.", resultado.nome)
    return resultado
