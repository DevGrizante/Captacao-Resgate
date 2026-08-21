"""
Base histórica de DEBÊNTURES da B3 — negociação, mês a mês, até onde a API tiver.

    python backend/scripts/analise/b3_historico_deb.py
    python backend/scripts/analise/b3_historico_deb.py --desde 2026-01
    python backend/scripts/analise/b3_historico_deb.py --destino D:\\dados\\Base_DEB

>>> O QUE ELE BAIXA

    ConsolidatedRecords  negociação consolidada por papel e por pregão:
                         ticker, ISIN, preço mín/méd/máx, último, preço de
                         referência, nº de negócios, volume e a classificação
                         INTRAGRUPO/EXTRAGRUPO.
    Trade                negócio a negócio: horário, quantidade, preço, taxa,
                         origem e data de liquidação.

    Guarda SÓ as linhas de `Instrumento financeiro == DEB`. O arquivo bruto de
    um mês tem ~1,16 milhão de linhas e 158 MB, das quais ~29 mil (2,5%) são
    debênture. Filtrar na entrada é o que faz a base caber em ~2 MB por mês em
    vez de 158.

>>> POR QUE MÊS A MÊS, E NÃO DIA A DIA

    A API aceita intervalo (`Date` != `FinalDate`), e um mês inteiro vem numa
    requisição só. Baixar dia a dia seria ~21 requisições para o mesmo dado,
    cada uma pagando a latência de montagem do CSV no servidor da B3.

    Não dá para ir além do mês: o corpo de resposta cresce linearmente e um
    trimestre passaria de 450 MB numa resposta só, sem paginação para segurar.

>>> POR QUE NÃO HÁ FILTRO NO SERVIDOR

    O campo `Filters` do payload existe, mas não aceita filtro por tipo de
    instrumento — testadas cinco grafias (`InstrumentFinancial`, `InstrumentType`,
    `TipoInstrumento`, …), todas devolvem HTTP 500. O filtro é nosso, no cliente.

>>> ATÉ ONDE VAI O HISTÓRICO

    O dado de balcão migrou para o BDI em 15/12/2025 (Comunicado Externo
    01/2026-VTEC). Antes disso o arquivo existe mas é bem menor — o BDI só
    publicava instrumento listado. O script não assume a data de corte: ele
    anda para trás até acumular `--paradas` meses seguidos sem nenhuma linha de
    debênture, e aí para.

>>> RETOMÁVEL

    Cada mês vira um arquivo próprio, e um mês já baixado é pulado. Se a rede
    cair no meio de uma janela de 20 meses, rodar de novo continua de onde
    parou em vez de recomeçar. `--refazer` força rebaixar.
"""
from __future__ import annotations

import argparse
import io
import logging
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.config import DATA_DIR  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
logger = logging.getLogger("b3_historico_deb")

BASE = "https://arquivos.b3.com.br/bdi"
CABECALHOS = {
    "Accept": "application/json",
    "Content-Type": "application/json",
    "User-Agent": "Mozilla/5.0",
}

DESTINO_PADRAO = DATA_DIR / "Base_DEB"

# As duas tabelas de negociação do capítulo "Renda fixa" do BDI.
TABELAS = {
    "ConsolidatedRecords": "consolidado",
    "Trade": "negocios",
}

TIPO_ALVO = "DEB"

# Um mês inteiro de balcão passa de 150 MB e o servidor da B3 leva minutos para
# montar o CSV. O timeout precisa acomodar isso, ou a janela grande morre no
# primeiro mês cheio.
TIMEOUT_S = 1800


# Quantas vezes insistir num intervalo antes de parti-lo ao meio.
_TENTATIVAS = 3


