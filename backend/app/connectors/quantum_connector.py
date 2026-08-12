"""
Conector Quantum Axis — STUB / ENRIQUECEDOR.

O Quantum não é usado como fonte primária de fluxo (isso vem da CVM), mas como
ENRIQUECEDOR: dado um CNPJ, devolve composição por indexador, duration,
cotização, taxa de administração e status de captação — que são exatamente os
campos que a CVM não entrega prontos.

>>> COMO ATIVAR (quando tiver o contrato/endpoint):
    1. Defina no .env:
        QUANTUM_ENABLED=true
        QUANTUM_BASE_URL=https://... (endpoint real do QWS / link de dados)
        QUANTUM_TOKEN=seu_token
    2. Implemente `_chamar_api` com o formato real de request/response do seu
       contrato. O portal developers.quantumaxis.com.br documenta os endpoints
       (ex.: "Consolidação de Carteiras"). O restante do pipeline já consome
       o que este método devolver.

Enquanto QUANTUM_ENABLED=false, `enriquecer` é um no-op: devolve o fundo
inalterado. Assim o app roda hoje com CVM+mock e ganha o Quantum sem tocar
em mais nada.
"""
from __future__ import annotations

from typing import Optional

import requests

from app.config import settings


class QuantumEnricher:
    name = "quantum"

    def __init__(self) -> None:
        self.enabled = settings.QUANTUM_ENABLED
        self.base = settings.QUANTUM_BASE_URL.rstrip("/")
        self.token = settings.QUANTUM_TOKEN

    def disponivel(self) -> bool:
        return self.enabled and bool(self.token)

    # ---------- API pública do enriquecedor ----------
    def enriquecer(self, fundo: dict) -> dict:
        """Preenche composição e métricas analíticas do fundo, se possível.

        Retorna o próprio dict (mutado). Campos preenchidos:
            pct_lf, pct_ipca, pct_cdi,
            duration, cotizacao_resgate, taxa_adm, aberto_captacao
        """
        if not self.disponivel():
            return fundo  # no-op: Quantum desligado

        dados = self._chamar_api(fundo["cnpj"])
        if dados is None:
            return fundo

        fundo["pct_lf"] = dados.get("pct_lf", fundo.get("pct_lf", 0.0))
        fundo["pct_ipca"] = dados.get("pct_ipca", fundo.get("pct_ipca", 0.0))
        fundo["pct_cdi"] = dados.get("pct_cdi", fundo.get("pct_cdi", 0.0))
        fundo["duration"] = dados.get("duration", fundo.get("duration"))
        fundo["cotizacao_resgate"] = dados.get("cotizacao_resgate", fundo.get("cotizacao_resgate"))
        fundo["taxa_adm"] = dados.get("taxa_adm", fundo.get("taxa_adm"))
        fundo["aberto_captacao"] = dados.get("aberto_captacao", fundo.get("aberto_captacao", True))
        return fundo

    def enriquecer_lote(self, fundos: list[dict]) -> list[dict]:
        """Enriquece uma lista. Sobrescreva com chamada em batch quando o
        endpoint suportar (mais eficiente que 1 request por CNPJ)."""
        if not self.disponivel():
            return fundos
        return [self.enriquecer(f) for f in fundos]

    # ---------- integração real (implemente aqui) ----------
    def _chamar_api(self, cnpj: str) -> Optional[dict]:
        import time
        import logging
        
        logger = logging.getLogger("quantum_connector")
        
        # Rate limit simples: 60 req / min -> 1 req / s. 
        # Em produção, uma lib como 'ratelimit' ou 'tenacity' seria melhor
        time.sleep(1.0)
        
        url = f"{self.base}/carteira/consolidada"
        params = {"cnpj": cnpj}
        headers = {"Authorization": f"Bearer {self.token}", "Accept": "application/json"}
        
        tentativas = 3
        for attempt in range(tentativas):
            try:
                resp = requests.get(url, params=params, headers=headers, timeout=30)
                
                # Resposta 429 = Too Many Requests
                if resp.status_code == 429:
                    logger.warning(f"Rate limit atingido no Quantum para o CNPJ {cnpj}. Tentativa {attempt+1}/{tentativas}")
                    time.sleep(2.0 * (attempt + 1))
                    continue
                    
                resp.raise_for_status()
                raw = resp.json()

                # Mapeia o JSON do Quantum para o nosso formato interno
                # Ajuste as chaves conforme o payload documentado no portal Developers Quantum Axis
                return {
                    "pct_lf": _peso(raw, "LETRA_FINANCEIRA"),
                    "pct_ipca": _peso(raw, "IPCA"),
                    "pct_cdi": _peso(raw, "CDI"),
                    "duration": raw.get("duration"),
                    "cotizacao_resgate": raw.get("prazo_cotizacao_resgate"),
                    "taxa_adm": raw.get("taxa_administracao"),
                    "aberto_captacao": raw.get("aberto_para_captacao", True),
                }
                
            except requests.exceptions.Timeout:
                logger.error(f"Timeout ao chamar Quantum para {cnpj} na tentativa {attempt+1}")
            except requests.exceptions.HTTPError as e:
                logger.error(f"HTTPError {e.response.status_code} ao chamar Quantum para {cnpj}: {e.response.text}")
                if e.response.status_code in (401, 403):
                    logger.critical("Credenciais do Quantum inválidas ou sem permissão.")
                    break # Sem sentido tentar de novo se o token falhou
            except ValueError:
                logger.error(f"Payload inesperado (não é JSON) da API Quantum para {cnpj}")
            except Exception as e:
                logger.exception(f"Erro inesperado no Quantum p/ {cnpj}: {e}")
                
            # Backoff para erros que permitam retry
            time.sleep(1.0 * (attempt + 1))

        return None


def _peso(raw: dict, chave: str) -> float:
    """Helper de exemplo p/ extrair peso de uma classe do payload do Quantum.
    Ajuste conforme a estrutura real do seu retorno."""
    try:
        comp = raw.get("composicao", {})
        return float(comp.get(chave, 0.0))
    except Exception:  # noqa: BLE001
        return 0.0
