"""
Conector VINCULADO — planilha `vinculado_*.xlsx` que chega por e-mail.

É a fonte primária enquanto a API do Quantum Axis não é liberada. O arquivo é
exportado do Quantum Axis e encaminhado por `ottavio.lucca@bgcg.com` todo dia
útil; `services/outlook_inbox.py` cuida de trazer sempre o mais recente.

LAYOUT DA PLANILHA (aba única, `tarefa`):

    linha 1 | Nome | Gestão | CNPJ | CNPJ Gestão | Diária | Semanal | Mensal | Semestral | Captação Janela ...
    linha 2 |                                                                            | diária
    linha 3 |                                                                            | 06/08/2026 - 12/08/2026 | 30/07/2026 - 05/08/2026 | ...
    linha 4+| ...dados...
    rodapé  | disclaimers da Quantum (linhas com Nome preenchido e Gestão vazia)

  * São 40 janelas semanais, da mais recente para a mais antiga.
  * `Semanal` é redundante com a primeira janela — conferido: as duas somam
    exatamente o mesmo. `Diária`/`Mensal`/`Semestral` têm recortes próprios.
  * Célula vazia = sem movimento; é o único valor não-numérico do arquivo.
  * O rodapé é descartado pelo teste "Gestão preenchida".

As colunas são localizadas pelo NOME no cabeçalho, não por posição fixa: as
duas colunas de CNPJ foram acrescentadas no meio da planilha em 14/08/2026, e
posição fixa quebraria silenciosamente (os fluxos passariam a ser lidos das
colunas erradas). Arquivos antigos, sem CNPJ, continuam sendo lidos.

SUBCLASSES: o export lista a classe-mãe e cada `... SUBCLASSE X` como linhas
separadas que compartilham o mesmo CNPJ de fundo (42 CNPJs, 122 linhas no
arquivo de 14/08). Os fluxos são de cada subclasse e devem ser somados; o PL,
não — quem enriquece precisa creditá-lo uma vez só. Ver `primeira_do_cnpj`.

O QUE A PLANILHA **NÃO** TRAZ: PL, composição por indexador, duration,
cotização, taxa de administração e status de captação. O PL passa a vir da CVM
(via `CVMCadastroEnricher`, casando pelo CNPJ do fundo); o resto fica vazio
(`None`) até o Quantum. Não inventamos número: sem composição, o fundo fica sem
bucket, e o front mostra "—" no lugar.
"""
from __future__ import annotations

import logging
import re
import unicodedata
import warnings
from datetime import date, datetime
from pathlib import Path

import pandas as pd

from app.config import settings
from app.connectors.base import DataConnector
from app.services import outlook_inbox
from app.utils import limpar_gestora

logger = logging.getLogger("vinculado_connector")

LINHA_CABECALHO = 0
LINHA_JANELAS = 2       # índice 0-based da linha com "06/08/2026 - 12/08/2026"
PRIMEIRA_LINHA_DADOS = 3

# Cabeçalho esperado -> nome interno. Só Nome e Gestão são obrigatórios; os
# demais podem faltar em arquivos antigos.
COLUNAS = {
    "nome": ("nome",),
    "gestao": ("gestora",),
    "cnpj": ("cnpj",),
    "cnpj gestao": ("cnpj_gestora",),
    "diaria": ("diaria",),
    "semanal": ("semanal",),
    "mensal": ("mensal",),
    "semestral": ("semestral",),
}
OBRIGATORIAS = ("nome", "gestora", "diaria", "semanal", "mensal", "semestral")

_RE_JANELA = re.compile(r"(\d{2}/\d{2}/\d{4})\s*-\s*(\d{2}/\d{2}/\d{4})")
_RE_SUBCLASSE = re.compile(r"\bSUBCLASSE\b", re.IGNORECASE)


def _chave_cabecalho(valor) -> str:
    """'CNPJ Gestão' -> 'cnpj gestao'. Tolera acento, caixa e espaço extra."""
    s = unicodedata.normalize("NFKD", str(valor))
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", s).strip().lower()


def so_digitos(valor) -> str | None:
    """'33.149.272/0001-07' -> '33149272000107'. None se não houver dígito."""
    if valor is None or (isinstance(valor, float) and pd.isna(valor)):
        return None
    d = re.sub(r"\D", "", str(valor))
    return d or None


