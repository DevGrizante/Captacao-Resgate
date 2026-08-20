"""
Recebe a planilha do e-mail pela rede, em vez de pelo Outlook local.

POR QUE ISTO EXISTE

    A fonte padrão (`DATA_SOURCE=vinculado`) lê o anexo direto do Outlook
    instalado, via COM — tecnologia que só existe no Windows. Num servidor
    Linux isso não roda, e o pior é COMO não roda: `outlook_inbox.sincronizar()`
    cai silenciosamente para o arquivo mais recente já baixado. O painel
    continuaria servindo a planilha de sempre, sem erro na tela, para sempre.

    Com este módulo a direção se inverte. Em vez de o servidor ir buscar, a
    máquina que TEM Outlook empurra o arquivo para dentro
    (`backend/scripts/coletar_vinculado.py`). O servidor deixa de precisar de
    Windows, e o arquivo do dia vira um recurso de rede que qualquer outro
    sistema pode consumir.

O QUE ESTE MÓDULO GARANTE

    · O nome do arquivo é REESCRITO pelo servidor, nunca aceito do cliente.
      Nome vindo de fora é caminho para escrever fora da pasta.
    · O conteúdo é conferido de verdade (é um .xlsx?), não pela extensão.
    · O mesmo arquivo enviado duas vezes não vira dois arquivos.
    · A pasta não cresce sem limite.

O que NÃO está aqui: autenticação e HTTP. Ficam no router, porque são
protocolo; aqui é só a regra de o que pode entrar e com que nome.
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
import zipfile
from dataclasses import dataclass
from datetime import datetime
from io import BytesIO
from pathlib import Path

from app.config import INBOX_DIR, settings

logger = logging.getLogger("ingestao")

# Mesmo padrão que o outlook_inbox usa ao salvar. Precisa ser idêntico: o
# "mais recente" é decidido por ordem alfabética do nome, e o formato
# AAAAMMDD_HHMM é o que faz alfabético e cronológico coincidirem.
_FORMATO_NOME = "vinculado_%Y%m%d_%H%M.xlsx"
_PADRAO_ARQUIVO = "vinculado_*.xlsx"

# Assinatura de arquivo ZIP. Todo .xlsx é um zip — é assim que o formato
# funciona desde o Office 2007.
_MAGICA_ZIP = b"PK\x03\x04"


class IngestaoInvalida(Exception):
    """O que chegou não serve. A mensagem vai para o cliente."""


@dataclass(frozen=True)
class Recebido:
    """Resultado de uma ingestão."""

    caminho: Path
    sha256: str
    bytes_: int
    ja_existia: bool

    @property
    def nome(self) -> str:
        return self.caminho.name


def _conferir_planilha(conteudo: bytes) -> None:
    """O corpo é mesmo uma planilha do Excel?

    Confere o conteúdo, não a extensão. Um `.xlsx` que na verdade é um HTML de
    erro de proxy — coisa que acontece em rede corporativa — passaria por
    qualquer checagem de nome e só quebraria lá na frente, no pandas, com uma
    mensagem que não ajuda ninguém a entender que o download é que falhou.
    """
    if not conteudo:
        raise IngestaoInvalida("Corpo vazio: nenhum byte chegou.")

    teto = settings.INGESTAO_TAMANHO_MAXIMO_MB * 1024 * 1024
    if len(conteudo) > teto:
        raise IngestaoInvalida(
            f"Arquivo de {len(conteudo) / 1048576:.1f} MB acima do teto de "
            f"{settings.INGESTAO_TAMANHO_MAXIMO_MB} MB."
        )

    if not conteudo.startswith(_MAGICA_ZIP):
        raise IngestaoInvalida(
            "Não é um .xlsx: o arquivo não começa com a assinatura de ZIP. "
            "Costuma ser página de erro do proxy salva no lugar do anexo."
        )

    # Um zip qualquer também começa com PK. O que distingue uma planilha é ter
    # a peça central do formato lá dentro.
    try:
        with zipfile.ZipFile(BytesIO(conteudo)) as z:
            nomes = z.namelist()
    except zipfile.BadZipFile as e:
        raise IngestaoInvalida(f"ZIP corrompido: {e}") from e

    if "xl/workbook.xml" not in nomes:
        raise IngestaoInvalida(
            "É um ZIP, mas não uma planilha do Excel: falta xl/workbook.xml."
        )


def _nome_do_arquivo(recebido_em: datetime | None) -> str:
    """Monta o nome no padrão do projeto, a partir da hora de recebimento.

    O nome NUNCA vem do cliente. Aceitar `filename` de fora é o caminho
    clássico para `../../` escrever onde não deve — e aqui não há nada a
    ganhar com isso, já que o padrão do projeto é derivado só da data.
    """
    quando = recebido_em or datetime.now()
    return quando.strftime(_FORMATO_NOME)


def _hash(conteudo: bytes) -> str:
    return hashlib.sha256(conteudo).hexdigest()


def _procurar_igual(sha: str) -> Path | None:
    """Já temos este mesmo arquivo, byte a byte?

    O e-mail costuma ser reenviado (encaminhado por mais de uma pessoa da
    mesa), e o coletor pode rodar duas vezes no mesmo dia. Sem esta conferência
    a inbox encheria de cópias idênticas com nomes diferentes, e "o mais
    recente" passaria a ser decidido por qual chegou por último em vez de por
    qual é mais novo de fato.
    """
    for caminho in INBOX_DIR.glob(_PADRAO_ARQUIVO):
        try:
            if _hash(caminho.read_bytes()) == sha:
                return caminho
        except OSError:  # arquivo sumiu no meio da varredura
            continue
    return None


def _podar() -> int:
    """Mantém só as N planilhas mais recentes. Devolve quantas removeu.

    Roda depois de gravar, nunca antes: se a poda falhar, o arquivo do dia já
    está salvo. O inverso perderia o dado novo para liberar espaço.
    """
    manter = settings.INGESTAO_MANTER_ARQUIVOS
    if manter <= 0:
        return 0

    arquivos = sorted(INBOX_DIR.glob(_PADRAO_ARQUIVO))
    excedente = arquivos[:-manter] if len(arquivos) > manter else []
    removidos = 0
    for velho in excedente:
        try:
            velho.unlink()
            removidos += 1
        except OSError as e:
            logger.warning("Não consegui remover %s: %s", velho.name, e)
    if removidos:
        logger.info("Poda da inbox: %d planilha(s) antiga(s) removida(s).", removidos)
    return removidos


def receber(conteudo: bytes, recebido_em: datetime | None = None) -> Recebido:
    """Valida, grava e devolve o que foi feito.

    Args:
        conteudo: os bytes crus do .xlsx.
        recebido_em: quando o e-mail chegou. Dá o nome ao arquivo, então
            mandar a hora real do e-mail (e não a do upload) mantém o
            histórico coerente quando o coletor roda atrasado.

    Raises:
        IngestaoInvalida: conteúdo que não serve, com o motivo em português.
    """
    _conferir_planilha(conteudo)

    sha = _hash(conteudo)
    igual = _procurar_igual(sha)
    if igual is not None:
        logger.info("Ingestão ignorada: conteúdo idêntico a %s.", igual.name)
        return Recebido(caminho=igual, sha256=sha, bytes_=len(conteudo), ja_existia=True)

    destino = INBOX_DIR / _nome_do_arquivo(recebido_em)

    # Mesmo nome com conteúdo diferente acontece quando duas versões saem no
    # mesmo minuto. Desempata com sufixo em vez de sobrescrever: perder a
    # primeira sem aviso seria pior que ter duas.
    if destino.exists():
        for n in range(2, 100):
            alternativo = destino.with_name(f"{destino.stem}_{n}.xlsx")
            if not alternativo.exists():
                destino = alternativo
                break

    # Grava em arquivo temporário e renomeia: `arquivo_mais_recente()` pode
    # rodar no meio de uma escrita longa, e um .xlsx pela metade quebraria a
    # leitura. O rename é atômico dentro do mesmo volume.
    provisorio = destino.with_suffix(".parcial")
    provisorio.write_bytes(conteudo)
    provisorio.replace(destino)

    # A data de modificação vira a hora do E-MAIL, não a do upload.
    #
    # Não é cosmético: `VinculadoConnector` lê `st_mtime` para preencher o
    # "recebido em" que aparece no rodapé do painel. Sem esta linha, um
    # servidor alimentado pela rede mostraria a hora em que o coletor rodou —
    # 14h25 para um e-mail das 7h43 — e qualquer conferência de "o dado é de
    # hoje?" passaria a medir o robô em vez do dado. Com ela, o caminho pela
    # rede fica indistinguível do caminho pelo Outlook.
    if recebido_em is not None:
        quando = recebido_em.timestamp()
        os.utime(destino, (quando, quando))

    logger.info("Planilha recebida: %s (%.1f KB).", destino.name, len(conteudo) / 1024)
    _podar()

    return Recebido(caminho=destino, sha256=sha, bytes_=len(conteudo), ja_existia=False)


def ultimo() -> Recebido | None:
    """A planilha mais recente já recebida, com hash — ou None se não há nenhuma."""
    arquivos = sorted(INBOX_DIR.glob(_PADRAO_ARQUIVO))
    if not arquivos:
        return None
    caminho = arquivos[-1]
    conteudo = caminho.read_bytes()
    return Recebido(
        caminho=caminho, sha256=_hash(conteudo), bytes_=len(conteudo), ja_existia=True
    )


def data_do_nome(nome: str) -> datetime | None:
    """Extrai a data de `vinculado_AAAAMMDD_HHMM.xlsx`, ou None se não casar."""
    m = re.search(r"vinculado_(\d{8})_(\d{4})", nome)
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M")
    except ValueError:
        return None
