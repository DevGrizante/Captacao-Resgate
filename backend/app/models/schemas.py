"""
Schemas Pydantic — contratos de dados da API.

Estes modelos definem o formato exato que o front recebe. Se você mudar
um campo aqui, o front (js/api.js) precisa acompanhar.

CONVENÇÃO IMPORTANTE: `None` significa "não temos esse dado", e não zero.
A fonte de hoje (planilha `vinculado_*.xlsx` que chega por e-mail) entrega
fluxo, mas não entrega PL, composição por indexador, duration, cotização nem
taxa de administração. Esses campos vêm como `None` e o front mostra "—".
Quando o Quantum Axis for liberado, o `QuantumEnricher` preenche e os mesmos
campos passam a vir com valor, sem mudar contrato.
"""
from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class Bucket(str, Enum):
    """Classificação por indexador majoritário da carteira."""
    IPCA = "ipca"
    CDI = "cdi"
    LF = "lf"
    MISTO = "misto"


class Janela(str, Enum):
    DIARIA = "diaria"
    SEMANAL = "semanal"
    MENSAL = "mensal"
    SEMESTRAL = "semestral"


class Fundo(BaseModel):
    """Um fundo individual, já enriquecido e classificado."""
    cnpj: Optional[str] = None
    nome: str
    gestora: str
    # --- cadastro oficial da CVM (registro_fundo_classe.zip) ---
    administrador: Optional[str] = None
    classificacao_anbima: Optional[str] = None
    # "Aberto"/"Fechado" — natureza do condomínio, NÃO é `aberto_captacao`.
    forma_condominio: Optional[str] = None
    cvm_situacao: Optional[str] = None
    pl_data: Optional[str] = None            # data de referência do PL

    # --- série do informe diário ---
    rentab_pct: Optional[float] = None       # variação da cota no período
    rentab_dias: Optional[int] = None        # tamanho da janela, p/ qualificar
    vol_pct: Optional[float] = None          # volatilidade anualizada
    cotistas: Optional[int] = None
    cotistas_var_pct: Optional[float] = None

    # --- EXTRATO (declaração anual) ---
    taxa_perfm: Optional[float] = None       # % sobre o que exceder o índice
    pagamento_resgate: Optional[int] = None  # D+ até o dinheiro cair
    aplicacao_minima: Optional[float] = None
    publico_alvo: Optional[str] = None
    extrato_data: Optional[str] = None       # quando foi declarado

    # --- PERFIL MENSAL ---
    pct_credito_privado: Optional[float] = None
    prazo_carteira_dias: Optional[float] = None     # proxy de duration
    conc_maior_cotista_pct: Optional[float] = None
    perfil_data: Optional[str] = None
    # Fração do PL por tipo de investidor — quem já compra este fundo.
    perfil_cotistas: dict[str, float] = Field(default_factory=dict)

    # --- perfil de indexador OBSERVADO ---
    # ATENÇÃO: não é `bucket`. Mede a exposição do cotista (benchmark que o
    # fundo persegue, ou como a cota se comporta), não a composição da
    # carteira. Em crédito as duas divergem — comprar IPCA+ e travar em CDI
    # via swap é rotina. Ver services/perfil_indexador.py.
    perfil_indexador: Optional[str] = None          # "pos" | "inflacao"
    perfil_indexador_origem: Optional[str] = None   # "declarado" | "inferido"
    perfil_indexador_detalhe: Optional[str] = None  # o índice, ou a vol. usada

    # --- universo ---
    # Quais sinais indicaram crédito privado (ver services/credito_privado.py).
    sinais_credito: list[str] = Field(default_factory=list)
    # None = sem composição para classificar (aguardando Quantum).
    bucket: Optional[Bucket] = None
    # Fluxos por janela (R$)
    diaria: float = 0.0
    semanal: float = 0.0
    mensal: float = 0.0
    semestral: float = 0.0
    # Enriquecimento (Quantum / CDA)
    pl: Optional[float] = None
    duration: Optional[float] = None         # anos
    cotizacao_resgate: Optional[int] = None  # D+X
    taxa_adm: Optional[float] = None         # % a.a.
    aberto_captacao: Optional[bool] = None
    resgate_pct_pl_semana: Optional[float] = None  # p/ sinal de estresse
    # --- composição da carteira (CDA da CVM + registro de debêntures do SND) ---
    # São as frações da CARTEIRA DE CRÉDITO, não do PL, e é o que gera `bucket`.
    # `pct_ipca`/`pct_cdi`/`pct_pre` medem o indexador do que não é LF: o
    # classificador tira LF da conta primeiro, porque LF é instrumento e não
    # indexador. Ver connectors/cvm_carteira.py.
    pct_ipca: Optional[float] = None
    pct_cdi: Optional[float] = None
    pct_lf: Optional[float] = None
    pct_pre: Optional[float] = None
    # Mix por instrumento — a outra pergunta da mesa: em que papel ele entra.
    pct_debenture: Optional[float] = None
    pct_cdb: Optional[float] = None
    pct_cri_cra: Optional[float] = None
    carteira_credito: Optional[float] = None            # R$ em crédito
    carteira_pct_pl: Optional[float] = None             # quanto do fundo é isso
    carteira_idx_conhecido_pct: Optional[float] = None  # qualidade da leitura
    carteira_sigilo_pct: Optional[float] = None         # % do PL sob sigilo
    # Mês do CDA lido. Vem com defasagem de propósito (o sigilo do mês corrente
    # esconde 46% do PL); a tela precisa dizer de quando é a carteira.
    carteira_data: Optional[str] = None
    # Por que este fundo apareceu no CDA e mesmo assim ficou sem bucket:
    # "carteira sob sigilo" | "indexador desconhecido" | "pouco crédito no PL".
    carteira_motivo: Optional[str] = None

    # --- uso interno do pipeline, não trafega para o front ---
    # Fluxo líquido por semana, {data_fim_iso: R$}. Alimenta a série temporal,
    # o sparkline do dossiê e o proxy de estresse. São 40 pontos por fundo —
    # mandar isso para 4.6k fundos deixaria o payload inutilizável.
    historico_semanal: dict[str, float] = Field(default_factory=dict, exclude=True)
    # PL de ~2 semanas atrás, para a variação de PL (só fontes com PL).
    pl_anterior: Optional[float] = Field(default=None, exclude=True)


