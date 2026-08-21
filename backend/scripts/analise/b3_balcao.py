"""
Puxa as bases públicas de balcão da B3: cadastro de instrumentos e negócios.

    python backend/scripts/analise/b3_balcao.py                      # último dia útil publicado
    python backend/scripts/analise/b3_balcao.py 2026-08-13           # uma data específica
    python backend/scripts/analise/b3_balcao.py 2026-08-13 --tudo    # sem filtrar por LF

Gera em `data/b3/`:
    cadastro_lf_<data>.csv       LF/LFS/LFSC/LFSN/LFV com ticker, ISIN, taxa e vencimento
    negocios_lf_<data>.csv       negócio a negócio do dia
    consolidado_lf_<data>.csv    o mesmo dia consolidado por papel (mín/méd/máx)
    b3_balcao_<data>.xlsx        as três abas juntas

>>> DE ONDE VEM

Da API que o próprio Boletim Diário do Mercado (BDI) usa. Não é endpoint
documentado como API pública, mas é o que serve a página aberta da B3 — sem
autenticação, sem chave. O portal antigo (`arquivos.b3.com.br/Web/Consolidated`)
só publica instrumentos LISTADOS, e Letra Financeira é balcão: não aparece lá.
A B3 migrou os dados de balcão para o BDI em 15/12/2025 (Comunicado Externo
01/2026-VTEC) e desativou as páginas antigas em 31/03/2026.

    POST /bdi/table/export/csv?lang=pt-BR
    {"Name": <tabela>, "Date": "AAAA-MM-DD", "FinalDate": ..., "ClientId": "", "Filters": {}}

`Filters` precisa ser objeto (`{}`), não lista — com lista a API devolve 400.

>>> AS TABELAS QUE INTERESSAM (capítulo "Renda fixa")

    InstrumentRegistration  cadastro de instrumentos de balcão   ~285 mil linhas
    Trade                   negócio a negócio                     ~31 mil linhas
    ConsolidatedRecords     negociação consolidada por papel      ~52 mil linhas

`table/classifications` lista todas as tabelas do BDI, se precisar de outra.

>>> CUIDADO COM A DATA

O arquivo do dia sai incompleto quando a B3 atrasa a publicação — em
2026-08-14 o cadastro veio truncado em 10.000 linhas, só com COE/CFF/CDCA, e
nenhuma LF. No dia anterior vieram as 285.409 linhas completas. Por isso
`_data_boa` confere se o resultado tem cara de completo e volta um dia se não
tiver, em vez de entregar um recorte silencioso.
"""
from __future__ import annotations

import io
import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import requests

BASE = "https://arquivos.b3.com.br/bdi"
CABECALHOS = {
    "Accept": "application/json",
    "Content-Type": "application/json",
    "User-Agent": "Mozilla/5.0",
}

# Letra Financeira e suas variantes de subordinação. É a quebra que o CDA da
# CVM não tem: lá tudo é "Letra Financeira", e LFSC/LFSN são outro risco e
# outro preço.
TIPOS_LF = ("LF", "LFS", "LFSC", "LFSN", "LFV")

SAIDA = Path(__file__).resolve().parents[3] / "data" / "b3"

# Abaixo disto o cadastro claramente veio truncado (o normal são ~285 mil).
_MINIMO_CADASTRO = 50_000


def exportar(tabela: str, data: str) -> pd.DataFrame:
    """Baixa uma tabela do BDI e devolve o DataFrame já com o cabeçalho certo."""
    corpo = {"Name": tabela, "Date": data, "FinalDate": data,
             "ClientId": "", "Filters": {}}
    r = requests.post(f"{BASE}/table/export/csv?lang=pt-BR", json=corpo,
                      headers=CABECALHOS, timeout=900)
    r.raise_for_status()
    # UTF-8 com BOM. Deixar o requests adivinhar destrói todos os acentos.
    texto = r.content.decode("utf-8-sig", errors="replace")

    # O CSV vem precedido de linhas livres de explicação e do link do glossário.
    # Localizamos o cabeçalho pelo conteúdo em vez de pular um número fixo de
    # linhas, que quebraria quando a B3 mexesse no texto.
    linhas = texto.split("\n")
    inicio = next(
        (i for i, l in enumerate(linhas) if "ISIN" in l and l.count(";") > 5), None
    )
    if inicio is None:
        raise RuntimeError(f"{tabela} {data}: cabeçalho não encontrado (formato mudou?)")
    df = pd.read_csv(io.StringIO("\n".join(linhas[inicio:])), sep=";", dtype=str)
    # A última linha costuma ser um aviso solto, não um registro.
    return df.dropna(how="all").reset_index(drop=True)


