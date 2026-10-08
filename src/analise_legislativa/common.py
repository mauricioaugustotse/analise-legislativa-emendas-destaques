"""Primitivas de proveniência e escrita segura usadas em todas as etapas."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from functools import lru_cache
from importlib.resources import files
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from referencing import Registry, Resource

from . import SCHEMA_VERSION, VERSION


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def normalize_date(value: Any) -> str:
    """ISO 8601 ordenável: '04/08/2026 18:06' -> '2026-08-04T18:06'; ISO fica como está; vazio continua vazio."""
    text = str(value or "").strip()
    match = re.fullmatch(r"(\d{2})/(\d{2})/(\d{4})(?:[ T]+(\d{2}:\d{2}(?::\d{2})?))?.*", text)
    if match:
        day, month, year, clock = match.groups()
        return f"{year}-{month}-{day}" + (f"T{clock}" if clock else "")
    return text


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def stable_hash(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return sha256_bytes(raw)


def safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip(".-")[:120] or "documento"


def write_json(path: Path, data: Any) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = (json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def write_bytes(path: Path, data: bytes) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def read_json(path: Path) -> dict[str, Any]:
    with Path(path).open(encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"JSON deve ser objeto: {path}")
    return value


SCHEMA_NAMES = ("manifest", "case", "analysis", "result")


@lru_cache(maxsize=None)
def schema(name: str) -> dict[str, Any]:
    resource = files("analise_legislativa").joinpath("schemas", f"{name}.schema.json")
    return json.loads(resource.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def _registry() -> Registry:
    """Os schemas se referenciam por $ref relativo (result -> analysis); o registro resolve sem rede."""
    return Registry().with_resources((schema(n)["$id"], Resource.from_contents(schema(n))) for n in SCHEMA_NAMES)


def check_schema(name: str, value: Any) -> None:
    Draft202012Validator(schema(name), registry=_registry()).validate(value)


def run_id(proposition: dict[str, Any], documents: list[dict[str, Any]]) -> str:
    identity = {
        "version": VERSION,
        "schema": SCHEMA_VERSION,
        "proposition": proposition,
        "documents": sorted((d["source_id"], d["sha256"]) for d in documents),
    }
    return stable_hash(identity)[:20]
