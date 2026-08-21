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
    """Classificação do fundo. Ver services/classifier.py para a regra.

    INCENTIVADA e TRADICIONAL substituíram os antigos IPCA e CDI: o eixo
    deixou de ser só o indexador da carteira e passou a combinar o nome do
    fundo com o comportamento observado dos ativos (compra em B + spread com
    hedge em DAP, ou carteira atrelada a CDI+).
    """
    INCENTIVADA = "incentivada"
    TRADICIONAL = "tradicional"
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
    # TODO fundo tem bucket desde 20/08/2026 — nenhum fica nulo. O que
    # distingue um bucket medido de um inferido é `bucket_origem`, abaixo.
    bucket: Optional[Bucket] = None
    # "carteira" = medido na composição do CDA. "inferido" = deduzido do nome
    # e do perfil da cota, porque não havia carteira legível.
    #
    # O campo existe para que juntar todo mundo num bucket não apague a
    # diferença entre "olhamos" e "não olhamos". Uma tela que precise só do
    # universo medido filtra por "carteira" e recupera exatamente o que o
    # painel mostrava antes.
    bucket_origem: Optional[str] = None
    # Por que este bucket, em uma frase. É o que explica na tela um fundo com
    # nome de incentivada que caiu em Misto — sem isso a classificação parece
    # arbitrária justamente nos casos em que ela é mais informativa.
    bucket_motivo: Optional[str] = None
    # O nome do fundo traz "Incentivada"/"Incentivado"? Primeira metade da
    # dupla verificação; viaja separado para a tela poder confrontá-lo com a
    # carteira quando os dois discordam.
    nome_incentivado: Optional[bool] = None
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
    # --- perna de hedge: futuro de DAP (cupom de DI x IPCA) ---
    # `dap_cobertura` é o nocional em DAP sobre o R$ da carteira IPCA+ — 1.0
    # significa perna de inflação inteiramente travada. É a MEDIÇÃO; o corte
    # que a transforma em "tem hedge" é o `HEDGE_DAP_MINIMO`, aplicado na
    # classificação para acompanhar o painel de controle.
    dap_nocional: Optional[float] = None
    dap_cobertura: Optional[float] = None
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
    # Mix por classificação (fração 0..1). None = sem composição para nenhum
    # fundo. `mix_incentivada`/`mix_tradicional` substituíram mix_ipca/mix_cdi.
    mix_incentivada: Optional[float] = None
    mix_tradicional: Optional[float] = None
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


class PressaoGestora(BaseModel):
    """Uma gestora na tela de pressão: direção, perfil e agenda de vencimento.

    Os dicionários `estoque_por_eixo` e `agenda` vêm com chave livre de
    propósito: os eixos (lf/cdb/ipca/cdi/pre/outro) são definidos em
    `services/pressao_gestora.EIXOS`, e declará-los campo a campo aqui obrigaria
    a mexer em dois arquivos toda vez que um eixo entrasse ou saísse.
    """
    gestora: str
    # comprador | vendedor | neutro — o SINAL da pressão.
    direcao: str
    direcao_rotulo: str
    direcao_motivo: str
    # bancario | debenture_ipca | debenture_cdi | misto | sem_carteira
    perfil: str
    perfil_rotulo: str
    perfil_motivo: str
    fluxo: float
    carteira_datada: float
    carteira_credito: Optional[float] = None
    # Quanto do estoque de crédito tem vencimento conhecido. Abaixo de 100% há
    # CRI/CRA no meio, que o CDA não data — e a agenda subestima.
    agenda_cobertura_pct: Optional[float] = None
    estoque_por_eixo: dict[str, float] = Field(default_factory=dict)
    # {"m3": {eixo: R$}, "m6": …, "m12": …}
    agenda: dict[str, dict[str, float]] = Field(default_factory=dict)
    vence_3m: float = 0.0
    vence_6m: float = 0.0
    vence_12m: float = 0.0
    # A frase que cruza fluxo e agenda — é o que a tela existe para dizer.
    leitura: str
    carteira_data: Optional[str] = None


