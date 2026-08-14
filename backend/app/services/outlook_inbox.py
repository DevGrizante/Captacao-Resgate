"""
Ingestão do anexo de captação/resgate que chega por e-mail.

Todo dia útil de manhã chega, na pasta `Quantum` do Outlook, um e-mail de
`ottavio.lucca@bgcg.com` com assunto "FW: Captação e resgate" e um anexo
`vinculado_*.xlsx` gerado pelo Quantum Axis. Este módulo acha o e-mail MAIS
RECENTE que bate com esses critérios, salva o anexo em `data/inbox/` e devolve
o caminho.

O nome do arquivo salvo carrega a data de recebimento
(`vinculado_AAAAMMDD_HHMM.xlsx`), então:
  * o histórico fica preservado em disco;
  * "o mais recente" é sempre o de maior nome/mtime;
  * re-sincronizar não baixa de novo o que já está lá.

Se o Outlook não estiver disponível (fechado, máquina sem Office, backend
rodando em servidor), `sincronizar()` não quebra: apenas devolve o arquivo mais
recente que já existir em `data/inbox/`. Assim o app sobe mesmo offline, só com
dados um pouco mais velhos.
"""
from __future__ import annotations

import logging
import re
import unicodedata
from datetime import datetime
from pathlib import Path

from app.config import INBOX_DIR, settings

logger = logging.getLogger("outlook_inbox")

# Propriedade MAPI PR_SENT_REPRESENTING_SMTP_ADDRESS — o SenderEmailAddress de
# um remetente interno vem como DN do Exchange (/O=EXCHANGELABS/...), inútil
# para comparar com um e-mail. Esta propriedade devolve o SMTP de verdade.
_PROP_SMTP = "http://schemas.microsoft.com/mapi/proptag/0x5D01001F"

_PADRAO_ARQUIVO = "vinculado_*.xlsx"


def _normalizar(texto: str) -> str:
    """Minúsculas, sem acento e sem espaço duplicado — para comparar assunto."""
    texto = unicodedata.normalize("NFKD", str(texto))
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", texto).strip().lower()


def arquivo_mais_recente() -> Path | None:
    """Último `vinculado_*.xlsx` já baixado, ou None se a inbox estiver vazia."""
    arquivos = sorted(INBOX_DIR.glob(_PADRAO_ARQUIVO))
    return arquivos[-1] if arquivos else None


def _smtp_do_remetente(mail) -> str:
    """Extrai o SMTP do remetente, tentando as duas rotas do MAPI."""
    try:
        smtp = mail.PropertyAccessor.GetProperty(_PROP_SMTP)
        if smtp:
            return str(smtp).lower()
    except Exception:  # noqa: BLE001 — propriedade ausente em alguns itens
        pass
    try:
        remetente = mail.Sender
        if remetente is not None:
            if remetente.Type == "EX":
                return str(remetente.GetExchangeUser().PrimarySmtpAddress).lower()
            return str(remetente.Address).lower()
    except Exception:  # noqa: BLE001
        pass
    return str(getattr(mail, "SenderEmailAddress", "") or "").lower()


def _achar_pasta(namespace, nome_alvo: str):
    """Procura uma pasta pelo nome dentro da caixa de correio principal.

    Fica restrito ao store padrão de propósito: varrer todos os stores encosta
    em "Public Folders" e no arquivo online, que são resolvidos pela rede e
    levam mais de um minuto. A pasta que interessa mora na caixa local.
    """
    alvo = _normalizar(nome_alvo)

    def buscar(pastas, profundidade: int):
        if profundidade > 2:
            return None
        for pasta in pastas:
            try:
                if _normalizar(pasta.Name) == alvo:
                    return pasta
                encontrada = buscar(pasta.Folders, profundidade + 1)
                if encontrada is not None:
                    return encontrada
            except Exception:  # noqa: BLE001
                continue
        return None

    try:
        raiz = namespace.DefaultStore.GetRootFolder()
    except Exception as e:  # noqa: BLE001
        logger.warning("Não consegui abrir o store padrão do Outlook: %s", e)
        return None
    return buscar(raiz.Folders, 0)


def _baixar_do_outlook() -> Path | None:
    """Salva o anexo do e-mail mais recente que casa com os filtros.

    Devolve o caminho salvo (ou o já existente, se o anexo daquele e-mail já
    tiver sido baixado antes). None se nada casar.
    """
    import pythoncom  # type: ignore[import-not-found]
    import win32com.client  # type: ignore[import-not-found]

    # A API roda os endpoints síncronos num threadpool; COM exige inicialização
    # por thread.
    pythoncom.CoInitialize()
    try:
        outlook = win32com.client.Dispatch("Outlook.Application")
        namespace = outlook.GetNamespace("MAPI")

        pasta = _achar_pasta(namespace, settings.OUTLOOK_PASTA)
        if pasta is None:
            logger.warning("Pasta '%s' não encontrada no Outlook.", settings.OUTLOOK_PASTA)
            return None

        itens = pasta.Items
        itens.Sort("[ReceivedTime]", True)  # mais recente primeiro

        remetente_alvo = settings.OUTLOOK_REMETENTE.strip().lower()
        assunto_alvo = _normalizar(settings.OUTLOOK_ASSUNTO)

        analisados = 0
        for mail in itens:
            analisados += 1
            if analisados > settings.OUTLOOK_MAX_ITENS:
                break
            try:
                if assunto_alvo not in _normalizar(mail.Subject):
                    continue
                if remetente_alvo and remetente_alvo not in _smtp_do_remetente(mail):
                    continue

                recebido: datetime = mail.ReceivedTime
                for anexo in mail.Attachments:
                    if not str(anexo.FileName).lower().endswith((".xlsx", ".xlsm")):
                        continue
                    destino = INBOX_DIR / (
                        f"vinculado_{recebido.strftime('%Y%m%d_%H%M')}.xlsx"
                    )
                    if destino.exists():
                        logger.info("Anexo de %s já estava na inbox.", recebido)
                        return destino
                    anexo.SaveAsFile(str(destino))
                    logger.info("Anexo salvo: %s", destino.name)
                    return destino
            except Exception as e:  # noqa: BLE001 — um item ruim não pode parar a varredura
                logger.debug("Item ignorado na varredura do Outlook: %s", e)
                continue

        logger.warning(
            "Nenhum e-mail de '%s' com assunto '%s' e anexo .xlsx nos %d itens "
            "mais recentes de '%s'.",
            settings.OUTLOOK_REMETENTE, settings.OUTLOOK_ASSUNTO,
            settings.OUTLOOK_MAX_ITENS, settings.OUTLOOK_PASTA,
        )
        return None
    finally:
        pythoncom.CoUninitialize()


def sincronizar() -> Path | None:
    """Garante que a inbox tenha o anexo mais recente e devolve o caminho dele.

    Nunca levanta exceção por causa do Outlook: se a leitura falhar, cai para o
    arquivo mais recente já baixado.
    """
    if not settings.OUTLOOK_ENABLED:
        logger.info("OUTLOOK_ENABLED=false — usando o arquivo local mais recente.")
        return arquivo_mais_recente()

    try:
        baixado = _baixar_do_outlook()
        if baixado is not None:
            return baixado
    except ImportError:
        logger.warning("pywin32 não instalado — pulando a leitura do Outlook.")
    except Exception as e:  # noqa: BLE001
        logger.warning("Falha ao ler o Outlook (%s) — usando o arquivo local.", e)

    return arquivo_mais_recente()
