"""Serviço de análise de referência: implementa `POST /analyze` chamando um modelo de linguagem.

Não faz parte do núcleo. Mostra, de ponta a ponta, o que o fluxo original faz na etapa semântica:

1. recebe o caso preparado pelo parser (alvo, operação, evidências literais com IDs) e as instruções;
2. monta duas mensagens: sistema (papel e formato) e usuário (instruções + caso em JSON);
3. pede resposta estruturada (JSON Schema estrito) a um endpoint compatível com Chat Completions —
   modelo hospedado na rede interna (vLLM, Ollama, LiteLLM) ou serviço contratado (Azure OpenAI etc.);
4. valida a resposta contra `analysis.schema.json` antes de devolvê-la ao pipeline.

Variáveis de ambiente:
  LLM_BASE_URL    base da API do modelo, ex.: http://llm.interno/v1
  LLM_MODEL       nome do modelo no provedor
  LLM_API_KEY     chave do provedor (opcional em servidor interno)
  LLM_EXTRA_JSON  parâmetros extras do provedor, ex.: {"temperature": 0}
  ANALYZER_TOKEN  se definido, exige "Authorization: Bearer <token>" do pipeline
  ANALYZER_PORT   porta local (padrão 8080)

Uso:
  python examples/analisador_referencia.py
  ANALYZER_URL=http://localhost:8080/analyze analise-legislativa analyze --workspace work/demo
"""

from __future__ import annotations

import copy
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import httpx

from analise_legislativa.common import check_schema, schema

SYSTEM = ("Você é consultor legislativo da Câmara dos Deputados. Responda somente com JSON válido no schema "
          "pedido, em português do Brasil, sem expor raciocínio.")
UNSUPPORTED = {"minLength", "minItems", "uniqueItems", "$id", "$schema", "title", "description"}


def model_schema() -> dict[str, Any]:
    """analysis.schema.json no subconjunto aceito pelo modo estrito dos provedores; schema_version fica de fora
    e é acrescentado aqui. A validação completa acontece depois, contra o schema original."""
    def strip(node: Any) -> Any:
        if isinstance(node, dict):
            return {k: strip(v) for k, v in node.items() if k not in UNSUPPORTED}
        if isinstance(node, list):
            return [strip(v) for v in node]
        return node

    full = copy.deepcopy(schema("analysis"))
    return strip({"type": "object", "additionalProperties": False, "required": ["analysis", "evidence_ids"],
                  "properties": {"analysis": full["properties"]["analysis"], "evidence_ids": full["properties"]["evidence_ids"]},
                  "$defs": full["$defs"]})


def build_messages(payload: dict[str, Any]) -> list[dict[str, str]]:
    case = json.dumps(payload["case"], ensure_ascii=False, indent=1)
    return [{"role": "system", "content": SYSTEM},
            {"role": "user", "content": f"{payload['instructions']}\n\nCASO (JSON):\n{case}"}]


def analyze_payload(payload: dict[str, Any], client: httpx.Client | None = None) -> dict[str, Any]:
    owned = client is None
    client = client or httpx.Client(timeout=110.0)
    try:
        request = {"model": os.environ["LLM_MODEL"], "messages": build_messages(payload),
                   "response_format": {"type": "json_schema",
                                       "json_schema": {"name": "analise_legislativa", "strict": True, "schema": model_schema()}},
                   **json.loads(os.getenv("LLM_EXTRA_JSON") or "{}")}
        headers = {"Authorization": f"Bearer {os.environ['LLM_API_KEY']}"} if os.getenv("LLM_API_KEY") else {}
        response = client.post(os.environ["LLM_BASE_URL"].rstrip("/") + "/chat/completions", json=request, headers=headers)
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]
        body = {"schema_version": "1.0", **json.loads(content)}
        check_schema("analysis", body)
        return body
    finally:
        if owned:
            client.close()


class Handler(BaseHTTPRequestHandler):
    client: httpx.Client | None = None

    def do_POST(self) -> None:  # noqa: N802 (nome exigido pelo http.server)
        token = os.getenv("ANALYZER_TOKEN", "")
        if self.path != "/analyze":
            return self._reply(404, {"error": "use POST /analyze"})
        if token and self.headers.get("Authorization") != f"Bearer {token}":
            return self._reply(401, {"error": "token inválido"})
        try:
            payload = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)))
            if payload.get("schema_version") != "1.0" or "case" not in payload or "instructions" not in payload:
                return self._reply(400, {"error": "pacote fora do contrato"})
        except (ValueError, TypeError):
            return self._reply(400, {"error": "JSON inválido"})
        try:
            self._reply(200, analyze_payload(payload, self.client))
        except httpx.HTTPStatusError as exc:
            self._reply(429 if exc.response.status_code == 429 else 502, {"error": f"modelo: HTTP {exc.response.status_code}"},
                        retry_after=exc.response.headers.get("Retry-After"))
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            self._reply(502, {"error": f"{type(exc).__name__}: {exc}"})

    def _reply(self, status: int, body: dict[str, Any], *, retry_after: str | None = None) -> None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        if retry_after:
            self.send_header("Retry-After", retry_after)
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt: str, *args: Any) -> None:  # sem teor dos documentos no log
        sys.stderr.write(f"analisador {self.address_string()} {fmt % args}\n")


def serve(port: int = 8080, client: httpx.Client | None = None) -> ThreadingHTTPServer:
    Handler.client = client
    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


if __name__ == "__main__":
    for name in ("LLM_BASE_URL", "LLM_MODEL"):
        if not os.getenv(name):
            sys.exit(f"Defina {name} (veja o cabeçalho deste arquivo)")
    server = serve(int(os.getenv("ANALYZER_PORT", "8080")))
    print(f"POST http://127.0.0.1:{server.server_port}/analyze")
    server.serve_forever()