class PressaoResposta(BaseModel):
    """A tela inteira: os totais do mercado no topo, as gestoras embaixo."""
    totais: dict = Field(default_factory=dict)
    gestoras: list[PressaoGestora] = []


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
    incentivada: Optional[float] = None
    tradicional: Optional[float] = None
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
    sparkline_incentivada: list[float] = []
    sparkline_tradicional: list[float] = []
    sparkline_lf: list[float] = []
    sparkline_misto: list[float] = []
    fundos: list[Fundo]
    # De quais tesourarias este asset compra, em ordem de tamanho. É o lado
    # inverso da tela de tesourarias: aqui a pergunta é "com quem esta casa já
    # opera", que é o que a mesa precisa antes de ligar oferecendo um emissor.
    tesourarias: list[TesourariaNaCarteira] = []
    papel_bancario: Optional[float] = None   # R$ total em LF/CDB/DPGE


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


# ---------- mesa Tesouraria x Asset ----------
class TesourariaResumo(BaseModel):
    """Uma tesouraria emissora, vista pelo papel dela que está nos fundos.

    Todos os valores vêm do BLC_5 do CDA, na data de `carteira_data`. São
    posições declaradas, não emissões: descrevem o estoque que o mercado
    carrega, e é isso que interessa para saber com quem falar.
    """
    raiz: str                                # raiz do CNPJ — a chave estável
    nome: str
    valor: float                             # R$ do papel deste emissor nos fundos
    share_pct: Optional[float] = None        # fatia do papel bancário do universo
    fundos: int
    gestoras: int                            # quantos assets distintos carregam
    # Preço ponderado por valor. Os dois convivem porque o mercado cota das duas
    # formas; `None` quando o papel do emissor não é indexado a CDI.
    pct_cdi: Optional[float] = None          # 103.5 = "103,5% do CDI"
    spread: Optional[float] = None           # 0.9   = "CDI + 0,9% a.a."
    prazo_dias: Optional[float] = None
    # O que vence em até 12 meses a partir da data-base da carteira. É a
    # agenda de rolagem: papel vencendo é conversa marcada.
    valor_venc_12m: float = 0.0
    pct_venc_12m: Optional[float] = None
    # Quanto do estoque está na asset do próprio grupo (`EMISSOR_LIGADO` do
    # CDA). Não é negócio disputável — a Bradesco Asset é a maior carregadora
    # de papel do Bradesco, e sem esta marca ela lideraria a lista de clientes
    # como se fosse uma conquista comercial.
    pct_ligado: Optional[float] = None
    carteira_data: Optional[str] = None


class CompradorTesouraria(BaseModel):
    """Um asset que carrega papel de uma tesouraria — a linha da lista de contatos."""
    gestora: str
    valor: float
    fundos: int
    pct_cdi: Optional[float] = None
    spread: Optional[float] = None
    prazo_dias: Optional[float] = None
    valor_venc_12m: float = 0.0
    # Contexto de fluxo: um asset captando tem dinheiro para alocar agora.
    # Vem do mesmo pipeline do dashboard, então é o fluxo real da janela.
    pl: Optional[float] = None
    fluxo_semanal: float = 0.0
    fluxo_mensal: float = 0.0
    # Concentração: quanto do papel bancário deste asset é desta tesouraria.
    # Alta demais é limite de crédito perto do teto; baixa é espaço para crescer.
    pct_do_bancario: Optional[float] = None
    # Posição intragrupo (asset do próprio banco emissor). A linha continua na
    # lista — ela é parte do estoque — mas marcada, porque não é disputável.
    ligado: bool = False


class OportunidadeTesouraria(BaseModel):
    """Um asset que compra papel bancário mas NÃO desta tesouraria.

    É a lista de prospecção: já tem apetite comprovado pela classe de ativo e
    limite operacional montado, só não comprou deste emissor. Ordenada pelo
    tamanho do que ele já carrega de outros bancos — é a melhor proxy de
    quanto ele poderia carregar deste.
    """
    gestora: str
    valor_bancario: float                    # R$ que carrega de OUTRAS tesourarias
    emissores: int                           # de quantas tesourarias diferentes
    fundos: int
    pl: Optional[float] = None
    fluxo_semanal: float = 0.0
    fluxo_mensal: float = 0.0
    spread_medio: Optional[float] = None     # a que preço ele compra dos outros


