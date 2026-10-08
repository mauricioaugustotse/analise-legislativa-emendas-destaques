from __future__ import annotations

from copy import deepcopy

from analise_legislativa.common import read_json, write_json
from analise_legislativa.extract import _assemble
from analise_legislativa.parser import (
    _request_clause, amendment_devices, classify_request, find_device, link_amendment, paragraph_marker, prepare,
)
from analise_legislativa.references import Device, resolve

REQUEST = ("CÂMARA DOS DEPUTADOS\nDESTAQUE PARA VOTAÇÃO EM SEPARADO - PL 100/2026\nRequeiro a V. Exa., nos termos do "
           "art. 161, {basis}, do Regimento Interno da Câmara dos Deputados, destaque para {obj} apresentado à(ao) "
           "PL 100/2026\nSala das Sessões, ____/_____/_____.\nLÍDER FICTÍCIO\nDTQ 1 => PL 100/2026")


def request(basis: str, obj: str) -> str:
    return REQUEST.format(basis=basis, obj=obj)


def extracted(text: str, page: int = 1):
    return _assemble([(page, None, text)], "test")


def test_request_clause_skips_header_ricd_reference_and_footer():
    text = request("I", "votação em separado do(a) supressão do art. 5º, § 2º, do substitutivo")
    clause = _request_clause(text)
    assert clause.startswith("supressão do art. 5º, § 2º, do substitutivo")
    assert "161" not in clause and "Sala das" not in clause
    assert [(d.article, d.paragraph) for d in resolve(clause)] == [("5", "2")]


def test_ricd_basis_decides_the_mode():
    dvs = request("I", "votação em separado do(a) supressão do art. 5º, § 2º, do substitutivo")
    assert classify_request(_request_clause(dvs), dvs)[:3] == ("dvs", "supressao_pretendida", "art. 161, I")
    amendment = request("II", "EMP 1")
    assert classify_request(_request_clause(amendment), amendment)[:3] == ("destaque_emenda", "inclusao_emenda", "art. 161, II")
    autonomous = request("III", "votação em separado do(a) art. 7º")
    mode, operation, _basis, warnings = classify_request(_request_clause(autonomous), autonomous)
    assert (mode, operation) == ("indefinido", "indeterminada") and warnings


def test_request_citing_amendment_is_not_dvs_even_without_the_amendment():
    text = request("I", "votação em separado do(a) Emenda nº 96")
    mode, operation, _basis, warnings = classify_request(_request_clause(text), text)
    assert (mode, operation) == ("destaque_emenda", "inclusao_emenda")
    assert any("art. 161, I" in w for w in warnings)
    linked, link_warnings = link_amendment({"metadata": {}}, {"sigla": "", "number": 96}, [])
    assert linked is None and "não está no lote" in link_warnings[0]


def test_official_type_decides_when_the_request_omits_the_ricd_article():
    amendment = "DESTAQUE DE EMENDA - PL 100/2026\nRequeiro destaque para EMP 3."
    assert classify_request(_request_clause(amendment), amendment)[:2] == ("destaque_emenda", "inclusao_emenda")
    preference = "DESTAQUE DE PREFERÊNCIA - PL 100/2026\nRequeiro preferência para o substitutivo."
    assert classify_request(_request_clause(preference), preference)[0] == "indefinido"
    plain = "Requeiro destaque do art. 3º."
    assert classify_request(_request_clause(plain), plain, "Destaque para Votação em Separado")[0] == "dvs"


def test_emr_is_rapporteur_amendment_not_redaction(tmp_path):
    from analise_legislativa.collect import collect_local, role_for
    from helpers import document

    assert role_for("ERD") == role_for("EMR") == "emenda"
    assert role_for("REQ", "Requer destaque para votação em separado do art. 2º") == "destaque"
    assert role_for("REQ", "Requer audiência pública") == "apoio"
    source = tmp_path / "input"
    source.mkdir()
    document(source / "EMR-1.docx", ["EMENDA DO RELATOR", "Dê-se ao art. 4º a seguinte redação:", "“Art. 4º Nova regra de mérito.”"],
             meta={"source_id": "local:emr", "role": "emenda", "kind": "EMR", "number": 1})
    document(source / "PL.docx", ["Art. 4º Regra original do projeto."], meta={"source_id": "local:pl", "role": "texto_principal", "kind": "PL", "number": 1})
    collect_local(source, tmp_path / "ws")
    [case] = prepare(tmp_path / "ws")["cases"]
    assert (case["mode"], case["operation"]) == ("emenda", "nova_redacao")


def test_amendment_link_rules():
    docs = [{"role": "emenda", "kind": "EMP", "number": 4, "source_id": "camara:1"},
            {"role": "emenda", "kind": "EMC", "number": 4, "source_id": "camara:2"}]
    linked, warnings = link_amendment({"metadata": {}}, {"sigla": "", "number": 4}, docs)
    assert linked is None and "ambíguo" in warnings[0]
    assert link_amendment({"metadata": {}}, {"sigla": "EMC", "number": 4}, docs)[0] == "camara:2"
    explicit = {"metadata": {"vinculo": {"emenda": {"id": 1}}}}
    assert link_amendment(explicit, {"sigla": "", "number": 4}, docs)[0] == "camara:1"


