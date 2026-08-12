"""
Regenera data/mock_fundos.json a partir de um Excel no formato "vinculado".

Uso:
    python scripts/gerar_mock.py caminho/para/vinculado.xlsx

O Excel deve ter, na linha 3 (header=2), as colunas:
    Nome | Gestão | Diária | Semanal | Mensal | Semestral | ...janelas semanais...

Fluxos são reais (do arquivo); duration/cotização/taxa/composição são
sintéticos porém estáveis (seed fixa) — apenas para validar o front até
plugar CVM + Quantum.
"""
from __future__ import annotations

import json
import random
import sys
from pathlib import Path

import pandas as pd

BASE_DIR = Path(__file__).resolve().parents[2]
OUT = BASE_DIR / "data" / "mock_fundos.json"


def main(xlsx_path: str) -> None:
    random.seed(42)
    df = pd.read_excel(xlsx_path, sheet_name=0, header=2)
    df.columns = ["Nome", "Gestao", "Diaria", "Semanal", "Mensal", "Semestral"] + list(df.columns[6:])
    df = df[df["Nome"].notna()].copy()
    for c in ["Diaria", "Semanal", "Mensal", "Semestral"]:
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0.0)

    fundos = []
    for _, r in df.iterrows():
        nome = str(r["Nome"]).upper()
        h = abs(hash(r["Gestao"])) % 1000 / 1000
        # Fundos com cara de renda fixa/DI têm chance de LF majoritária,
        # para o bucket LF aparecer povoado ao validar o front.
        is_rf = any(k in nome for k in ["RENDA FIXA", "REFERENCIADO DI", "RF ", "FIRF"])
        if is_rf and random.random() < 0.18:
            pct_lf = round(random.uniform(0.52, 0.85), 3)
        else:
            pct_lf = round(min(0.30, 0.03 + h * 0.22), 3)
        resto = 1 - pct_lf
        ipca_share = 0.25 + h * 0.4
        pct_ipca = round(resto * ipca_share, 3)
        pct_cdi = round(resto * (1 - ipca_share) * 0.85, 3)
        pl = abs(r["Semestral"]) * 3 + random.uniform(1e7, 5e8)
        dmais = random.choice([1, 5, 15, 30, 45, 60, 90, 180])
        fundos.append({
            "cnpj": f"{random.randint(10,99)}.{random.randint(100,999)}."
                    f"{random.randint(100,999)}/0001-{random.randint(10,99)}",
            "nome": str(r["Nome"]),
            "gestora": str(r["Gestao"]),
            "diaria": float(r["Diaria"]),
            "semanal": float(r["Semanal"]),
            "mensal": float(r["Mensal"]),
            "semestral": float(r["Semestral"]),
            "pl": round(pl, 2),
            "pct_lf": pct_lf,
            "pct_ipca": pct_ipca,
            "pct_cdi": pct_cdi,
            "duration": round(random.uniform(1.5, 5.5), 1),
            "cotizacao_resgate": dmais,
            "taxa_adm": round(random.uniform(0.35, 0.95), 2),
            "aberto_captacao": random.random() > 0.30,
            "resgate_pct_pl_semana": round(r["Semanal"] / pl, 4) if pl > 0 else 0.0,
        })

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(fundos, f, ensure_ascii=False)
    print(f"OK — {len(fundos)} fundos gravados em {OUT}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Uso: python scripts/gerar_mock.py caminho/para/vinculado.xlsx")
        sys.exit(1)
    main(sys.argv[1])
