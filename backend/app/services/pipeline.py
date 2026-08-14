"""
Pipeline de dados: da fonte crua ao payload da API.

Fluxo:
    fonte (vinculado / CVM / mock) -> enriquecimento (Quantum)
    -> classificação (bucket) -> agregação por gestora / bucket / série semanal
    -> cache em memória.

O resultado fica cacheado por CACHE_TTL_HORAS; um POST /admin/refresh força
recálculo — que, na fonte "vinculado", também re-varre o Outlook atrás de um
anexo mais novo.

REGRA DE OURO: nada de número inventado. Métrica sem dado vira `None` e o
front mostra "—". Hoje a planilha do e-mail entrega fluxo, mas não entrega PL
nem composição de carteira; então PL, mix por indexador, duration, cotização e
taxa de administração ficam vazios até o Quantum Axis ser liberado.
"""
from __future__ import annotations

import logging
import time
from collections import defaultdict

from app.config import settings
from app.connectors.base import DataConnector
from app.connectors.cvm_cadastro import CVMCadastroEnricher
from app.connectors.cvm_connector import CVMConnector
from app.connectors.mock_connector import MockConnector
from app.connectors.quantum_connector import QuantumEnricher
from app.connectors.vinculado_connector import VinculadoConnector
from app.models.schemas import (
    Bucket,
    BucketResumo,
    DashboardResponse,
    DossieResponse,
    FonteInfo,
    Fundo,
    GestoraResumo,
    KpisFluxo,
    KpisMesa,
    MoverGestora,
    SerieTemporalPonto,
    StressFundo,
)
from app.services import credito_privado, perfil_indexador
from app.services.classifier import classificar_dict

logger = logging.getLogger("pipeline")

# Campos que a planilha do e-mail não entrega. O front usa esta lista para
# esconder painel e explicar o "—" em vez de mostrar zero.
CAMPOS_SO_QUANTUM = [
    "pl", "indexador", "duration", "cotizacao_resgate", "taxa_adm",
    "aberto_captacao", "premio_ipca", "premio_cdi",
]

# Campos que a CVM passou a cobrir, cada um da sua base. Saem da lista de
# "faltando" assim que o enriquecimento preenche pelo menos um fundo.
COBERTOS_PELA_CVM = {
    "pl": "com_pl",
    "taxa_adm": "com_extrato",
    "cotizacao_resgate": "com_extrato",
    # O bucket deixou de ser exclusivo do Quantum: sai do CDA da CVM cruzado
    # com o registro de debêntures do SND. Ver connectors/cvm_carteira.py.
    "indexador": "com_carteira",
}


