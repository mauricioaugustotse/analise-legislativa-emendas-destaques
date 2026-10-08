"""Coleta de fontes oficiais e locais, com identidade e integridade verificáveis."""

from __future__ import annotations

import logging
import os
import re
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urljoin, urlparse, urlunparse

import httpx

from . import SCHEMA_VERSION
from .common import (check_schema, normalize_date, now_iso, read_json, run_id, safe_name, sha256_bytes, sha256_file,
                     write_bytes, write_json)

LOG = logging.getLogger(__name__)
CAMARA_API = "https://dadosabertos.camara.leg.br/api/v2"
SENADO_API = "https://legis.senado.leg.br/dadosabertos"
OFFICIAL_DOMAINS = ("camara.leg.br", "senado.leg.br", "congressonacional.leg.br")
PROP_RE = re.compile(r"\b([A-Z]{2,6})\s*(?:n[.º°o]*\s*)?(\d{1,3}(?:\.\d{3})+|\d{1,6})\s*[/_-]\s*(\d{4})\b", re.I)
CN_MPV_URL = re.compile(r"congressonacional\.leg\.br/materias/medidas-provisorias/-/mpv/(\d+)", re.I)
NAME_RE = re.compile(
    r"\b(DTQ|DVS|EMPV|EMRP|EMP|EMC|EMS|ESB|SBE|SBR|EMR|ERD|EMA|SBTA|SBT|SSP|PLV|PRLP|PRLE|PRL|PAR|PPR)[-_ ]*(\d+)\b", re.I
)
AMENDMENT_CITATION = re.compile(r"\b(?:EMENDA|EMPV|EMP|EMC|EMS|ESB|SBE|EMR|ERD|EMA)\s*(?:n[.º°o]*\s*)?0*(\d+)\b", re.I)
# Papéis pela tabela oficial /referencias/proposicoes/siglaTipo da API da Câmara. Atenção: EMR é
# "Emenda de Relator"; emenda de redação é ERD.
EMENDAS = {"EMP", "EMC", "EMS", "ESB", "SBE", "SBR", "EMR", "EMRP", "ERD", "EMA", "EMPV"}
PARECERES = {"PRL", "PAR", "PRLP", "PPR", "PRLE"}
SUBSTITUTIVOS = {"SBT", "SBTA", "SSP", "PLV"}
LOCAL_ROLES = {"destaque", "emenda", "parecer", "texto_principal", "substitutivo", "apoio"}
# Sistemas antigos registravam destaques como REQ ("Requerimento de Destaque para votação em separado...").
REQUEST_FOR_HIGHLIGHT = re.compile(r"\bdestaque\b|\bDVS\b|vota[cç][aã]o\s+em\s+separado", re.I)


def role_for(sigla: str, description: str = "") -> str:
    sigla = sigla.upper()
    if sigla in {"DTQ", "DVS"} or (sigla == "REQ" and REQUEST_FOR_HIGHLIGHT.search(description or "")):
        return "destaque"
    if sigla in EMENDAS:
        return "emenda"
    if sigla in PARECERES:
        return "parecer"
    if sigla in SUBSTITUTIVOS:
        return "substitutivo"
    return "apoio"


def _is_official(host: str) -> bool:
    host = host.lower()
    return any(host == domain or host.endswith("." + domain) for domain in OFFICIAL_DOMAINS)


def official_url(url: str) -> str:
    """Só domínios oficiais. HTTP de domínio oficial é promovido a HTTPS: a API do Senado devolve
    `UrlTexto` com http://, e sem a promoção o complemento de MPV nunca seria baixado."""
    parsed = urlparse(url)
    host = parsed.hostname or ""
    if not _is_official(host):
        raise ValueError(f"Domínio fora das fontes oficiais permitidas: {host or url}")
    if parsed.scheme == "http":
        return urlunparse(parsed._replace(scheme="https"))
    if parsed.scheme != "https":
        raise ValueError(f"Esquema não permitido em fonte oficial: {parsed.scheme or '(vazio)'}")
    return url