class GestoraResumo(BaseModel):
    """Consolidado de uma gestora — a linha da tabela principal."""
    nome: str
    fundos: int
    abertos: Optional[int] = None
    diaria: float
    semanal: float
    mensal: float
    semestral: float
    pl: Optional[float] = None
    var_pl_pct: Optional[float] = None
    duration_media: Optional[float] = None
    cotizacao_media: Optional[int] = None
    taxa_adm_media: Optional[float] = None
    # Mix de composição (fração 0..1). None = sem composição para nenhum fundo.
    mix_ipca: Optional[float] = None
    mix_cdi: Optional[float] = None
    mix_lf: Optional[float] = None
    mix_misto: Optional[float] = None
    # Fundos da gestora ainda sem classificação de indexador.
    sem_classificacao: int = 0
    # --- em que PAPEL a casa entra (fração da carteira de crédito dela) ---
    # Eixo diferente do `mix_*`: aquele é o indexador, este é o instrumento.
    # Um broker precisa dos dois — "compra IPCA+" e "compra debênture" são
    # perguntas distintas, e uma casa de LF é um cliente de outro produto.
    papel_debenture: Optional[float] = None
    papel_lf: Optional[float] = None
    papel_cdb: Optional[float] = None
    papel_cri_cra: Optional[float] = None
    carteira_credito: Optional[float] = None   # R$ em crédito, somado
    carteira_data: Optional[str] = None        # mês do CDA lido

    # --- métricas de mesa, ponderadas por PL quando há PL ---
    rentab_media_pct: Optional[float] = None
    vol_media_pct: Optional[float] = None
    prazo_carteira_dias: Optional[float] = None      # proxy de duration
    pct_credito_privado: Optional[float] = None
    cotistas: Optional[int] = None                   # soma dos fundos
    cotistas_var_pct: Optional[float] = None
    # Menor bilhete de entrada entre os fundos abertos — "dá para entrar com
    # quanto nessa gestora?". Mediana enganaria: o broker quer o mínimo real.
    aplicacao_minima: Optional[float] = None
    publico_alvo: Optional[str] = None               # predominante, por PL
    # Fração do PL da gestora por tipo de investidor — quem já é cliente dela.
    perfil_cotistas: dict[str, float] = Field(default_factory=dict)
    share_pl_pct: Optional[float] = None             # fatia do PL do universo
    # Mix por perfil de indexador OBSERVADO (fração do PL). Convive com o
    # `mix_*` acima sem se confundir: aquele vem da composição da carteira
    # (Quantum), este da exposição do cotista.
    perfil_pos_pct: Optional[float] = None
    perfil_inflacao_pct: Optional[float] = None
    perfil_indefinido: int = 0                       # fundos sem perfil


class BucketResumo(BaseModel):
    bucket: Bucket
    fluxo: float
    fundos: int
    pct_pl: Optional[float] = None
    duration_media: Optional[float] = None
    cotizacao_media: Optional[int] = None
    pct_abertos: Optional[float] = None


class KpisFluxo(BaseModel):
    fluxo_liquido: float
    # Mesma janela, período imediatamente anterior. None quando a fonte não dá
    # histórico suficiente para calcular.
    fluxo_liquido_anterior: Optional[float] = None
    captacao_bruta: float
    resgates: float
    fundos_entrada: int
    fundos_saida: int
    pl_total: Optional[float] = None
    pl_var_30d_pct: Optional[float] = None


