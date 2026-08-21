"""
Busca o anexo da Quantum na caixa de e-mail e o entrega ao painel. Uma rodada.

    python backend/scripts/coletar_email.py
    python backend/scripts/coletar_email.py --sem-recalcular
    python backend/scripts/coletar_email.py --verificar

>>> QUANDO USAR ESTE SCRIPT, E QUANDO NÃO USAR

    NÃO use se `EMAIL_INTERVALO_MIN` estiver preenchido no `.env`. Nesse caso a
    própria API já busca o e-mail sozinha, de dentro do processo
    (`services/agendador.py`), e rodar isto em paralelo só duplicaria a busca.
    Não corromperia nada — a ingestão deduplica por SHA-256 —, mas seriam duas
    coisas fazendo a mesma coisa, e daqui a seis meses ninguém saberia qual das
    duas está funcionando.

    USE quando você preferir que a coleta seja um processo VISÍVEL, agendado
    fora da aplicação: cron no Linux, Agendador de Tarefas no Windows, ou um
    job do OCI/Kubernetes. Aí deixe `EMAIL_INTERVALO_MIN=0` e mande neste
    script. A vantagem é operacional: o horário fica registrado num lugar que o
    time de infraestrutura já monitora, e uma falha aparece como job vermelho
    em vez de uma linha de log.

    As duas opções usam EXATAMENTE o mesmo código (`services/email_inbox.py`).
    A escolha é de onde parte o gatilho, não de o que acontece.

>>> CÓDIGOS DE SAÍDA (o cron e o Agendador de Tarefas enxergam)

    0  coletou uma planilha nova, ou o servidor já tinha exatamente essa
    1  erro de configuração (EMAIL_MODO=off, credencial faltando)
    2  nenhum e-mail novo casou com o filtro — não é erro
    3  a caixa de e-mail recusou ou não respondeu

    O 2 é separado do 0 de propósito. "Não havia e-mail novo" às 7h05 é normal;
    às 11h de um dia útil é sintoma. Um monitor que só olhe 0-ou-erro não
    consegue distinguir os dois, e o modo de falha mais perigoso deste
    pipeline é justamente o silencioso — o painel servindo a planilha da
    véspera sem nada na tela indicando isso.
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings  # noqa: E402
from app.services import email_inbox, ingestao  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("coletar_email")

OK, ERRO_CONFIG, SEM_NOVIDADE, ERRO_CAIXA = 0, 1, 2, 3


def _verificar() -> int:
    """Diz o que está configurado, sem tocar na caixa. Para conferir o .env."""
    print(f"EMAIL_MODO            {settings.EMAIL_MODO!r}")
    print(f"OUTLOOK_ASSUNTO       {settings.OUTLOOK_ASSUNTO!r}")
    print(f"OUTLOOK_REMETENTE     {settings.OUTLOOK_REMETENTE!r} "
          f"{'(qualquer remetente)' if not settings.OUTLOOK_REMETENTE else ''}")
    print(f"OUTLOOK_PASTA         {settings.OUTLOOK_PASTA!r}")
    print(f"EMAIL_INTERVALO_MIN   {settings.EMAIL_INTERVALO_MIN}")

    if settings.EMAIL_MODO == "graph":
        faltando = [
            nome for nome in ("GRAPH_TENANT_ID", "GRAPH_CLIENT_ID",
                              "GRAPH_CLIENT_SECRET", "GRAPH_CAIXA")
            if not getattr(settings, nome)
        ]
        print(f"GRAPH_CAIXA           {settings.GRAPH_CAIXA!r}")
    elif settings.EMAIL_MODO == "imap":
        faltando = [
            nome for nome in ("IMAP_HOST", "IMAP_USUARIO") if not getattr(settings, nome)
        ]
        if not (settings.IMAP_SENHA or settings.IMAP_OAUTH_TOKEN):
            faltando.append("IMAP_SENHA ou IMAP_OAUTH_TOKEN")
        print(f"IMAP_HOST             {settings.IMAP_HOST!r}:{settings.IMAP_PORT}")
        print(f"IMAP_PASTA            {settings.IMAP_PASTA!r}")
    else:
        print("\n  EMAIL_MODO=off — a coleta por e-mail está desligada.")
        return ERRO_CONFIG

    if faltando:
        print(f"\n  FALTA CONFIGURAR: {', '.join(faltando)}")
        return ERRO_CONFIG

    print("\n  Configuração completa.")
    if settings.EMAIL_INTERVALO_MIN > 0:
        print(f"  ATENÇÃO: a API já coleta sozinha a cada {settings.EMAIL_INTERVALO_MIN} min.")
        print("  Rodar este script por cron duplicaria a coleta. Ver o cabeçalho do arquivo.")
    return OK


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    p.add_argument("--verificar", action="store_true",
                   help="só mostra o que está configurado, sem acessar a caixa")
    p.add_argument("--sem-recalcular", action="store_true",
                   help="grava a planilha mas não refaz o pipeline")
    args = p.parse_args()

    if args.verificar:
        return _verificar()

    if not email_inbox.habilitado():
        logger.error(
            "EMAIL_MODO=%r. Defina EMAIL_MODO=graph ou EMAIL_MODO=imap no .env.",
            settings.EMAIL_MODO,
        )
        return ERRO_CONFIG

    recebido = email_inbox.sincronizar()

    if recebido is None:
        # `sincronizar` já registrou no log o que aconteceu: nenhum e-mail
        # casou, ou a caixa não respondeu. Ele nunca levanta, porque no modo
        # servidor uma caixa fora do ar não pode derrubar o painel — mas aqui,
        # como processo agendado, a diferença precisa virar código de saída.
        ultimo = ingestao.ultimo()
        if ultimo is not None:
            logger.info("Nada novo. O painel segue com %s.", ultimo.nome)
        return SEM_NOVIDADE

    if recebido.ja_existia:
        logger.info("O servidor já tinha esta planilha: %s.", recebido.nome)
        return OK

    logger.info("Planilha nova: %s (%.1f KB).", recebido.nome, recebido.bytes_ / 1024)

    if args.sem_recalcular:
        logger.info("--sem-recalcular: o painel só verá a mudança no próximo refresh.")
        return OK

    # O import fica aqui, e não no topo, porque construir o pipeline carrega as
    # bases da CVM — trabalho pesado que `--verificar` e o caminho "sem
    # novidade" não devem pagar.
    from app.services.pipeline import pipeline

    try:
        pipeline.refresh()
    except Exception as e:  # noqa: BLE001
        # A planilha ESTÁ salva. Falhar aqui faria o agendador tentar de novo
        # amanhã achando que não enviou nada.
        logger.exception("Planilha salva, mas o recálculo falhou: %s", e)
        return OK

    logger.info("Pipeline recalculado. Painel lendo %s.", pipeline.fonte_info().arquivo)
    return OK


if __name__ == "__main__":
    raise SystemExit(main())