def _exportar(tabela: str, inicio: str, fim: str, profundidade: int = 0) -> pd.DataFrame:
    """Baixa um intervalo e devolve só as linhas de DEB.

    >>> POR QUE HÁ RETRY E DIVISÃO DE JANELA

    O servidor da B3 monta o CSV na hora, e um mês cheio de ConsolidatedRecords
    (150+ MB) às vezes estoura o tempo do gateway dele. Observado na carga de
    20/08/2026: 202604 devolveu `504 Gateway Timeout` e 202603 devolveu `499`,
    enquanto os meses vizinhos passaram na primeira tentativa. Não é limite de
    cota nem bloqueio — é carga.

    A resposta é em dois degraus, do mais barato para o mais caro:

        1. insistir `_TENTATIVAS` vezes, com espera crescente. Boa parte dos
           504 passa na segunda tentativa.
        2. se ainda assim falhar, PARTIR O INTERVALO AO MEIO e baixar as duas
           metades. Duas quinzenas de 75 MB são pedidos que o gateway aguenta,
           e o resultado concatenado é idêntico ao do mês inteiro.

    A recursão para em `profundidade > 2` (mês -> quinzena -> semana). Além
    disso o problema não é tamanho, e continuar dividindo só multiplicaria
    requisições contra um servidor que já está recusando.
    """
    corpo = {
        "Name": tabela, "Date": inicio, "FinalDate": fim,
        "ClientId": "", "Filters": {},
    }
    ultimo_erro: Exception | None = None
    for tentativa in range(1, _TENTATIVAS + 1):
        try:
            resp = requests.post(
                f"{BASE}/table/export/csv?lang=pt-BR",
                json=corpo, headers=CABECALHOS, timeout=TIMEOUT_S,
            )
            resp.raise_for_status()
            break
        except Exception as e:  # noqa: BLE001 — 504/499 do gateway são recuperáveis
            ultimo_erro = e
            if tentativa < _TENTATIVAS:
                espera = 20 * tentativa
                logger.warning(
                    "    %s %s a %s: tentativa %d falhou (%s). Nova tentativa em %ds.",
                    tabela, inicio, fim, tentativa, e, espera,
                )
                time.sleep(espera)
    else:
        resp = None

    if resp is None:
        if profundidade >= 2:
            raise ultimo_erro  # type: ignore[misc]
        meio = _meio(inicio, fim)
        if meio is None:
            raise ultimo_erro  # type: ignore[misc]
        logger.warning(
            "    %s %s a %s: partindo o intervalo ao meio em %s.",
            tabela, inicio, fim, meio,
        )
        primeira = _exportar(tabela, inicio, meio, profundidade + 1)
        segunda = _exportar(
            tabela, (date.fromisoformat(meio) + timedelta(days=1)).isoformat(),
            fim, profundidade + 1,
        )
        partes = [p for p in (primeira, segunda) if not p.empty]
        return pd.concat(partes, ignore_index=True) if partes else pd.DataFrame()

    # UTF-8 com BOM. Deixar o requests adivinhar destrói todos os acentos.
    texto = resp.content.decode("utf-8-sig", errors="replace")
    linhas = texto.split("\n")

    # O CSV vem precedido de linhas livres de explicação e do link do glossário.
    # Localizamos o cabeçalho pelo CONTEÚDO em vez de pular um número fixo de
    # linhas, que quebraria quando a B3 mexesse no texto.
    inicio_csv = next(
        (i for i, l in enumerate(linhas) if "ISIN" in l and l.count(";") > 5), None
    )
    if inicio_csv is None:
        # Mês sem dado devolve um corpo curto sem cabeçalho. Não é erro.
        return pd.DataFrame()

    df = pd.read_csv(
        io.StringIO("\n".join(linhas[inicio_csv:])), sep=";", dtype=str
    ).dropna(how="all")
    if df.empty:
        return df

    coluna = next((c for c in df.columns if "nstrumento financeiro" in c), None)
    if coluna is None:
        logger.warning("%s %s: sem coluna de instrumento — nada filtrado.", tabela, inicio)
        return pd.DataFrame()

    return df[df[coluna] == TIPO_ALVO].reset_index(drop=True)


def _meio(inicio: str, fim: str) -> str | None:
    """Data do meio do intervalo, ou None se ele já é curto demais para partir."""
    d1, d2 = date.fromisoformat(inicio), date.fromisoformat(fim)
    if (d2 - d1).days < 2:
        return None
    return (d1 + (d2 - d1) / 2).isoformat()


def _meses(ate: date, quantos: int) -> list[tuple[str, str, str]]:
    """[(AAAAMM, primeiro_dia, ultimo_dia)], do mais recente para o mais antigo."""
    out = []
    ano, mes = ate.year, ate.month
    for _ in range(quantos):
        primeiro = date(ano, mes, 1)
        # Último dia do mês = primeiro do mês seguinte menos um dia. Evita a
        # tabela de 28/29/30/31 e acerta fevereiro bissexto de graça.
        if mes == 12:
            ultimo = date(ano, 12, 31)
        else:
            ultimo = date(ano, mes + 1, 1) - timedelta(days=1)
        out.append((f"{ano:04d}{mes:02d}", primeiro.isoformat(), ultimo.isoformat()))
        mes -= 1
        if mes == 0:
            ano, mes = ano - 1, 12
    return out


