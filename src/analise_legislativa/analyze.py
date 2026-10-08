"""Contrato do serviço interno de análise, checkpoint e validação de evidências."""

from __future__ import annotations

import ipaddress
import logging
import os
import re
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from . import SCHEMA_VERSION, VERSION
from .common import check_schema, read_json, sha256_file, stable_hash, write_json
from .extract import extract, segment_at

LOG = logging.getLogger(__name__)

# Regras de redação e de conteúdo do fluxo original, enviadas ao serviço junto com cada caso.
BASE_RULES = """Você é consultor legislativo da Câmara dos Deputados e escreve para a Presidência da Mesa, que lê o texto durante a votação.
- summary: parágrafo único de 55 a 75 palavras, em prosa corrida. A 1ª frase diz o efeito material com verbo concreto (institui, eleva, reduz, autoriza, proíbe, condiciona) e os dados exatos das evidências; depois, em 1 ou 2 frases, a operação normativa e o dispositivo atingido. Frases de até 25 palavras, em ordem direta, numerais em algarismos. Não repita o cabeçalho (tipo, número e autor do objeto).
- Diga o que a regra manda, permite ou proíbe. São proibidas fórmulas vazias ("a regra nele prevista", "o referido dispositivo", "sua alteração") e metalinguagem processual ("operação legislativa", "texto normativo final").
- Não fabrique: todo número, valor, percentual, prazo, setor e dispositivo citado precisa constar das evidências. Sem o teor necessário, descreva só o verificável, aceite um parágrafo mais curto e registre a lacuna em uncertainties.
- Texto-alvo: só atribua a incidência ao substitutivo quando target.text_role for "substitutivo"; nomeie-o com sigla, número e data quando constarem das evidências ("o substitutivo do relator (SBT 1, de 12/05/2026)"). Comissões só por sigla.
- Não afirme aprovação, rejeição, inconstitucionalidade, prejudicialidade nem vigência futura como fato.
- Rastreabilidade: cada afirmação factual vai em claims com os evidence_ids que a sustentam; summary_evidence_ids, material_effect_evidence_ids e evidence_ids são obrigatórios."""

MODE_RULES = {
    "dvs": """Objeto: destaque para votação em separado de parte da proposição (RICD, art. 161, I). Pelo art. 162, VI, a parte destacada só integra o texto se for aprovada; o efeito pretendido é retirá-la. Comece por "Pretende-se suprimir o [dispositivo], retirando do texto a regra que [conteúdo concreto]" e diga o que continua valendo sem ela. Não trate DVS como inclusão de emenda. Cite o dispositivo literal (kind=dispositivo) e, quando houver, a expressão destacada (kind=expressao). change_kind = supressao_pretendida.""",
    "destaque_emenda": """Objeto: destaque de emenda (RICD, art. 161, II); a emenda só integra o texto se for aprovada. Comece por "Pretende-se aprovar a Emenda N, que [efeito concreto]". A fonte primária do efeito é o texto integral da emenda (kind=emenda_vinculada); compare-o com o dispositivo atual (kind=dispositivo), com os números exatos (50% para 100%). Não descreva como supressão nem use "destacado para votação em separado" para a emenda. Sem a emenda nas evidências, não deduza seu teor do contexto. change_kind = inclusao_emenda.""",
    "emenda": """Objeto: emenda (sem destaque). Diga o que a emenda faz no mérito e compare com a redação atual do dispositivo (kind=dispositivo). change_kind = a operação do comando: nova_redacao, acrescimo, supressao ou substituicao.""",
    "emenda_redacao": """Objeto: emenda de redação. Faça o antes/depois da redação com as expressões exatas e explique em 1 frase o ganho de clareza ou de técnica legislativa, sem discutir mérito, valores ou arrecadação. change_kind = ajuste_redacao e assessment = null.""",
    "indefinido": """A modalidade não foi identificada pelo parser. Descreva apenas o verificável, registre a incerteza e use change_kind = indeterminada.""",
}