class FaixaPrazo(BaseModel):
    """Uma faixa da curva de vencimentos."""
    rotulo: str                              # "até 3m", "3-6m", …
    valor: float
    pct: Optional[float] = None


class TesourariaDossie(BaseModel):
    """Tudo sobre uma tesouraria: quem compra, a que preço, o que vence, quem falta."""
    resumo: TesourariaResumo
    compradores: list[CompradorTesouraria]
    oportunidades: list[OportunidadeTesouraria]
    curva_vencimento: list[FaixaPrazo]


class TesourariaNaCarteira(BaseModel):
    """Uma tesouraria dentro da carteira de um asset — o lado inverso da tela."""
    raiz: str
    nome: str
    valor: float
    pct_do_bancario: Optional[float] = None
    spread: Optional[float] = None
    prazo_dias: Optional[float] = None
    valor_venc_12m: float = 0.0


# ---------- carteira de papel bancário, posição a posição ----------
class PosicaoBancaria(BaseModel):
    """Uma linha da carteira: emissor + tipo + mês de vencimento + taxa.

    CONSOLIDADA, não posição individual. Papéis do mesmo emissor, mesmo tipo e
    mesmo mês de vencimento viram uma linha só, com o volume somado — é assim
    que a mesa lê a carteira, porque o que se negocia é "o bloco do Safra que
    vence em fev/27", não cada registro solto do CDA.

    A taxa entra na consolidação: papéis do mesmo bloco mas com taxas em formas
    diferentes NÃO se juntam (ver `forma`), senão a média misturaria "CDI+1,35"
    com "102% do DI" e produziria um número que não existe.
    """
    raiz_emissor: str
    emissor: str
    instrumento: str                    # lf | cdb | dpge
    mes_venc: str                       # "2027-02" — o eixo da agenda
    indexador: str                      # cdi | selic | ipca | pre | outro
    # UM número, e o que ele significa. A separação existe porque o mercado
    # cota papel bancário de duas formas e o CDA guarda as duas:
    #   cdi_spread -> "CDI + 1,35%"     (taxa abaixo de 90)
    #   pct_di     -> "102,0% do DI"    (taxa acima de 90)
    #   ipca       -> "IPCA + 5,37%"
    #   pre        -> "13,50% a.a."
    taxa: Optional[float] = None
    forma: Optional[str] = None
    valor: float
    papeis: int = 1                     # quantos registros foram somados aqui
    quantidade: Optional[float] = None
    # Quanto do bloco é posição intragrupo (`EMISSOR_LIGADO` do CDA), em % do
    # valor. `ligado` é a leitura binária disso e só liga quando a maior parte
    # do bloco é intragrupo — marcar por "existe pelo menos uma" pintava R$ 1,39
    # bi de papel de terceiro como se fosse da casa por causa de R$ 3,3 mi.
    pct_ligado: Optional[float] = None
    ligado: bool = False


class VencimentoMes(BaseModel):
    """Quanto vence num mês. O eixo da agenda de rolagem."""
    mes: str                            # "2027-02"
    valor: float
    posicoes: int


class FundoPapelBancario(BaseModel):
    """Uma GESTORA, resumida pelo papel bancário que a casa carrega.

    A unidade é a casa, não o veículo: quem decide alocação é a gestora, e uma
    asset com 40 fundos carregando LF do mesmo banco é uma conversa só.
    """
    gestora: str
    fundos: int                         # quantos fundos da casa carregam papel
    valor: float                        # R$ em LF + CDB + DPGE, somado
    posicoes: int
    emissores: int
    # PL somado dos fundos da casa que carregam papel bancário — contado uma
    # vez por CNPJ, para a subclasse não repetir o mesmo patrimônio.
    pl: Optional[float] = None
    pct_pl: Optional[float] = None      # quanto desse PL é papel bancário
    # Taxa média ponderada por valor, só do papel pós-fixado em CDI/Selic —
    # misturar com IPCA e pré daria um número sem significado.
    spread_cdi: Optional[float] = None
    prazo_dias: Optional[float] = None
    # Duas janelas de rolagem. A de 3 meses é a agenda da mesa — o que
    # precisa de conversa agora; a de 12 meses é o horizonte do ano.
    valor_venc_3m: float = 0.0
    valor_venc_12m: float = 0.0
    pct_lf: Optional[float] = None
    pct_cdb: Optional[float] = None
    pct_dpge: Optional[float] = None
    carteira_data: Optional[str] = None