class Janela:
    """Uma janela semanal do arquivo: 06/08/2026 - 12/08/2026."""

    __slots__ = ("inicio", "fim", "rotulo")

    def __init__(self, inicio: date, fim: date, rotulo: str) -> None:
        self.inicio = inicio
        self.fim = fim
        self.rotulo = rotulo

    @property
    def chave(self) -> str:
        """Data de fim em ISO — ordenável e estável entre arquivos."""
        return self.fim.isoformat()

    def to_dict(self) -> dict:
        return {
            "chave": self.chave,
            "inicio": self.inicio.isoformat(),
            "fim": self.fim.isoformat(),
            "rotulo": self.rotulo,
            "curto": self.fim.strftime("%d/%m"),
        }


class VinculadoConnector(DataConnector):
    name = "vinculado"

    def __init__(self, caminho: Path | None = None) -> None:
        # `caminho` explícito é útil em testes e no gerar_mock; em produção o
        # arquivo vem do e-mail mais recente.
        self.caminho = caminho or outlook_inbox.sincronizar()
        self.janelas: list[Janela] = []
        self.recebido_em: datetime | None = None
        self.fundos_sem_dados = 0
        self.tem_cnpj = False

    # ---------- metadados p/ o pipeline expor na API ----------
    @property
    def data_referencia(self) -> str | None:
        """Fim da janela semanal mais recente do arquivo."""
        return self.janelas[-1].chave if self.janelas else None

    def metadados(self) -> dict:
        return {
            "arquivo": self.caminho.name if self.caminho else None,
            "recebido_em": self.recebido_em.isoformat() if self.recebido_em else None,
            "data_referencia": self.data_referencia,
            "janelas": [j.to_dict() for j in self.janelas],
            "fundos_sem_dados": self.fundos_sem_dados,
            "tem_cnpj": self.tem_cnpj,
        }

    def disponivel(self) -> bool:
        return self.caminho is not None and self.caminho.exists()

    # ---------- leitura ----------
    def _mapear_colunas(self, raw: pd.DataFrame) -> dict[str, int]:
        """Localiza as colunas pelo cabeçalho da linha 1."""
        achadas: dict[str, int] = {}
        for col in range(raw.shape[1]):
            chave = _chave_cabecalho(raw.iat[LINHA_CABECALHO, col])
            if chave in COLUNAS:
                achadas.setdefault(COLUNAS[chave][0], col)

        faltando = [c for c in OBRIGATORIAS if c not in achadas]
        if faltando:
            raise ValueError(
                f"{self.caminho.name}: colunas obrigatórias não encontradas no "
                f"cabeçalho: {', '.join(faltando)}. O layout da planilha mudou?"
            )
        if "cnpj" not in achadas:
            logger.warning(
                "%s não tem coluna CNPJ — o enriquecimento com a CVM fica "
                "desligado para este arquivo.", self.caminho.name,
            )
        return achadas

    def _ler_janelas(self, raw: pd.DataFrame, primeira_col: int) -> list[Janela]:
        """Lê a linha de cabeçalho das janelas e devolve em ordem cronológica."""
        janelas: list[Janela] = []
        for col in range(primeira_col, raw.shape[1]):
            bruto = raw.iat[LINHA_JANELAS, col]
            if not isinstance(bruto, str):
                continue
            m = _RE_JANELA.search(bruto)
            if not m:
                logger.warning("Janela não reconhecida na coluna %d: %r", col, bruto)
                continue
            inicio = datetime.strptime(m.group(1), "%d/%m/%Y").date()
            fim = datetime.strptime(m.group(2), "%d/%m/%Y").date()
            janelas.append((col, Janela(inicio, fim, bruto.strip())))

        # O arquivo vem do mais recente para o mais antigo; o resto do sistema
        # (série temporal, médias móveis) quer ordem cronológica.
        janelas.sort(key=lambda cj: cj[1].fim)
        self._colunas_janela = [col for col, _ in janelas]
        return [j for _, j in janelas]

    def carregar_fundos(self) -> list[dict]:
        if not self.disponivel():
            # A mensagem muda conforme o lado: numa máquina com Outlook o
            # caminho é reler a caixa; num servidor não existe Outlook nenhum, e
            # mandar o usuário abri-lo seria uma pista falsa.
            if settings.OUTLOOK_ENABLED:
                caminho = (
                    "Abra o Outlook (o e-mail fica na pasta "
                    f"'{settings.OUTLOOK_PASTA}') e chame POST /api/admin/refresh, "
                    "ou copie o anexo manualmente para data/inbox/."
                )
            else:
                caminho = (
                    "Este servidor não lê o Outlook. A planilha precisa ser "
                    "enviada pela rede: rode o Coletar_e_Enviar.bat na máquina "
                    "que recebe o e-mail, ou faça POST /api/inbox com o .xlsx."
                )
            raise FileNotFoundError(f"Nenhuma planilha recebida ainda. {caminho}")

        logger.info("Lendo %s", self.caminho.name)
        self.recebido_em = datetime.fromtimestamp(self.caminho.stat().st_mtime)
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", category=UserWarning, module="openpyxl")
            raw = pd.read_excel(self.caminho, sheet_name=0, header=None)

        cols = self._mapear_colunas(raw)
        self.tem_cnpj = "cnpj" in cols

        # As janelas ocupam tudo à direita da última coluna nomeada.
        self.janelas = self._ler_janelas(raw, max(cols.values()) + 1)
        if not self.janelas:
            raise ValueError(
                f"{self.caminho.name}: nenhuma janela semanal reconhecida na linha "
                f"{LINHA_JANELAS + 1}. O layout do arquivo mudou?"
            )

        dados = raw.iloc[PRIMEIRA_LINHA_DADOS:]
        # Gestão vazia = linha de disclaimer do rodapé, não é fundo.
        dados = dados[dados[cols["gestora"]].notna()].copy()

        fluxo_cols = [cols[c] for c in ("diaria", "semanal", "mensal", "semestral")]
        numericas = fluxo_cols + self._colunas_janela
        for col in numericas:
            # Célula vazia = sem movimento na janela.
            dados[col] = pd.to_numeric(dados[col], errors="coerce").fillna(0.0)

        # Uma linha por CNPJ carrega o PL; as irmãs (subclasses do mesmo fundo)
        # ficam sem, para o PL do fundo não ser contado várias vezes. Preferimos
        # a linha da classe-mãe — a que não tem "SUBCLASSE" no nome.
        vistos: set[str] = set()
        if self.tem_cnpj:
            ordenadas = sorted(
                dados.index,
                key=lambda i: bool(_RE_SUBCLASSE.search(str(dados.at[i, cols["nome"]]))),
            )
            principais = set()
            for i in ordenadas:
                cnpj = so_digitos(dados.at[i, cols["cnpj"]])
                if cnpj and cnpj not in vistos:
                    vistos.add(cnpj)
                    principais.add(i)
        else:
            principais = set(dados.index)

        fundos: list[dict] = []
        sem_dados = 0
        subclasses = 0
        for i, linha in dados.iterrows():
            historico = {
                janela.chave: float(linha[col])
                for col, janela in zip(self._colunas_janela, self.janelas, strict=True)
            }
            if not any(linha[c] for c in numericas):
                sem_dados += 1
            cnpj = so_digitos(linha[cols["cnpj"]]) if self.tem_cnpj else None
            principal = i in principais
            if cnpj and not principal:
                subclasses += 1

            fundos.append({
                "cnpj": cnpj,
                "cnpj_gestora": (
                    so_digitos(linha[cols["cnpj_gestora"]])
                    if "cnpj_gestora" in cols else None
                ),
                # Só esta linha recebe o PL do CNPJ; ver bloco acima.
                "primeira_do_cnpj": principal,
                "nome": str(linha[cols["nome"]]).strip(),
                # Normalizado aqui, no ponto de entrada: o nome da gestora é a
                # chave de agrupamento de todo o painel, e limpá-lo só na tela
                # faria a mesma casa contar duas vezes.
                "gestora": limpar_gestora(linha[cols["gestora"]]),
                "diaria": float(linha[cols["diaria"]]),
                "semanal": float(linha[cols["semanal"]]),
                "mensal": float(linha[cols["mensal"]]),
                "semestral": float(linha[cols["semestral"]]),
                "historico_semanal": historico,
                # --- campos preenchidos por enriquecimento ---
                # Deliberadamente None: sem dado é melhor que dado inventado.
                # `pl` vem da CVM (CVMCadastroEnricher); o resto, do Quantum.
                "pl": None,
                "pct_lf": None,
                "pct_ipca": None,
                "pct_cdi": None,
                "duration": None,
                "cotizacao_resgate": None,
                "taxa_adm": None,
                "aberto_captacao": None,
                "resgate_pct_pl_semana": None,
            })

        self.fundos_sem_dados = sem_dados
        logger.info(
            "%d fundos lidos (%d sem movimento, %d subclasses de CNPJ repetido), "
            "%d janelas semanais de %s a %s, CNPJ=%s",
            len(fundos), sem_dados, subclasses, len(self.janelas),
            self.janelas[0].inicio, self.janelas[-1].fim,
            "sim" if self.tem_cnpj else "não",
        )
        return fundos
