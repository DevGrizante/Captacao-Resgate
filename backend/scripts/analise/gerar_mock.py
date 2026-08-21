"""
Regenera data/mock_fundos.json a partir de um `vinculado_*.xlsx`.

Uso:
    python scripts/gerar_mock.py                      # usa o último da inbox
    python scripts/gerar_mock.py caminho/vinculado.xlsx

O mock existe só para desenvolver o front offline. Os FLUXOS são reais (vêm do
arquivo, lidos pelo VinculadoConnector); PL, composição, duration, cotização e
taxa são SINTÉTICOS (seed fixa).

>>> Não use DATA_SOURCE=mock para decisão de mesa. Para dado real, use
    DATA_SOURCE=vinculado, que deixa os campos sintéticos vazios.
"""
from __future__ import annotations

import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import DATA_DIR  # noqa: E402
from app.connectors.vinculado_connector import VinculadoConnector  # noqa: E402
from app.services import outlook_inbox  # noqa: E402

OUT = DATA_DIR / "mock_fundos.json"


def main(xlsx_path: str | None) -> None:
    random.seed(42)
    caminho = Path(xlsx_path) if xlsx_path else outlook_inbox.arquivo_mais_recente()
    if caminho is None:
        print("Nenhum vinculado_*.xlsx em data/inbox/. Passe o caminho como argumento.")
        sys.exit(1)

    conn = VinculadoConnector(caminho=caminho)
    fundos = conn.carregar_fundos()

    for f in fundos:
        nome = f["nome"].upper()
        h = abs(hash(f["gestora"])) % 1000 / 1000
        # Fundos com cara de renda fixa/DI têm chance de LF majoritária,
        # para o bucket LF aparecer povoado ao validar o front.
        is_rf = any(k in nome for k in ["RENDA FIXA", "REFERENCIADO DI", "RF ", "FIRF"])
        if is_rf and random.random() < 0.18:
            pct_lf = round(random.uniform(0.52, 0.85), 3)
        else:
            pct_lf = round(min(0.30, 0.03 + h * 0.22), 3)
        resto = 1 - pct_lf
        ipca_share = 0.25 + h * 0.4
        pl = abs(f["semestral"]) * 3 + random.uniform(1e7, 5e8)

        f.update({
            # O CNPJ real vem da planilha e é preservado — é o que permite
            # exercitar o casamento com a CVM também no mock.
            "pl": round(pl, 2),
            "pl_anterior": round(pl * random.uniform(0.94, 1.06), 2),
            "pct_lf": pct_lf,
            "pct_ipca": round(resto * ipca_share, 3),
            "pct_cdi": round(resto * (1 - ipca_share) * 0.85, 3),
            "duration": round(random.uniform(1.5, 5.5), 1),
            "cotizacao_resgate": random.choice([1, 5, 15, 30, 45, 60, 90, 180]),
            "taxa_adm": round(random.uniform(0.35, 0.95), 2),
            "aberto_captacao": random.random() > 0.30,
            "resgate_pct_pl_semana": round(f["semanal"] / pl, 4) if pl > 0 else 0.0,
        })

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(fundos, fh, ensure_ascii=False)
    print(f"OK — {len(fundos)} fundos gravados em {OUT} (a partir de {caminho.name})")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else None)
