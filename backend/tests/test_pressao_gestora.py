"""A leitura de pressão: fluxo x agenda de vencimento, por gestora."""
from __future__ import annotations

import pytest
from app.config import settings
from app.services import pressao_gestora as pg


@pytest.fixture(autouse=True)
def _corte_conhecido(monkeypatch):
    """Fixa as réguas: os testes descrevem a REGRA, não o valor do dia.

    `THRESHOLD_MAJORITARIO` é editável em produção pelo painel de controle —
    sem isto, um ajuste de mesa quebraria o CI.
    """
    monkeypatch.setattr(settings, "THRESHOLD_MAJORITARIO", 0.2)
    monkeypatch.setattr(settings, "PRESSAO_LIMIAR_PCT", 0.005)


class TestPerfil:
    def test_papel_bancario_e_testado_primeiro(self):
        """LF não é indexador — é outro mercado, com outro interlocutor."""
        perfil, motivo = pg._perfil({"lf": 60.0, "ipca": 40.0})
        assert perfil == pg.PERFIL_BANCARIO
        assert "tesouraria" in motivo

    def test_lf_e_cdb_somam_no_eixo_bancario(self):
        perfil, _ = pg._perfil({"lf": 15.0, "cdb": 15.0, "cdi": 70.0})
        assert perfil == pg.PERFIL_BANCARIO

    def test_indexador_e_medido_sobre_a_base_ex_bancario(self):
        """Tirar o bancário do denominador é o que deixa o indexador visível.

        Com 19% de LF e o resto dividido, medir IPCA sobre a carteira INTEIRA
        daria 48,6% — ainda dominante. O teste que importa é o outro: uma casa
        cujo crédito corporativo é quase todo IPCA precisa aparecer como IPCA,
        e não diluída pelo papel de banco que ela carrega em caixa.
        """
        perfil, _ = pg._perfil({"lf": 19.0, "ipca": 60.0, "cdi": 21.0})
        assert perfil == pg.PERFIL_IPCA

    def test_carteira_vazia_nao_quebra(self):
        perfil, motivo = pg._perfil({})
        assert perfil == pg.PERFIL_SEM_CARTEIRA
        assert motivo

    def test_nada_dominando_vira_misto(self):
        perfil, motivo = pg._perfil({"lf": 10.0, "ipca": 15.0, "cdi": 15.0, "pre": 60.0})
        assert perfil == pg.PERFIL_MISTO
        assert "Nada domina" in motivo

    def test_todo_perfil_tem_rotulo(self):
        for chave in (pg.PERFIL_BANCARIO, pg.PERFIL_IPCA, pg.PERFIL_CDI,
                      pg.PERFIL_MISTO, pg.PERFIL_SEM_CARTEIRA):
            assert pg.ROTULOS_PERFIL[chave]


class TestDirecao:
    def test_captacao_e_compra(self):
        direcao, _ = pg._direcao(fluxo=100e6, credito=1e9)
        assert direcao == pg.DIRECAO_COMPRADOR

    def test_resgate_e_venda(self):
        direcao, _ = pg._direcao(fluxo=-100e6, credito=1e9)
        assert direcao == pg.DIRECAO_VENDEDOR

    def test_o_corte_e_relativo_ao_tamanho_da_casa(self):
        """R$ 50 mi é movimento numa casa pequena e ruído numa grande.

        Um piso absoluto encheria a lista de gigantes que não fizeram nada.
        """
        pequena, _ = pg._direcao(fluxo=50e6, credito=400e6)     # 12,5%
        grande, _ = pg._direcao(fluxo=50e6, credito=40e9)       # 0,125%
        assert pequena == pg.DIRECAO_COMPRADOR
        assert grande == pg.DIRECAO_NEUTRO

    def test_sem_carteira_vale_o_sinal_puro(self):
        """Dizer "neutro" sem base de comparação esconderia um fluxo que existe."""
        direcao, _ = pg._direcao(fluxo=-10e6, credito=None)
        assert direcao == pg.DIRECAO_VENDEDOR

    def test_fluxo_zero_e_neutro(self):
        direcao, _ = pg._direcao(fluxo=0.0, credito=None)
        assert direcao == pg.DIRECAO_NEUTRO


class TestLeitura:
    """O cruzamento fluxo x agenda — é para isto que a tela existe."""

    def test_resgate_coberto_pelo_vencimento_nao_e_venda_forcada(self):
        """O vencimento entra caixa sem a casa vender nada.

        Ler só o fluxo inverteria o sinal desta gestora: ela aparece vendedora
        e na prática tem dinheiro sobrando para recomprar.
        """
        leitura = pg._ler_pressao(pg.DIRECAO_VENDEDOR, fluxo=-752e6, vence_3m=1589e6)
        assert "Não é vendedora forçada" in leitura

    def test_resgate_maior_que_o_vencimento_e_pressao_real(self):
        leitura = pg._ler_pressao(pg.DIRECAO_VENDEDOR, fluxo=-375e6, vence_3m=244e6)
        assert "Pressão vendedora real" in leitura

    def test_resgate_sem_agenda_precisa_vender(self):
        """Carteira longa (infra) e resgate: não há rolagem para cobrir."""
        leitura = pg._ler_pressao(pg.DIRECAO_VENDEDOR, fluxo=-1057e6, vence_3m=0.0)
        assert "vender papel em mercado" in leitura

    def test_captacao_soma_com_a_agenda(self):
        leitura = pg._ler_pressao(pg.DIRECAO_COMPRADOR, fluxo=2488e6, vence_3m=14717e6)
        assert "para alocar" in leitura

    def test_neutro_com_agenda_ainda_precisa_rolar(self):
        """Rolagem acontece capte a casa ou não — é a dimensão independente."""
        leitura = pg._ler_pressao(pg.DIRECAO_NEUTRO, fluxo=0.0, vence_3m=500e6)
        assert "rolados" in leitura

    def test_sempre_devolve_frase(self):
        for direcao in (pg.DIRECAO_COMPRADOR, pg.DIRECAO_VENDEDOR, pg.DIRECAO_NEUTRO):
            for vence in (0.0, 100e6):
                assert pg._ler_pressao(direcao, 10e6, vence)


class TestContexto:
    def test_contexto_vazio_e_detectavel(self):
        """Sem agenda a tela fica vazia — não quebra e não inventa."""
        assert pg.Contexto().vazio()

    def test_totais_de_contexto_vazio(self):
        assert pg.totais(pg.Contexto()) == {}

    def test_resumir_gestora_desconhecida_devolve_none(self):
        assert pg.resumir(pg.Contexto(), "Casa Inexistente", 0.0) is None
