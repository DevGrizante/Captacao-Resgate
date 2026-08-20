"""
Lê a planilha do Outlook e publica no servidor.

O PAPEL DESTE SCRIPT

    É a única peça que ainda precisa rodar no Windows, porque é a única que
    toca no Outlook. Tudo o mais — painel, API, classificação — passa a poder
    morar em qualquer lugar, porque o dado chega até lá por HTTP.

    Rode uma vez por dia, logo depois de o e-mail cair. Pelo Agendador de
    Tarefas do Windows, apontando para `Coletar_e_Enviar.bat`.

O QUE ELE FAZ

    1. Pede ao Outlook o e-mail mais recente com o assunto configurado e salva
       o anexo em data/inbox/ (mesma rotina que o painel já usava).
    2. Pergunta ao servidor qual planilha ele tem, pelo hash.
    3. Se for a mesma, para por aí — não gasta subida nem recálculo.
    4. Se for nova, envia e espera o servidor recalcular.

USO

    python coletar_vinculado.py                    # usa o .env
    python coletar_vinculado.py --destino http://192.168.0.9:8000
    python coletar_vinculado.py --arquivo C:\\caminho\\vinculado.xlsx
    python coletar_vinculado.py --forcar           # envia mesmo se for igual
    python coletar_vinculado.py --sem-outlook      # só publica o que já está na inbox

CÓDIGOS DE SAÍDA (o Agendador de Tarefas enxerga)

    0  publicou, ou o servidor já tinha esta mesma planilha
    1  erro de configuração (destino ou token faltando)
    2  não achei planilha nenhuma para enviar
    3  o servidor recusou ou não respondeu
"""
from __future__ import annotations

import argparse
import hashlib
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

# Permite `python backend/scripts/coletar_vinculado.py` de qualquer lugar.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import requests  # noqa: E402

from app.config import settings  # noqa: E402
from app.services import ingestao, outlook_inbox  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%d/%m %H:%M:%S",
)
log = logging.getLogger("coletor")

# Timeout largo no envio porque o servidor recalcula o painel antes de
# responder. Em dia normal são segundos; no dia em que o cache da CVM expira
# junto, a carga pesada entra na mesma requisição e passa de dez minutos.
# Cortar antes disso faria o script achar que falhou algo que deu certo.
_TIMEOUT_ENVIO = (10, 1800)   # (conectar, ler)
_TIMEOUT_CONSULTA = (10, 30)


def _sha256(caminho: Path) -> str:
    h = hashlib.sha256()
    with caminho.open("rb") as f:
        for bloco in iter(lambda: f.read(1024 * 1024), b""):
            h.update(bloco)
    return h.hexdigest()


def _achar_planilha(explicito: str | None, usar_outlook: bool) -> Path | None:
    """De onde sai o arquivo a publicar."""
    if explicito:
        caminho = Path(explicito)
        if not caminho.is_file():
            log.error("Arquivo não encontrado: %s", caminho)
            return None
        return caminho

    if usar_outlook:
        log.info("Consultando o Outlook (pasta %r, assunto %r)...",
                 settings.OUTLOOK_PASTA, settings.OUTLOOK_ASSUNTO)
        # sincronizar() nunca levanta por causa do Outlook: se a leitura
        # falhar, devolve o arquivo local mais recente. Bom para o painel, mas
        # aqui esconderia a falha — por isso comparamos o antes e o depois.
        antes = outlook_inbox.arquivo_mais_recente()
        caminho = outlook_inbox.sincronizar()
        if caminho is not None and antes is not None and caminho == antes:
            log.info("O Outlook não trouxe nada novo; usando %s.", caminho.name)
        return caminho

    return outlook_inbox.arquivo_mais_recente()


def _ja_esta_la(destino: str, token: str, sha: str) -> bool:
    """O servidor já tem esta mesma planilha?

    Erro de rede aqui NÃO interrompe: na dúvida, é melhor enviar de novo (o
    servidor deduplica por hash de qualquer forma) do que pular o envio do dia
    por causa de uma consulta que falhou.
    """
    try:
        r = requests.get(
            f"{destino}/api/inbox/ultimo",
            headers={"Authorization": f"Bearer {token}"},
            timeout=_TIMEOUT_CONSULTA,
        )
    except requests.RequestException as e:
        log.warning("Não consegui consultar o servidor (%s). Vou enviar assim mesmo.", e)
        return False

    if r.status_code == 404:
        log.info("O servidor ainda não tem planilha nenhuma.")
        return False
    if r.status_code == 401:
        log.error("Token recusado pelo servidor. Confira INGESTAO_TOKEN dos dois lados.")
        sys.exit(3)
    if r.status_code == 503:
        log.error("Ingestão desligada no servidor: falta INGESTAO_TOKEN lá.")
        sys.exit(3)
    if not r.ok:
        log.warning("Consulta devolveu %s. Vou enviar assim mesmo.", r.status_code)
        return False

    dados = r.json()
    if dados.get("sha256") == sha:
        log.info("O servidor já tem esta planilha (%s). Nada a fazer.", dados.get("arquivo"))
        return True

    log.info("O servidor está com %s; a minha é diferente.", dados.get("arquivo"))
    return False


