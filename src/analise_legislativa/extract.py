"""Extração ordenada de PDF e DOCX; a posição global permite citar a origem."""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class Extracted:
    text: str
    segments: list[dict[str, Any]]
    method: str
    warnings: list[str]


# Carimbo de autenticação que o Infoleg repete em toda página dos documentos da Câmara. Ele cai no
# meio dos dispositivos (entre o caput e o § 1º, por exemplo) e não é texto legislativo.
STAMP_LINE = re.compile(
    r"(?im)^[ \t]*(?:\*CD\d{6,}\*.*|Apresenta[cç][aã]o:\s*\d{2}/\d{2}/\d{4}\s+\d{2}:\d{2}.*"
    r"|[A-Z]{2,5}\s+\d+\s+(?:[A-Z0-9]+\s+)?=>\s+[A-Z]{2,5}\s+\d+/\d{4}.*"
    r"|Assinad[oa]\s+(?:eletronicamente|por\s+chancela\s+eletr[oô]nica)\s+pel[oa].*"
    r"|Para\s+verificar\s+as?\s+assinaturas?,\s+acesse\s+https?://infoleg-autenticidade-assinatura\.camara\.leg\.br\S*)[ \t]*$"
)


def _assemble(parts: list[tuple[int | None, int | None, str]], method: str,
              warnings: list[str] | None = None) -> Extracted:
    segments = []
    chunks = []
    offset = 0
    for page, block, raw in parts:
        clean = str(raw or "").replace("\r\n", "\n")
        clean = STAMP_LINE.sub("", clean)
        clean = re.sub(r"[ \t\f\v]+", " ", clean)
        clean = re.sub(r"\n{3,}", "\n\n", clean).strip()
        if not clean:
            continue
        if chunks:
            chunks.append("\n\n")
            offset += 2
        start = offset
        chunks.append(clean)
        offset += len(clean)
        segments.append({"page": page, "block": block, "start": start, "end": offset, "text": clean})
    return Extracted("".join(chunks), segments, method, warnings or [])


def _fitz_pages(path: Path) -> list[str]:
    import pymupdf

    with pymupdf.open(path) as pdf:
        return [page.get_text("text", sort=True) or "" for page in pdf]


def _pypdf_pages(path: Path) -> list[str]:
    from pypdf import PdfReader

    return [page.extract_text() or "" for page in PdfReader(str(path)).pages]


def _ocr_pages(path: Path, *, dpi: int = 220) -> list[str]:
    """OCR página a página com Tesseract `por`. `TESSERACT_CMD` aponta o executável quando ele não está no
    PATH (instalação padrão no Windows: C:\\Program Files\\Tesseract-OCR\\tesseract.exe)."""
    import pymupdf

    command = os.getenv("TESSERACT_CMD") or "tesseract"
    texts = []
    with pymupdf.open(path) as pdf, tempfile.TemporaryDirectory() as directory:
        for index, page in enumerate(pdf):
            image = Path(directory) / f"p{index}.png"
            page.get_pixmap(dpi=dpi).save(str(image))
            # Tesseract escreve UTF-8; sem encoding explícito, o Windows decodificaria em cp1252 e perderia a página.
            result = subprocess.run(
                [command, str(image), "stdout", "-l", "por"],
                capture_output=True, encoding="utf-8", errors="replace", timeout=120, check=False,
            )
            if result.returncode != 0:
                raise RuntimeError(f"OCR falhou na página {index + 1}: {(result.stderr or '').strip()[:250]}")
            texts.append(result.stdout or "")
    return texts


def extract_pdf(path: Path) -> Extracted:
    path = Path(path)
    errors = []
    def quality(values: list[str]) -> int:
        return sum(len(" ".join(value.split())) for value in values)

    try:
        pages = _fitz_pages(path)
        method = "pymupdf"
    except Exception as exc:
        pages, method = [], ""
        errors.append(f"PyMuPDF: {type(exc).__name__}")
    try:
        fallback = _pypdf_pages(path)
        if quality(fallback) > quality(pages) or (
            quality(fallback) == quality(pages) and sum(map(len, fallback)) < sum(map(len, pages))
        ):
            pages, method = fallback, "pypdf"
    except Exception as exc:
        errors.append(f"pypdf: {type(exc).__name__}")
    native_len = quality(pages)
    shallow = not native_len or (len(pages) > 1 and native_len / len(pages) < 100)
    if shallow:
        try:
            ocr = _ocr_pages(path)
            if quality(ocr) > native_len:
                pages, method = ocr, "tesseract-por"
        except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
            errors.append(f"OCR: {type(exc).__name__}: {str(exc)[:160]}")
    result = _assemble([(index + 1, None, value) for index, value in enumerate(pages)], method, errors)
    if not result.text.strip():
        raise ValueError(f"PDF sem texto extraível: {path.name}; {'; '.join(errors)}")
    if shallow and method != "tesseract-por":
        result.warnings.append("Cobertura textual rasa; conferir se o OCR recuperou todo o documento")
    return result


def extract_docx(path: Path) -> Extracted:
    from docx import Document
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    doc = Document(str(path))
    parts: list[tuple[int | None, int | None, str]] = []
    for index, child in enumerate(doc.element.body.iterchildren(), start=1):
        if child.tag.endswith("}p"):
            parts.append((None, index, Paragraph(child, doc).text))
        elif child.tag.endswith("}tbl"):
            rows = Table(child, doc).rows
            for row_index, row in enumerate(rows, start=1):
                parts.append((None, index * 10000 + row_index, " | ".join(cell.text for cell in row.cells)))
    result = _assemble(parts, "python-docx")
    if not result.text.strip():
        raise ValueError(f"DOCX sem texto extraível: {path.name}")
    return result


def extract(path: Path) -> Extracted:
    if path.suffix.lower() == ".pdf":
        return extract_pdf(path)
    if path.suffix.lower() == ".docx":
        return extract_docx(path)
    raise ValueError(f"Formato não aceito: {path.suffix}")


def segment_at(extracted: Extracted, offset: int) -> dict[str, Any] | None:
    return next((s for s in extracted.segments if s["start"] <= offset < s["end"]), None)


def evidence(source_id: str, extracted: Extracted, start: int, end: int, kind: str) -> dict[str, Any]:
    from .common import stable_hash

    start = max(0, start)
    end = min(len(extracted.text), max(start, end))
    raw = extracted.text[start:end]
    start += len(raw) - len(raw.lstrip())
    end -= len(raw) - len(raw.rstrip())
    segment = segment_at(extracted, start)
    quote = extracted.text[start:end]
    if not quote:
        raise ValueError("Evidência vazia")
    return {
        "evidence_id": stable_hash([source_id, start, end])[:20],
        "source_id": source_id, "page": segment["page"] if segment else None,
        "block": segment["block"] if segment else None,
        "start": start, "end": end, "quote": quote, "kind": kind,
    }