class Pipeline:
    def __init__(self) -> None:
        self._caches: dict[tuple, dict] = {}
        self._cache_ts: float = 0.0
        self._fundos: list[Fundo] = []
        self._janelas: list[dict] = []
        self._fonte_info: FonteInfo = FonteInfo(fonte=settings.DATA_SOURCE)
        self._data_referencia: str | None = None
        self._descartados_nao_credito = 0

    # ---------- seleção de fonte ----------
    def _fonte(self) -> DataConnector:
        if settings.DATA_SOURCE == "cvm":
            return CVMConnector()
        if settings.DATA_SOURCE == "mock":
            return MockConnector()
        return VinculadoConnector()

    # ---------- construção ----------
    def _construir(self) -> None:
        fonte = self._fonte()
        crus = fonte.carregar_fundos()

        # Dois enriquecedores, cada um no que sabe: a CVM entrega PL e cadastro
        # (casando pelo CNPJ do fundo); o Quantum entrega composição, duration,
        # cotização, taxa e status de captação.
        cvm = CVMCadastroEnricher()
        crus = cvm.enriquecer_lote(crus)
        quantum = QuantumEnricher()
        crus = quantum.enriquecer_lote(crus)

        # Universo: só crédito privado. O relatório é para mesa de crédito;
        # multimercado macro que passou no export só ocupa linha. O corte vem
        # ANTES das estatísticas de cobertura — elas descrevem o que o usuário
        # vê na tela, não o que veio no arquivo.
        modo = settings.CREDITO_PRIVADO_MODO
        limiar = settings.CREDITO_PRIVADO_LIMIAR
        antes = len(crus)
        selecionados = []
        for c in crus:
            ok, quais = credito_privado.avaliar(c, modo, limiar)
            if ok:
                c["sinais_credito"] = quais
                selecionados.append(c)
        crus = selecionados
        self._descartados_nao_credito = antes - len(crus)
        logger.info(
            "Universo crédito privado (modo=%s): %d de %d fundos, %d descartados",
            modo, len(crus), antes, self._descartados_nao_credito,
        )

        meta = fonte.metadados() if hasattr(fonte, "metadados") else {}
        self._janelas = meta.get("janelas", [])

        indisponiveis: list[str] = []
        if not quantum.disponivel():
            indisponiveis = [
                campo for campo in CAMPOS_SO_QUANTUM
                if not cvm.stats.get(COBERTOS_PELA_CVM.get(campo, ""))
            ]

        self._fonte_info = FonteInfo(
            fonte=fonte.name,
            arquivo=meta.get("arquivo"),
            recebido_em=meta.get("recebido_em"),
            quantum_ativo=quantum.disponivel(),
            cvm_ativo=bool(cvm.stats.get("com_pl")),
            campos_indisponiveis=indisponiveis,
            modo_credito_privado=modo,
            descartados_nao_credito=self._descartados_nao_credito or None,
            **_cobertura_pl(crus, cvm.stats),
        )
        self._data_referencia = meta.get("data_referencia")

        fundos: list[Fundo] = []
        for c in crus:
            # Perfil de indexador OBSERVADO — campo próprio, nunca o bucket.
            # Mede exposição do cotista (benchmark / comportamento da cota),
            # não composição da carteira. Ver services/perfil_indexador.py.
            perfil, origem, detalhe = perfil_indexador.avaliar(c)
            fundos.append(Fundo(
                perfil_indexador=perfil,
                perfil_indexador_origem=origem,
                perfil_indexador_detalhe=detalhe,
                cnpj=c.get("cnpj"),
                nome=str(c.get("nome", "")),
                gestora=str(c.get("gestora") or "Não classificado"),
                administrador=c.get("administrador"),
                classificacao_anbima=c.get("classificacao_anbima"),
                forma_condominio=c.get("forma_condominio"),
                cvm_situacao=c.get("cvm_situacao"),
                pl_data=c.get("pl_data"),
                bucket=_bucket_de(c),
                diaria=_f(c.get("diaria")),
                semanal=_f(c.get("semanal")),
                mensal=_f(c.get("mensal")),
                semestral=_f(c.get("semestral")),
                pl=_opt_f(c.get("pl")),
                duration=_opt_f(c.get("duration")),
                cotizacao_resgate=c.get("cotizacao_resgate"),
                taxa_adm=_opt_f(c.get("taxa_adm")),
                aberto_captacao=c.get("aberto_captacao"),
                resgate_pct_pl_semana=_opt_f(c.get("resgate_pct_pl_semana")),
                # --- composição da carteira (CDA + SND) -> gera o bucket ---
                pct_ipca=_opt_f(c.get("pct_ipca")),
                pct_cdi=_opt_f(c.get("pct_cdi")),
                pct_lf=_opt_f(c.get("pct_lf")),
                pct_pre=_opt_f(c.get("pct_pre")),
                pct_debenture=_opt_f(c.get("pct_debenture")),
                pct_cdb=_opt_f(c.get("pct_cdb")),
                pct_cri_cra=_opt_f(c.get("pct_cri_cra")),
                carteira_credito=_opt_f(c.get("carteira_credito")),
                carteira_pct_pl=_opt_f(c.get("carteira_pct_pl")),
                carteira_idx_conhecido_pct=_opt_f(c.get("carteira_idx_conhecido_pct")),
                carteira_sigilo_pct=_opt_f(c.get("carteira_sigilo_pct")),
                carteira_data=c.get("carteira_data"),
                carteira_motivo=c.get("carteira_motivo"),
                historico_semanal=c.get("historico_semanal") or {},
                pl_anterior=_opt_f(c.get("pl_anterior")),
                # --- informe diário ---
                rentab_pct=_opt_f(c.get("rentab_pct")),
                rentab_dias=c.get("rentab_dias"),
                vol_pct=_opt_f(c.get("vol_pct")),
                cotistas=c.get("cotistas"),
                cotistas_var_pct=_opt_f(c.get("cotistas_var_pct")),
                # --- EXTRATO ---
                taxa_perfm=_opt_f(c.get("taxa_perfm")),
                pagamento_resgate=c.get("pagamento_resgate"),
                aplicacao_minima=_opt_f(c.get("aplicacao_minima")),
                publico_alvo=c.get("publico_alvo"),
                extrato_data=c.get("extrato_data"),
                # --- PERFIL MENSAL ---
                pct_credito_privado=_opt_f(c.get("pct_credito_privado")),
                prazo_carteira_dias=_opt_f(c.get("prazo_carteira_dias")),
                conc_maior_cotista_pct=_opt_f(c.get("conc_maior_cotista_pct")),
                perfil_data=c.get("perfil_data"),
                perfil_cotistas=c.get("perfil_cotistas") or {},
                sinais_credito=c.get("sinais_credito") or [],
            ))
        self._fundos = fundos
        self._caches.clear()
        self._cache_ts = time.time()
        logger.info(
            "Pipeline construído: %d fundos, fonte=%s, arquivo=%s",
            len(fundos), self._fonte_info.fonte, self._fonte_info.arquivo,
        )

    def _garantir(self) -> None:
        ttl = settings.CACHE_TTL_HORAS * 3600
        if not self._fundos or (time.time() - self._cache_ts) > ttl:
            self._construir()

    def refresh(self) -> None:
        self._construir()

    # ---------- agregações ----------
    def _agregar(self, fundos: list[Fundo], janela: str) -> dict:
        # ----- por gestora -----
        by_g: dict[str, list[Fundo]] = defaultdict(list)
        for f in fundos:
            by_g[f.gestora].append(f)

        pl_universo = _soma_opt(f.pl for f in fundos)
        gestoras: list[GestoraResumo] = []
        for nome, fs in by_g.items():
            pl_total = _soma_opt(f.pl for f in fs)
            pl_ant = _soma_opt(f.pl_anterior for f in fs)
            var_pl = None
            if pl_total and pl_ant:
                var_pl = _round((pl_total / pl_ant - 1) * 100, 2)

            classificados = [f for f in fs if f.bucket is not None]
            abertos = [f for f in fs if f.aberto_captacao is not None]
            cotistas = _soma_opt(f.cotistas for f in fs)

            gestoras.append(GestoraResumo(
                nome=nome,
                fundos=len(fs),
                abertos=sum(1 for f in abertos if f.aberto_captacao) if abertos else None,
                diaria=sum(f.diaria for f in fs),
                semanal=sum(f.semanal for f in fs),
                mensal=sum(f.mensal for f in fs),
                semestral=sum(f.semestral for f in fs),
                pl=pl_total,
                var_pl_pct=var_pl,
                share_pl_pct=(
                    _round(pl_total / pl_universo * 100, 2)
                    if pl_total and pl_universo else None
                ),
                duration_media=_round(_wavg_pl(fs, "duration"), 1),
                # Taxa e cotização vão pela MEDIANA, não pela média ponderada
                # por PL: a ponderação é dominada pelos fundos master, que não
                # cobram taxa (ela é cobrada no feeder), e faria uma casa
                # grande parecer cobrar 0,01% a.a. A mediana responde a
                # pergunta certa — "quanto essa gestora tipicamente cobra".
                cotizacao_media=_round_int(_mediana(fs, "cotizacao_resgate")),
                taxa_adm_media=_round(_mediana(fs, "taxa_adm"), 2),
                rentab_media_pct=_round(_wavg_pl(fs, "rentab_pct"), 2),
                vol_media_pct=_round(_wavg_pl(fs, "vol_pct"), 2),
                prazo_carteira_dias=_round(_wavg_pl(fs, "prazo_carteira_dias"), 0),
                pct_credito_privado=_round(_wavg_pl(fs, "pct_credito_privado"), 1),
                cotistas=int(cotistas) if cotistas else None,
                cotistas_var_pct=_round(_wavg_pl(fs, "cotistas_var_pct"), 1),
                # Também pela mediana, e pelo mesmo motivo da taxa: o MENOR
                # mínimo de qualquer casa grande é zero, porque todas têm um
                # fundo master sem aplicação mínima — e master não se vende a
                # cliente. A mediana diz o bilhete típico da casa.
                aplicacao_minima=_mediana(fs, "aplicacao_minima"),
                publico_alvo=_predominante(fs, "publico_alvo"),
                perfil_cotistas=_perfil_gestora(fs),
                sem_classificacao=len(fs) - len(classificados),
                **_mix(classificados),
                **_mix_perfil(fs),
                **_mix_papel(fs),
            ))
        gestoras.sort(key=lambda g: abs(g.semestral), reverse=True)

        # ----- por bucket -----
        pl_mercado = _soma_opt(f.pl for f in fundos)
        buckets: list[BucketResumo] = []
        for b in Bucket:
            fs = [f for f in fundos if f.bucket == b]
            pl_b = _soma_opt(f.pl for f in fs)
            abertos = [f for f in fs if f.aberto_captacao is not None]
            buckets.append(BucketResumo(
                bucket=b,
                fluxo=sum(_fluxo(f, janela) for f in fs),
                fundos=len(fs),
                pct_pl=(pl_b / pl_mercado) if (pl_b and pl_mercado) else None,
                duration_media=_round(_wavg_pl(fs, "duration"), 1),
                cotizacao_media=_round_int(_wavg_pl(fs, "cotizacao_resgate")),
                pct_abertos=(
                    sum(1 for f in abertos if f.aberto_captacao) / len(abertos)
                    if abertos else None
                ),
            ))

        # ----- série temporal (fluxo líquido semanal, dado real) -----
        serie = self._serie(fundos)

        # ----- KPIs de fluxo -----
        kpis_fluxo = KpisFluxo(
            fluxo_liquido=sum(_fluxo(f, janela) for f in fundos),
            # Só dá para comparar com o período anterior na janela semanal, que
            # é a única com histórico ponto a ponto no arquivo.
            fluxo_liquido_anterior=(
                serie[-2].total if (janela == "semanal" and len(serie) >= 2) else None
            ),
            captacao_bruta=sum(v for f in fundos if (v := _fluxo(f, janela)) > 0),
            resgates=sum(v for f in fundos if (v := _fluxo(f, janela)) < 0),
            fundos_entrada=sum(1 for f in fundos if _fluxo(f, janela) > 0),
            fundos_saida=sum(1 for f in fundos if _fluxo(f, janela) < 0),
            pl_total=pl_mercado,
            pl_var_30d_pct=_var_pl(fundos),
        )

        # ----- KPIs de mesa -----
        com_status = [f for f in fundos if f.aberto_captacao is not None]
        abertos = [f for f in com_status if f.aberto_captacao]
        cots = [f.cotizacao_resgate for f in fundos if f.cotizacao_resgate is not None]
        estressados = self._estressados(fundos, janela)
        kpis_mesa = KpisMesa(
            fundos_abertos=len(abertos) if com_status else None,
            fundos_abertos_pct=(len(abertos) / len(com_status)) if com_status else None,
            pl_investivel=_soma_opt(f.pl for f in abertos),
            duration_media=_round(_wavg_pl(fundos, "duration"), 2),
            cotizacao_media=_round_int(_wavg_pl(fundos, "cotizacao_resgate")),
            fundos_cot_curta=sum(1 for c in cots if c < 15) if cots else None,
            # premio_ipca / premio_cdi: só o Quantum entrega. Sem ele, None.
            stress_fundos=len(estressados),
            stress_valor=abs(sum(_fluxo(f, janela) for f, _ in estressados)),
        )

        return {
            "dashboard": DashboardResponse(
                data_referencia=(
                    self._data_referencia
                    or (serie[-1].semana if serie else "")
                ),
                total_fundos=len(fundos),
                total_gestoras=len(by_g),
                total_sem_classificacao=sum(1 for f in fundos if f.bucket is None),
                fonte=self._fonte_info,
                kpis_fluxo=kpis_fluxo,
                kpis_mesa=kpis_mesa,
                buckets=buckets,
                serie_temporal=serie,
                gestoras=gestoras,
            ),
            "by_gestora": by_g,
            "gestoras_idx": {g.nome: g for g in gestoras},
            "fundos": fundos,
        }

    def _serie(self, fundos: list[Fundo]) -> list[SerieTemporalPonto]:
        """Fluxo líquido por janela semanal, total e (quando dá) por indexador."""
        if not self._janelas:
            return []

        pontos: list[SerieTemporalPonto] = []
        for j in self._janelas:
            chave = j["chave"]
            por_bucket: dict[Bucket, float] = defaultdict(float)
            total = 0.0
            algum_classificado = False
            for f in fundos:
                v = f.historico_semanal.get(chave)
                if v is None:
                    continue
                total += v
                if f.bucket is not None:
                    por_bucket[f.bucket] += v
                    algum_classificado = True
            pontos.append(SerieTemporalPonto(
                semana=chave,
                rotulo=j["rotulo"],
                curto=j["curto"],
                total=total,
                ipca=por_bucket[Bucket.IPCA] if algum_classificado else None,
                cdi=por_bucket[Bucket.CDI] if algum_classificado else None,
                lf=por_bucket[Bucket.LF] if algum_classificado else None,
                misto=por_bucket[Bucket.MISTO] if algum_classificado else None,
            ))
        return pontos

    # ---------- estresse ----------
    def _estressados(self, fundos: list[Fundo], janela: str) -> list[tuple[Fundo, dict]]:
        """Fundos com resgate anormal na janela.

        Duas réguas, escolhidas POR FUNDO conforme ele tenha PL ou não:

          * com PL  -> resgate acima de X% do PL (a régua de sempre);
          * sem PL  -> resgate acima de N vezes o movimento típico do próprio
            fundo nas últimas semanas, o que separa "fundo grande movimentando
            o de sempre" de "fundo saindo pela porta".

        A escolha é por fundo, e não pelo conjunto, de propósito: o casamento
        com a CVM cobre ~72% do universo, e aplicar a régua de %PL a todos
        faria os ~28% sem PL desaparecerem da tela sem aviso — justamente os
        fundos sobre os quais sabemos menos.
        """
        achados: list[tuple[Fundo, dict]] = []
        for f in fundos:
            fluxo = _fluxo(f, janela)
            if fluxo >= 0:
                continue

            if f.pl and f.resgate_pct_pl_semana is not None:
                if f.resgate_pct_pl_semana >= -settings.THRESHOLD_STRESS:
                    continue
                achados.append((f, {
                    "resgate_pct_pl": _round(f.resgate_pct_pl_semana * 100, 1),
                }))
                continue

            if abs(fluxo) < settings.STRESS_MINIMO_ABS:
                continue
            tipico = _movimento_tipico(f)
            severidade = (abs(fluxo) / tipico) if tipico else None
            if severidade is not None and severidade < settings.STRESS_MULTIPLO:
                continue
            achados.append((f, {
                "movimento_tipico": tipico,
                "severidade": _round(severidade, 1),
            }))

        # Ordena pelo tamanho do resgate: é o que a mesa olha primeiro, e é
        # comparável entre as duas réguas.
        achados.sort(key=lambda t: _fluxo(t[0], janela))
        return achados

    # ---------- filtros ----------
    def _get_data(self, janela: str, indexador: str, abertos: bool) -> dict:
        self._garantir()
        key = (janela, indexador, abertos)
        if key not in self._caches:
            fs = self._fundos
            # `aberto_captacao is None` = não sabemos; filtrar por isso zeraria
            # o dashboard inteiro enquanto o Quantum não responde.
            if abertos:
                fs = [f for f in fs if f.aberto_captacao is not False]
            if indexador != "todos":
                b_map = {"ipca": Bucket.IPCA, "cdi": Bucket.CDI,
                         "lf": Bucket.LF, "misto": Bucket.MISTO}
                if indexador in b_map:
                    fs = [f for f in fs if f.bucket == b_map[indexador]]
            self._caches[key] = self._agregar(fs, janela)
        return self._caches[key]

    # ---------- API consumida pelos routers ----------
    def dashboard(self, janela: str = "semanal", indexador: str = "todos",
                  abertos: bool = True) -> DashboardResponse:
        return self._get_data(janela, indexador, abertos)["dashboard"]

    def dossie(self, gestora: str, janela: str = "semanal", indexador: str = "todos",
               abertos: bool = True) -> DossieResponse | None:
        data = self._get_data(janela, indexador, abertos)
        g = data["gestoras_idx"].get(gestora)
        if not g:
            return None
        fundos = sorted(
            data["by_gestora"][gestora],
            key=lambda f: abs(_fluxo(f, janela)),
            reverse=True,
        )

        mixes = [("IPCA+", g.mix_ipca), ("CDI+", g.mix_cdi),
                 ("LF", g.mix_lf), ("Misto", g.mix_misto)]
        classe = None
        if any(v is not None for _, v in mixes):
            classe = max(mixes, key=lambda x: x[1] or 0)[0]

        # Sparkline = fluxo líquido semanal real da gestora, últimas 13 janelas.
        ultimas = self._janelas[-13:]
        sparkline = [
            sum(f.historico_semanal.get(j["chave"], 0.0) for f in fundos)
            for j in ultimas
        ]
        sparkline_cdi = [
            sum(f.historico_semanal.get(j["chave"], 0.0) for f in fundos if f.bucket == "cdi")
            for j in ultimas
        ]
        sparkline_ipca = [
            sum(f.historico_semanal.get(j["chave"], 0.0) for f in fundos if f.bucket == "ipca")
            for j in ultimas
        ]
        sparkline_lf = [
            sum(f.historico_semanal.get(j["chave"], 0.0) for f in fundos if f.bucket == "lf")
            for j in ultimas
        ]
        sparkline_misto = [
            sum(f.historico_semanal.get(j["chave"], 0.0) for f in fundos if f.bucket == "misto")
            for j in ultimas
        ]

        return DossieResponse(
            gestora=g,
            classe_majoritaria=classe,
            sparkline=sparkline,
            sparkline_labels=[j["curto"] for j in ultimas],
            sparkline_cdi=sparkline_cdi,
            sparkline_ipca=sparkline_ipca,
            sparkline_lf=sparkline_lf,
            sparkline_misto=sparkline_misto,
            fundos=fundos[:50],
        )

    def movers(self, direcao: str = "pos", limite: int = 6, janela: str = "semanal",
               indexador: str = "todos", abertos: bool = True) -> list[MoverGestora]:
        data = self._get_data(janela, indexador, abertos)
        gs = list(data["gestoras_idx"].values())
        # Sem PL não há "variação de PL"; a ordenação cai para o próprio fluxo
        # da janela, que é o dado real que temos.
        tem_pl = any(g.var_pl_pct is not None for g in gs)
        if tem_pl:
            # Piso de materialidade: uma gestora de R$ 2 mi que saiu do zero
            # marca +736% e ocupa o topo do painel sem dizer nada. "Top movers"
            # só é informativo entre casas que movem dinheiro relevante.
            gs = [g for g in gs if (g.pl or 0) >= settings.MOVERS_PL_MINIMO]
        chave = (lambda g: g.var_pl_pct or 0) if tem_pl else (lambda g: _janela_g(g, janela))
        gs.sort(key=chave, reverse=(direcao == "pos"))
        return [
            MoverGestora(
                nome=g.nome,
                duration_media=g.duration_media,
                cotizacao_media=g.cotizacao_media,
                fluxo=_janela_g(g, janela),
                var_pl_pct=g.var_pl_pct,
            )
            for g in gs[:limite]
        ]

    def stress(self, limite: int = 50, janela: str = "semanal",
               indexador: str = "todos", abertos: bool = True) -> list[StressFundo]:
        data = self._get_data(janela, indexador, abertos)
        achados = self._estressados(data["fundos"], janela)
        return [
            StressFundo(
                nome=f.nome,
                gestora=f.gestora,
                duration=f.duration,
                cotizacao_resgate=f.cotizacao_resgate,
                pl=f.pl,
                resgate=_fluxo(f, janela),
                **extra,
            )
            for f, extra in achados[:limite]
        ]

    def fonte_info(self) -> FonteInfo:
        self._garantir()
        return self._fonte_info


