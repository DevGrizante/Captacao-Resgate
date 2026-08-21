"""A cascata de classificação — a regra de negócio central do painel."""
from __future__ import annotations

import pytest
from app.config import settings
from app.models.schemas import Bucket
from app.services.classifier import (
    classificar,
    classificar_sem_carteira,
    nome_incentivado,
    tem_hedge_dap,
)


@pytest.fixture(autouse=True)
def _corte_conhecido(monkeypatch):
    """Fixa o corte em 20%: os testes descrevem a REGRA, não o valor do dia.

    Sem isto a suíte passaria a depender de `data/parametros.json`, que o
    painel de controle edita em produção — e um ajuste de mesa quebraria o CI.
    """
    monkeypatch.setattr(settings, "THRESHOLD_MAJORITARIO", 0.2)
    monkeypatch.setattr(settings, "HEDGE_DAP_MINIMO", 0.2)
    monkeypatch.setattr(settings, "INCENTIVADA_EXIGE_HEDGE_DAP", False)


class TestNomeIncentivado:
    @pytest.mark.parametrize("nome", [
        "FI DEBENTURES INCENTIVADAS",
        "Fundo Incentivado de Infraestrutura",
        "XP INFRA FIC FIM",
        "ITAU INFRAESTRUTURA RF",
    ])
    def test_reconhece(self, nome):
        assert nome_incentivado(nome)

    @pytest.mark.parametrize("nome", [
        "FI CREDITO PRIVADO",
        # A âncora do padrão existe para isto: "INFRANET" é nome de empresa na
        # carteira, não o mandato do fundo.
        "FUNDO INFRANET PARTICIPACOES",
        "",
    ])
    def test_recusa(self, nome):
        assert not nome_incentivado(nome)


class TestHedgeDap:
    def test_sem_leitura_de_carteira_e_falso(self):
        """None é "não sabemos" — e um hedge não observado não promove nada."""
        assert tem_hedge_dap(None) is False

    def test_zero_nao_conta_nem_com_corte_zerado(self, monkeypatch):
        """Baixar o corte a 0% não pode tornar a verificação vacuosa.

        Sem a exigência de cobertura estritamente positiva, um corte em 0%
        faria TODO fundo sem posição nenhuma em DAP contar como travado —
        justamente no ajuste que parece só afrouxar a régua.
        """
        monkeypatch.setattr(settings, "HEDGE_DAP_MINIMO", 0.0)
        assert tem_hedge_dap(0.0) is False
        assert tem_hedge_dap(0.01) is True


class TestClassificar:
    def test_lf_domina(self):
        bucket, motivo = classificar(pct_lf=0.5, pct_ipca=0.3, pct_cdi=0.2)
        assert bucket is Bucket.LF
        assert "50%" in motivo

    def test_tradicional_por_cdi(self):
        bucket, _ = classificar(pct_lf=0.0, pct_ipca=0.1, pct_cdi=0.9,
                                nome="FI CREDITO PRIVADO")
        assert bucket is Bucket.TRADICIONAL

    def test_incentivada_precisa_do_nome_e_da_carteira(self):
        bucket, _ = classificar(pct_lf=0.0, pct_ipca=0.9, pct_cdi=0.1,
                                nome="FI DEBENTURES INCENTIVADAS")
        assert bucket is Bucket.INCENTIVADA

    def test_nome_de_incentivada_com_carteira_cdi_vira_misto(self):
        """Nome e carteira discordando não é Incentivada — e o motivo diz qual falhou."""
        bucket, motivo = classificar(pct_lf=0.0, pct_ipca=0.05, pct_cdi=0.95,
                                     nome="FI INFRA")
        assert bucket is Bucket.MISTO
        assert "incentivada" in motivo.lower()

    def test_ipca_travado_em_dap_entrega_cdi(self):
        """Quem compra IPCA+ e trava em DAP entrega spread puro: é Tradicional.

        Sem esta linha, a casa que FAZ o hedge apareceria em Misto justamente
        por fazê-lo.
        """
        bucket, motivo = classificar(pct_lf=0.0, pct_ipca=0.9, pct_cdi=0.05,
                                     nome="FI CREDITO", dap_cobertura=0.8)
        assert bucket is Bucket.TRADICIONAL
        assert "DAP" in motivo

    def test_ipca_sem_hedge_e_sem_nome_vira_misto(self):
        bucket, _ = classificar(pct_lf=0.0, pct_ipca=0.9, pct_cdi=0.05,
                                nome="FI CREDITO", dap_cobertura=None)
        assert bucket is Bucket.MISTO

    def test_a_base_do_indexador_exclui_lf(self):
        """LF sai do denominador: é instrumento, não indexador.

        Metade LF e metade debênture IPCA+ é uma carteira com indexador
        dominante. Deixar a LF no denominador faria o IPCA parecer 45% e o
        fundo cairia em Misto por um erro de conta.
        """
        bucket, _ = classificar(pct_lf=0.15, pct_ipca=0.45, pct_cdi=0.40,
                                nome="FI INCENTIVADO")
        # 0,45 / 0,85 = 53% > 20% -> IPCA domina a base ex-LF.
        assert bucket is Bucket.INCENTIVADA

    def test_carteira_toda_em_lf_nao_divide_por_zero(self):
        """Guarda de divisão por zero — não pode virar 500 na tela."""
        bucket, motivo = classificar(pct_lf=1.0, pct_ipca=0.0, pct_cdi=0.0)
        # 1,0 > 0,2, então cai em LF antes de chegar à divisão.
        assert bucket is Bucket.LF
        assert motivo


