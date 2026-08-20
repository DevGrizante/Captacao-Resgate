"""
Configuração central. Lê variáveis de ambiente com defaults sensatos.

Copie `.env.example` para `.env` e ajuste conforme necessário. Nada aqui
guarda segredos em texto — o token do Quantum vem do ambiente.
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# Raiz do projeto (…/Captacao_Resgate)
BASE_DIR = Path(__file__).resolve().parents[2]

# Carrega backend/.env antes de qualquer os.getenv abaixo. Variáveis já
# definidas no ambiente têm precedência sobre o arquivo.
load_dotenv(BASE_DIR / "backend" / ".env")
DATA_DIR = BASE_DIR / "data"
CACHE_DIR = DATA_DIR / "cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)
# Onde os anexos vinculado_*.xlsx baixados do e-mail ficam guardados.
INBOX_DIR = DATA_DIR / "inbox"
INBOX_DIR.mkdir(parents=True, exist_ok=True)


class Settings:
    # --- Fonte de dados ---
    # "vinculado" -> planilha vinculado_*.xlsx que chega por e-mail (padrão hoje)
    # "cvm"       -> baixa e processa dados abertos da CVM (real)
    # "mock"      -> amostra fixa embutida (roda offline, valores sintéticos)
    DATA_SOURCE: str = os.getenv("DATA_SOURCE", "vinculado")

    # --- Ingestão do e-mail (fonte "vinculado") ---
    # Lê o Outlook local via COM. Com false, usa só o que já está em data/inbox.
    OUTLOOK_ENABLED: bool = os.getenv("OUTLOOK_ENABLED", "true").lower() == "true"
    OUTLOOK_PASTA: str = os.getenv("OUTLOOK_PASTA", "Quantum")
    # VAZIO = qualquer remetente. O relatório passou a ser encaminhado por mais
    # de uma pessoa, e travar num endereço fazia o app ficar com o arquivo da
    # véspera sempre que outra pessoa mandava o do dia. O que identifica o
    # e-mail é o assunto; preencha isto só para voltar a restringir.
    OUTLOOK_REMETENTE: str = os.getenv("OUTLOOK_REMETENTE", "")
    # Casa por SUBSTRING, sem acento e sem caixa: "Captação e resgate" acha
    # tanto "FW: Captação e resgate" quanto o original sem o prefixo de
    # encaminhamento. Exigir o "FW:" deixaria de fora o e-mail direto.
    OUTLOOK_ASSUNTO: str = os.getenv("OUTLOOK_ASSUNTO", "Captação e resgate")
    # Quantos e-mails recentes varrer antes de desistir.
    OUTLOOK_MAX_ITENS: int = int(os.getenv("OUTLOOK_MAX_ITENS", "60"))

    # --- CVM ---
    CVM_BASE_URL: str = os.getenv(
        "CVM_BASE_URL",
        "https://dados.cvm.gov.br/dados/FI/DOC",
    )
    # Quantos meses de informe diário puxar para montar as janelas
    CVM_MESES_INFORME: int = int(os.getenv("CVM_MESES_INFORME", "7"))
    CVM_TIMEOUT_S: int = int(os.getenv("CVM_TIMEOUT_S", "180"))

    # Enriquecer a planilha do e-mail com PL e cadastro da CVM, casando pelo
    # CNPJ do fundo. Depende da coluna CNPJ existir no anexo.
    CVM_ENRIQUECER: bool = os.getenv("CVM_ENRIQUECER", "true").lower() == "true"
    # O cadastro muda pouco; um dia de cache evita rebaixar 7 MB a cada refresh.
    CVM_CADASTRO_TTL_HORAS: int = int(os.getenv("CVM_CADASTRO_TTL_HORAS", "24"))
    # Piso de credibilidade do PL. O registro tem valores de placeholder (205
    # classes com PL <= 0, algumas com PL = R$ 1) que, divididos pelo fluxo,
    # produzem percentuais absurdos. Abaixo deste piso tratamos como "sem PL".
    CVM_PL_MINIMO: float = float(os.getenv("CVM_PL_MINIMO", "100000"))
    # Informe diário: fonte primária de PL, e também de rentabilidade,
    # volatilidade e nº de cotistas. 7 meses dá série suficiente para uma
    # janela de 6 meses de retorno.
    CVM_INFORME_MESES: int = int(os.getenv("CVM_INFORME_MESES", "7"))
    CVM_INFORME_TTL_HORAS: int = int(os.getenv("CVM_INFORME_TTL_HORAS", "12"))
    # Série mais curta que isso não descreve retorno — o campo fica vazio.
    CVM_RENTAB_DIAS_MIN: int = int(os.getenv("CVM_RENTAB_DIAS_MIN", "150"))
    # Janela da variação de PL ("Top movers"): PL de hoje contra o de N dias.
    CVM_PL_VAR_DIAS: int = int(os.getenv("CVM_PL_VAR_DIAS", "30"))
    # PL mínimo (R$) para uma gestora aparecer no painel de "Top movers".
    # Sem piso, quem sai do zero marca +700% e domina a lista.
    MOVERS_PL_MINIMO: float = float(os.getenv("MOVERS_PL_MINIMO", "100000000"))

    # --- EXTRATO e PERFIL MENSAL ---
    # O EXTRATO é anual: unir vários anos leva a cobertura de 13,7% para 68,9%.
    CVM_EXTRATO_ANOS: int = int(os.getenv("CVM_EXTRATO_ANOS", "5"))
    # O PERFIL é mensal mas nem todo fundo declara todo mês: unir 6 meses leva
    # a cobertura de 7,8% para 82,8%.
    CVM_PERFIL_MESES: int = int(os.getenv("CVM_PERFIL_MESES", "6"))
    CVM_DOCUMENTOS_TTL_HORAS: int = int(os.getenv("CVM_DOCUMENTOS_TTL_HORAS", "24"))
    # Teto de sanidade da taxa de administração (% a.a.). O campo chega a
    # 40.000 em alguns fundos — é o valor em reais preenchido no lugar do
    # percentual, e sem o corte ele destrói a média da gestora inteira.
    CVM_TAXA_ADM_TETO: float = float(os.getenv("CVM_TAXA_ADM_TETO", "10"))

    # --- Composição da carteira / bucket (connectors/cvm_carteira.py) ---
    # Lê o CDA da CVM com atraso de propósito: no mês corrente 45,9% do PL do
    # nosso universo está sob sigilo, e o que sobra é amostra enviesada. Com 4
    # meses o sigilo praticamente zera e a carteira visível dobra.
    CDA_DEFASAGEM_MESES: int = int(os.getenv("CDA_DEFASAGEM_MESES", "4"))
    CDA_TTL_HORAS: int = int(os.getenv("CDA_TTL_HORAS", "24"))
    # Guardas por fundo: abaixo delas o fundo sai sem bucket em vez de entrar
    # com uma composição lida pela metade.
    CDA_IDX_MINIMO_PCT: float = float(os.getenv("CDA_IDX_MINIMO_PCT", "80"))
    CDA_SIGILO_MAXIMO_PCT: float = float(os.getenv("CDA_SIGILO_MAXIMO_PCT", "20"))
    CDA_CREDITO_MINIMO_PCT: float = float(os.getenv("CDA_CREDITO_MINIMO_PCT", "10"))
    # --- Papel bancário: papel já vencido não é negócio ---
    # O CDA é uma foto com 4 meses de defasagem, então ele lista papel que
    # estava vivo na data-base e já venceu quando a tela é aberta. Medido em
    # 18/08/2026 sobre o CDA de 2026-04: R$ 103,1 bi em 8.775 posições, 12,2%
    # do estoque, com vencimento entre maio e julho. Para uma mesa que negocia
    # rolagem isso é ruído puro — não há o que rolar num papel que já liquidou.
    # O mês corrente FICA: ele ainda tem vencimentos por acontecer.
    CDA_EXCLUIR_VENCIDOS: bool = (
        os.getenv("CDA_EXCLUIR_VENCIDOS", "true").lower() == "true"
    )

    # Registro de debêntures do SND: dá o indexador de 99,4% do valor do BLC_4.
    SND_TTL_HORAS: int = int(os.getenv("SND_TTL_HORAS", "168"))

    # --- Perfil de indexador observado (services/perfil_indexador.py) ---
    # NÃO é o bucket: mede exposição do cotista, não composição da carteira.
    PERFIL_INDEXADOR_INFERIR: bool = (
        os.getenv("PERFIL_INDEXADOR_INFERIR", "true").lower() == "true"
    )
    # Abaixo da zona baixa = pós-fixado; acima da alta = inflação; no meio,
    # não afirmamos (lá o acerto cai de 87% para 77%).
    PERFIL_VOL_ZONA_BAIXA: float = float(os.getenv("PERFIL_VOL_ZONA_BAIXA", "2.5"))
    PERFIL_VOL_ZONA_ALTA: float = float(os.getenv("PERFIL_VOL_ZONA_ALTA", "5.0"))

    # --- Universo: crédito privado ---
    # "todos" | "sinal" (qualquer indício) | "estrito" (só o % declarado à CVM)
    CREDITO_PRIVADO_MODO: str = os.getenv("CREDITO_PRIVADO_MODO", "sinal")
    # % mínimo do PL em crédito privado no modo estrito / para o sinal do perfil
    CREDITO_PRIVADO_LIMIAR: float = float(os.getenv("CREDITO_PRIVADO_LIMIAR", "5"))

    # --- Quantum Axis (stub por enquanto) ---
    QUANTUM_ENABLED: bool = os.getenv("QUANTUM_ENABLED", "false").lower() == "true"
    QUANTUM_BASE_URL: str = os.getenv(
        "QUANTUM_BASE_URL", "https://api.quantumaxis.com.br"
    )
    QUANTUM_TOKEN: str = os.getenv("QUANTUM_TOKEN", "")

    # --- Classificação de fundos (LF vs. fundo de crédito) ---
    # Corte único da classificação, em fração da carteira (0..1). Governa duas
    # decisões em sequência: LF domina a carteira? e, no que sobra, o indexador
    # é majoritário? É EDITÁVEL EM TEMPO DE EXECUÇÃO pelo painel de controle
    # (ver services/parametros.py) — o valor daqui é só o ponto de partida.
    THRESHOLD_MAJORITARIO: float = float(os.getenv("THRESHOLD_MAJORITARIO", "0.2"))

    # --- Hedge em DAP (services/classifier.py + connectors/cvm_carteira.py) ---
    # O fundo de debênture incentivada compra papel em B+spread e vende cupom
    # de IPCA no futuro de DAP, ficando só com o spread de crédito. Este é o
    # piso de cobertura para chamar isso de hedge: nocional em DAP sobre o R$
    # da carteira indexada a IPCA. Medido no CDA de 2026-04, entre os fundos
    # com posição em DAP a cobertura tem mediana de 0,75 e p10 de 0,20 — abaixo
    # disso a posição é residual e não trava a carteira.
    HEDGE_DAP_MINIMO: float = float(os.getenv("HEDGE_DAP_MINIMO", "0.2"))
    # Face do contrato futuro de DAP na B3. O CDA informa a quantidade de
    # contratos e o ajuste a mercado, nunca o nocional — ele é reconstruído.
    DAP_NOCIONAL_CONTRATO: float = float(os.getenv("DAP_NOCIONAL_CONTRATO", "100000"))
    # O nome ("Incentivada/o" ou "Infra") mais a carteira IPCA+ bastam para o
    # bucket Incentivada. Com true, exige-se também a perna de hedge em DAP,
    # e quem tem o mandato mas carrega juro real na cota cai em Misto —
    # eram 669 fundos, decisão de negócio de 18/08/2026 foi não separar.
    # A cobertura de DAP continua medida e visível; ela só deixou de
    # decidir o bucket. Ver a nota em classifier.py.
    INCENTIVADA_EXIGE_HEDGE_DAP: bool = (
        os.getenv("INCENTIVADA_EXIGE_HEDGE_DAP", "false").lower() == "true"
    )
    # Limiar de resgate semanal (fração do PL) para virar sinal de estresse.
    # Só se aplica quando há PL — hoje, só na fonte "cvm"/"mock".
    THRESHOLD_STRESS: float = float(os.getenv("THRESHOLD_STRESS", "0.05"))

    # --- Estresse sem PL (fonte "vinculado") ---
    # Sem PL não dá para medir "resgatou X% do fundo". O proxy é comparar o
    # resgate da semana com o tamanho típico de movimento do próprio fundo nas
    # últimas semanas: um fundo que sempre movimenta R$ 1 mi e resgata R$ 20 mi
    # está estressado; um que sempre movimenta R$ 500 mi, não.
    STRESS_SEMANAS_BASE: int = int(os.getenv("STRESS_SEMANAS_BASE", "13"))
    # Quantas vezes o movimento típico o resgate precisa ser.
    STRESS_MULTIPLO: float = float(os.getenv("STRESS_MULTIPLO", "2.0"))
    # Piso absoluto (R$) — evita encher a lista com fundo minúsculo.
    STRESS_MINIMO_ABS: float = float(os.getenv("STRESS_MINIMO_ABS", "10000000"))

    # --- Cache ---
    CACHE_TTL_HORAS: int = int(os.getenv("CACHE_TTL_HORAS", "12"))

    # --- CORS ---
    # Origens autorizadas a chamar a API (o front). Em produção, restrinja.
    CORS_ORIGINS: list[str] = os.getenv(
        "CORS_ORIGINS", "http://localhost:5500,http://127.0.0.1:5500,http://localhost:8080"
    ).split(",")

    # --- Senha do painel ---
    # Uma senha só, igual para todo mundo. É o mínimo para publicar o painel
    # num endereço alcançável: sem ela, quem descobrir a URL vê fluxo por
    # gestora e pode alterar o corte de classificação para todos.
    #
    # VAZIO = SEM SENHA. É o padrão de propósito: quem roda em 127.0.0.1 na
    # própria máquina não deveria ter de digitar senha para ver o próprio
    # painel. Em servidor, preencher é obrigatório — o app avisa no log ao
    # subir se estiver vazio.
    PAINEL_SENHA: str = os.getenv("PAINEL_SENHA", "")
    # Assina o cookie de sessão. Sem valor, é sorteado a cada partida e todo
    # mundo cai no reinício. Nunca ter um padrão fixo no código: seria a mesma
    # chave em toda instalação, e qualquer um forjaria um cookie válido.
    #   python -c "import secrets; print(secrets.token_urlsafe(32))"
    PAINEL_SEGREDO: str = os.getenv("PAINEL_SEGREDO", "")
    # Quanto tempo a sessão dura. 12 h cobre o dia inteiro de mesa sem pedir
    # senha de novo no meio de uma negociação.
    PAINEL_SESSAO_HORAS: int = int(os.getenv("PAINEL_SESSAO_HORAS", "12"))
    # Marca o cookie como `Secure` (só trafega em HTTPS). Deixe true no
    # servidor; em 127.0.0.1 precisa ser false, senão o navegador descarta o
    # cookie e o login entra em laço infinito.
    PAINEL_COOKIE_SEGURO: bool = (
        os.getenv("PAINEL_COOKIE_SEGURO", "false").lower() == "true"
    )

    # --- Ingestão pela rede (POST /api/inbox) ---
    # Permite que a planilha do e-mail chegue por HTTP em vez de pelo Outlook
    # local. É o que torna o app rodável em servidor: no Linux não existe COM,
    # então alguém precisa EMPURRAR o arquivo para dentro.
    #
    # SEM TOKEN A INGESTÃO FICA DESLIGADA, e não aberta. Um endpoint que
    # aceita upload sem autenticação é pior que endpoint nenhum: qualquer um
    # que alcance a URL passa a decidir que números a mesa vê. O valor vazio é
    # o padrão justamente para que subir sem configurar não exponha nada.
    INGESTAO_TOKEN: str = os.getenv("INGESTAO_TOKEN", "")
    # Teto do corpo aceito. A planilha tem ~1,2 MB; 25 MB dá folga de sobra e
    # ainda impede que um corpo gigante consuma a memória do servidor.
    INGESTAO_TAMANHO_MAXIMO_MB: int = int(os.getenv("INGESTAO_TAMANHO_MAXIMO_MB", "25"))
    # Quantas planilhas manter na inbox. Uma por dia útil ≈ 1,2 MB/dia, o que
    # em um ano encheria 300 MB de um disco de VPS sem que ninguém percebesse.
    # 0 = guardar todas.
    INGESTAO_MANTER_ARQUIVOS: int = int(os.getenv("INGESTAO_MANTER_ARQUIVOS", "60"))


settings = Settings()