# ---------- helpers ----------
def _f(v) -> float:
    """Para campos de fluxo, onde ausência realmente significa zero."""
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _opt_f(v) -> float | None:
    """Para métricas, onde ausência significa 'não sabemos' — nunca zero."""
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _fluxo(f: Fundo, janela: str) -> float:
    return float(getattr(f, janela, 0.0) or 0.0)


def _janela_g(g: GestoraResumo, janela: str) -> float:
    return float(getattr(g, janela, 0.0) or 0.0)


def _bucket_de(cru: dict) -> Bucket | None:
    """Classifica só quando há composição; sem ela, o fundo fica sem bucket.

    Cair em MISTO por falta de dado seria mentira: MISTO significa "olhamos a
    carteira e nenhum indexador é majoritário", não "não olhamos".
    """
    if all(cru.get(k) is None for k in ("pct_lf", "pct_ipca", "pct_cdi")):
        return None
    return classificar_dict(cru)


def _cobertura_pl(crus: list[dict], stats: dict) -> dict:
    """Quanto do universo tem PL — vai no rodapé para qualificar os números.

    Importa dizer isso em voz alta: o "PL total" cobre ~80% dos fundos, porque
    o resto não está nas bases da CVM. Um total parcial apresentado como total
    vira erro de leitura na mesa.
    """
    total = len(crus)
    com_pl = sum(1 for f in crus if f.get("pl"))
    datas = [f["pl_data"] for f in crus if f.get("pl_data")]
    return {
        "fundos_com_pl": com_pl,
        "pl_cobertura_pct": round(com_pl / total * 100, 1) if total else None,
        "fundos_sem_pl": (total - com_pl) or None,
        "pl_do_informe": stats.get("pl_informe") or None,
        "pl_do_registro": stats.get("pl_registro") or None,
        "pl_data_max": max(datas) if datas else None,
        "fundos_com_extrato": sum(1 for f in crus if f.get("extrato_data")) or None,
        "fundos_com_perfil": sum(1 for f in crus if f.get("perfil_data")) or None,
        "fundos_com_rentab": sum(1 for f in crus if f.get("rentab_pct") is not None) or None,
        **_cobertura_carteira(crus),
        **_cobertura_perfil(crus),
    }


