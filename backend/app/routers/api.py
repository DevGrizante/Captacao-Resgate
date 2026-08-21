"""Endpoints da API de captação/resgate."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from app.models.schemas import (
    DashboardResponse,
    DossieResponse,
    EmissorPapelBancarioDetalhe,
    EmissorPapelBancarioNaLista,
    FonteInfo,
    FundoPapelBancario,
    FundoPapelBancarioDetalhe,
    MoverGestora,
    PressaoResposta,
    StressFundo,
    TesourariaDossie,
    TesourariaResumo,
)
from app.services.pipeline import pipeline

router = APIRouter(prefix="/api", tags=["captacao"])


@router.get("/dashboard", response_model=DashboardResponse)
def get_dashboard(
    janela: str = Query("semanal"),
    indexador: str = Query("todos"),
    abertos: bool = Query(False)
):
    """Payload principal: KPIs, buckets, série temporal e ranking de gestoras."""
    return pipeline.dashboard(janela, indexador, abertos)


@router.get("/dossie/{gestora}", response_model=DossieResponse)
def get_dossie(
    gestora: str,
    janela: str = Query("semanal"),
    indexador: str = Query("todos"),
    abertos: bool = Query(False)
):
    """Painel lateral de uma gestora: resumo, mix, métricas, spark e fundos."""
    resp = pipeline.dossie(gestora, janela, indexador, abertos)
    if resp is None:
        raise HTTPException(status_code=404, detail=f"Gestora '{gestora}' não encontrada")
    return resp


@router.get("/movers", response_model=list[MoverGestora])
def get_movers(
    direcao: str = Query("pos", pattern="^(pos|neg)$"),
    limite: int = Query(6, ge=1, le=50),
    janela: str = Query("semanal"),
    indexador: str = Query("todos"),
    abertos: bool = Query(False)
):
    """Gestoras com maior variação % de PL (ganhando/perdendo)."""
    return pipeline.movers(direcao, limite, janela, indexador, abertos)


@router.get("/stress", response_model=list[StressFundo])
def get_stress(
    limite: int = Query(50, ge=1, le=200),
    janela: str = Query("semanal"),
    indexador: str = Query("todos"),
    abertos: bool = Query(False)
):
    """Fundos com resgate acima do limiar de estresse na semana."""
    return pipeline.stress(limite, janela, indexador, abertos)


@router.get("/tesourarias", response_model=list[TesourariaResumo])
def get_tesourarias(limite: int = Query(2000, ge=1, le=5000)):
    """Tesourarias emissoras ordenadas pelo estoque que os fundos carregam.

    Preço (% do CDI e spread) e prazo vêm ponderados por valor; `valor_venc_12m`
    é a agenda de rolagem. Tudo do BLC_5 do CDA, na data de `carteira_data`.
    """
    return pipeline.tesourarias(limite)


@router.get("/tesourarias/{raiz}", response_model=TesourariaDossie)
def get_tesouraria(raiz: str, limite: int = Query(40, ge=1, le=200)):
    """Dossiê de uma tesouraria: quem compra, o que vence e quem ainda não compra.

    `raiz` é a raiz do CNPJ do emissor (8 dígitos) — é a chave estável, porque
    o nome vem como texto livre e a mesma casa aparece com dezenas de grafias.
    """
    resp = pipeline.tesouraria(raiz, limite)
    if resp is None:
        raise HTTPException(
            status_code=404,
            detail=f"Tesouraria '{raiz}' não encontrada no universo desta carga.",
        )
    return resp


@router.get("/carteira-bancaria", response_model=list[FundoPapelBancario])
def get_carteira_bancaria(
    limite: int = Query(5000, ge=1, le=20000),
    busca: str = Query("", max_length=120),
):
    """Gestoras que carregam papel bancário (LF, CDB, DPGE), da maior à menor.

    A visão é da GESTORA: quem decide alocação é a casa, e a posição que
    interessa à mesa é a somada entre os fundos dela, não a fatia de cada
    veículo.

    O teto é alto de propósito. A tela soma os KPIs (estoque, papéis, taxa
    média, o que vence em 12 meses) sobre a lista que recebe, e um teto baixo
    faria esses totais descreverem só o topo do ranking enquanto se apresentam
    como o universo — um erro silencioso, que é o pior tipo. Hoje são ~2,2 mil
    fundos e o payload completo fica em ~790 KB.
    """
    return pipeline.carteira_bancaria(limite, busca)


@router.get("/carteira-bancaria/{gestora}", response_model=FundoPapelBancarioDetalhe)
def get_carteira_bancaria_gestora(gestora: str):
    """Os papéis de uma gestora, consolidados por emissor + tipo + mês.

    Cada linha é um bloco: mesmo emissor, mesmo tipo e mesmo mês de vencimento
    somam o volume, com a taxa média ponderada. Formas de taxa diferentes
    (spread sobre CDI vs. percentual do DI) ficam em linhas separadas.
    """
    resp = pipeline.carteira_bancaria_gestora(gestora)
    if resp is None:
        raise HTTPException(
            status_code=404,
            detail=f"Gestora '{gestora}' não tem papel bancário nesta carga.",
        )
    return resp


@router.get("/papel-por-emissor", response_model=list[EmissorPapelBancarioNaLista])
def get_papel_por_emissor(
    limite: int = Query(1000, ge=1, le=5000),
    busca: str = Query("", max_length=120),
):
    """A carteira bancária lida pela ponta do EMISSOR: quem carrega o papel dele.

    A visão inversa de `/carteira-bancaria`, sobre exatamente a mesma matéria-
    prima — a tela alterna entre as duas sem recarregar. Aqui a linha é o
    emissor: quanto do papel dele está nos fundos, em quantas casas, a que
    preço, com que agenda de vencimento.

    Não substitui `/tesourarias`: lá o recorte é o par fundo × tesouraria já
    resumido em médias e faixas de prazo. Aqui o eixo é o mês de vencimento, e
    o detalhe desce até o bloco que a mesa negocia.

    O caminho não é `/carteira-bancaria/emissores` de propósito: essa rota
    colidiria com `/carteira-bancaria/{gestora}`, e a ordem de declaração
    passaria a ser o que separa um payload do outro — frágil demais para uma
    diferença invisível na URL.
    """
    return pipeline.papel_por_emissor(limite, busca)


@router.get("/papel-por-emissor/{raiz}", response_model=EmissorPapelBancarioDetalhe)
def get_papel_por_emissor_detalhe(raiz: str):
    """Quem tem o papel deste emissor em carteira, por gestora + tipo + mês.

    `raiz` é a raiz do CNPJ do emissor (8 dígitos) — a mesma chave da tela de
    tesourarias, porque o nome vem como texto livre e a mesma casa aparece com
    dezenas de grafias.
    """
    resp = pipeline.papel_por_emissor_detalhe(raiz)
    if resp is None:
        raise HTTPException(
            status_code=404,
            detail=f"Emissor '{raiz}' não tem papel em carteira nesta carga.",
        )
    return resp


@router.get("/pressao", response_model=PressaoResposta)
def get_pressao(
    janela: str = Query("semanal"),
    limite: int = Query(500, ge=1, le=2000),
    direcao: str = Query("todas", pattern="^(todas|comprador|vendedor|neutro)$"),
):
    """Pressão de compra/venda por gestora, cruzada com a agenda de vencimento.

    A leitura central da mesa:

        captação líquida  -> a casa vai COMPRAR papel
        resgate líquido   -> vai VENDER
        papel vencendo    -> precisa ROLAR, capte ela ou não

    Cada gestora vem com o estoque e a agenda (3, 6 e 12 meses) quebrados por
    eixo — LF, CDB, IPCA, CDI, pré —, mais a frase que cruza fluxo e agenda.

    A ordenação é pelo que vence em 3 meses, e não pelo tamanho da casa: a tela
    mostra onde a pressão está prestes a aparecer, e a maior gestora do mercado
    sem nada vencendo não é notícia.
    """
    return pipeline.pressao(janela, limite, direcao)


@router.get("/fonte", response_model=FonteInfo)
def get_fonte():
    """De onde vieram os dados em cache e o que essa fonte não entrega."""
    return pipeline.fonte_info()


@router.post("/admin/refresh")
def post_refresh():
    """Recarrega a fonte e recalcula o pipeline.

    Na fonte "vinculado" isso também re-varre a pasta do Outlook: se chegou um
    e-mail novo com anexo, é ele que passa a valer. É o botão para apertar
    depois que o e-mail da manhã cai na caixa.
    """
    pipeline.refresh()
    info = pipeline.fonte_info()
    return {
        "status": "ok",
        "message": "Pipeline recalculado.",
        "fonte": info.fonte,
        "arquivo": info.arquivo,
        "recebido_em": info.recebido_em,
    }


@router.post("/admin/coletar-email")
def post_coletar_email():
    """Vai à caixa de e-mail agora, sem esperar o próximo ciclo do agendador.

    É o botão para quando o relatório chega fora de hora. Com `EMAIL_MODO=off`
    responde 503 e diz o que configurar — nunca finge que coletou.
    """
    from app.services import email_inbox

    if not email_inbox.habilitado():
        raise HTTPException(
            status_code=503,
            detail="Ingestão por e-mail desligada. Defina EMAIL_MODO=graph ou "
                   "EMAIL_MODO=imap no .env do servidor.",
        )

    recebido = email_inbox.sincronizar()
    if recebido is None:
        return {"status": "sem_novidade",
                "message": "Nenhum e-mail novo com anexo que casasse."}

    if not recebido.ja_existia:
        pipeline.refresh()

    return {
        "status": "ja_tinha" if recebido.ja_existia else "novo",
        "arquivo": recebido.nome,
        "sha256": recebido.sha256,
        "bytes": recebido.bytes_,
        "recalculado": not recebido.ja_existia,
    }
