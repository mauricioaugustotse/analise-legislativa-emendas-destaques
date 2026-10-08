from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from analise_legislativa.collect import (
    CAMARA_API, OfficialClient, _congress_texts, collect_local, collect_official, official_url, parse_proposition,
    select_congress_documents,
)
from helpers import document

PDF = b"%PDF-1.4\nsynthetic"


def test_pagination_retry_and_official_identifiers(monkeypatch, tmp_path: Path):
    calls = {"related": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/proposicoes"):
            return httpx.Response(200, json={"dados": [{"id": 111, "siglaTipo": "PL", "numero": 100, "ano": 2026}], "links": []})
        if path.endswith("/proposicoes/111/relacionadas"):
            assert "itens" not in request.url.params
            calls["related"] += 1
            if calls["related"] == 1:
                return httpx.Response(429, headers={"Retry-After": "0"})
            if request.url.params.get("page", "1") == "1":
                return httpx.Response(200, json={"dados": [{"id": 222, "siglaTipo": "DTQ", "numero": 1, "ano": 2026}],
                                                 "links": [{"rel": "next", "href": f"{CAMARA_API}/proposicoes/111/relacionadas?page=2"}]})
            return httpx.Response(200, json={"dados": [{"id": 333, "siglaTipo": "EMP", "numero": 2, "ano": 2026},
                                                       {"id": 444, "siglaTipo": "SBT", "numero": 1, "ano": 2026}], "links": []})
        if path.endswith("/proposicoes/111"):
            return httpx.Response(200, json={"dados": {"id": 111, "siglaTipo": "PL", "numero": 100, "ano": 2026,
                                                       "urlInteiroTeor": "https://www.camara.leg.br/doc/111.pdf"}})
        if path.endswith(("/proposicoes/222", "/proposicoes/333")):
            item_id = path.split("/")[-1]
            return httpx.Response(200, json={"dados": {"urlInteiroTeor": f"https://www.camara.leg.br/doc/{item_id}.pdf",
                                                       "dataApresentacao": "2026-03-10T15:00"}})
        if path.endswith("/proposicoes/444"):
            return httpx.Response(200, json={"dados": {"urlInteiroTeor": None}})
        if path.startswith("/doc/"):
            return httpx.Response(200, content=PDF)
        raise AssertionError(str(request.url))

    monkeypatch.setattr("analise_legislativa.collect.time.sleep", lambda _: None)
    api = OfficialClient(httpx.Client(transport=httpx.MockTransport(handler)), interval=0)
    manifest = collect_official("PL 100/2026", tmp_path, api)
    assert calls["related"] == 3
    assert {x["source_id"] for x in manifest["documents"]} == {"camara:111", "camara:222", "camara:333"}
    assert all(len(x["sha256"]) == 64 for x in manifest["documents"])
    assert next(x for x in manifest["documents"] if x["source_id"] == "camara:222")["metadata"]["date"] == "2026-03-10T15:00"
    [failure] = manifest["failures"]
    assert (failure["source_id"], failure["role"], failure["kind"]) == ("camara:444", "substitutivo", "SBT")
    assert json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))["run_id"] == manifest["run_id"]


def test_redirect_to_external_host_is_rejected():
    api = OfficialClient(httpx.Client(transport=httpx.MockTransport(
        lambda _: httpx.Response(302, headers={"Location": "https://example.org/file.pdf"}))), interval=0)
    with pytest.raises(ValueError, match="Redirecionamento"):
        api.get("https://www.camara.leg.br/doc.pdf")


def test_official_http_is_upgraded_and_other_hosts_are_refused():
    assert official_url("http://legis.senado.leg.br/sdleg-getter/documento?dm=1") == "https://legis.senado.leg.br/sdleg-getter/documento?dm=1"
    with pytest.raises(ValueError, match="Domínio"):
        official_url("https://example.org/a.pdf")
    with pytest.raises(ValueError, match="Esquema"):
        official_url("ftp://www.camara.leg.br/a.pdf")


def test_proposition_reference_forms():
    assert parse_proposition("PL 5229/2025") == {"id": None, "sigla": "PL", "numero": 5229, "ano": 2025}
    assert parse_proposition("MPV nº 1.357/2026")["numero"] == 1357
    assert parse_proposition("2572690")["id"] == 2572690
    assert parse_proposition("https://www.camara.leg.br/proposicoesWeb/fichadetramitacao?idProposicao=2572690")["id"] == 2572690
    assert parse_proposition("https://www.congressonacional.leg.br/materias/medidas-provisorias/-/mpv/174123")["codigo_materia"] == 174123


def mpv_texts(request: httpx.Request) -> httpx.Response:
    if request.url.path.endswith("/materia/MPV/100/2026"):
        return httpx.Response(200, json={"DetalheMateria": {"Materia": {"IdentificacaoMateria": {"CodigoMateria": 900}}}})
    return httpx.Response(200, json={"TextoMateria": {"Materia": {"Textos": {"Texto": [
        {"CodigoTexto": 10, "DescricaoTexto": "EMENDA 4", "DescricaoTipoTexto": "Emenda", "UrlTexto": "http://legis.senado.leg.br/d.pdf"},
        {"CodigoTexto": 11, "DescricaoTexto": "EMENDA 5", "DescricaoTipoTexto": "Emenda", "UrlTexto": "http://legis.senado.leg.br/e.pdf"},
        {"CodigoTexto": 12, "DescricaoTexto": "Parecer", "DescricaoTipoTexto": "Parecer", "UrlTexto": "https://legis.senado.leg.br/p.pdf", "DataTexto": "2026-06-01"},
        {"CodigoTexto": 13, "DescricaoTexto": "PLV 7", "DescricaoTipoTexto": "Projeto de Lei de Conversão", "UrlTexto": "https://legis.senado.leg.br/q.pdf", "DataTexto": "2026-06-02"},
        {"CodigoTexto": 14, "DescricaoTexto": "Lista", "DescricaoTipoTexto": "Listagem ou relatório descritivo", "UrlTexto": "https://legis.senado.leg.br/l.pdf"},
    ]}}}})