def _cobertura_carteira(crus: list[dict]) -> dict:
    """Quantos fundos têm composição de carteira — e de que mês ela é.

    A data importa tanto quanto a contagem: a carteira vem com defasagem de
    propósito (o CDA do mês corrente esconde 46% do PL sob sigilo), e um bucket
    de quatro meses atrás apresentado como "hoje" seria erro de leitura na mesa.
    """
    com = [f for f in crus if f.get("carteira_data")]
    if not com:
        return {"fundos_com_carteira": None, "carteira_data": None, "carteira_pl": None}
    return {
        "fundos_com_carteira": len(com),
        "carteira_data": max(f["carteira_data"] for f in com),
        "carteira_pl": _soma_opt(f.get("pl") for f in com),
    }


def _cobertura_perfil(crus: list[dict]) -> dict:
    """Quantos fundos têm perfil de indexador, e por qual rota.

    A separação declarado/inferido importa na tela: o primeiro é o benchmark
    que o fundo declarou, o segundo é leitura da volatilidade com ~87% de
    acerto. Misturar os dois num número só esconderia a diferença.
    """
    declarado = inferido = 0
    for c in crus:
        perfil, origem, _ = perfil_indexador.avaliar(c)
        if not perfil:
            continue
        if origem == perfil_indexador.DECLARADO:
            declarado += 1
        else:
            inferido += 1
    return {
        "fundos_com_perfil_indexador": (declarado + inferido) or None,
        "perfil_declarado": declarado or None,
        "perfil_inferido": inferido or None,
    }


