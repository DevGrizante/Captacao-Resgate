"""
Relatório de cobertura: onde a planilha do Quantum e as bases da CVM divergem.

Gera três CSVs em `data/relatorios/`:

  1. `cnpjs_sem_match_cvm.csv`
     Fundos que ESTÃO na planilha mas cujo CNPJ não achamos na CVM — logo ficam
     sem PL no dashboard. Serve para conferir se o CNPJ do export está certo.

  2. `cnpjs_ausentes_no_quantum.csv`
     Fundos que ESTÃO na CVM, no mesmo universo que a planilha já cobre, mas
     que NÃO estão no export — candidatos a incluir no filtro do Quantum.

  3. `fundos_sem_bucket.csv`
     Fundos do universo que ficaram sem classificação de indexador, com o
     motivo. Separa "nem apareceu no CDA" de "apareceu mas foi barrado pela
     guarda", que são problemas diferentes: o primeiro é ausência de
     declaração, o segundo é qualidade insuficiente do que foi declarado.

O "mesmo universo" não é chutado: é inferido das classificações ANBIMA que os
fundos já presentes na planilha têm. Assim o relatório se ajusta sozinho se o
recorte do export mudar.

Uso:
    python backend/scripts/analise/relatorio_cobertura.py
"""
from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.config import DATA_DIR, settings  # noqa: E402
from app.connectors import cvm_cadastro  # noqa: E402
from app.connectors.vinculado_connector import VinculadoConnector  # noqa: E402
from app.services import credito_privado, outlook_inbox  # noqa: E402

SAIDA = DATA_DIR / "relatorios"
# Só reporta como "faltando" o fundo grande o bastante para importar na mesa.
PL_MINIMO_SUGESTAO = 10_000_000
# Uma classificação ANBIMA entra no universo se pelo menos esta fração dos
# fundos da CVM naquela classificação já está na planilha. Evita puxar
# categoria inteira por causa de dois ou três fundos soltos.
COBERTURA_MINIMA = 0.30


def _motivo(cnpj: str, cad, diario) -> str:
    """Por que este fundo ficou sem PL. Só o primeiro caso é acionável.

    Os demais são a realidade do arquivo: o export do Quantum inclui fundos que
    tiveram fluxo nas últimas 40 semanas mas já foram encerrados desde então —
    para eles, não ter PL está certo.
    """
    if cnpj in diario.index:
        pl = diario.at[cnpj, "pl"]
        if pd.isna(pl) or pl <= 0:
            return "3. declara PL zero (esvaziado/liquidado)"
        return "4. PL abaixo do piso de credibilidade"
    if cnpj in cad.index:
        return "2. encerrado na CVM, não declara mais"
    return "1. AUSENTE das bases da CVM (verificar CNPJ)"