class OfficialClient:
    def __init__(self, client: httpx.Client | None = None, *, interval: float = 0.5):
        self.client = client or httpx.Client(timeout=30.0, follow_redirects=False)
        self.owned = client is None
        self.interval = interval
        self.next_at = 0.0

    def close(self) -> None:
        if self.owned:
            self.client.close()

    def get(self, url: str, *, params: dict[str, Any] | None = None) -> httpx.Response:
        url = official_url(url)
        for attempt in range(4):
            delay = self.next_at - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            self.next_at = time.monotonic() + self.interval
            try:
                current_url, current_params = url, params
                for _ in range(6):
                    response = self.client.get(current_url, params=current_params,
                                               headers={"Accept": "application/json,application/pdf,*/*"},
                                               follow_redirects=False)
                    if response.status_code not in {301, 302, 303, 307, 308}:
                        break
                    location = response.headers.get("Location", "")
                    if not location:
                        raise ValueError("Redirecionamento sem destino na fonte oficial")
                    try:
                        current_url = official_url(urljoin(str(response.url), location))
                    except ValueError as exc:
                        raise ValueError(f"Redirecionamento saiu dos domínios oficiais permitidos ({exc})") from exc
                    current_params = None
                else:
                    raise ValueError("Redirecionamentos em excesso na fonte oficial")
                if response.status_code in {429, 500, 502, 503, 504} and attempt < 3:
                    header = response.headers.get("Retry-After", "")
                    try:
                        wait = min(30.0, max(0.0, float(header))) if header else min(8.0, 2.0 ** attempt)
                    except ValueError:
                        wait = min(8.0, 2.0 ** attempt)
                    LOG.warning("source_retry status=%s attempt=%s", response.status_code, attempt + 1)
                    time.sleep(wait)
                    continue
                response.raise_for_status()
                return response
            except (httpx.TimeoutException, httpx.NetworkError):
                if attempt == 3:
                    raise
                LOG.warning("source_retry network attempt=%s", attempt + 1)
                time.sleep(min(8.0, 2.0 ** attempt))
        raise RuntimeError("Limite de novas tentativas da fonte oficial")

    def json(self, url: str, *, params: dict[str, Any] | None = None) -> dict[str, Any]:
        value = self.get(url, params=params).json()
        if not isinstance(value, dict):
            raise ValueError("Resposta JSON inesperada da fonte oficial")
        return value

    def pages(self, url: str, *, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        seen: set[str] = set()
        while url:
            key = str(httpx.URL(url, params=params)) if params is not None else str(httpx.URL(url))
            if key in seen:
                raise ValueError("Ciclo na paginação da API")
            seen.add(key)
            page = self.json(url, params=params)
            data = page.get("dados", [])
            if not isinstance(data, list):
                raise ValueError("Campo dados da API não é lista")
            result.extend(x for x in data if isinstance(x, dict))
            next_url = next((x.get("href", "") for x in page.get("links", [])
                             if isinstance(x, dict) and x.get("rel") == "next"), "")
            url = str(next_url)
            params = None
        return result


def parse_proposition(value: str) -> dict[str, Any]:
    """Aceita "PL 5229/2025", o ID da Câmara, a URL da ficha ou a URL da MPV no Congresso Nacional."""
    value = value.strip()
    match = PROP_RE.search(value)
    if match and not urlparse(value).scheme:
        return {"id": None, "sigla": match[1].upper(), "numero": int(match[2].replace(".", "")), "ano": int(match[3])}
    parsed = urlparse(value)
    if parsed.scheme and parsed.hostname:
        official_url(value)
        cn = CN_MPV_URL.search(value)
        if cn:
            return {"id": None, "sigla": "", "numero": 0, "ano": 0, "codigo_materia": int(cn[1])}
        query_id = parse_qs(parsed.query).get("idProposicao", [])
        tail = re.search(r"/(\d+)/?$", parsed.path)
        if query_id and query_id[0].isdigit():
            return {"id": int(query_id[0]), "sigla": "", "numero": 0, "ano": 0}
        if tail:
            return {"id": int(tail[1]), "sigla": "", "numero": 0, "ano": 0}
    if re.fullmatch(r"\d{4,10}", value):
        return {"id": int(value), "sigla": "", "numero": 0, "ano": 0}
    raise ValueError("Use sigla número/ano, ID ou URL oficial da proposição")


def _identity_from_name(name: str) -> tuple[str, int]:
    match = NAME_RE.search(name)
    return (match[1].upper(), int(match[2])) if match else ("", 0)


def _document(source_id: str, role: str, kind: str, number: int, year: int,
              relative_path: str, checksum: str, *, url: str = "", proposition_id: int | None = None,
              metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "source_id": source_id, "role": role, "kind": kind, "number": number,
        "year": year, "proposition_id": proposition_id, "url": url,
        "path": relative_path, "sha256": checksum, "retrieved_at": now_iso(),
        "metadata": metadata or {},
    }


def _failure(source_id: str, item: dict[str, Any], role: str, reason: str) -> dict[str, Any]:
    return {"source_id": source_id, "role": role, "kind": str(item.get("siglaTipo") or "").upper(),
            "number": int(item.get("numero") or 0), "date": normalize_date(item.get("date") or item.get("dataApresentacao")),
            "reason": reason}


def _copy_file_atomic(source: Path, destination: Path) -> None:
    fd, name = tempfile.mkstemp(prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent)
    try:
        with source.open("rb") as original, os.fdopen(fd, "wb") as target:
            shutil.copyfileobj(original, target, length=1024 * 1024)
            target.flush()
            os.fsync(target.fileno())
        os.replace(name, destination)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def _local_meta(path: Path) -> tuple[dict[str, Any], bool]:
    """Sidecar `<arquivo>.meta.json` (formato deste repositório) ou `<arquivo>.camara_meta.json`
    (formato gravado pelo fluxo original ao baixar o pacote da Câmara)."""
    own = path.with_name(path.stem + ".meta.json")
    if own.exists():
        return read_json(own), True
    legacy = path.with_name(path.stem + ".camara_meta.json")
    if not legacy.exists():
        return {}, False
    raw = read_json(legacy)
    principal = raw.get("proposicao_principal") if isinstance(raw.get("proposicao_principal"), dict) else {}
    role = str(raw.get("papel") or "")
    meta: dict[str, Any] = {
        "kind": raw.get("siglaTipo") or "", "number": raw.get("numero") or 0, "year": raw.get("ano") or 0,
        "role": role if role in LOCAL_ROLES else ("apoio" if role else ""),
        "url": raw.get("url_inteiro_teor") or raw.get("urlInteiroTeor") or "",
        "description": raw.get("ementa") or raw.get("descricao") or "",
        "date": raw.get("dataApresentacao") or "",
    }
    if raw.get("id"):
        meta["source_id"] = f"camara:{raw['id']}"
    if principal:
        meta["proposition"] = {"id": principal.get("id"), "sigla": str(principal.get("sigla") or ""),
                               "numero": int(principal.get("numero") or 0), "ano": int(principal.get("ano") or 0)}
    if isinstance(raw.get("vinculo"), dict):
        meta["vinculo"] = raw["vinculo"]
    return meta, True


def collect_local(input_dir: Path, workspace: Path) -> dict[str, Any]:
    input_dir = Path(input_dir).resolve()
    workspace = Path(workspace).resolve()
    if not input_dir.is_dir():
        raise ValueError(f"Pasta de entrada ausente: {input_dir}")
    paths = sorted(p for p in input_dir.iterdir() if p.is_file() and p.suffix.lower() in {".pdf", ".docx"})
    if not paths:
        raise ValueError("Nenhum PDF ou DOCX encontrado")
    documents, warnings = [], []
    source_ids: set[str] = set()
    proposition: dict[str, Any] = {"id": None, "sigla": "", "numero": 0, "ano": 0}
    destination = workspace / "sources"
    destination.mkdir(parents=True, exist_ok=True)
    for path in paths:
        meta, has_sidecar = _local_meta(path)
        kind, number = _identity_from_name(path.name)
        kind = str(meta.get("kind") or meta.get("siglaTipo") or kind).upper()
        number = int(meta.get("number") or meta.get("numero") or number)
        role = str(meta.get("role") or meta.get("papel") or role_for(kind))
        if role not in LOCAL_ROLES:
            raise ValueError(f"Papel desconhecido em {path.name}: {role}")
        if not kind:
            warnings.append(f"Identidade não identificada em {path.name}; classificado como apoio")
        if not has_sidecar:
            warnings.append(f"Papel de {path.name} inferido pelo nome; conferir")
            meta = {**meta, "inferred_role": True}
        if isinstance(meta.get("proposition"), dict):
            proposition = meta["proposition"]
        if meta.get("date"):
            meta = {**meta, "date": normalize_date(meta["date"])}
        checksum = sha256_file(path)
        dest = destination / f"{checksum[:12]}-{safe_name(path.name)}"
        if not dest.exists() or sha256_file(dest) != checksum:
            _copy_file_atomic(path, dest)
        source_id = str(meta.get("source_id") or f"local:{checksum[:20]}")
        if source_id in source_ids:
            raise ValueError(f"source_id repetido na entrada local: {source_id}")
        source_ids.add(source_id)
        documents.append(_document(source_id, role, kind, number, int(meta.get("year") or meta.get("ano") or 0),
                                   dest.relative_to(workspace).as_posix(), checksum,
                                   url=str(meta.get("url") or ""), proposition_id=meta.get("proposition_id"), metadata=meta))
    manifest = {"schema_version": SCHEMA_VERSION, "proposition": proposition, "documents": documents,
                "warnings": warnings, "failures": []}
    manifest["run_id"] = run_id(proposition, documents)
    check_schema("manifest", manifest)
    write_json(workspace / "manifest.json", manifest)
    return manifest


def _resolve(client: OfficialClient, reference: str) -> dict[str, Any]:
    parsed = parse_proposition(reference)
    if parsed.get("codigo_materia"):
        detail = client.json(f"{SENADO_API}/materia/{parsed['codigo_materia']}")
        ident = detail.get("DetalheMateria", {}).get("Materia", {}).get("IdentificacaoMateria", {})
        parsed = {"id": None, "sigla": str(ident.get("SiglaSubtipoMateria") or "").upper(),
                  "numero": int(str(ident.get("NumeroMateria") or "0").lstrip("0") or 0),
                  "ano": int(ident.get("AnoMateria") or 0)}
        if not parsed["sigla"] or not parsed["numero"]:
            raise ValueError(f"Matéria do Congresso não identificada: {reference}")
    if parsed["id"]:
        data = client.json(f"{CAMARA_API}/proposicoes/{parsed['id']}").get("dados") or {}
    else:
        values = client.pages(f"{CAMARA_API}/proposicoes", params={
            "siglaTipo": parsed["sigla"], "numero": parsed["numero"], "ano": parsed["ano"], "itens": 100,
        })
        exact = [x for x in values if str(x.get("siglaTipo", "")).upper() == parsed["sigla"]
                 and int(x.get("numero") or 0) == parsed["numero"] and int(x.get("ano") or 0) == parsed["ano"]]
        if len(exact) != 1:
            raise ValueError(f"Proposição não resolvida univocamente: {reference}")
        data = exact[0]
    if not data or not data.get("id"):
        raise ValueError(f"Proposição não encontrada: {reference}")
    return {"id": int(data["id"]), "sigla": str(data.get("siglaTipo") or ""),
            "numero": int(data.get("numero") or 0), "ano": int(data.get("ano") or 0)}


def _congress_texts(client: OfficialClient, proposition: dict[str, Any]) -> list[dict[str, Any]]:
    """Textos da MPV nos dados abertos do Senado: emendas ("EMENDA n"), pareceres e PLV."""
    base = f"{SENADO_API}/materia"
    detail = client.json(f"{base}/MPV/{proposition['numero']}/{proposition['ano']}")
    ident = detail.get("DetalheMateria", {}).get("Materia", {}).get("IdentificacaoMateria", {})
    codigo = ident.get("CodigoMateria")
    if not codigo:
        raise ValueError("MPV sem código da matéria nos dados do Senado")
    payload = client.json(f"{base}/textos/{codigo}")
    entries = payload.get("TextoMateria", {}).get("Materia", {}).get("Textos", {}).get("Texto", [])
    if isinstance(entries, dict):
        entries = [entries]
    result = []
    for item in entries:
        if not isinstance(item, dict) or not item.get("UrlTexto"):
            continue
        description = str(item.get("DescricaoTexto") or "")
        kind = str(item.get("DescricaoTipoTexto") or "").lower()
        number = re.match(r"EMENDA\s+0*(\d+)\b", description, re.I)
        if number:
            role, sigla, numero, rank = "emenda", "EMC", int(number[1]), 0
        elif "listagem ou relat" in kind:
            continue
        elif "parecer" in kind or "parecer" in description.lower() or ("complementa" in kind and "voto" in kind):
            role, sigla, numero, rank = "parecer", "PAR", 0, 3
        elif kind.startswith("relat"):
            role, sigla, numero, rank = "parecer", "PAR", 0, 2
        elif "projeto de lei de convers" in kind or description.upper().startswith("PLV"):
            role, sigla, numero, rank = "substitutivo", "PLV", 0, 1
        else:
            continue
        result.append({"id": str(item.get("CodigoTexto") or ""), "siglaTipo": sigla, "numero": numero,
                       "ano": proposition["ano"], "role": role, "rank": rank,
                       "urlInteiroTeor": str(item["UrlTexto"]), "description": description,
                       "date": normalize_date(item.get("DataTexto")), "origin": "congresso"})
    return result


def select_congress_documents(congress: list[dict[str, Any]], existing: list[dict[str, Any]],
                              linked_numbers: set[int], include_all: bool) -> list[dict[str, Any]]:
    """Complemento do Congresso, como no fluxo original: só cobre o que a Câmara não trouxe.

    Emendas: as citadas nos destaques (ou todas, com --all-congress-amendments) cujo número ainda não
    está no lote, para não criar duas cópias da mesma emenda e um vínculo ambíguo. Parecer: um só,
    se a Câmara não trouxe nenhum, escolhido por classe (Parecer > Relatório > PLV) e depois por data.
    """
    known = {x["source_id"] for x in existing}
    have_numbers = {x["number"] for x in existing if x["role"] == "emenda"}
    selected = [x for x in congress if x["role"] == "emenda" and f"congresso:{x['id']}" not in known
                and x["numero"] not in have_numbers and (include_all or x["numero"] in linked_numbers)]
    if not any(x["role"] == "parecer" for x in existing):
        references = sorted((x for x in congress if x["role"] in {"parecer", "substitutivo"}),
                            key=lambda x: (x["rank"], x["date"], x["id"]))
        if references:
            selected.append(references[-1])
    return selected


def collect_official(reference: str, workspace: Path, client: OfficialClient | None = None,
                     *, include_all_congress_amendments: bool = False) -> dict[str, Any]:
    workspace = Path(workspace).resolve()
    api = client or OfficialClient()
    try:
        proposition = _resolve(api, reference)
        related = api.pages(f"{CAMARA_API}/proposicoes/{proposition['id']}/relacionadas")
        candidates: list[dict[str, Any]] = []
        for entry in related:
            role = role_for(str(entry.get("siglaTipo") or ""), str(entry.get("ementa") or ""))
            if role != "apoio":
                candidates.append({**entry, "role": role, "origin": "camara"})
        principal = api.json(f"{CAMARA_API}/proposicoes/{proposition['id']}").get("dados") or {}
        if principal.get("urlInteiroTeor"):
            candidates.append({**principal, "role": "texto_principal", "origin": "camara"})
        warnings: list[str] = []
        failures: list[dict[str, Any]] = []
        destination = workspace / "sources"
        destination.mkdir(parents=True, exist_ok=True)
        documents: list[dict[str, Any]] = []
        seen: set[str] = set()

        def download_items(items: list[dict[str, Any]]) -> None:
            for item in items:
                origin = item["origin"]
                item_id = str(item.get("id") or "")
                identity = f"{origin}:{item_id}"
                if not item_id or identity in seen:
                    continue
                seen.add(identity)
                label = f"{item.get('siglaTipo', '')} {item.get('numero', '')}".strip()
                try:
                    detail = item if item.get("urlInteiroTeor") else api.json(f"{CAMARA_API}/proposicoes/{item_id}").get("dados") or {}
                    url = str(detail.get("urlInteiroTeor") or "")
                    if not url:
                        warnings.append(f"{identity} ({label}): inteiro teor não informado pela fonte")
                        failures.append(_failure(identity, {**item, **detail}, item["role"], "inteiro teor não informado"))
                        continue
                    raw = api.get(url).content
                    if raw.startswith(b"%PDF"):
                        ext = ".pdf"
                    elif raw.startswith(b"PK\x03\x04"):
                        ext = ".docx"
                    else:
                        raise ValueError("resposta não é PDF nem DOCX (página HTML ou formato .doc antigo)")
                    checksum = sha256_bytes(raw)
                    name = safe_name(f"{identity}-{item.get('siglaTipo', '')}-{item.get('numero', 0)}") + ext
                    path = destination / name
                    if not path.exists() or sha256_file(path) != checksum:
                        write_bytes(path, raw)
                    metadata = {"description": str(item.get("description") or detail.get("ementa") or ""),
                                "type_description": str(detail.get("descricaoTipo") or ""),
                                "date": normalize_date(item.get("date") or detail.get("dataApresentacao")),
                                "proposition": proposition}
                    documents.append(_document(identity, item["role"], str(item.get("siglaTipo") or "").upper(),
                                               int(item.get("numero") or 0), int(item.get("ano") or 0),
                                               path.relative_to(workspace).as_posix(), checksum,
                                               url=url, proposition_id=proposition["id"], metadata=metadata))
                except (httpx.HTTPError, ValueError) as exc:
                    reason = f"{type(exc).__name__}: {exc}"
                    warnings.append(f"{identity} ({label}): falha ao obter inteiro teor ({reason})")
                    failures.append(_failure(identity, item, item["role"], reason))

        download_items(candidates)
        if proposition["sigla"].upper() == "MPV":
            try:
                congress = _congress_texts(api, proposition)
                linked_numbers: set[int] = set()
                if not include_all_congress_amendments:
                    from .extract import extract
                    from .parser import _request_clause

                    for doc in documents:
                        if doc["role"] != "destaque":
                            continue
                        try:
                            clause = _request_clause(extract(workspace / doc["path"]).text)
                            linked_numbers.update(int(m[1]) for m in AMENDMENT_CITATION.finditer(clause))
                        except (ValueError, OSError, RuntimeError) as exc:
                            warnings.append(f"{doc['source_id']}: vínculo não legível ({type(exc).__name__})")
                download_items(select_congress_documents(congress, documents, linked_numbers,
                                                         include_all_congress_amendments))
                found_numbers = {x["number"] for x in documents if x["role"] == "emenda"}
                for missing in sorted(linked_numbers - found_numbers):
                    warnings.append(f"Emenda {missing} citada em destaque não foi obtida na Câmara nem no Congresso")
            except (httpx.HTTPError, ValueError, KeyError) as exc:
                warnings.append(f"Complemento do Congresso indisponível: {type(exc).__name__}: {exc}")
        if not any(x["role"] in {"destaque", "emenda"} for x in documents):
            warnings.append("Sem destaques ou emendas nas fontes consultadas; não presumir inexistência")
        if not documents:
            raise ValueError("Nenhum inteiro teor válido foi obtido; consultar avisos e fontes")
        manifest = {"schema_version": SCHEMA_VERSION, "proposition": proposition,
                    "documents": documents, "warnings": warnings, "failures": failures}
        manifest["run_id"] = run_id(proposition, documents)
        check_schema("manifest", manifest)
        write_json(workspace / "manifest.json", manifest)
        return manifest
    finally:
        if client is None:
            api.close()