def _soma_opt(valores) -> float | None:
    """Soma ignorando None; devolve None se nada tinha valor."""
    vs = [v for v in valores if v is not None]
    return sum(vs) if vs else None


def _wavg_pl(fundos: list[Fundo], attr: str) -> float | None:
    """Média do atributo ponderada por PL, com fallback para média simples.

    Sem PL (fonte do e-mail) a ponderação não é possível, mas a média simples
    ainda é informativa — e vira None se ninguém tem o atributo.
    """
    pares = [(getattr(f, attr), f.pl) for f in fundos if getattr(f, attr) is not None]
    if not pares:
        return None
    com_peso = [(v, p) for v, p in pares if p and p > 0]
    if com_peso:
        return sum(v * p for v, p in com_peso) / sum(p for _, p in com_peso)
    return sum(v for v, _ in pares) / len(pares)


def _mediana(fundos: list[Fundo], attr: str) -> float | None:
    """Mediana simples do atributo entre os fundos que o têm."""
    vs = sorted(v for f in fundos if (v := getattr(f, attr, None)) is not None)
    if not vs:
        return None
    meio = len(vs) // 2
    return vs[meio] if len(vs) % 2 else (vs[meio - 1] + vs[meio]) / 2


def _predominante(fundos: list[Fundo], attr: str) -> str | None:
    """Valor mais representativo do atributo, pesado por PL.

    Pesar por PL e não contar fundos importa: uma gestora com 20 exclusivos
    minúsculos e um fundo de varejo de R$ 5 bi é, na prática, uma casa de
    varejo — a contagem simples diria o contrário.
    """
    peso: dict[str, float] = defaultdict(float)
    for f in fundos:
        v = getattr(f, attr, None)
        if v:
            peso[str(v)] += (f.pl or 0) or 1.0
    return max(peso, key=peso.get) if peso else None