def baixar_mes(aaaamm: str, inicio: str, fim: str, destino: Path,
               refazer: bool) -> dict[str, int]:
    """Baixa as duas tabelas de um mês. Devolve {rotulo: linhas}."""
    resultado: dict[str, int] = {}

    for tabela, rotulo in TABELAS.items():
        arquivo = destino / f"deb_{rotulo}_{aaaamm}.parquet"
        if arquivo.exists() and not refazer:
            try:
                resultado[rotulo] = len(pd.read_parquet(arquivo))
                logger.info("  %s %s: já baixado (%d linhas).", rotulo, aaaamm,
                            resultado[rotulo])
                continue
            except Exception:  # noqa: BLE001 — parquet corrompido: rebaixa
                logger.warning("  %s %s: arquivo ilegível, rebaixando.", rotulo, aaaamm)

        marcado = time.time()
        try:
            df = _exportar(tabela, inicio, fim)
        except Exception as e:  # noqa: BLE001 — um mês que falha não derruba a janela
            logger.error("  %s %s: FALHOU (%s)", rotulo, aaaamm, e)
            resultado[rotulo] = -1
            continue

        resultado[rotulo] = len(df)
        if df.empty:
            logger.info("  %s %s: nenhuma debênture (%.0fs).", rotulo, aaaamm,
                        time.time() - marcado)
            continue

        df.to_parquet(arquivo, index=False)
        # O CSV vai junto porque a base é para ser USADA fora daqui também —
        # Excel, Power BI, um colega sem Python. O parquet é para o código.
        df.to_csv(destino / f"deb_{rotulo}_{aaaamm}.csv", sep=";",
                  index=False, encoding="utf-8-sig")
        logger.info("  %s %s: %d linhas em %.0fs -> %s", rotulo, aaaamm, len(df),
                    time.time() - marcado, arquivo.name)

    return resultado


def consolidar(destino: Path) -> None:
    """Junta os meses num arquivo só por tabela. É o que o código vai ler."""
    for rotulo in TABELAS.values():
        partes = sorted(destino.glob(f"deb_{rotulo}_2*.parquet"))
        if not partes:
            continue
        df = pd.concat((pd.read_parquet(p) for p in partes), ignore_index=True)

        alvo = destino / f"deb_{rotulo}_TUDO.parquet"
        df.to_parquet(alvo, index=False)
        df.to_csv(destino / f"deb_{rotulo}_TUDO.csv", sep=";",
                  index=False, encoding="utf-8-sig")

        col_data = next((c for c in df.columns if "Data neg" in c), None)
        pregoes = df[col_data].nunique() if col_data else "?"
        col_if = next((c for c in df.columns if c.endswith("IF")), None)
        papeis = df[col_if].nunique() if col_if else "?"
        logger.info(
            "CONSOLIDADO %s: %d linhas | %s pregões | %s papéis -> %s (%.1f MB)",
            rotulo, len(df), pregoes, papeis, alvo.name,
            alvo.stat().st_size / 1e6,
        )


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    p.add_argument("--destino", type=Path, default=DESTINO_PADRAO)
    p.add_argument("--desde", help="mês mais antigo a tentar, AAAA-MM")
    p.add_argument("--maximo", type=int, default=36,
                   help="teto de meses a percorrer (padrão: 36)")
    p.add_argument("--paradas", type=int, default=3,
                   help="meses seguidos sem debênture antes de desistir (padrão: 3)")
    p.add_argument("--refazer", action="store_true", help="rebaixa mês já salvo")
    args = p.parse_args()

    destino: Path = args.destino
    destino.mkdir(parents=True, exist_ok=True)
    logger.info("Destino: %s", destino)

    limite = None
    if args.desde:
        ano, mes = (int(x) for x in args.desde.split("-"))
        limite = f"{ano:04d}{mes:02d}"

    vazios = 0
    baixados = 0
    for aaaamm, inicio, fim in _meses(date.today(), args.maximo):
        if limite and aaaamm < limite:
            logger.info("Cheguei ao limite --desde %s. Parando.", args.desde)
            break

        logger.info("=== %s (%s a %s) ===", aaaamm, inicio, fim)
        res = baixar_mes(aaaamm, inicio, fim, destino, args.refazer)
        total = sum(v for v in res.values() if v > 0)

        if total == 0:
            vazios += 1
            if not limite and vazios >= args.paradas:
                logger.info(
                    "%d meses seguidos sem debênture — fim do histórico disponível.",
                    vazios,
                )
                break
        else:
            vazios = 0
            baixados += 1

    logger.info("Meses com dado: %d. Consolidando…", baixados)
    consolidar(destino)

    arquivos = sorted(destino.glob("*"))
    tamanho = sum(a.stat().st_size for a in arquivos if a.is_file())
    print()
    print(f"  Arquivos em {destino}: {len(arquivos)}  ({tamanho / 1e6:.1f} MB)")
    for a in arquivos:
        if a.is_file() and "TUDO" in a.name:
            print(f"    {a.name:<34} {a.stat().st_size / 1e6:>8.1f} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