def _enviar(destino: str, token: str, caminho: Path, recebido_em: datetime) -> int:
    conteudo = caminho.read_bytes()
    log.info("Enviando %s (%.1f KB) para %s ...", caminho.name, len(conteudo) / 1024, destino)

    try:
        r = requests.post(
            f"{destino}/api/inbox",
            data=conteudo,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/octet-stream",
                # A hora do E-MAIL, não a do envio: ela nomeia o arquivo no
                # servidor e vira a data de modificação dele, que é de onde o
                # painel tira o "recebido em". Precisão cheia de propósito —
                # truncar em minutos faria o servidor da rede reportar 07:43:00
                # onde o local reporta 07:43:17, e as duas respostas deixariam
                # de ser comparáveis.
                "X-Recebido-Em": recebido_em.isoformat(),
            },
            timeout=_TIMEOUT_ENVIO,
        )
    except requests.RequestException as e:
        log.error("Falha ao enviar: %s", e)
        return 3

    if not r.ok:
        detalhe = ""
        try:
            detalhe = r.json().get("detail", "")
        except ValueError:
            detalhe = r.text[:300]
        log.error("Servidor recusou (%s): %s", r.status_code, detalhe)
        return 3

    resposta = r.json()
    if resposta.get("status") == "ja_tinha":
        log.info("O servidor já tinha este conteúdo como %s.", resposta.get("arquivo"))
        return 0

    log.info("Publicado como %s.", resposta.get("arquivo"))
    if resposta.get("erro_recalculo"):
        log.warning("Arquivo salvo, mas o painel não recalculou: %s",
                    resposta["erro_recalculo"])
    elif resposta.get("recalculado"):
        lendo = resposta.get("painel_lendo")
        log.info("Painel recalculado, lendo %s.", lendo)
        # Enviar uma planilha mais VELHA que a do servidor a salva sem
        # promovê-la. Sem este aviso, o log diria "publicado" e ninguém
        # perceberia que a tela continua no arquivo anterior.
        if lendo and lendo != resposta.get("arquivo"):
            log.warning(
                "Atenção: o painel continua no %s. A planilha enviada é mais "
                "antiga que a que já estava no servidor.", lendo,
            )
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="Coleta a planilha do Outlook e publica no servidor.")
    p.add_argument("--destino", default=os.getenv("INGESTAO_DESTINO", "http://127.0.0.1:8000"),
                   help="URL base do servidor (padrão: INGESTAO_DESTINO ou 127.0.0.1:8000)")
    p.add_argument("--token", default=os.getenv("INGESTAO_TOKEN", settings.INGESTAO_TOKEN),
                   help="Token de ingestão (padrão: INGESTAO_TOKEN do .env)")
    p.add_argument("--arquivo", default=None, help="Publica este arquivo em vez de consultar o Outlook")
    p.add_argument("--sem-outlook", action="store_true",
                   help="Não consulta o Outlook; publica o mais recente de data/inbox/")
    p.add_argument("--forcar", action="store_true",
                   help="Envia mesmo que o servidor já tenha o mesmo conteúdo")
    args = p.parse_args()

    destino = args.destino.rstrip("/")
    if not args.token:
        log.error("Sem token. Defina INGESTAO_TOKEN no backend/.env ou passe --token.")
        return 1
    if not destino.startswith(("http://", "https://")):
        log.error("Destino inválido: %r. Precisa começar com http:// ou https://", destino)
        return 1

    caminho = _achar_planilha(args.arquivo, usar_outlook=not args.sem_outlook and not args.arquivo)
    if caminho is None:
        log.error("Nenhuma planilha encontrada — nem no Outlook, nem em data/inbox/.")
        return 2

    sha = _sha256(caminho)
    log.info("Planilha: %s  (sha256 %s...)", caminho.name, sha[:12])

    if not args.forcar and _ja_esta_la(destino, args.token, sha):
        return 0

    return _enviar(destino, args.token, caminho, _hora_do_email(caminho))


def _hora_do_email(caminho: Path) -> datetime:
    """Quando este anexo chegou, com a maior precisão disponível.

    Duas fontes, e as duas são parciais:

      · o NOME (`vinculado_AAAAMMDD_HHMM`) é autoritativo mas só tem minutos;
      · a data de modificação tem segundos, mas muda se alguém copiar o arquivo.

    Então usamos a mtime quando ela concorda com o nome até o minuto — aí ela é
    o mesmo instante, só que mais detalhado. Quando discordam, o nome vence: é
    ele que carrega a hora do e-mail de verdade.

    Isso importa mais do que parece. O `recebido_em` que aparece no rodapé do
    painel sai da mtime do arquivo no servidor; casar as duas pontas aqui é o
    que faz um servidor alimentado pela rede reportar exatamente o mesmo
    instante que um alimentado pelo Outlook — e é o que permite comparar as
    duas respostas byte a byte para provar que são equivalentes.
    """
    do_nome = ingestao.data_do_nome(caminho.name)
    do_arquivo = datetime.fromtimestamp(caminho.stat().st_mtime)

    if do_nome is None:
        log.info("Nome fora do padrão; usando a data do arquivo (%s).",
                 do_arquivo.strftime("%d/%m %H:%M"))
        return do_arquivo

    if do_arquivo.replace(second=0, microsecond=0) == do_nome:
        return do_arquivo
    return do_nome


if __name__ == "__main__":
    sys.exit(main())