def _perfil_gestora(fundos: list[Fundo]) -> dict:
    """Perfil de cotistas da gestora: média das frações, ponderada por PL."""
    acc: dict[str, float] = defaultdict(float)
    peso_total = 0.0
    for f in fundos:
        if not f.perfil_cotistas:
            continue
        peso = (f.pl or 0) or 1.0
        peso_total += peso
        for k, v in f.perfil_cotistas.items():
            acc[k] += v * peso
    if not peso_total:
        return {}
    # Só o que é material — abaixo de 1% do PL vira ruído na tela.
    return {k: round(v / peso_total, 1) for k, v in acc.items() if v / peso_total >= 1.0}


def _mix(classificados: list[Fundo]) -> dict:
    """Mix por indexador, em fração do PL. Sem PL ou sem classificação, None."""
    vazio = {"mix_ipca": None, "mix_cdi": None, "mix_lf": None, "mix_misto": None}
    if not classificados:
        return vazio
    pl_total = _soma_opt(f.pl for f in classificados)
    if not pl_total:
        return vazio
    acc: dict[Bucket, float] = defaultdict(float)
    for f in classificados:
        acc[f.bucket] += f.pl or 0.0
    return {
        "mix_ipca": acc[Bucket.IPCA] / pl_total,
        "mix_cdi": acc[Bucket.CDI] / pl_total,
        "mix_lf": acc[Bucket.LF] / pl_total,
        "mix_misto": acc[Bucket.MISTO] / pl_total,
    }