def test_paragraph_marker_is_exact_and_inline_citation_is_not_boundary():
    text = ("Art. 1º Regra sintética.\n§ 2º-B Registra o recebimento.\n"
            "§ 2º-F Verifica a integridade do documento citado no inciso II do § 2º-B, antes da decisão.\n"
            "§ 2º-G Comunica a decisão.")
    [found] = find_device("fonte:1", extracted(text), Device(article="1", paragraph="2-F"))
    assert "antes da decisão" in found["quote"] and "§ 2º-G" not in found["quote"]
    assert found["page"] == 1 and found["quote"] == text[found["start"]:found["end"]]
    assert paragraph_marker("2").search("§ 2º-B") is None
    assert paragraph_marker("2-F").search("§ 2º-B") is None
    assert paragraph_marker("unico").search("Parágrafo único. Texto.")


def test_article_with_suffix_is_not_the_base_article():
    text = "Art. 144. Regra-base do artigo.\nIV - inciso do caput.\nArt. 144-A. Outro artigo.\nIV - inciso do artigo 144-A."
    [found] = find_device("fonte:1", extracted(text), Device(article="144", inciso="IV"))
    assert found["quote"] == "IV - inciso do caput."
    [suffixed] = find_device("fonte:1", extracted(text), Device(article="144-A", inciso="IV"))
    assert "144-A" in suffixed["quote"]


def test_prose_line_starting_with_article_is_not_a_heading():
    text = ("Art. 5º Regra verdadeira do artigo cinco.\n"
            "art. 5º da Constituição Federal, segundo a justificação, já protege o direito.")
    found = find_device("fonte:1", extracted(text), Device(article="5"))
    assert len(found) == 1 and "justificação" in found[0]["quote"]


def test_inciso_of_caput_stops_at_first_paragraph():
    text = "Art. 144. Caput.\nVII - guardas municipais.\n§ 1º Parágrafo seguinte.\n§ 8º-A Novo parágrafo:\nVII - outro inciso."
    [found] = find_device("fonte:1", extracted(text), Device(article="144", inciso="VII"))
    assert found["quote"] == "VII - guardas municipais."


def test_amended_norm_separates_proposition_article_from_altered_law():
    text = ("Art. 1º O Decreto-Lei nº 1.804, de 3 de setembro de 1980, passa a vigorar com as seguintes alterações:\n"
            "“Art. 1º ............................................\n"
            "§ 2º A tributação simplificada observará o regulamento.\n"
            "§ 2º-F O Poder Executivo poderá estabelecer limites quantitativos para a redução de alíquota.” (NR)\n"
            "Art. 2º Esta Medida Provisória entra em vigor na data de sua publicação.\n"
            "§ 2º Parágrafo da própria medida provisória, sem relação com o decreto-lei.")
    source = extracted(text)
    [law] = find_device("mpv", source, Device(article="1", paragraph="2-F", amended_norm="Decreto-Lei 1804/1980"))
    assert "limites quantitativos" in law["quote"]
    [own] = find_device("mpv", source, Device(article="1"))
    assert own["quote"].startswith("Art. 1º O Decreto-Lei")
    assert find_device("mpv", source, Device(article="1", paragraph="2", amended_norm="Lei 9363/1996")) == []


def test_amendment_commands_and_partial_redaction():
    text = ("EMENDA DE PLENÁRIO\nDê-se ao art. 6º do Projeto de Lei nº 100, de 2026, a seguinte redação:\n"
            "“Art. 6º Os documentos somente serão aceitos após a conferência.\n"
            "...............................................\n"
            "§ 3º Sem conferência, o órgão não decidirá.\n"
            "..............................................”\nJUSTIFICAÇÃO\nTexto.")
    devices, notes, start, body, operation = amendment_devices(text)
    assert [(d.article, d.caput, d.paragraph, d.operation) for d in devices] == [("6", True, "", "nova_redacao"), ("6", False, "3", "nova_redacao")]
    assert operation == "nova_redacao" and "JUSTIFICAÇÃO" not in body and start == 0 and not notes


def test_new_article_amendment_is_an_addition_without_target():
    text = "Acrescente-se, onde couber, na Medida Provisória o seguinte artigo:\n“Art. Fica criado benefício fictício.”"
    devices, notes, *_rest, operation = amendment_devices(text)
    assert devices == [] and operation == "acrescimo" and "artigo novo" in notes[0]


