"""Integração: pipeline -> analisador de referência (HTTP local) -> modelo simulado -> validação."""

from __future__ import annotations

import json
import threading

import httpx
from helpers import load_example

from analise_legislativa.analyze import InternalAnalyzer, analyze, validate


def reference_module():
    return load_example("analisador_referencia")


def fake_model(requests: list) -> httpx.Client:
    """Modelo simulado: responde no schema pedido, citando as evidências que recebeu."""
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.read())
        requests.append(body)
        case = json.loads(body["messages"][1]["content"].split("CASO (JSON):\n", 1)[1])
        ids = [e["evidence_id"] for e in case["evidence"]]
        analysis = {
            "summary": "Pretende-se alterar a regra descrita nas evidências, sem acrescentar dados.",
            "summary_evidence_ids": ids, "material_effect": "Altera a regra citada.", "material_effect_evidence_ids": ids,
            "claims": [{"text": "O objeto altera a regra citada.", "evidence_ids": ids}],
            "change_kind": case["operation"],
            "assessment": None if case["mode"] == "emenda_redacao" else
            {"category": "merito" if case["role"] == "emenda" else "pontual", "evidence_ids": ids,
             "justification": "O texto passaria a ter outra regra, diferentemente do texto atual."},
            "uncertainties": [],
        }
        content = json.dumps({"analysis": analysis, "evidence_ids": ids}, ensure_ascii=False)
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_reference_analyzer_end_to_end(monkeypatch, example_lot):
    workspace, _cases, package = example_lot
    module = reference_module()
    monkeypatch.setenv("LLM_BASE_URL", "http://llm.interno/v1")
    monkeypatch.setenv("LLM_MODEL", "modelo-homologado")
    requests: list = []
    server = module.serve(0, client=fake_model(requests))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        adapter = InternalAnalyzer(f"http://127.0.0.1:{server.server_port}/analyze")
        output = analyze(workspace, adapter=adapter)
        adapter.close()
    finally:
        server.shutdown()
        server.server_close()
    assert [r["status"] for r in output["results"]] == ["pending_review"] * len(package["cases"])
    assert validate(workspace)["counts"]["pending_review"] == len(package["cases"])
    first = requests[0]
    assert first["model"] == "modelo-homologado" and first["response_format"]["json_schema"]["strict"] is True
    assert "minLength" not in json.dumps(first["response_format"]) and "Presidência da Mesa" in first["messages"][1]["content"]


def test_reference_analyzer_refuses_requests_outside_the_contract(monkeypatch):
    module = reference_module()
    monkeypatch.setenv("ANALYZER_TOKEN", "segredo")
    server = module.serve(0, client=fake_model([]))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_port}/analyze"
        assert httpx.post(url, json={}).status_code == 401
        assert httpx.post(url, json={"schema_version": "9"}, headers={"Authorization": "Bearer segredo"}).status_code == 400
    finally:
        server.shutdown()
        server.server_close()