class TestFallbackSemCarteira:
    """Todo fundo precisa de bucket, inclusive o que não está no CDA.

    Antes de 20/08/2026 esses fundos saíam com `bucket = None` e sumiam de
    qualquer visão agrupada por bucket — levando o fluxo deles junto. Eram
    1.759 fundos e 13,6% do fluxo semanal, num relatório cujo assunto é fluxo.
    """

    def test_nunca_devolve_none(self):
        for nome in ("", "FUNDO QUALQUER", "FI INFRA", "XYZ"):
            for perfil in (None, "pos", "inflacao", "outro"):
                bucket, motivo = classificar_sem_carteira(nome, perfil)
                assert bucket is not None
                assert motivo

    def test_nome_de_infra_manda(self):
        bucket, motivo = classificar_sem_carteira("FI INFRA XYZ", perfil_indexador="pos")
        assert bucket is Bucket.INCENTIVADA
        assert "NOME" in motivo

    def test_cota_de_inflacao_sem_nome_vira_misto(self):
        """Sem carteira não dá para saber se é juro real ou spread travado."""
        bucket, motivo = classificar_sem_carteira("FI CREDITO", perfil_indexador="inflacao")
        assert bucket is Bucket.MISTO
        assert "não declara infraestrutura" in motivo

    def test_cota_pos_vira_tradicional(self):
        bucket, _ = classificar_sem_carteira("FI CREDITO", perfil_indexador="pos")
        assert bucket is Bucket.TRADICIONAL

    def test_sem_sinal_nenhum_cai_em_tradicional(self):
        """TRADICIONAL é o padrão, e não MISTO — MISTO tem sentido próprio.

        MISTO significa "olhamos a carteira e nada domina". Usá-lo como
        depósito de "não olhamos" apagaria essa informação nos 43 fundos em
        que ela foi realmente medida.
        """
        bucket, motivo = classificar_sem_carteira("FI QUALQUER", perfil_indexador=None)
        assert bucket is Bucket.TRADICIONAL
        assert "não uma medição" in motivo

    def test_motivo_distingue_ausente_de_descartada(self):
        """"Não estava no CDA" e "estava e não deu para usar" são coisas
        diferentes, e a tela precisa poder dizer qual foi."""
        _, m_ausente = classificar_sem_carteira("FI X")
        _, m_descartada = classificar_sem_carteira(
            "FI X", carteira_motivo="pouco crédito no PL"
        )
        assert "não consta no CDA" in m_ausente
        assert "pouco crédito no PL" in m_descartada