class KpisMesa(BaseModel):
    fundos_abertos: Optional[int] = None
    fundos_abertos_pct: Optional[float] = None
    pl_investivel: Optional[float] = None
    duration_media: Optional[float] = None
    cotizacao_media: Optional[int] = None
    fundos_cot_curta: Optional[int] = None   # D+ < 15
    premio_ipca: Optional[float] = None
    premio_cdi: Optional[float] = None
    taxa_adm_media: Optional[float] = None
    stress_fundos: int = 0
    stress_valor: float = 0.0


class SerieTemporalPonto(BaseModel):
    """Um ponto da série de fluxo semanal.

    `total` sempre vem preenchido. As quebras por indexador só existem quando
    há composição — sem Quantum, vêm `None` e o front plota só o total.
    """
    semana: str          # data de fim da janela, ISO (ordenável)
    rotulo: str          # "06/08/2026 - 12/08/2026"
    curto: str           # "12/08" — o que vai no eixo X
    total: float
    ipca: Optional[float] = None
    cdi: Optional[float] = None
    lf: Optional[float] = None
    misto: Optional[float] = None


class FonteInfo(BaseModel):
    """De onde vieram os dados desta resposta — vai no rodapé do dashboard."""
    fonte: str                                # "vinculado" | "cvm" | "mock"
    arquivo: Optional[str] = None             # vinculado_20260814_1245.xlsx
    recebido_em: Optional[str] = None         # ISO
    quantum_ativo: bool = False
    cvm_ativo: bool = False                   # PL veio do cadastro da CVM
    # Campos que esta fonte não entrega — o front usa para mostrar "—" e avisar.
    campos_indisponiveis: list[str] = []
    # Cobertura do PL. O casamento por CNPJ não é total: fundo fora das bases
    # da CVM fica sem PL, e o "PL total" precisa dizer isso em voz alta.
    fundos_com_pl: int = 0
    pl_cobertura_pct: Optional[float] = None
    fundos_sem_pl: Optional[int] = None
    # De onde veio cada PL: informe diário (D-1) ou registro de fundos.
    pl_do_informe: Optional[int] = None
    pl_do_registro: Optional[int] = None
    pl_data_max: Optional[str] = None
    # Cobertura das demais bases da CVM.
    fundos_com_extrato: Optional[int] = None    # taxa adm, cotização, mínimo
    fundos_com_perfil: Optional[int] = None     # % crédito, prazo, cotistas
    fundos_com_rentab: Optional[int] = None
    # Composição da carteira (CDA) — é o que preenche o bucket.
    fundos_com_carteira: Optional[int] = None
    carteira_data: Optional[str] = None         # mês do CDA lido (AAAA-MM)
    carteira_pl: Optional[float] = None         # PL dos fundos com carteira
    # Perfil de indexador observado — não é o bucket, ver Fundo.perfil_indexador
    fundos_com_perfil_indexador: Optional[int] = None
    perfil_declarado: Optional[int] = None
    perfil_inferido: Optional[int] = None
    # Universo: quantos fundos o filtro de crédito privado descartou.
    modo_credito_privado: Optional[str] = None
    descartados_nao_credito: Optional[int] = None


class DashboardResponse(BaseModel):
    """Payload principal consumido pela home do front."""
    data_referencia: str
    total_fundos: int
    total_gestoras: int
    cobertura_pct: Optional[float] = None
    # Fundos sem composição de carteira e portanto sem bucket de indexador.
    total_sem_classificacao: int = 0
    fonte: FonteInfo
    kpis_fluxo: KpisFluxo
    kpis_mesa: KpisMesa
    buckets: list[BucketResumo]
    serie_temporal: list[SerieTemporalPonto]
    gestoras: list[GestoraResumo]


class DossieResponse(BaseModel):
    """Payload do painel lateral de uma gestora."""
    gestora: GestoraResumo
    classe_majoritaria: Optional[str] = None
    sparkline: list[float]           # fluxo das últimas semanas
    sparkline_labels: list[str] = []
    sparkline_cdi: list[float] = []
    sparkline_ipca: list[float] = []
    sparkline_lf: list[float] = []
    sparkline_misto: list[float] = []
    fundos: list[Fundo]


class StressFundo(BaseModel):
    """Um fundo sinalizado. A régua varia por fundo, conforme ele tenha PL.

    Exatamente um dos dois sinais vem preenchido: `resgate_pct_pl` (fundo com
    PL da CVM) ou `severidade` (fundo sem PL, comparado ao próprio histórico).
    """
    nome: str
    gestora: str
    duration: Optional[float] = None
    cotizacao_resgate: Optional[int] = None
    pl: Optional[float] = None
    resgate: float                          # R$ na janela (negativo)
    resgate_pct_pl: Optional[float] = None  # só quando há PL
    # Proxy usado quando não há PL: resgate vs. movimento típico do fundo.
    movimento_tipico: Optional[float] = None
    severidade: Optional[float] = None      # |resgate| / movimento_tipico


class MoverGestora(BaseModel):
    nome: str
    duration_media: Optional[float] = None
    cotizacao_media: Optional[int] = None
    fluxo: float = 0.0
    var_pl_pct: Optional[float] = None