class FundoPapelBancarioDetalhe(BaseModel):
    """O que aparece ao clicar numa gestora: os papéis, e a agenda deles."""
    gestora: FundoPapelBancario
    posicoes: list[PosicaoBancaria]
    por_mes: list[VencimentoMes]
    # Concentração por emissor dentro desta gestora, já ordenada.
    por_emissor: list[TesourariaNaCarteira]


# ---------- a mesma carteira lida pela ponta do EMISSOR ----------
# A tela de papel bancário tem duas leituras da MESMA matéria-prima. Acima, a
# pergunta é "o que esta casa tem na carteira?". Abaixo, a inversa: "quem tem
# o meu papel, e vencendo quando?" — que é a pergunta de quem vai ligar
# oferecendo a rolagem de um bloco específico.
#
# Não é a tela de Tesourarias com outro nome: lá tudo é média (prazo médio,
# spread médio, faixas de prazo), e média não diz com quem falar em fev/27.
# Aqui o eixo é o mês de vencimento, e cada linha é uma gestora.
class EmissorPapelBancario(BaseModel):
    """Um EMISSOR, resumido pelo papel dele que está nas carteiras.

    Espelha `FundoPapelBancario` de propósito: as duas visões da tela mostram
    os mesmos KPIs e a mesma barra de mix, e campos com nomes diferentes para a
    mesma conta obrigariam a tela a ter dois caminhos para cada número.
    """
    raiz: str                           # raiz do CNPJ — a chave estável
    emissor: str
    gestoras: int                       # quantas casas carregam este papel
    fundos: int
    valor: float                        # R$ em LF + CDB + DPGE, somado
    posicoes: int
    # Fatia deste emissor no papel bancário de todo o universo. É o "tamanho
    # relativo" que o `pct_pl` da gestora não tem equivalente aqui — um emissor
    # não tem PL nesta base.
    share_pct: Optional[float] = None
    spread_cdi: Optional[float] = None
    prazo_dias: Optional[float] = None
    valor_venc_3m: float = 0.0
    valor_venc_12m: float = 0.0
    pct_lf: Optional[float] = None
    pct_cdb: Optional[float] = None
    pct_dpge: Optional[float] = None
    # Quanto do estoque está na asset do próprio grupo. Não é negócio
    # disputável, e sem esta marca o maior "cliente" de um banco costuma ser
    # ele mesmo — o que faria a lista de contatos começar errada.
    pct_ligado: Optional[float] = None
    carteira_data: Optional[str] = None


class MesDoEmissor(BaseModel):
    """Uma fatia da agenda de um emissor: o que vence naquele mês, e por quem.

    Existe para a LISTA poder ser filtrada por vencimento sem ida ao servidor.
    Filtrar a lista e deixar as colunas descrevendo o estoque inteiro seria
    mentira de tela: "quem tem papel vencendo em dez/26" com o volume total do
    emissor ao lado faz a mesa ligar com o número errado na mão. Com esta
    quebra, as colunas passam a falar do mês escolhido.

    São 2.070 pares (emissor, mês) no CDA de abr/2026 — cabe inteiro no mesmo
    payload da lista, e a troca de mês vira instantânea.
    """
    mes: str                            # "2027-02"; vazio é papel perpétuo
    valor: float
    posicoes: int
    gestoras: int                       # quantas casas têm papel vencendo aqui
    taxa: Optional[float] = None        # spread sobre CDI, ponderado, só pós-fixado
    pct_lf: Optional[float] = None
    pct_cdb: Optional[float] = None
    pct_dpge: Optional[float] = None


