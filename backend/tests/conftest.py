"""
Configuração comum dos testes.

>>> OS TESTES NÃO TOCAM A REDE

Nenhum teste aqui baixa nada da CVM, do BCB ou do Outlook. Isso é regra, não
conveniência: um teste que depende de rede falha quando o servidor da CVM está
fora do ar, e um CI que fica vermelho por motivo alheio ao código é um CI que
as pessoas aprendem a ignorar.

O que se testa é a REGRA — classificação, normalização de CNPJ, validação da
planilha, cascata da subclassificação. São elas que erram em silêncio, e são
elas que este arquivo protege.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Os testes rodam de backend/, mas o CI pode chamar da raiz. Resolver o caminho
# aqui evita um `PYTHONPATH` diferente em cada ambiente.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture(autouse=True)
def _sem_rede(monkeypatch):
    """Qualquer chamada HTTP durante um teste vira falha explícita.

    Sem isto, um teste que acidentalmente saia para a internet passaria na
    máquina de quem escreveu e falharia no CI — ou, pior, passaria nos dois e
    tornaria a suíte lenta e intermitente sem ninguém entender por quê.
    """
    import requests

    def recusar(*args, **kwargs):
        raise AssertionError(
            "Teste tentou acessar a rede. Se a rede é o que se quer testar, "
            "use um duplê explícito em vez de sair para a internet."
        )

    monkeypatch.setattr(requests, "get", recusar)
    monkeypatch.setattr(requests, "post", recusar)