def test_congress_complement_only_covers_what_the_camara_lacks():
    api = OfficialClient(httpx.Client(transport=httpx.MockTransport(mpv_texts)), interval=0)
    texts = _congress_texts(api, {"sigla": "MPV", "numero": 100, "ano": 2026})
    assert {(x["role"], x["numero"]) for x in texts} == {("emenda", 4), ("emenda", 5), ("parecer", 0), ("substitutivo", 0)}
    selected = select_congress_documents(texts, [], {4}, False)
    assert [(x["role"], x["numero"]) for x in selected] == [("emenda", 4), ("parecer", 0)]
    camara_has = [{"source_id": "camara:7", "role": "emenda", "kind": "EMC", "number": 4},
                  {"source_id": "camara:8", "role": "parecer", "kind": "PAR", "number": 1}]
    assert select_congress_documents(texts, camara_has, {4}, False) == []
    assert [x["numero"] for x in select_congress_documents(texts, camara_has, set(), True)] == [5]


def test_mpv_amendment_cited_in_request_is_downloaded_from_the_senate(monkeypatch, tmp_path: Path):
    from docx import Document

    request_path = tmp_path / "dtq.docx"
    request = Document()
    request.add_paragraph("Requeiro, nos termos do art. 161, II, do Regimento Interno, destaque para votação em separado da Emenda nº 4")
    request.save(request_path)
    request_bytes = request_path.read_bytes()

    def handler(request: httpx.Request) -> httpx.Response:
        host, path = request.url.host, request.url.path
        if host == "legis.senado.leg.br" and "/dadosabertos/" in path:
            return mpv_texts(request)
        if host == "legis.senado.leg.br":
            assert request.url.scheme == "https"
            return httpx.Response(200, content=PDF)
        if path.endswith("/proposicoes"):
            return httpx.Response(200, json={"dados": [{"id": 1, "siglaTipo": "MPV", "numero": 100, "ano": 2026}], "links": []})
        if path.endswith("/relacionadas"):
            return httpx.Response(200, json={"dados": [{"id": 2, "siglaTipo": "DTQ", "numero": 1, "ano": 2026}], "links": []})
        if path.endswith("/proposicoes/1"):
            return httpx.Response(200, json={"dados": {"id": 1, "siglaTipo": "MPV", "numero": 100, "ano": 2026}})
        if path.endswith("/proposicoes/2"):
            return httpx.Response(200, json={"dados": {"urlInteiroTeor": "https://www.camara.leg.br/dtq.docx"}})
        if path == "/dtq.docx":
            return httpx.Response(200, content=request_bytes)
        raise AssertionError(str(request.url))

    monkeypatch.setattr("analise_legislativa.collect.time.sleep", lambda _: None)
    api = OfficialClient(httpx.Client(transport=httpx.MockTransport(handler)), interval=0)
    manifest = collect_official("MPV 100/2026", tmp_path / "ws", api)
    assert {x["source_id"] for x in manifest["documents"]} == {"camara:2", "congresso:10", "congresso:12"}
    assert manifest["warnings"] == []


def test_local_collection_reads_sidecars_of_the_original_workflow(tmp_path: Path):
    source = tmp_path / "input"
    source.mkdir()
    document(source / "DTQ-1-=_-PL-100-2026.docx", ["Destaque para EMP 1"], meta={})
    (source / "DTQ-1-=_-PL-100-2026.meta.json").unlink()
    (source / "DTQ-1-=_-PL-100-2026.camara_meta.json").write_text(json.dumps({
        "id": 2638335, "siglaTipo": "DTQ", "numero": 1, "ano": 2026, "papel": "destaque",
        "dataApresentacao": "08/07/2026 16:30", "ementa": "Destaque de Emenda - PL 100/2026",
        "url_inteiro_teor": "https://www.camara.leg.br/proposicoesWeb/prop_mostrarintegra?codteor=1",
        "proposicao_principal": {"sigla": "PL", "numero": 100, "ano": 2026, "id": 111},
        "vinculo": {"tipo": "destaque_emenda", "emenda": {"sigla": "EMP", "numero": 1, "id": 999}},
    }), encoding="utf-8")
    document(source / "inteiroTeor-123.docx", ["Texto qualquer"], meta={})
    (source / "inteiroTeor-123.meta.json").unlink()
    manifest = collect_local(source, tmp_path / "ws")
    dtq = next(x for x in manifest["documents"] if x["kind"] == "DTQ")
    assert (dtq["source_id"], dtq["role"], dtq["metadata"]["date"]) == ("camara:2638335", "destaque", "2026-07-08T16:30")
    assert dtq["metadata"]["vinculo"]["emenda"]["id"] == 999 and manifest["proposition"]["numero"] == 100
    unknown = next(x for x in manifest["documents"] if x["kind"] == "")
    assert unknown["role"] == "apoio" and unknown["metadata"]["inferred_role"]
    assert any("inferido pelo nome" in w for w in manifest["warnings"])
