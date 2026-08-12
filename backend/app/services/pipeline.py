"""
Pipeline de dados: da fonte crua ao payload da API.

Fluxo:
    fonte (CVM ou mock) -> enriquecimento (Quantum) -> classificação (bucket)
    -> agregação por gestora / bucket / série temporal -> cache em memória.

O resultado fica cacheado por CACHE_TTL_HORAS; um POST /admin/refresh força
recálculo (útil após atualizar os dados da CVM).
"""
from __future__ import annotations

import time
from collections import defaultdict
from datetime import date

from app.config import settings
from app.connectors.base import DataConnector
from app.connectors.cvm_connector import CVMConnector
from app.connectors.mock_connector import MockConnector
from app.connectors.quantum_connector import QuantumEnricher
from app.models.schemas import (
    Bucket,
    BucketResumo,
    DashboardResponse,
    DossieResponse,
    Fundo,
    GestoraResumo,
    KpisFluxo,
    KpisMesa,
    MoverGestora,
    SerieTemporalPonto,
    StressFundo,
)
from app.services.classifier import classificar_dict


class Pipeline:
    def __init__(self) -> None:
        self._caches: dict[tuple, dict] = {}
        self._cache_ts: float = 0.0
        self._fundos: list[Fundo] = []

    # ---------- seleção de fonte ----------
    def _fonte(self) -> DataConnector:
        if settings.DATA_SOURCE == "cvm":
            return CVMConnector()
        return MockConnector()

    # ---------- construção ----------
    def _construir(self) -> None:
        fonte = self._fonte()
        crus = fonte.carregar_fundos()

        # Enriquecimento (no-op se Quantum desligado)
        enricher = QuantumEnricher()
        crus = enricher.enriquecer_lote(crus)

        fundos: list[Fundo] = []
        for c in crus:
            bucket = classificar_dict(c)
            fundos.append(Fundo(
                cnpj=str(c.get("cnpj", "")),
                nome=str(c.get("nome", "")),
                gestora=str(c.get("gestora", "Não classificado")),
                bucket=bucket,
                diaria=float(c.get("diaria", 0) or 0),
                semanal=float(c.get("semanal", 0) or 0),
                mensal=float(c.get("mensal", 0) or 0),
                semestral=float(c.get("semestral", 0) or 0),
                pl=float(c.get("pl", 0) or 0),
                duration=c.get("duration"),
                cotizacao_resgate=c.get("cotizacao_resgate"),
                taxa_adm=c.get("taxa_adm"),
                aberto_captacao=bool(c.get("aberto_captacao", True)),
                resgate_pct_pl_semana=c.get("resgate_pct_pl_semana"),
                pct_ipca=float(c.get("pct_ipca", 0) or 0),
                pct_cdi=float(c.get("pct_cdi", 0) or 0),
                pct_lf=float(c.get("pct_lf", 0) or 0),
            ))
        self._fundos = fundos
        self._caches.clear()
        self._cache_ts = time.time()

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

        gestoras: list[GestoraResumo] = []
        for nome, fs in by_g.items():
            pl_total = sum(f.pl for f in fs) or 1.0
            def wavg(attr):
                vals = [(getattr(f, attr), f.pl) for f in fs if getattr(f, attr) is not None and f.pl > 0]
                if not vals:
                    return None
                return sum(v * p for v, p in vals) / sum(p for _, p in vals)

            mix = defaultdict(float)
            pl_30d_total = 0.0
            for f in fs:
                mix[f.bucket] += f.pl
                pl_30d_total += getattr(f, 'pl_2semanas', f.pl)
            
            var_pl_pct_gestora = ((pl_total / pl_30d_total) - 1) * 100 if pl_30d_total > 0 else 0.0
            
            gestoras.append(GestoraResumo(
                nome=nome,
                fundos=len(fs),
                abertos=sum(1 for f in fs if f.aberto_captacao),
                diaria=sum(f.diaria for f in fs),
                semanal=sum(f.semanal for f in fs),
                mensal=sum(f.mensal for f in fs),
                semestral=sum(f.semestral for f in fs),
                pl=sum(f.pl for f in fs),
                var_pl_pct=_round(var_pl_pct_gestora, 2),
                duration_media=_round(wavg("duration"), 1),
                cotizacao_media=_round_int(wavg("cotizacao_resgate")),
                taxa_adm_media=_round(wavg("taxa_adm"), 2),
                mix_ipca=mix[Bucket.IPCA] / pl_total,
                mix_cdi=mix[Bucket.CDI] / pl_total,
                mix_lf=mix[Bucket.LF] / pl_total,
                mix_misto=mix[Bucket.MISTO] / pl_total,
            ))
        gestoras.sort(key=lambda g: abs(g.semestral), reverse=True)

        # ----- por bucket -----
        pl_mercado = sum(f.pl for f in fundos) or 1.0
        buckets: list[BucketResumo] = []
        for b in Bucket:
            fs = [f for f in fundos if f.bucket == b]
            if not fs:
                buckets.append(BucketResumo(bucket=b, fluxo=0, fundos=0, pct_pl=0, duration_media=None, cotizacao_media=None, pct_abertos=0))
                continue
            pl_b = sum(f.pl for f in fs)
            durs = [(f.duration, f.pl) for f in fs if f.duration and f.pl > 0]
            cots = [(f.cotizacao_resgate, f.pl) for f in fs if f.cotizacao_resgate and f.pl > 0]
            buckets.append(BucketResumo(
                bucket=b,
                fluxo=sum(getattr(f, janela, 0) for f in fs),
                fundos=len(fs),
                pct_pl=pl_b / pl_mercado,
                duration_media=_round(_wavg(durs), 1),
                cotizacao_media=_round_int(_wavg(cots)),
                pct_abertos=sum(1 for f in fs if f.aberto_captacao) / len(fs),
            ))

        # ----- KPIs de fluxo -----
        captacao = sum(getattr(f, janela, 0) for f in fundos if getattr(f, janela, 0) > 0)
        resgates = sum(getattr(f, janela, 0) for f in fundos if getattr(f, janela, 0) < 0)
        kpis_fluxo = KpisFluxo(
            fluxo_liquido=sum(getattr(f, janela, 0) for f in fundos),
            fluxo_liquido_anterior=sum(getattr(f, janela, 0) for f in fundos) * 0.85,  # placeholder
            captacao_bruta=captacao,
            resgates=resgates,
            fundos_entrada=sum(1 for f in fundos if getattr(f, janela, 0) > 0),
            fundos_saida=sum(1 for f in fundos if getattr(f, janela, 0) < 0),
            pl_total=pl_mercado,
            pl_var_30d_pct=_round(((pl_mercado / sum(getattr(f, 'pl_2semanas', f.pl) for f in fundos)) - 1) * 100, 2) if sum(getattr(f, 'pl_2semanas', f.pl) for f in fundos) > 0 else 0.0,
        )

        # ----- KPIs de mesa -----
        abertos = [f for f in fundos if f.aberto_captacao]
        durs_all = [(f.duration, f.pl) for f in fundos if f.duration and f.pl > 0]
        cots_all = [(f.cotizacao_resgate, f.pl) for f in fundos if f.cotizacao_resgate and f.pl > 0]
        txs_all = [(f.taxa_adm, f.pl) for f in fundos if f.taxa_adm and f.pl > 0]
        stress = [f for f in fundos if (f.resgate_pct_pl_semana or 0) < -settings.THRESHOLD_STRESS]
        kpis_mesa = KpisMesa(
            fundos_abertos=len(abertos),
            fundos_abertos_pct=len(abertos) / len(fundos) if fundos else 0,
            pl_investivel=sum(f.pl for f in abertos),
            duration_media=_round(_wavg(durs_all), 2) or 0,
            cotizacao_media=_round_int(_wavg(cots_all)) or 0,
            fundos_cot_curta=sum(1 for f in fundos if f.cotizacao_resgate and f.cotizacao_resgate < 15),
            premio_ipca=7.42,  # placeholder — vem do Quantum
            premio_cdi=2.18,   # placeholder — vem do Quantum
            taxa_adm_media=_round(_wavg(txs_all), 2) or 0,
            stress_fundos=len(stress),
            stress_valor=abs(sum(getattr(f, janela, 0) for f in stress)),
        )

        # ----- série temporal real -----
        # Coletar todas as semanas disponíveis no histórico
        semanas_set = set()
        for f in fundos:
            if hasattr(f, 'historico_pl'):
                semanas_set.update(f.historico_pl.keys())
        semanas_ordenadas = sorted(list(semanas_set))
        
        serie = []
        if semanas_ordenadas:
            # Pegamos as últimas 26 semanas ou o que tiver
            semanas_ordenadas = semanas_ordenadas[-26:]
            for sem in semanas_ordenadas:
                ipca = sum(f.historico_pl.get(sem, 0.0) for f in fundos if f.bucket == Bucket.IPCA and hasattr(f, 'historico_pl'))
                cdi = sum(f.historico_pl.get(sem, 0.0) for f in fundos if f.bucket == Bucket.CDI and hasattr(f, 'historico_pl'))
                lf = sum(f.historico_pl.get(sem, 0.0) for f in fundos if f.bucket == Bucket.LF and hasattr(f, 'historico_pl'))
                misto = sum(f.historico_pl.get(sem, 0.0) for f in fundos if f.bucket == Bucket.MISTO and hasattr(f, 'historico_pl'))
                
                serie.append(SerieTemporalPonto(
                    semana=sem,
                    ipca=ipca,
                    cdi=cdi,
                    lf=lf,
                    misto=misto,
                ))
        else:
            serie = _serie_placeholder()

        return {
            "dashboard": DashboardResponse(
                data_referencia=date.today().isoformat(),
                total_fundos=len(fundos),
                total_gestoras=len(by_g),
                cobertura_pct=96.4,  # placeholder até cruzar com universo CVM total
                kpis_fluxo=kpis_fluxo,
                kpis_mesa=kpis_mesa,
                buckets=buckets,
                serie_temporal=serie,
                gestoras=gestoras,
            ),
            "by_gestora": by_g,
            "gestoras_idx": {g.nome: g for g in gestoras},
        }

    def _get_data(self, janela: str, indexador: str, abertos: bool) -> dict:
        self._garantir()
        key = (janela, indexador, abertos)
        if key not in self._caches:
            fs = self._fundos
            if abertos:
                fs = [f for f in fs if f.aberto_captacao]
            if indexador != "todos":
                b_map = {"ipca": Bucket.IPCA, "cdi": Bucket.CDI, "lf": Bucket.LF, "misto": Bucket.MISTO}
                if indexador in b_map:
                    fs = [f for f in fs if f.bucket == b_map[indexador]]
            self._caches[key] = self._agregar(fs, janela)
        return self._caches[key]

    # ---------- API consumida pelos routers ----------
    def dashboard(self, janela: str = "semanal", indexador: str = "todos", abertos: bool = True) -> DashboardResponse:
        data = self._get_data(janela, indexador, abertos)
        return data["dashboard"]

    def dossie(self, gestora: str, janela: str = "semanal", indexador: str = "todos", abertos: bool = True) -> DossieResponse | None:
        data = self._get_data(janela, indexador, abertos)
        g = data["gestoras_idx"].get(gestora)
        if not g:
            return None
        fundos = sorted(
            data["by_gestora"][gestora],
            key=lambda f: abs(getattr(f, janela, 0)),
            reverse=True,
        )
        classe = max(
            [("IPCA+", g.mix_ipca), ("CDI+", g.mix_cdi), ("LF", g.mix_lf), ("Misto", g.mix_misto)],
            key=lambda x: x[1],
        )[0]
        sparkline = [getattr(f, janela, 0) * (0.5 + (i % 5 - 2) * 0.3) for i in range(13) for f in fundos[:1]]
        if not sparkline:
            sparkline = [0]*13
        return DossieResponse(
            gestora=g,
            classe_majoritaria=classe,
            sparkline=sparkline,
            fundos=fundos[:50],
        )

    def movers(self, direcao: str = "pos", limite: int = 6, janela: str = "semanal", indexador: str = "todos", abertos: bool = True) -> list[MoverGestora]:
        data = self._get_data(janela, indexador, abertos)
        gs = list(data["gestoras_idx"].values())
        gs.sort(key=lambda g: getattr(g, janela, 0), reverse=(direcao == "pos"))
        return [
            MoverGestora(
                nome=g.nome,
                duration_media=g.duration_media,
                cotizacao_media=g.cotizacao_media,
                var_pl_pct=g.var_pl_pct,
            )
            for g in gs[:limite]
        ]

    def stress(self, limite: int = 50, janela: str = "semanal", indexador: str = "todos", abertos: bool = True) -> list[StressFundo]:
        data = self._get_data(janela, indexador, abertos)
        # Para o stress, pegamos direto dos fundos (já filtrados no cache por _get_data)
        # Precisamos buscar de data["by_gestora"]
        fs = []
        for gest_fs in data["by_gestora"].values():
            fs.extend(gest_fs)
            
        fs = [f for f in fs if (f.resgate_pct_pl_semana or 0) < -settings.THRESHOLD_STRESS]
        fs.sort(key=lambda f: f.resgate_pct_pl_semana or 0)
        return [
            StressFundo(
                nome=f.nome,
                gestora=f.gestora,
                duration=f.duration,
                cotizacao_resgate=f.cotizacao_resgate,
                resgate_pct_pl=(f.resgate_pct_pl_semana or 0) * 100,
            )
            for f in fs[:limite]
        ]