class EmissorPapelBancarioNaLista(EmissorPapelBancario):
    """O resumo do emissor mais a agenda dele, que só a lista precisa.

    Subclasse, e não um campo opcional no resumo, porque o mesmo objeto é
    servido dentro do detalhe — onde a agenda já vem completa em `por_mes` e
    repeti-la seria payload jogado fora. O Pydantic serializa pelo tipo
    DECLARADO do campo, então o detalhe descarta `meses` sozinho.
    """
    meses: list[MesDoEmissor] = []


class CarregadorPapel(BaseModel):
    """Uma linha do detalhe do emissor: gestora + tipo + mês de vencimento + taxa.

    O espelho de `PosicaoBancaria`. Mesma consolidação, mesma regra da forma da
    taxa — o que muda é o que fica fixo (o emissor) e o que varia (a gestora).
    """
    gestora: str
    instrumento: str                    # lf | cdb | dpge
    mes_venc: str                       # "2027-02" — o eixo da agenda
    indexador: str
    taxa: Optional[float] = None
    forma: Optional[str] = None
    valor: float
    papeis: int = 1
    fundos: int = 1                     # quantos veículos da casa entraram aqui
    quantidade: Optional[float] = None
    pct_ligado: Optional[float] = None
    ligado: bool = False


class GestoraNoEmissor(BaseModel):
    """Uma casa dentro do estoque de um emissor — o chip de concentração."""
    gestora: str
    valor: float
    pct_do_emissor: Optional[float] = None
    spread: Optional[float] = None
    valor_venc_12m: float = 0.0
    ligado: bool = False


class EmissorPapelBancarioDetalhe(BaseModel):
    """O que aparece ao clicar num emissor: quem carrega, e vencendo quando."""
    emissor: EmissorPapelBancario
    posicoes: list[CarregadorPapel]
    por_mes: list[VencimentoMes]
    # Concentração por gestora dentro deste emissor, já ordenada.
    por_gestora: list[GestoraNoEmissor]


# ---------- painel de controle (área admin) ----------
class ParametroInfo(BaseModel):
    """Um parâmetro editável, com a régua junto — o painel monta o campo daqui.

    `minimo`/`maximo`/`passo` viajam para o front de propósito: a validação
    real acontece no backend, mas repetir a faixa na tela evita que o usuário
    descubra o limite só depois de enviar.
    """
    chave: str
    rotulo: str
    descricao: str
    valor: float           # percentual, como o usuário vê
    padrao_codigo: float   # o valor de .env/código, p/ o botão "restaurar"
    minimo: float
    maximo: float
    passo: float


class ParametrosResponse(BaseModel):
    parametros: list[ParametroInfo]
    # Retrato de como o universo está classificado AGORA, para o painel poder
    # mostrar o efeito de uma mudança em vez de só confirmar que ela ocorreu.
    distribuicao: dict[str, int]
    total_fundos: int
    total_sem_classificacao: int


class ParametrosUpdate(BaseModel):
    """Corpo do PUT. Chaves são as de `ParametroInfo.chave`, valores em %."""
    valores: dict[str, float] = Field(
        ...,
        description='Ex.: {"threshold_majoritario": 20}',
    )


class ReclassificacaoResponse(BaseModel):
    """O que a rotina de reclassificação fez com a base.

    `alteracoes` é o miolo: quantos fundos saíram de cada bucket para cada
    outro. Um painel que respondesse só "ok" deixaria o usuário sem saber se
    mexer de 20% para 25% moveu 3 fundos ou 3 mil.
    """
    status: str
    mudancas: dict[str, dict[str, float]] = Field(default_factory=dict)
    total_fundos: int
    fundos_reclassificados: int
    distribuicao_antes: dict[str, int]
    distribuicao_depois: dict[str, int]
    # "lf -> misto": 42
    alteracoes: dict[str, int] = Field(default_factory=dict)
    duracao_s: float = 0.0