def test_amendment_inside_committee_report_starts_at_its_heading():
    text = ("PARECER DA COMISSÃO\nVoto pela aprovação com a emenda de redação a seguir.\nArt. 9º Texto do relatório.\n"
            "EMENDA DE REDAÇÃO Nº 2\nDê-se ao inciso II do art. 3º a seguinte redação:\n“II - registro da data;”")
    devices, _notes, start, body, _op = amendment_devices(text, 2)
    assert body.startswith("EMENDA DE REDAÇÃO Nº 2") and start > 0
    assert [(d.article, d.inciso) for d in devices] == [("3", "II")]


def test_example_lot_covers_every_mode(example_lot):
    _workspace, cases, package = example_lot
    assert package["warnings"] == [] and not any(c["review_required"] for c in cases.values())
    dtq1, dtq2, dtq3 = cases["local:dtq-1"], cases["local:dtq-2"], cases["local:dtq-3"]
    assert (dtq1["mode"], dtq1["operation"], dtq1["ricd_basis"], dtq1["linked_amendment_id"]) == \
        ("destaque_emenda", "inclusao_emenda", "art. 161, II", "local:emp-1")
    assert {e["kind"] for e in dtq1["evidence"]} == {"requerimento_ou_emenda", "emenda_vinculada", "dispositivo"}
    assert (dtq2["mode"], dtq2["operation"], dtq2["target"]["paragraph"]) == ("dvs", "supressao_pretendida", "2")
    [device] = [e for e in dtq2["evidence"] if e["kind"] == "dispositivo"]
    assert device["source_id"] == "local:prlp-2" and "5 dias úteis" in device["quote"] and "*CD" not in device["quote"]
    assert any("versão anterior (local:prlp-1)" in n for n in dtq2["notes"])
    assert [e["quote"] for e in dtq3["evidence"] if e["kind"] == "expressao"] == ["antes da decisão", "em até 30 dias"]
    assert cases["local:erd-1"]["mode"] == "emenda_redacao"
    assert any("datta" in e["quote"] for e in cases["local:erd-1"]["evidence"] if e["kind"] == "dispositivo")


def test_prepared_case_links_exact_excerpt(prepared):
    _workspace, package = prepared
    [case] = package["cases"]
    assert (case["mode"], case["operation"], case["target"]["paragraph"], case["target"]["text_role"]) == \
        ("dvs", "supressao_pretendida", "2-F", "substitutivo")
    [support] = [x for x in case["evidence"] if x["kind"] == "dispositivo"]
    assert support["source_id"] == "local:sbt1" and "integridade documental" in support["quote"]
    assert not case["review_required"]


def test_newer_unavailable_version_blocks_but_unrelated_failure_does_not(prepared):
    workspace, _package = prepared
    manifest = read_json(workspace / "manifest.json")
    baseline = deepcopy(manifest)
    manifest["failures"].append({"source_id": "camara:999", "role": "substitutivo", "kind": "SBT", "number": 2,
                                 "date": "2099-01-01", "reason": "inteiro teor não informado"})
    write_json(workspace / "manifest.json", manifest)
    [case] = prepare(workspace)["cases"]
    assert case["review_required"] and any("camara:999" in w for w in case["warnings"])
    baseline["warnings"].append("camara:888 (PRL 1): inteiro teor não informado pela fonte")
    write_json(workspace / "manifest.json", baseline)
    package = prepare(workspace)
    assert not package["cases"][0]["review_required"] and "camara:888" in package["warnings"][0]


def test_current_law_is_attached_when_captured_as_support(tmp_path):
    from analise_legislativa.collect import collect_local
    from helpers import document

    source = tmp_path / "input"
    source.mkdir()
    document(source / "DTQ-1.docx", [request("I", "votação em separado do(a) § 2º do art. 1º do Decreto-Lei nº 1.804, "
                                                 "de 3 de setembro de 1980, na forma do art. 1º da Medida Provisória")],
             meta={"source_id": "local:dtq", "role": "destaque", "kind": "DTQ", "number": 1})
    document(source / "MPV.docx", ["Art. 1º O Decreto-Lei nº 1.804, de 3 de setembro de 1980, passa a vigorar com as seguintes alterações:",
                                   "“Art. 1º .........................", "§ 2º Nova regra de tributação simplificada.” (NR)"],
             meta={"source_id": "local:mpv", "role": "texto_principal", "kind": "MPV", "number": 1})
    document(source / "DL-1804.docx", ["Art. 1º Fica instituído o regime de tributação simplificada.",
                                       "§ 2º Regra vigente de tributação simplificada."],
             meta={"source_id": "local:dl", "role": "apoio", "kind": "DEL", "number": 1804,
                   "law_ref": "Decreto-Lei 1804/1980", "url": "https://www.planalto.gov.br/ccivil_03/decreto-lei/del1804.htm"})
    workspace = tmp_path / "work"
    collect_local(source, workspace)
    [case] = prepare(workspace)["cases"]
    kinds = {e["source_id"]: e["kind"] for e in case["evidence"] if e["kind"] != "requerimento_ou_emenda"}
    assert kinds == {"local:mpv": "dispositivo", "local:dl": "redacao_vigente"}
