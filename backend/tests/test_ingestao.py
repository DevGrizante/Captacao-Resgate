"""O portão da planilha: o que pode entrar em data/inbox e com que nome."""
from __future__ import annotations

import io
import zipfile
from datetime import datetime

import pytest
from app.config import settings
from app.services import ingestao


def _xlsx_valido() -> bytes:
    """Um .xlsx mínimo: zip com a peça central do formato."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as z:
        z.writestr("xl/workbook.xml", "<workbook/>")
    return buffer.getvalue()


class TestConferirPlanilha:
    def test_aceita_xlsx(self):
        ingestao._conferir_planilha(_xlsx_valido())  # não levanta

    def test_recusa_corpo_vazio(self):
        with pytest.raises(ingestao.IngestaoInvalida, match="vazio"):
            ingestao._conferir_planilha(b"")

    def test_recusa_html_de_proxy(self):
        """O modo de falha real em rede corporativa: erro de proxy no lugar do anexo.

        Sem esta checagem o arquivo passaria e só quebraria adiante no pandas,
        com uma mensagem que não ajuda ninguém a entender que foi o download
        que falhou.
        """
        with pytest.raises(ingestao.IngestaoInvalida, match="ZIP"):
            ingestao._conferir_planilha(b"<html>407 Proxy Authentication</html>")

    def test_recusa_zip_que_nao_e_planilha(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as z:
            z.writestr("qualquer.txt", "nada")
        with pytest.raises(ingestao.IngestaoInvalida, match="workbook.xml"):
            ingestao._conferir_planilha(buffer.getvalue())

    def test_recusa_acima_do_teto(self, monkeypatch):
        monkeypatch.setattr(settings, "INGESTAO_TAMANHO_MAXIMO_MB", 0)
        with pytest.raises(ingestao.IngestaoInvalida, match="teto"):
            ingestao._conferir_planilha(_xlsx_valido())


class TestNomeDoArquivo:
    def test_deriva_da_hora_do_email(self):
        assert ingestao._nome_do_arquivo(
            datetime(2026, 8, 20, 7, 27)
        ) == "vinculado_20260820_0727.xlsx"

    def test_ida_e_volta_com_data_do_nome(self):
        """O nome carrega a data, e `data_do_nome` a lê de volta.

        As duas pontas precisam concordar: é o formato AAAAMMDD_HHMM que faz
        "o mais recente" (ordem alfabética) coincidir com "o mais novo".
        """
        quando = datetime(2026, 8, 20, 7, 27)
        nome = ingestao._nome_do_arquivo(quando)
        assert ingestao.data_do_nome(nome) == quando

    def test_nome_fora_do_padrao_devolve_none(self):
        assert ingestao.data_do_nome("planilha.xlsx") is None


class TestReceber:
    def test_grava_e_deduplica(self, monkeypatch, tmp_path):
        """O mesmo conteúdo enviado duas vezes não vira dois arquivos.

        O e-mail é encaminhado por mais de uma pessoa e o coletor pode rodar
        duas vezes no mesmo dia; sem isto a inbox encheria de cópias idênticas
        com nomes diferentes, e "o mais recente" passaria a ser decidido por
        qual chegou por último em vez de por qual é mais novo de fato.
        """
        monkeypatch.setattr(ingestao, "INBOX_DIR", tmp_path)
        conteudo = _xlsx_valido()
        quando = datetime(2026, 8, 20, 7, 27)

        primeiro = ingestao.receber(conteudo, recebido_em=quando)
        assert primeiro.ja_existia is False
        assert primeiro.nome == "vinculado_20260820_0727.xlsx"

        segundo = ingestao.receber(conteudo, recebido_em=datetime(2026, 8, 20, 9, 0))
        assert segundo.ja_existia is True
        assert segundo.caminho == primeiro.caminho
        assert len(list(tmp_path.glob("vinculado_*.xlsx"))) == 1

    def test_nome_nunca_vem_do_cliente(self, monkeypatch, tmp_path):
        """Nome vindo de fora é o caminho clássico para escrever onde não deve.

        A assinatura de `receber` nem aceita nome: ele é sempre derivado da
        hora. Este teste existe para que acrescentar esse parâmetro um dia
        quebre a suíte em vez de passar despercebido.
        """
        import inspect
        assinatura = inspect.signature(ingestao.receber)
        assert set(assinatura.parameters) == {"conteudo", "recebido_em"}
