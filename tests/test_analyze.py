from __future__ import annotations

import httpx
import pytest

from analise_legislativa.analyze import InternalAnalyzer, analyze, instructions_for, validate
from analise_legislativa.common import read_json, write_json


def analysis(eid: str, **changes) -> dict:
    base = {
        "summary": "Pretende-se suprimir o § 2º-F do art. 1º, que exige verificar a integridade documental antes da decisão.",
        "summary_evidence_ids": [eid],
        "material_effect": "O texto deixaria de exigir a verificação da integridade documental antes da decisão.",
        "material_effect_evidence_ids": [eid],
        "claims": [{"text": "O § 2º-F exige verificação da integridade documental.", "evidence_ids": [eid]}],
        "change_kind": "supressao_pretendida",
        "assessment": {"category": "merito", "evidence_ids": [eid],
                       "justification": "O substitutivo deixaria de exigir a verificação, diferentemente do texto atual, que a exige."},
        "uncertainties": [],
    }
    base.update(changes)
    return base


def write_result(workspace, package, body: dict) -> None:
    case = package["cases"][0]
    write_json(workspace / "results.json", {"schema_version": "1.0", "run_id": package["run_id"], "results": [{
        "case_id": case["case_id"], "status": "pending_review", "analysis": body,
        "evidence_ids": [e["evidence_id"] for e in case["evidence"]], "warnings": []}]})


def dispositivo(package) -> str:
    return next(e["evidence_id"] for e in package["cases"][0]["evidence"] if e["kind"] == "dispositivo")


def test_analyze_requires_internal_endpoint(prepared):
    workspace, _ = prepared
    with pytest.raises(ValueError, match="ANALYZER_URL"):
        analyze(workspace, url="")
    with pytest.raises(ValueError, match="interno"):
        InternalAnalyzer("https://api.example.org/analyze")
    assert InternalAnalyzer("http://analisador.interno/analyze").url


def test_analysis_checkpoint_retry_and_clean_validation(monkeypatch, prepared):
    workspace, package = prepared
    case = package["cases"][0]
    cited = [x["evidence_id"] for x in case["evidence"]]
    eid = dispositivo(package)
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(429, headers={"Retry-After": "0"})
        payload = request.read().decode("utf-8")
        assert "art. 161, I" in payload and "supressao_pretendida" in payload
        return httpx.Response(200, json={"schema_version": "1.0", "analysis": analysis(eid), "evidence_ids": cited})

    monkeypatch.setattr("analise_legislativa.analyze.time.sleep", lambda _: None)
    adapter = InternalAnalyzer("http://localhost:7000/analyze", client=httpx.Client(transport=httpx.MockTransport(handler)))
    output = analyze(workspace, adapter=adapter)
    assert len(calls) == 2 and output["results"][0]["status"] == "pending_review"
    analyze(workspace, adapter=adapter)
    assert len(calls) == 2
    report = validate(workspace)
    assert report["counts"]["pending_review"] == 1 and report["requires_human_review"] is True
    assert read_json(workspace / "results.json")["results"][0]["warnings"] == []


@pytest.mark.parametrize("changes, expected", [
    ({"summary": "Prazo de 999 dias.", "summary_evidence_ids": ["{eid}"]}, "número 999"),
    ({"claims": [{"text": "O prazo seria de 40 dias.", "evidence_ids": ["inexistente"]}]}, "afirmação 1: citação aponta"),
    ({"change_kind": "inclusao_emenda"}, "Tipo de mudança"),
    ({"assessment": None}, "Veredito de substancialidade ausente"),
    ({"uncertainties": ["O texto do substitutivo pode ter mudado."]}, "incertezas"),
])
def test_validation_flags_unsupported_output(prepared, changes, expected):
    workspace, package = prepared
    eid = dispositivo(package)
    changes = {k: ([x.replace("{eid}", eid) for x in v] if k.endswith("_ids") else v) for k, v in changes.items()}
    write_result(workspace, package, analysis(eid, **changes))
    assert validate(workspace)["counts"]["review_required"] == 1
    assert any(expected in w for w in read_json(workspace / "results.json")["results"][0]["warnings"])


def test_notorious_norms_do_not_trigger_the_number_check(prepared):
    workspace, package = prepared
    eid = dispositivo(package)
    justification = ("A renúncia criada viria sem a estimativa exigida pelo art. 113 do ADCT e sem a compensação "
                     "exigida pelo art. 14 da LRF (LC 101/2000), conforme o RICD, art. 162, IV.")
    write_result(workspace, package, analysis(eid, assessment={"category": "substancial_ricd",
                                                                "justification": justification, "evidence_ids": [eid]}))
    assert validate(workspace)["counts"]["pending_review"] == 1


def test_ricd_categories_apply_only_to_destaques(example_lot):
    workspace, cases, package = example_lot
    emenda = cases["local:emp-1"]
    eid = next(e["evidence_id"] for e in emenda["evidence"] if e["kind"] == "requerimento_ou_emenda")
    body = analysis(eid, change_kind="nova_redacao", summary="Restringe a exigência de laudo técnico.",
                    material_effect="O laudo técnico deixaria de ser exigido para todos os documentos.",
                    claims=[{"text": "A emenda restringe o laudo técnico.", "evidence_ids": [eid]}],
                    assessment={"category": "substancial_ricd", "justification": "Restringe a exigência.", "evidence_ids": [eid]})
    results = []
    for case in package["cases"]:
        result = {"case_id": case["case_id"], "status": "failed", "analysis": None, "evidence_ids": [], "warnings": []}
        if case is emenda:
            result = {"case_id": case["case_id"], "status": "pending_review", "analysis": body,
                      "evidence_ids": [eid], "warnings": []}
        results.append(result)
    write_json(workspace / "results.json", {"schema_version": "1.0", "run_id": package["run_id"], "results": results})
    validate(workspace)
    [checked] = [r for r in read_json(workspace / "results.json")["results"] if r["case_id"] == emenda["case_id"]]
    assert any("aplicam-se só a destaques" in w for w in checked["warnings"])


def test_instructions_follow_the_mode():
    dvs = instructions_for({"mode": "dvs"})
    assert "art. 161, I" in dvs and "art. 162, VI" in dvs and "assessment" in dvs and "art. 113 do ADCT" in dvs
    amendment = instructions_for({"mode": "destaque_emenda"})
    assert "Pretende-se aprovar a Emenda N" in amendment and "emenda_vinculada" in amendment
    redaction = instructions_for({"mode": "emenda_redacao"})
    assert "assessment = null" in redaction and "Fecho orçamentário" not in redaction