ASSESSMENT_RULES = """assessment (veredito de substancialidade) — category, exatamente um valor:
- substancial_ricd: SOMENTE destaque (DTQ/DVS) vedado pelo art. 162, IV, do RICD: modifica substancialmente a proposição ou retira expressão cuja supressão inverte o sentido (suprimir um "não", uma vedação, uma ressalva). Emenda nunca recebe este valor.
- sem_ajuste_ricd: SOMENTE destaque cujo texto não possa ajustar-se à proposição ou não forme sentido completo (art. 162, V): supressão que deixa dispositivo truncado ou remissão órfã. Emenda nunca recebe este valor. Se couberem IV e V, use substancial_ricd.
- merito: alteração de mérito fora do art. 162, IV e V, inclusive toda emenda que altere o alcance material.
- pontual: ajusta detalhe sem alterar o alcance.
- nao: sem efeito normativo relevante.
justification: 2 a 3 frases que comparam com o texto-alvo nomeado, com o verbo da mudança no futuro do pretérito ("A MPV passaria a fixar...") e o contraste completo ("..., diferentemente do texto-base, que [regra atual]"). Se o texto-alvo não trata do ponto, diga isso.
Fecho orçamentário, só quando a mudança criar ou ampliar renúncia de receita ou despesa obrigatória: diga se o texto da emenda traz estimativa de impacto e medida de compensação. Ausentes, diga que viria sem a estimativa exigida pelo art. 113 do ADCT e sem a compensação exigida pelo art. 14 da LRF (LC 101/2000), e pare no fato técnico. Só afirme presença ou ausência com o texto integral da emenda nas evidências. Compensar crédito com outros tributos é forma de uso do benefício, não compensação fiscal. Nunca escreva que algo "é inconstitucional" ou "está prejudicado", nem anuncie o que a Presidência fará. Item sem dimensão orçamentária não menciona ADCT, LRF nem arrecadação."""

EXPECTED_CHANGE = {
    "dvs": {"supressao_pretendida"},
    "destaque_emenda": {"inclusao_emenda"},
    "emenda_redacao": {"ajuste_redacao"},
}
RICD_ONLY_FOR_DESTAQUES = {"substancial_ricd", "sem_ajuste_ricd"}
NUMERIC = re.compile(r"(?<!\w)\d+(?:[.,]\d+)*(?:%)?(?!\w)")
# Normas notórias citadas pelas próprias regras de redação; seus números não vêm das evidências.
NOTORIOUS_REFERENCES = re.compile(
    r"(?i)\bart\.?\s*113\s+do\s+ADCT\b|\bart\.?\s*14\s+da\s+(?:LRF|Lei\s+de\s+Responsabilidade\s+Fiscal)\b"
    r"|\b(?:LC|Lei\s+Complementar)\s*(?:n[.ºo°]*\s*)?101(?:/2000|,\s*de\s+(?:4\s+de\s+maio\s+de\s+)?2000)?\b"
    r"|\bRICD,?\s*art\.?\s*16[12](?:,\s*(?:inciso\s+)?[IVX]+)?\b|\bart\.?\s*16[12],?\s*(?:inciso\s+)?[IVX]*,?\s*do\s+RICD\b"
)


def instructions_for(case: dict[str, Any]) -> str:
    parts = [BASE_RULES, MODE_RULES.get(case["mode"], MODE_RULES["indefinido"])]
    if case["mode"] != "emenda_redacao":
        parts.append(ASSESSMENT_RULES)
    return "\n\n".join(parts)


def _internal_endpoint(url: str) -> bool:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme not in {"http", "https"} or not host:
        return False
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_private or ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host.endswith((".interno", ".internal", ".local"))


class InternalAnalyzer:
    """Cliente do serviço de análise. O endereço precisa ser da rede interna: o pipeline fala com um
    serviço que a DITEC controla, e é esse serviço que escolhe e chama o modelo de linguagem."""

    def __init__(self, url: str, token: str = "", client: httpx.Client | None = None):
        if not _internal_endpoint(url):
            raise ValueError("O endpoint de análise deve ter endereço interno ou localhost")
        self.url = url
        self.token = token
        self.client = client or httpx.Client(timeout=120.0, follow_redirects=False)
        self.owned = client is None

    def close(self) -> None:
        if self.owned:
            self.client.close()

    def analyze_case(self, case: dict[str, Any]) -> dict[str, Any]:
        payload = {"schema_version": SCHEMA_VERSION, "case": case, "instructions": instructions_for(case)}
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        for attempt in range(4):
            try:
                response = self.client.post(self.url, json=payload, headers=headers)
                if response.status_code in {429, 500, 502, 503, 504} and attempt < 3:
                    try:
                        wait = min(30.0, float(response.headers.get("Retry-After", "")))
                    except ValueError:
                        wait = min(8.0, 2.0 ** attempt)
                    LOG.warning("analyzer_retry case_id=%s status=%s attempt=%s", case["case_id"], response.status_code, attempt + 1)
                    time.sleep(max(0.0, wait))
                    continue
                response.raise_for_status()
                body = response.json()
                if not isinstance(body, dict):
                    raise ValueError("Resposta de análise deve ser objeto JSON")
                check_schema("analysis", body)
                return body
            except (httpx.TimeoutException, httpx.NetworkError):
                if attempt == 3:
                    raise
                LOG.warning("analyzer_retry case_id=%s network attempt=%s", case["case_id"], attempt + 1)
                time.sleep(min(8.0, 2.0 ** attempt))
        raise RuntimeError("Limite de novas tentativas do analisador")