# ---------- helpers ----------
def _wavg(pairs: list[tuple]) -> float | None:
    pairs = [(v, w) for v, w in pairs if v is not None and w > 0]
    if not pairs:
        return None
    return sum(v * w for v, w in pairs) / sum(w for _, w in pairs)


def _round(v, n):
    return round(v, n) if v is not None else None


def _round_int(v):
    return int(round(v)) if v is not None else None


def _serie_placeholder() -> list[SerieTemporalPonto]:
    import math
    semanas = ["S-25", "S-24", "S-23", "S-22", "S-21", "S-20", "S-19", "S-18",
               "S-17", "S-16", "S-15", "S-14", "S-13", "S-12", "S-11", "S-10",
               "S-9", "S-8", "S-7", "S-6", "S-5", "S-4", "S-3", "S-2", "S-1", "S-0"]
    out = []
    for i, s in enumerate(semanas):
        out.append(SerieTemporalPonto(
            semana=s,
            ipca=math.sin(i * .5) * 2e9 + 1e9,
            cdi=math.sin(i * .5 + 2) * 2e9 - 3e9,
            lf=math.sin(i * .5 + 4) * 2e9 - 5e8,
            misto=math.cos(i * .4) * 1e9 - 8e8,
        ))
    return out


# Instância singleton usada pelos routers.
pipeline = Pipeline()