def _mix_papel(fundos: list[Fundo]) -> dict:
    """Em que instrumento a casa entra, em fração da carteira de crédito dela.

    Pesa pelo R$ de carteira, não por PL nem por contagem: a pergunta é "deste
    dinheiro em crédito, quanto está em debênture, LF, CDB, CRI/CRA", e o único
    peso que responde isso é o próprio tamanho da carteira de cada fundo.
    """
    com = [f for f in fundos if f.carteira_credito]
    vazio = {"papel_debenture": None, "papel_lf": None, "papel_cdb": None,
             "papel_cri_cra": None, "carteira_credito": None, "carteira_data": None}
    if not com:
        return vazio
    total = sum(f.carteira_credito for f in com)
    if not total:
        return vazio
    peso = lambda attr: round(  # noqa: E731
        sum((getattr(f, attr) or 0.0) * f.carteira_credito for f in com) / total, 4
    )
    return {
        "papel_debenture": peso("pct_debenture"),
        "papel_lf": peso("pct_lf"),
        "papel_cdb": peso("pct_cdb"),
        "papel_cri_cra": peso("pct_cri_cra"),
        "carteira_credito": total,
        # Todos vêm do mesmo CDA; o max cobre a hipótese de meses misturados.
        "carteira_data": max((f.carteira_data for f in com if f.carteira_data),
                             default=None),
    }