def _data_boa(hoje: date | None = None) -> str:
    """Acha a data mais recente cujo cadastro veio completo.

    Tenta os últimos dias úteis. Não basta o HTTP 200: a B3 publica o arquivo
    truncado quando está atrasada, e um recorte de 10 mil linhas sem nenhuma LF
    passaria por "deu certo".
    """
    d = hoje or date.today()
    for _ in range(10):
        d -= timedelta(days=1)
        if d.weekday() >= 5:
            continue
        try:
            cad = exportar("InstrumentRegistration", d.isoformat())
        except Exception:
            continue
        if len(cad) >= _MINIMO_CADASTRO:
            return d.isoformat()
        print(f"  [aviso] {d}: cadastro veio com {len(cad):,} linhas "
              f"(incompleto) — tentando o dia anterior")
    raise RuntimeError("Nenhuma data recente com cadastro completo.")


def _so_lf(df: pd.DataFrame) -> pd.DataFrame:
    col = next((c for c in df.columns if "nstrumento financeiro" in c), None)
    return df[df[col].isin(TIPOS_LF)].copy() if col else df.iloc[0:0]


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    tudo = "--tudo" in sys.argv
    data = args[0] if args else None

    if data is None:
        print("Procurando a data mais recente com cadastro completo...")
        data = _data_boa()
    print(f"Data de referência: {data}\n")

    SAIDA.mkdir(parents=True, exist_ok=True)
    escritos: list[tuple[str, pd.DataFrame]] = []

    for rotulo, tabela, arquivo in (
        ("Cadastro de instrumentos", "InstrumentRegistration", "cadastro"),
        ("Negócio a negócio", "Trade", "negocios"),
        ("Negociação consolidada", "ConsolidatedRecords", "consolidado"),
    ):
        try:
            df = exportar(tabela, data)
        except Exception as e:  # noqa: BLE001
            print(f"  {rotulo}: FALHOU ({e})")
            continue
        total = len(df)
        if not tudo:
            df = _so_lf(df)
        nome = f"{arquivo}_{'todos' if tudo else 'lf'}_{data}.csv"
        df.to_csv(SAIDA / nome, sep=";", index=False, encoding="utf-8-sig")
        escritos.append((rotulo, df))
        print(f"  {rotulo:<26} {total:>7,} linhas -> {len(df):>6,} gravadas em {nome}")

    if escritos:
        xlsx = SAIDA / f"b3_balcao_{data}.xlsx"
        with pd.ExcelWriter(xlsx) as w:
            for rotulo, df in escritos:
                df.to_excel(w, sheet_name=rotulo[:31], index=False)
        print(f"\n  Excel com as três abas: {xlsx}")

    # Um resumo do que saiu, para conferir sem abrir o arquivo.
    for rotulo, df in escritos:
        if df.empty:
            continue
        col = next((c for c in df.columns if "nstrumento financeiro" in c), None)
        if col:
            print(f"\n  {rotulo}: {df[col].value_counts().to_dict()}")
        vol = next((c for c in df.columns if "Volume financeiro" in c), None)
        if vol is not None:
            v = pd.to_numeric(df[vol].astype(str).str.replace(".", "", regex=False)
                              .str.replace(",", ".", regex=False), errors="coerce")
            print(f"     volume financeiro: R$ {v.sum() / 1e6:,.1f} mi")


if __name__ == "__main__":
    main()