def analyze(workspace: Path, *, url: str | None = None, token: str | None = None,
            adapter: InternalAnalyzer | None = None) -> dict[str, Any]:
    workspace = Path(workspace).resolve()
    package = read_json(workspace / "cases.json")
    check_schema("case", package)
    endpoint = url or os.getenv("ANALYZER_URL", "")
    if adapter is None and not endpoint:
        raise ValueError("Configure ANALYZER_URL para um serviço interno antes de executar analyze")
    active = adapter or InternalAnalyzer(endpoint, token if token is not None else os.getenv("ANALYZER_TOKEN", ""))
    identity = stable_hash([VERSION, package["run_id"], package["cases"], active.url])
    checkpoint_path = workspace / ".analysis.checkpoint.json"
    saved: dict[str, Any] = {}
    if checkpoint_path.exists():
        checkpoint = read_json(checkpoint_path)
        if checkpoint.get("identity") == identity:
            saved = checkpoint.get("results", {})
    results = []
    try:
        for case in package["cases"]:
            case_id = case["case_id"]
            if case_id in saved and saved[case_id].get("status") != "failed":
                results.append(saved[case_id])
                continue
            try:
                body = active.analyze_case(case)
                result = {"case_id": case_id, "status": "pending_review", "analysis": body["analysis"],
                          "evidence_ids": body["evidence_ids"], "warnings": list(case["warnings"])}
            except (httpx.HTTPError, ValueError, RuntimeError) as exc:
                LOG.error("analyzer_failed case_id=%s type=%s", case_id, type(exc).__name__)
                result = {"case_id": case_id, "status": "failed", "analysis": None,
                          "evidence_ids": [], "warnings": list(case["warnings"]) + [f"Análise falhou: {type(exc).__name__}: {exc}"]}
            saved[case_id] = result
            results.append(result)
            write_json(checkpoint_path, {"schema_version": SCHEMA_VERSION, "identity": identity,
                                         "results": saved})
    finally:
        if adapter is None:
            active.close()
    output = {"schema_version": SCHEMA_VERSION, "run_id": package["run_id"], "results": results}
    check_schema("result", output)
    write_json(workspace / "results.json", output)
    return output


def _unsupported_numbers(statement: str, cited: str) -> list[str]:
    text = NOTORIOUS_REFERENCES.sub(" ", statement)
    return [n for n in sorted(set(NUMERIC.findall(text))) if not re.search(rf"(?<!\w){re.escape(n)}(?!\w)", cited)]


