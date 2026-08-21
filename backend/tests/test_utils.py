"""Normalização de CNPJ — a raiz de todo casamento entre bases."""
from __future__ import annotations

import pytest
from app.utils import limpar_gestora, so_digitos


@pytest.mark.parametrize(
    "entrada, esperado",
    [
        ("33.149.272/0001-07", "33149272000107"),
        ("33149272000107", "33149272000107"),
        (" 33.149.272/0001-07 ", "33149272000107"),
        (None, None),
        ("", None),
        ("sem digito nenhum", None),
    ],
)
def test_formatos_comuns(entrada, esperado):
    assert so_digitos(entrada) == esperado


@pytest.mark.parametrize(
    "truncado, esperado",
    [
        # O pandas lê `07661541000100` sem pontuação como int64 e devolve
        # 7661541000100. Era isso que fazia 358 dos 4.603 fundos do relatório
        # de 20/08/2026 nunca casarem com o cadastro da CVM.
        (7661541000100, "07661541000100"),
        ("7661541000100", "07661541000100"),
        (566154100010, "00566154100010"),
        # Float vindo de um join com NaN: o `.0` não pode virar dígito.
        ("7661541000100.0", "07661541000100"),
    ],
)
def test_zero_a_esquerda_e_reposto(truncado, esperado):
    assert so_digitos(truncado) == esperado


def test_cpf_nao_vira_cnpj():
    """11 dígitos é CPF e fica intocado — inventar um CNPJ seria pior que não casar."""
    assert so_digitos("123.456.789-09") == "12345678909"


class TestLimparGestora:
    """"Management" não distingue uma gestora de outra numa lista de assets."""

    @pytest.mark.parametrize(
        "entrada, esperado",
        [
            ("BTG Pactual Asset Management", "BTG Pactual Asset"),
            ("Bradesco Asset Management", "Bradesco Asset"),
            ("Aurum Wealth Management", "Aurum Wealth"),
            ("Argucia Capital Management", "Argucia Capital"),
            # Caixa preservada: o painel mostra "BB Asset", não "BB ASSET".
            ("BB Asset Management", "BB Asset"),
            ("bb asset management", "bb asset"),
        ],
    )
    def test_remove_a_palavra(self, entrada, esperado):
        assert limpar_gestora(entrada) == esperado

    @pytest.mark.parametrize(
        "entrada",
        ["Vinci Partners", "SPX Gestão", "Itaú Asset", ""],
    )
    def test_nao_toca_em_quem_nao_tem(self, entrada):
        assert limpar_gestora(entrada) == entrada

    def test_so_palavra_inteira(self):
        """Um replace ingênuo comeria o miolo de um nome que contém a sequência."""
        assert limpar_gestora("Managementum Capital") == "Managementum Capital"

    def test_limpa_sobras_de_pontuacao_e_espaco(self):
        assert limpar_gestora("Vinci  Management  Ltda") == "Vinci Ltda"
        assert limpar_gestora("Kinea, Management") == "Kinea"

    def test_none_vira_string_vazia(self):
        """A gestora é chave de agrupamento — devolver None quebraria o dict."""
        assert limpar_gestora(None) == ""
