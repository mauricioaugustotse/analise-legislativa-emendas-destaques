"""Utilitários dos testes: documentos DOCX com sidecar e carga dos módulos de examples/."""

from __future__ import annotations

import importlib.util
from pathlib import Path

from docx import Document

from analise_legislativa.common import write_json

ROOT = Path(__file__).resolve().parents[1]


def document(path: Path, paragraphs: list[str], *, meta: dict) -> None:
    doc = Document()
    for paragraph in paragraphs:
        doc.add_paragraph(paragraph)
    doc.save(path)
    write_json(path.with_name(path.stem + ".meta.json"), meta)


def load_example(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "examples" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
