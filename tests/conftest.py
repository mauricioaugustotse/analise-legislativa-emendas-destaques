from __future__ import annotations

from pathlib import Path

import pytest
from helpers import document, load_example

from analise_legislativa.collect import collect_local
from analise_legislativa.parser import prepare


@pytest.fixture
def prepared(tmp_path: Path):
    """Lote mínimo: um DVS de parágrafo com sufixo e o substitutivo que o contém."""
    source = tmp_path / "input"
    source.mkdir()
    proposition = {"id": 111, "sigla": "PL", "numero": 100, "ano": 2026}
    document(source / "DVS-2.docx", [
        "Requeiro, nos termos do art. 161, I, do Regimento Interno, destaque para votação em separado do "
        "§ 2º-F do art. 1º do substitutivo ao PL 100/2026, com a finalidade de suprimir o dispositivo.",
        "JUSTIFICAÇÃO", "Exemplo sintético."
    ], meta={"source_id": "local:dvs2", "role": "destaque", "kind": "DVS", "number": 2, "proposition": proposition})
    document(source / "SBT-1.docx", [
        "SUBSTITUTIVO AO PL 100/2026",
        "Art. 1º Fica criado um procedimento fictício de conferência documental.",
        "§ 2º-B O órgão registrará o recebimento.",
        "§ 2º-F O órgão verificará a integridade documental antes da decisão.",
        "§ 2º-G A decisão será comunicada ao interessado."
    ], meta={"source_id": "local:sbt1", "role": "substitutivo", "kind": "SBT", "number": 1, "proposition": proposition})
    workspace = tmp_path / "work"
    collect_local(source, workspace)
    package = prepare(workspace)
    return workspace, package


@pytest.fixture
def example_lot(tmp_path: Path):
    """O lote fictício de examples/, no formato dos documentos reais (todos os modos)."""
    module = load_example("build_synthetic_inputs")
    source = tmp_path / "input"
    source.mkdir()
    for name, (lines, meta) in module.FILES.items():
        module.create(source / name, lines, meta)
    workspace = tmp_path / "work"
    collect_local(source, workspace)
    package = prepare(workspace)
    return workspace, {case["source_id"]: case for case in package["cases"]}, package