def main() -> None:
    caminho = outlook_inbox.arquivo_mais_recente()
    if caminho is None:
        print("Nenhum vinculado_*.xlsx em data/inbox/.")
        sys.exit(1)

    fundos = VinculadoConnector(caminho=caminho).carregar_fundos()
    na_planilha = {f["cnpj"] for f in fundos if f["cnpj"]}
    print(f"planilha: {caminho.name} — {len(fundos)} linhas, {len(na_planilha)} CNPJs")

    cad = cvm_cadastro.carregar(apenas_ativos=False)
    diario = cvm_cadastro.metricas_diarias()
    ativos = cad[cad["situacao"] == cvm_cadastro.ATIVO]
    print(f"CVM: {len(cad)} fundos no registro, {len(diario)} no informe diário")

    # ---------- 1) na planilha, sem PL no dashboard ----------
    # Roda o enricher de verdade em vez de reimplementar a regra: assim o
    # relatório não descola do que o dashboard mostra quando a regra mudar.
    cvm_cadastro.CVMCadastroEnricher().enriquecer_lote(fundos)

    # E aplica o mesmo recorte de crédito privado do dashboard: reportar um
    # fundo multimercado como "faltando PL" seria ruído, ele nem aparece.
    modo = settings.CREDITO_PRIVADO_MODO
    antes = len(fundos)
    fundos = [f for f in fundos
              if credito_privado.avaliar(f, modo, settings.CREDITO_PRIVADO_LIMIAR)[0]]
    print(f"universo crédito privado (modo={modo}): {len(fundos)} de {antes}")

    linhas = []
    for f in fundos:
        cnpj = f["cnpj"]
        if not cnpj or f.get("pl"):
            continue
        if not f.get("primeira_do_cnpj", True):
            continue  # subclasse: o PL foi creditado à classe-mãe, de propósito
        linhas.append({
            "cnpj": cnpj,
            "nome": f["nome"],
            "gestora": f["gestora"],
            "cnpj_gestora": f["cnpj_gestora"],
            "motivo": _motivo(cnpj, cad, diario),
            "situacao_cvm": f.get("cvm_situacao") or "",
            "semanal": f["semanal"],
            "semestral": f["semestral"],
        })
    sem_match = pd.DataFrame(linhas).sort_values(
        ["motivo", "semestral"],
        key=lambda c: c if c.name == "motivo" else c.abs(),
        ascending=[True, False],
    )
    n_sem_pl = len(sem_match)
    print("\nmotivos de não ter PL:")
    for k, v in sem_match["motivo"].value_counts().items():
        print(f"   {k:44} {v:5}")

    # ---------- 2) na CVM, ausentes da planilha ----------
    # O universo do export é inferido das classificações que ele já contém.
    presentes = ativos[ativos.index.isin(na_planilha)]
    contagem_planilha = Counter(presentes["classificacao_anbima"].dropna())
    contagem_cvm = Counter(ativos["classificacao_anbima"].dropna())
    universo = {
        c for c, n in contagem_planilha.items()
        if n / contagem_cvm[c] >= COBERTURA_MINIMA
    }
    print(f"\nuniverso inferido: {len(universo)} classificações ANBIMA")
    for c in sorted(universo, key=lambda c: -contagem_planilha[c])[:12]:
        print(f"   {c[:48]:48} {contagem_planilha[c]:5}/{contagem_cvm[c]:5}"
              f"  ({contagem_planilha[c]/contagem_cvm[c]*100:4.0f}% já na planilha)")

    candidatos = ativos[
        ativos["classificacao_anbima"].isin(universo)
        & ~ativos.index.isin(na_planilha)
    ].copy()
    # PL do informe diário é mais fresco; cai para o do registro se faltar.
    pl_informe = pd.Series(candidatos.index.map(diario["pl"]), index=candidatos.index)
    candidatos["pl_final"] = pl_informe.fillna(candidatos["pl"])
    candidatos = candidatos[candidatos["pl_final"] >= PL_MINIMO_SUGESTAO]
    ausentes = (
        candidatos.reset_index()[[
            "cnpj", "nome", "gestor", "administrador",
            "classificacao_anbima", "forma_condominio", "pl_final",
        ]]
        .rename(columns={"gestor": "gestora", "pl_final": "pl"})
        .sort_values("pl", ascending=False)
    )

    # ---------- 3) sem bucket, e por quê ----------
    # `carteira_motivo` é preenchido pelo próprio conector quando o fundo
    # aparece no CDA mas não passa nas guardas; vazio com carteira_data vazia
    # significa que ele nem consta do CDA daquele mês.
    sem_bucket = pd.DataFrame([
        {
            "cnpj": f["cnpj"],
            "nome": f["nome"],
            "gestora": f["gestora"],
            "motivo": f.get("carteira_motivo") or "não consta do CDA do mês lido",
            "sigilo_pct_pl": f.get("carteira_sigilo_pct"),
            "idx_conhecido_pct": f.get("carteira_idx_conhecido_pct"),
            "credito_pct_pl": f.get("carteira_pct_pl"),
            "pl": f.get("pl"),
            "semestral": f["semestral"],
        }
        for f in fundos
        if f.get("pct_ipca") is None and f.get("pct_cdi") is None and f.get("pct_lf") is None
    ]).sort_values("pl", ascending=False, na_position="last")
    print("\nmotivos de não ter bucket:")
    for k, v in sem_bucket["motivo"].value_counts().items():
        pl = sem_bucket.loc[sem_bucket["motivo"] == k, "pl"].sum()
        print(f"   {k:34} {v:5}   PL R$ {pl/1e9:8.1f} bi")

    SAIDA.mkdir(parents=True, exist_ok=True)
    a = SAIDA / "cnpjs_sem_match_cvm.csv"
    b = SAIDA / "cnpjs_ausentes_no_quantum.csv"
    c = SAIDA / "fundos_sem_bucket.csv"
    sem_match.to_csv(a, index=False, sep=";", encoding="utf-8-sig")
    ausentes.to_csv(b, index=False, sep=";", encoding="utf-8-sig")
    sem_bucket.to_csv(c, index=False, sep=";", encoding="utf-8-sig")

    print(f"\n>>> {n_sem_pl:>5} fundos da planilha ficam SEM PL       -> {a.name}")
    print(f">>> {len(ausentes):>5} fundos da CVM ausentes da planilha   -> {b.name}")
    print(f">>> {len(sem_bucket):>5} fundos sem bucket de indexador       -> {c.name}")
    print(f"    PL somado dos ausentes: R$ {ausentes['pl'].sum()/1e9:,.1f} bi")
    print(f"\nsalvos em {SAIDA}")

    print("\n--- 10 maiores ausentes da planilha ---")
    for _, r in ausentes.head(10).iterrows():
        print(f"  {r['cnpj']}  R$ {r['pl']/1e9:7.2f} bi  {str(r['nome'])[:56]}")

    print("\n--- os AUSENTES das bases da CVM (motivo 1: acionável) ---")
    acionaveis = sem_match[sem_match["motivo"].str.startswith("1.")]
    for _, r in acionaveis.head(15).iterrows():
        print(f"  {r['cnpj']}  sem={r['semestral']/1e6:9.1f} mi  {str(r['nome'])[:54]}")
    if len(acionaveis) > 15:
        print(f"  … mais {len(acionaveis)-15} em {a.name}")


if __name__ == "__main__":
    main()