def _mix_perfil(fundos: list[Fundo]) -> dict:
    """Fração do PL da gestora em pós-fixado vs. inflação, pelo perfil observado.

    Pondera por PL quando há PL, e cai para contagem de fundos quando não há —
    aqui a cobertura de perfil (~82%) é maior que a de PL (~80%), então perder
    o fundo inteiro por falta de PL desperdiçaria informação.
    """
    com_perfil = [f for f in fundos if f.perfil_indexador]
    indefinidos = len(fundos) - len(com_perfil)
    if not com_perfil:
        return {"perfil_pos_pct": None, "perfil_inflacao_pct": None,
                "perfil_indefinido": indefinidos}
    acc: dict[str, float] = defaultdict(float)
    for f in com_perfil:
        acc[f.perfil_indexador] += (f.pl or 0) or 1.0
    total = sum(acc.values())
    return {
        "perfil_pos_pct": round(acc["pos"] / total * 100, 1),
        "perfil_inflacao_pct": round(acc["inflacao"] / total * 100, 1),
        "perfil_indefinido": indefinidos,
    }


def _var_pl(fundos: list[Fundo]) -> float | None:
    atual = _soma_opt(f.pl for f in fundos)
    anterior = _soma_opt(f.pl_anterior for f in fundos)
    if not atual or not anterior:
        return None
    return _round((atual / anterior - 1) * 100, 2)


def _movimento_tipico(f: Fundo) -> float | None:
    """Média do |fluxo semanal| nas semanas anteriores à mais recente."""
    if not f.historico_semanal:
        return None
    # historico_semanal é chaveado pela data de fim em ISO, então ordenar por
    # chave já dá ordem cronológica.
    semanas = sorted(f.historico_semanal)[:-1][-settings.STRESS_SEMANAS_BASE:]
    if not semanas:
        return None
    valores = [abs(f.historico_semanal[s]) for s in semanas]
    media = sum(valores) / len(valores)
    return media or None


def _round(v, n):
    return round(v, n) if v is not None else None


def _round_int(v):
    return int(round(v)) if v is not None else None


# Instância singleton usada pelos routers.
pipeline = Pipeline()