def validate(workspace: Path) -> dict[str, Any]:
    workspace = Path(workspace).resolve()
    manifest = read_json(workspace / "manifest.json")
    cases = read_json(workspace / "cases.json")
    output = read_json(workspace / "results.json")
    for name, obj in (("manifest", manifest), ("case", cases), ("result", output)):
        check_schema(name, obj)
    if len({manifest["run_id"], cases["run_id"], output["run_id"]}) != 1:
        raise ValueError("Arquivos de execução pertencem a rodadas diferentes")
    case_by_id = {x["case_id"]: x for x in cases["cases"]}
    if len(case_by_id) != len(cases["cases"]):
        raise ValueError("case_id duplicado")
    if {x["case_id"] for x in output["results"]} != set(case_by_id):
        raise ValueError("Conjunto de resultados difere dos casos")
    document_by_id = {x["source_id"]: x for x in manifest["documents"]}
    extracted_cache = {}
    count = {"pending_review": 0, "review_required": 0, "failed": 0}
    for result in output["results"]:
        case = case_by_id[result["case_id"]]
        issues = list(case["warnings"])
        evidences = {x["evidence_id"]: x for x in case["evidence"]}
        for item in case["evidence"]:
            doc = document_by_id.get(item["source_id"])
            if not doc:
                issues.append(f"Fonte de evidência não existe: {item['source_id']}")
                continue
            if item["source_id"] not in extracted_cache:
                path = (workspace / doc["path"]).resolve()
                if not path.is_relative_to(workspace) or sha256_file(path) != doc["sha256"]:
                    raise ValueError(f"Integridade da fonte divergente: {item['source_id']}")
                extracted_cache[item["source_id"]] = extract(path)
            original = extracted_cache[item["source_id"]]
            if original.text[item["start"]:item["end"]] != item["quote"]:
                issues.append(f"Trecho de evidência não corresponde à fonte: {item['evidence_id']}")
            segment = segment_at(original, item["start"])
            if segment and (segment["page"] != item["page"] or segment["block"] != item["block"]):
                issues.append(f"Localização da evidência divergente: {item['evidence_id']}")
        if result["status"] == "failed":
            result["warnings"] = list(dict.fromkeys(issues + [w for w in result["warnings"] if w.startswith("Análise falhou")]))
            count["failed"] += 1
            continue
        if not result["evidence_ids"]:
            issues.append("Análise sem citação de evidências")
        if set(result["evidence_ids"]) - set(evidences):
            issues.append("Análise cita evidência inexistente")
        analysis = result["analysis"]
        assessment = analysis["assessment"]
        statements = [("síntese", analysis["summary"], analysis["summary_evidence_ids"]),
                      ("efeito", analysis["material_effect"], analysis["material_effect_evidence_ids"])]
        statements += [(f"afirmação {i}", claim["text"], claim["evidence_ids"])
                       for i, claim in enumerate(analysis["claims"], start=1)]
        if assessment:
            statements.append(("veredito", assessment["justification"], assessment["evidence_ids"]))
        for label, statement, identifiers in statements:
            if set(identifiers) - set(result["evidence_ids"]):
                issues.append(f"{label}: citação não consta da lista geral de evidências")
            if set(identifiers) - set(evidences):
                issues.append(f"{label}: citação aponta para evidência inexistente")
            cited = " ".join(evidences[x]["quote"] for x in identifiers if x in evidences)
            for number in _unsupported_numbers(statement, cited):
                issues.append(f"{label}: número {number} não consta das evidências citadas")
        kinds = {x["kind"] for x in case["evidence"]}
        cited_kinds = {evidences[x]["kind"] for x in result["evidence_ids"] if x in evidences}
        if case["mode"] == "destaque_emenda" and "emenda_vinculada" in kinds and "emenda_vinculada" not in cited_kinds:
            issues.append("Destaque de emenda sem citação do teor da emenda vinculada")
        if case["mode"] == "dvs" and "dispositivo" in kinds and "dispositivo" not in cited_kinds:
            issues.append("DVS sem citação do dispositivo literal localizado")
        allowed = EXPECTED_CHANGE.get(case["mode"]) or (
            {case["operation"]} | {t["operation"] for t in case["additional_targets"] if t["operation"]})
        if analysis["change_kind"] not in allowed | {"indeterminada"}:
            issues.append(f"Tipo de mudança da análise ({analysis['change_kind']}) diverge do objeto identificado")
        if case["mode"] == "emenda_redacao" and assessment:
            issues.append("Emenda de redação não recebe veredito de substancialidade")
        elif case["mode"] != "emenda_redacao" and not assessment:
            issues.append("Veredito de substancialidade ausente")
        elif assessment and case["role"] == "emenda" and assessment["category"] in RICD_ONLY_FOR_DESTAQUES:
            issues.append("Categorias do art. 162, IV e V, do RICD aplicam-se só a destaques")
        if analysis["uncertainties"]:
            issues.append("Analisador registrou incertezas")
        result["warnings"] = list(dict.fromkeys(issues))
        result["status"] = "review_required" if issues else "pending_review"
        count[result["status"]] += 1
    check_schema("result", output)
    write_json(workspace / "results.json", output)
    report = {"schema_version": SCHEMA_VERSION, "run_id": output["run_id"], "counts": count,
              "requires_human_review": True}
    write_json(workspace / "validation.json", report)
    return report
