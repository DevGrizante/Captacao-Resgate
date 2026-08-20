"""
Dispara a primeira carga de dados antes de o navegador abrir.

POR QUE ISTO VIROU UM SCRIPT

    O `Iniciar.bat` fazia isso com um `python -c` de uma linha, chamando
    /api/dashboard direto. Assim que o painel ganhou senha, essa chamada passou
    a levar 401 e o aquecimento parou de funcionar em silêncio — o script dizia
    "não consegui pré-carregar" e o usuário voltava a esperar nove minutos na
    frente da tela.

    Aqui ele faz login primeiro, como uma pessoa faria, e só então pede os
    dados. Com `PAINEL_SENHA` vazio, pula o login e vai direto.

POR QUE NÃO ABRIR UMA EXCEÇÃO PARA O LOCALHOST

    Seria mais simples liberar 127.0.0.1 do portão. Mas em servidor a API
    também escuta só em 127.0.0.1 — quem fala com a internet é o proxy — então
    a exceção valeria para todo mundo que chegasse pelo proxy, ou seja, para
    todos. Fazer login de verdade custa três linhas e não abre nada.

    python aquecer.py [porta]
"""
from __future__ import annotations

import http.cookiejar
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings  # noqa: E402

PORTA = sys.argv[1] if len(sys.argv) > 1 else "8000"
BASE = f"http://127.0.0.1:{PORTA}"

# Sem teto curto de propósito: com o cache da CVM vazio a carga passa de nove
# minutos. Cortar no meio não economiza nada — o trabalho continua no servidor
# e quem esperaria seria o usuário, já com a tela aberta e sem saber por quê.
TIMEOUT = 1800


def main() -> int:
    navegador = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
    )

    if settings.PAINEL_SENHA:
        corpo = urllib.parse.urlencode({"senha": settings.PAINEL_SENHA, "proximo": "/"}).encode()
        try:
            navegador.open(f"{BASE}/login", data=corpo, timeout=30)
        except urllib.error.HTTPError as e:
            # 3xx é sucesso aqui: o login responde com redirecionamento.
            if e.code >= 400:
                print(f"  login recusado ({e.code}) — confira PAINEL_SENHA no .env")
                return 1
        except Exception as e:  # noqa: BLE001
            print(f"  não consegui fazer login: {e}")
            return 1

    inicio = time.time()
    rota = "/api/dashboard?janela=diaria&indexador=incentivada&abertos=false"
    try:
        with navegador.open(BASE + rota, timeout=TIMEOUT) as r:
            lidos = len(r.read())
    except Exception as e:  # noqa: BLE001
        print(f"  a carga falhou: {e}")
        return 1

    print(f"  bases carregadas em {time.time() - inicio:.0f}s ({lidos / 1024:.0f} KB).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
