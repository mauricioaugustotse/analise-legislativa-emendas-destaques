"""Parser determinístico: identifica objeto, operação pretendida e dispositivo antes de qualquer síntese."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import replace
from pathlib import Path
from typing import Any

from . import SCHEMA_VERSION
from .common import check_schema, read_json, sha256_file, stable_hash, write_json
from .extract import Extracted, evidence, extract
from .references import (
    NORM_RE, Device, commands, is_new_article, normalize_norm, operation_for, proposed_devices, resolve,
    same_norm, text_role_for,
)

EMENDA_REF = re.compile(r"\b(EMPV|EMRP|EMP|EMC|EMS|ESB|SBE|SBR|EMR|ERD|EMA)\s*[-_]?\s*(?:n[.º°o]*\s*)?0*(\d{1,4})\b", re.I)
EMENDA_GENERIC = re.compile(r"\bemenda(?:\s+de\s+plen[aá]rio)?\s*(?:n[.º°o]*\s*)?0*(\d{1,4})\b", re.I)
RICD_BASIS = re.compile(r"\bart(?:igo)?\.?\s*161\s*,?\s*(?:inciso\s+)?(IV|I{1,3})\b", re.I)
# Tipo de destaque pelo título do PDF ou pelo descricaoTipo da API, usado quando o requerimento não cita o
# inciso do art. 161 do RICD.
TITLE_BASIS = (
    (re.compile(r"destaque\s+de\s+emenda", re.I), "II"),
    (re.compile(r"destaque\s+para\s+vota[cç][aã]o\s+em\s+separado", re.I), "I"),
    (re.compile(r"proposi[cç][aã]o\s+aut[oô]noma|projeto\s+aut[oô]nomo", re.I), "III"),
    (re.compile(r"destaque\s+de\s+prefer[eê]ncia", re.I), "IV"),
)
RICD_REFERENCE = re.compile(r"(?i)\bart(?:igo)?s?\.?\s*\d+[^.;\n]{0,40}?\b(?:do\s+Regimento\s+Interno|do\s+RICD)\b")
REQUEST_START = re.compile(
    r"destaque\s+para\s+vota[cç][aã]o\s+em\s+separado\s+(?:do\(a\)|d[oa]s?|de)?\s*|destaque\s+para\s*,?\s*", re.I
)
REQUEST_END = re.compile(
    r"\b(?:JUSTIFICA[CÇ][AÃ]O|Sala\s+das\s+(?:Sess[oõ]es|Reuni[oõ]es)|com\s+a\s+finalidade|requeiro\s+deferimento|Bras[ií]lia,)\b",
    re.I,
)
JUSTIFICATION = re.compile(r"(?im)^[ \t]*JUSTIFICA[CÇ][AÃ]O\b")
HEADING_PREFIX = r"\bEMENDA\s+(?:DE\s+REDA[CÇ][AÃ]O\s+|DE\s+RELATOR\s+|MODIFICATIVA\s+|ADITIVA\s+|SUPRESSIVA\s+)?N[º°o.]*\s*"
AMENDMENT_HEADING = re.compile(HEADING_PREFIX + r"(\d+)", re.I)
SEPARATE_VOTE = re.compile(r"vota[cç][aã]o\s+em\s+separado", re.I)
EMENDA_REDACAO = re.compile(r"\bemendas?\s+de\s+reda[cç][aã]o\b", re.I)
SHARED_TAIL = re.compile(r"\b(?:ambos|ambas|todos|todas)\b", re.I)
SAME_SENTENCE = re.compile(r"\s*[;,]?\s*(?:e\s*)?")
PARTIAL_REDACTION = re.compile(r"\.{6,}|…{3,}|(?:\.\s?){6,}")
SUBSTITUTIVE_HEADING = re.compile(r"(?m)^[ \t]*(?:SUBSTITUTIVO|SUBEMENDA\s+SUBSTITUTIVA|PROJETO\s+DE\s+LEI\s+DE\s+CONVERS)")
# Cabeçalho normativo de artigo: "Art. 5º O ...", "Art. 144.", "“Art. 22 (...)", "Art. 1º-A.". Exclui prosa
# que só começa a linha com "art. 144 da Constituição..." por quebra de linha do PDF.
ARTICLE_LINE = re.compile(
    r"(?m)^[ \t]*(?P<quote>[\"“'‘«]?)[ \t]*(?:Art|ART|Artigo|ARTIGO)\.?\s*(\d{1,4})\s*[ºo°]?\s*"
    r"(?:-\s*([A-Z](?![a-zà-ú])|\d{1,2}(?!\d)))?(?=\s*[.:\-–—(…]|\s*$|\s+[A-ZÁÉÍÓÚÂÊÔÃÕÇ\"“\d])"
)
PARAGRAPH_ANY = re.compile(r"(?i)(?:§\s*\d+\s*[ºo°]?(?:\s*-\s*[A-Z](?![a-z]))?|par[aá]grafo\s+(?:\d+|[uú]nico))")
LINE_START = r"(?m)^[ \t\"“]*"
INCISO_START = r"[IVXLCDM]+(?:\s*-\s*[A-Z])?\s*[-–—]"
ALINEA_START = r"[a-z]\s*\)"
ITEM_START = r"\d+(?:\.\d+)*\s*[.)\-–]"
PARAGRAPH_START = r"(?:§\s*\d|Par[aá]grafo\s+[uú]nico)"
# Preâmbulo de alteração: "O Decreto-Lei nº 1.804, de 1980, passa a vigorar..." ou "Ficam acrescidos à CF...".
AMENDING_AFTER = re.compile(r"[^.;:]{0,160}?\b(?:passa(?:m)?\s+a\s+vigorar|fica(?:m)?\s+acrescid)", re.I)
AMENDING_BEFORE = re.compile(r"\b(?:acrescid[oa]s?|alterad[oa]s?|inclu[ií]d[oa]s?)\s+(?:à|ao|na|no)\s*$", re.I)
# Substitutivo e parecer formam um só grupo, ordenado por data: o substitutivo em votação costuma estar
# dentro do parecer de plenário mais recente (PRLP/PPR), e não no SBT cadastrado na comissão.
SEARCH_ORDER = {
    "substitutivo": (("substitutivo", "parecer"), ("texto_principal",), ("apoio",)),
    "texto_principal": (("texto_principal",), ("substitutivo", "parecer"), ("apoio",)),
}
WORD = re.compile(r"[A-Za-zÀ-ú]{2,}")
MAX_DEVICES = 12
MAX_CANDIDATES = 3


def _request_clause(text: str) -> str:
    """Objeto do requerimento de destaque, sem cabeçalho, justificação nem rodapé.

    O sistema da Câmara gera "Requeiro ... nos termos do art. 161, I, do Regimento Interno ..., destaque
    para votação em separado do(a) <texto do autor> apresentado à(ao) PL n/aaaa"; o objeto é o que vem
    depois do último "destaque para" antes da justificação.
    """
    head = REQUEST_END.split(text[:7000], maxsplit=1)[0]
    markers = list(REQUEST_START.finditer(head))
    clause = head[markers[-1].end():] if markers else RICD_REFERENCE.sub(" ", head)
    return clause[:2500].strip(" ,\n")


def _amendment_body(text: str, number: int) -> tuple[int, str]:
    """Início e texto do comando da emenda até a justificação.

    Emendas de comissão (de relator ou de redação) costumam vir dentro do PDF do parecer: nesse caso o
    corpo vai do título "EMENDA (DE REDAÇÃO) Nº n" seguido de comando até o título da emenda seguinte.
    """
    start, end = 0, len(text)
    if number:
        heading = re.compile(HEADING_PREFIX + rf"0*{number}\b", re.I)
        for match in heading.finditer(text):
            if next(commands(text[match.end():match.end() + 600]), None):
                start = match.start()
                following = next((m for m in AMENDMENT_HEADING.finditer(text, match.end()) if int(m[1]) != number), None)
                end = following.start() if following else end
                break
    body = text[start:end]
    cut = JUSTIFICATION.search(body)
    return start, body[:cut.start()] if cut else body[:20000]


def _refine_with_proposed(devices: list[Device], proposed: str) -> list[Device]:
    """Nova redação parcial (linhas pontilhadas) de artigo inteiro: usa os dispositivos efetivamente redigidos.

    "Dê-se ao art. 6º a seguinte redação: 'Art. 6º ... (caput) ....... § 3º ... § 4º ...'" altera caput,
    § 3º e § 4º, e mantém os demais.
    """
    if not devices or any(d.has_sub() for d in devices) or not PARTIAL_REDACTION.search(proposed):
        return devices
    wanted = {d.article.upper(): d for d in devices}
    refined = [replace(wanted[p.article.upper()], paragraph=p.paragraph, caput=p.caput, inciso=p.inciso, alinea=p.alinea)
               for p in proposed_devices(proposed) if p.article.upper() in wanted]
    return refined or devices


def amendment_devices(text: str, number: int = 0) -> tuple[list[Device], list[str], int, str, str]:
    """Dispositivos atingidos pelos comandos de uma emenda, com a operação de cada comando.

    Devolve (dispositivos, notas, início do corpo, corpo, operação do primeiro comando).
    """
    start, body = _amendment_body(text, number)
    found_commands = list(commands(body))
    role = text_role_for(body[:600] + " " + " ".join(clause for _, clause, _, _ in found_commands))
    found: list[tuple[int, list[Device], str]] = []
    notes: list[str] = []
    first_operation = operation_for(found_commands[0][0]) if found_commands else ""
    sentence = 0
    previous_end = None
    for index, (verb, clause, clause_start, clause_end) in enumerate(found_commands):
        if previous_end is not None and not SAME_SENTENCE.fullmatch(body[previous_end:clause_start]):
            sentence += 1
        previous_end = clause_end
        next_start = found_commands[index + 1][2] if index + 1 < len(found_commands) else len(body)
        proposed = body[clause_end:min(next_start, clause_end + 8000)]
        operation = operation_for(verb)
        devices = resolve(clause, operation=operation, text_role=role)
        if operation == "nova_redacao":
            devices = _refine_with_proposed(devices, proposed)
        if not devices and verb.lower().startswith("passa"):
            if found and found[-1][0] == sentence and found[-1][1]:
                # "Altere-se o art. 5º da EC 103/2019, que passa a vigorar com a seguinte redação:": a
                # continuação declarativa tem o mesmo objeto do comando anterior.
                found[-1] = (sentence, _refine_with_proposed(found[-1][1], proposed), found[-1][2])
                continue
            norm = next((normalize_norm(m) for m in NORM_RE.finditer(clause) if normalize_norm(m)), "")
            devices = [replace(d, operation=operation, text_role=role, amended_norm=norm) for d in proposed_devices(proposed)]
        if not devices and operation == "acrescimo" and is_new_article(clause):
            notes.append("Acréscimo de artigo novo, sem dispositivo correspondente no texto-base")
            continue
        for device in devices:
            device.raw = " ".join(clause.split())[:500]
        found.append((sentence, devices, clause))
    for index, (sentence, devices, _clause) in enumerate(found):
        later = [d for s, ds, c in found[index + 1:] if s == sentence and SHARED_TAIL.search(c) for d in ds]
        for device in devices:
            donor = next((d for d in later if d.amended_norm or d.container_article), None)
            if donor and not device.amended_norm and not device.container_article:
                device.amended_norm, device.container_article = donor.amended_norm, donor.container_article
    unique, seen = [], set()
    for device in (d for _, ds, _ in found for d in ds):
        key = (device.article, device.paragraph, device.caput, device.inciso, device.alinea, device.item,
               device.expression, device.amended_norm)
        if key not in seen:
            seen.add(key)
            unique.append(device)
    if len(unique) > MAX_DEVICES:
        notes.append(f"{len(unique) - MAX_DEVICES} dispositivo(s) adicional(is) citado(s) no comando não recortado(s)")
    return unique[:MAX_DEVICES], notes, start, body, first_operation


def classify_request(clause: str, text: str, type_description: str = "") -> tuple[str, str, str, list[str]]:
    """Modo e operação do destaque segundo o RICD.

    Art. 161, I: votação em separado de parte da proposição (DVS). Pelo art. 162, VI, a parte destacada só
    integra o texto se for aprovada: o efeito pretendido é a supressão. Art. 161, II: destaque de emenda,
    que só integra o texto se aprovada: o efeito pretendido é a inclusão da emenda. O inciso citado no
    requerimento vence; sem ele, vale o tipo oficial do destaque (título do PDF ou descricaoTipo da API).
    """
    basis_match = RICD_BASIS.search(text[:4000])
    basis = f"art. 161, {basis_match[1].upper()}" if basis_match else ""
    title = f"{type_description}\n{text[:400]}"
    inciso = basis_match[1].upper() if basis_match else next((i for rx, i in TITLE_BASIS if rx.search(title)), "")
    cites_amendment = bool(EMENDA_REF.search(clause) or EMENDA_GENERIC.search(clause))
    warnings: list[str] = []
    if inciso in {"III", "IV"}:
        label = basis or f"art. 161, {inciso} (pelo tipo do destaque)"
        return "indefinido", "indeterminada", basis, [f"Destaque do {label} do RICD (projeto autônomo ou preferência) fora do escopo do parser"]
    if cites_amendment or inciso == "II":
        if basis.endswith(", I"):
            warnings.append("Requerimento cita o art. 161, I, do RICD (parte da proposição), mas o objeto é emenda; conferir")
        return "destaque_emenda", "inclusao_emenda", basis, warnings
    if inciso == "I" or SEPARATE_VOTE.search(clause) or SEPARATE_VOTE.search(text[:3000]):
        return "dvs", "supressao_pretendida", basis, warnings
    return "indefinido", "indeterminada", basis, ["Modalidade do destaque não identificada"]


def linked_amendment_ref(clause: str) -> dict[str, Any] | None:
    explicit = EMENDA_REF.search(clause)
    if explicit:
        return {"sigla": explicit[1].upper(), "number": int(explicit[2])}
    generic = EMENDA_GENERIC.search(clause)
    return {"sigla": "", "number": int(generic[1])} if generic else None


def link_amendment(doc: dict[str, Any], ref: dict[str, Any] | None,
                   documents: list[dict[str, Any]]) -> tuple[str | None, list[str]]:
    """Resolve a emenda destacada. ID oficial no metadado vence; número sozinho só vale com candidata única."""
    explicit = (doc.get("metadata", {}).get("vinculo") or {}).get("emenda")
    if isinstance(explicit, dict) and explicit.get("id"):
        wanted = f"camara:{explicit['id']}"
        if any(x["source_id"] == wanted and x["role"] == "emenda" for x in documents):
            return wanted, []
    if not ref:
        return None, ["Emenda destacada não identificada no requerimento"]
    emendas = [x for x in documents if x["role"] == "emenda" and x["number"] == ref["number"]]
    candidates = [x for x in emendas if x["kind"] == ref["sigla"]] if ref["sigla"] else emendas
    label = f"{ref['sigla'] or 'Emenda'} {ref['number']}"
    if len(candidates) == 1:
        return candidates[0]["source_id"], []
    if candidates:
        return None, [f"Vínculo com a {label} é ambíguo: {', '.join(x['source_id'] for x in candidates)}"]
    return None, [f"{label} destacada não está no lote; a análise ficaria limitada ao requerimento"]


def paragraph_marker(value: str) -> re.Pattern[str]:
    """Marcador exato: '§ 2º' não casa '§ 2º-B' e vice-versa; 'unico' casa 'Parágrafo único'."""
    if value == "unico":
        return re.compile(r"(?i)par[aá]grafo\s+[uú]nico")
    match = re.fullmatch(r"(\d{1,3})(?:-([A-Za-z]))?", value.strip())
    if not match:
        raise ValueError(f"Parágrafo inválido: {value}")
    digits, suffix = match[1], (match[2] or "").upper()
    head = rf"(?:§|par[aá]grafo)\s*{re.escape(digits)}(?!\d)"
    if suffix:
        return re.compile(head + rf"\s*(?:[ºo°])?\s*-\s*{suffix}\b", re.I)
    return re.compile(head + r"(?!\s*(?:[ºo°])?\s*-\s*[A-Za-z]\b)\s*(?:[ºo°])?", re.I)


def _is_boundary(text: str, pos: int) -> bool:
    """Um marcador só abre dispositivo no início de linha ou depois de pontuação final."""
    prefix = text[max(0, pos - 4):pos]
    return pos == 0 or "\n" in prefix or prefix.rstrip().endswith((".", ";", ":", "\"", "”"))


def _article_starts(text: str) -> list[tuple[int, str, bool]]:
    """(posição, artigo, entre aspas). Pela LC 95/1998, texto alterado de outra norma vem entre aspas;
    artigo sem aspas é da própria proposição."""
    return [(m.start(), f"{m[2]}-{m[3].upper()}" if m[3] else m[2], bool(m["quote"])) for m in ARTICLE_LINE.finditer(text)]


def _end_at_next(text: str, start: int, limit: int, *, paragraph: bool) -> int:
    end = min(len(text), limit)
    for match in ARTICLE_LINE.finditer(text, start + 1):
        if match.start() < end:
            end = match.start()
        break
    if paragraph:
        for match in PARAGRAPH_ANY.finditer(text, start + 1, end):
            if _is_boundary(text, match.start()):
                return match.start()
    return end


def _inciso_marker(value: str) -> str:
    head, _, suffix = value.partition("-")
    if suffix:
        return rf"{re.escape(head)}\s*-\s*{re.escape(suffix)}\s*[-–—]"
    return rf"{re.escape(head)}(?!\s*-\s*[A-Z]\b)\s*[-–—]"


def _narrow(text: str, start: int, end: int, device: Device) -> tuple[int, int] | None:
    """Recorta caput, inciso, alínea e item dentro da região do nível superior.

    Cada recorte termina no próximo dispositivo do mesmo nível ou de nível superior: um inciso do caput
    para no inciso seguinte ou no primeiro parágrafo. Inciso citado sem parágrafo é do caput.
    """
    if (device.caput or device.inciso) and not device.paragraph:
        end = next((m.start() for m in PARAGRAPH_ANY.finditer(text, start + 1, end) if _is_boundary(text, m.start())), end)
    levels = (
        (device.inciso, _inciso_marker(device.inciso) if device.inciso else "", (INCISO_START, PARAGRAPH_START)),
        (device.alinea, rf"{re.escape(device.alinea)}\s*\)", (ALINEA_START, INCISO_START, PARAGRAPH_START)),
        (device.item, rf"{re.escape(device.item)}\s*[.)\-–]", (ITEM_START, ALINEA_START, INCISO_START, PARAGRAPH_START)),
    )
    for value, marker, stops in levels:
        if not value:
            continue
        hit = re.compile(LINE_START + marker).search(text, start, end)
        if not hit:
            return None
        start = hit.start()
        nxt = re.compile(LINE_START + "(?:" + "|".join(stops) + ")").search(text, hit.end(), end)
        end = nxt.start() if nxt else end
    return start, end


def _norm_owner(text: str, pos: int) -> str:
    """Norma cujo bloco de alteração contém a posição ("O Decreto-Lei nº 1.804... passa a vigorar...")."""
    owner = ""
    for match in NORM_RE.finditer(text, 0, pos):
        value = normalize_norm(match)
        if value and (AMENDING_AFTER.match(text, match.end()) or AMENDING_BEFORE.search(text[max(0, match.start() - 60):match.start()])):
            owner = value
    return owner


def find_device(source_id: str, extracted: Extracted, device: Device, *, prefer_substitutive: bool = False) -> list[dict[str, Any]]:
    """Recortes literais compatíveis com o dispositivo, na ordem do texto.

    Com `amended_norm`, só valem recortes dentro do bloco que altera essa norma: o "art. 1º" do Decreto-Lei
    alterado não se confunde com o "art. 1º" da própria MPV. Em parecer, vale o trecho depois do cabeçalho
    do substitutivo quando houver mais de uma ocorrência.
    """
    text = extracted.text
    if not device.article and not device.paragraph:
        return []
    starts = _article_starts(text)
    regions: list[tuple[int, int, bool]] = []
    if device.paragraph:
        for match in paragraph_marker(device.paragraph).finditer(text):
            if not _is_boundary(text, match.start()):
                continue
            preceding = [(ref, quoted) for pos, ref, quoted in starts if pos <= match.start()]
            if device.article and (not preceding or preceding[-1][0].upper() != device.article.upper()):
                continue
            quoted = preceding[-1][1] if preceding else False
            regions.append((match.start(), _end_at_next(text, match.start(), match.start() + 3200, paragraph=True), quoted))
    else:
        for pos, ref, quoted in starts:
            if ref.upper() == device.article.upper():
                regions.append((pos, _end_at_next(text, pos, pos + 3200, paragraph=False), quoted))
    out, quoted_flags = [], []
    for start, end, quoted in regions:
        narrowed = _narrow(text, start, end, device)
        # Recorte sem conteúdo ("§ 2º-B do", sobra de tabela ou remissão) não é evidência.
        if narrowed and len(WORD.findall(text, narrowed[0], narrowed[1])) >= 3:
            out.append(evidence(source_id, extracted, *narrowed, "dispositivo"))
            quoted_flags.append(quoted)
    if device.amended_norm:
        out = [x for x in out if same_norm(_norm_owner(text, x["start"]), device.amended_norm)]
    elif any(quoted_flags) and not all(quoted_flags):
        out = [x for x, quoted in zip(out, quoted_flags) if not quoted]
    if (prefer_substitutive or device.text_role == "substitutivo") and len(out) > 1:
        heading = SUBSTITUTIVE_HEADING.search(text)
        out = [x for x in out if heading and x["start"] > heading.start()] or out
    return out


def expression_evidence(source_id: str, extracted: Extracted, region: dict[str, Any], expression: str) -> dict[str, Any] | None:
    words = [re.escape(w) for w in expression.split()]
    if not words:
        return None
    match = re.compile(r"\s+".join(words), re.I).search(extracted.text, region["start"], region["end"])
    return evidence(source_id, extracted, match.start(), match.end(), "expressao") if match else None


def _date_key(doc: dict[str, Any]) -> tuple[str, int]:
    digits = re.sub(r"\D", "", doc["source_id"].split(":")[-1])
    official = doc["source_id"].startswith("camara:") and digits.isdigit()
    return str(doc.get("metadata", {}).get("date") or ""), int(digits[:12]) if official else 0


def ranked_sources(documents: list[dict[str, Any]], device: Device, exclude: set[str]) -> list[list[dict[str, Any]]]:
    """Grupos de fontes na ordem de busca; dentro de cada grupo, a versão mais recente primeiro."""
    groups = SEARCH_ORDER.get(device.text_role, SEARCH_ORDER["texto_principal"])
    eligible = [d for d in documents if d["source_id"] not in exclude and not d.get("metadata", {}).get("law_ref")]
    return [sorted((d for d in eligible if d["role"] in roles), key=_date_key, reverse=True) for roles in groups]


def locate(device: Device, documents: list[dict[str, Any]], extracted: dict[str, Extracted],
           failed: list[dict[str, Any]], exclude: set[str]) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    """Recorte do dispositivo no texto-base, com a regra de versões do fluxo original.

    A versão mais recente de cada grupo vence (o operador do fluxo original marca "o mais atual de cada
    sigla"); versões anteriores com redação diferente viram nota, não ambiguidade. Uma fonte que falhou
    (download ou extração) só bloqueia o caso quando poderia conter a redação procurada.
    """
    warnings: list[str] = []
    notes: list[str] = []
    label = device.label()
    groups = SEARCH_ORDER.get(device.text_role, SEARCH_ORDER["texto_principal"])
    hits: list[tuple[int, dict[str, Any], list[dict[str, Any]]]] = []
    for index, group in enumerate(ranked_sources(documents, device, exclude)):
        for doc in group:
            source = extracted.get(doc["source_id"])
            found = find_device(doc["source_id"], source, device, prefer_substitutive=doc["role"] == "parecer") if source else []
            if found:
                hits.append((index, doc, found))
    searched_roles = {role for roles in groups[:2] for role in roles}
    relevant_failures = [f for f in failed if f.get("role") in searched_roles]
    if not hits:
        tail = f" (fontes indisponíveis: {', '.join(f['source_id'] for f in relevant_failures)})" if relevant_failures else ""
        if device.operation == "acrescimo":
            notes.append(f"{label}: dispositivo novo; não há redação atual a recortar{tail}")
        elif device.amended_norm and device.operation != "supressao_pretendida" and not relevant_failures:
            notes.append(f"{label}: não consta do texto-base (a proposição não o altera); para o 'antes', "
                         f"capture a redação vigente como apoio com law_ref \"{device.amended_norm}\"")
        else:
            warnings.append(f"{label}: trecho literal não localizado nas fontes de apoio{tail}")
        return [], warnings, notes
    group_index, doc, found = hits[0]
    distinct = list(dict.fromkeys(_norm_quote(x) for x in found))
    chosen = found[:1] if len(distinct) == 1 else [next(x for x in found if _norm_quote(x) == q) for q in distinct[:MAX_CANDIDATES]]
    if len(distinct) > 1:
        warnings.append(f"{label}: {len(distinct)} redações diferentes em {doc['source_id']}; conferir texto-base")
    if group_index > 0:
        notes.append(f"{label}: localizado em {doc['role']} ({doc['source_id']}), e não em {'/'.join(groups[0])}")
        missing = [f["source_id"] for f in relevant_failures if f.get("role") in groups[0]]
        if missing:
            warnings.append(f"{label}: fonte do papel declarado indisponível ({', '.join(missing)})")
    newer = [f["source_id"] for f in relevant_failures if f.get("role") in groups[group_index]
             and str(f.get("date") or "") > _date_key(doc)[0]]
    if newer:
        warnings.append(f"{label}: versão mais recente indisponível ({', '.join(newer)}); recorte tirado de {doc['source_id']}")
    first = _norm_quote(chosen[0])
    older = [d["source_id"] for i, d, f in hits[1:] if i == group_index and all(_norm_quote(x) != first for x in f)]
    if older:
        notes.append(f"{label}: redação diferente em versão anterior ({', '.join(older[:3])})")
    if device.text_role == "texto_principal" and group_index == 0:
        voted = next(((d, f) for i, d, f in hits if i == 1), None)
        if voted and _norm_quote(voted[1][0]) != first:
            chosen.append(voted[1][0])
            notes.append(f"{label}: redação diferente no substitutivo ou parecer mais recente ({voted[0]['source_id']}), anexada")
    return chosen, warnings, notes


def _norm_quote(item: dict[str, Any]) -> str:
    return " ".join(item["quote"].split()).casefold()


def current_law(device: Device, documents: list[dict[str, Any]], extracted: dict[str, Extracted]) -> list[dict[str, Any]]:
    """Redação vigente da norma alterada, quando capturada como apoio com `law_ref` igual a `amended_norm`.

    Equivale à consulta ao Planalto do fluxo original, mas com proveniência: a cópia da lei entra pelo
    manifesto, com URL, data e hash, e o recorte é tão literal quanto os demais.
    """
    if not device.amended_norm:
        return []
    out = []
    for doc in documents:
        if doc["role"] != "apoio" or not same_norm(str(doc.get("metadata", {}).get("law_ref") or ""), device.amended_norm):
            continue
        source = extracted.get(doc["source_id"])
        hits = find_device(doc["source_id"], source, replace(device, amended_norm="", text_role="texto_principal")) if source else []
        out += [{**hit, "kind": "redacao_vigente"} for hit in hits[:1]]
    return out


def prepare(workspace: Path) -> dict[str, Any]:
    workspace = Path(workspace).resolve()
    manifest = read_json(workspace / "manifest.json")
    check_schema("manifest", manifest)
    documents = manifest["documents"]
    extracted: dict[str, Extracted] = {}
    failed: list[dict[str, Any]] = list(manifest.get("failures", []))
    for doc in documents:
        path = (workspace / doc["path"]).resolve()
        if not path.is_relative_to(workspace):
            raise ValueError("Caminho do manifesto sai da área de trabalho")
        if sha256_file(path) != doc["sha256"]:
            raise ValueError(f"Integridade divergente: {doc['source_id']}")
        try:
            extracted[doc["source_id"]] = extract(path)
        except (ValueError, OSError, RuntimeError) as exc:
            failed.append({"source_id": doc["source_id"], "role": doc["role"], "kind": doc["kind"],
                           "number": doc["number"], "date": doc.get("metadata", {}).get("date", ""),
                           "reason": f"{type(exc).__name__}: {exc}"})
    targets = [x for x in documents if x["role"] in {"destaque", "emenda"}]
    if not targets:
        raise ValueError("Manifesto sem destaques ou emendas analisáveis")
    package_warnings = list(manifest["warnings"])
    package_warnings += [f"Fonte {f['source_id']} ({f.get('kind', '')} {f.get('number', '')}, {f.get('role', '')}) "
                         f"indisponível: {f['reason']}" for f in failed]
    numbering = Counter((x["kind"], x["number"]) for x in targets)
    cases = [_case(doc, documents, extracted, failed, numbering, manifest) for doc in targets]
    package = {"schema_version": SCHEMA_VERSION, "run_id": manifest["run_id"],
               "warnings": list(dict.fromkeys(package_warnings)), "cases": cases}
    check_schema("case", package)
    write_json(workspace / "cases.json", package)
    return package


def _case(doc: dict[str, Any], documents: list[dict[str, Any]], extracted: dict[str, Extracted],
          failed: list[dict[str, Any]], numbering: Counter, manifest: dict[str, Any]) -> dict[str, Any]:
    source_id = doc["source_id"]
    raw = extracted.get(source_id)
    text = raw.text if raw else ""
    warnings: list[str] = []
    notes: list[str] = []
    case_evidence: list[dict[str, Any]] = []
    linked: str | None = None
    ref: dict[str, Any] | None = None
    basis = ""
    devices: list[Device] = []
    own_failure = next((f["reason"] for f in failed if f["source_id"] == source_id), "")
    if own_failure:
        warnings.append(f"Documento do caso sem texto utilizável: {own_failure}")
    warnings += [w for w in manifest["warnings"] if source_id in w]
    if doc["role"] == "destaque":
        clause = _request_clause(text)
        mode, operation, basis, mode_warnings = classify_request(clause, text, str(doc.get("metadata", {}).get("type_description") or ""))
        warnings += mode_warnings
        if raw:
            case_evidence.append(evidence(source_id, raw, 0, min(3500, len(text)), "requerimento_ou_emenda"))
        if mode == "destaque_emenda":
            ref = linked_amendment_ref(clause)
            linked, link_warnings = link_amendment(doc, ref, documents)
            warnings += link_warnings
            part = [d for d in resolve(clause, operation="inclusao_emenda", text_role=text_role_for(clause)) if d.article]
            if part:
                devices = part
                notes.append(f"Destaque de parte da emenda: {', '.join(d.label() for d in part)}")
            linked_text = extracted.get(linked) if linked else None
            if linked_text:
                linked_doc = next(d for d in documents if d["source_id"] == linked)
                amendment, amendment_notes, body_start, body, _ = amendment_devices(linked_text.text, linked_doc["number"])
                case_evidence.append(evidence(linked, linked_text, body_start, body_start + min(12000, len(body)), "emenda_vinculada"))
                notes += amendment_notes
                for device in part:
                    case_evidence += [{**hit, "kind": "emenda_vinculada"} for hit in find_device(linked, linked_text, replace(device, amended_norm=""))[:1]]
                if not part:
                    devices = amendment
            elif linked:
                warnings.append(f"Emenda vinculada {linked} sem texto utilizável")
        elif mode == "dvs":
            role = text_role_for(clause) if text_role_for(clause) == "substitutivo" else text_role_for(text[:3000])
            devices = resolve(clause, operation="supressao_pretendida", text_role=role)
            for device in devices:
                device.raw = " ".join(clause.split())[:500]
            if not devices:
                warnings.append("Dispositivo destacado não identificado no requerimento")
    else:
        devices, amendment_notes, body_start, body, first_operation = (
            amendment_devices(text, int(doc.get("number") or 0)) if raw else ([], [], 0, "", ""))
        notes += amendment_notes
        # ERD é a sigla oficial de emenda de redação; EMR é "Emenda de Relator" e pode alterar mérito.
        if doc["kind"].upper() == "ERD" or EMENDA_REDACAO.search((body or text)[:1500]):
            mode, operation = "emenda_redacao", "ajuste_redacao"
        else:
            mode = "emenda"
            operation = (devices[0].operation if devices else first_operation) or "indeterminada"
        if raw:
            case_evidence.append(evidence(source_id, raw, body_start, body_start + min(12000, len(body)), "requerimento_ou_emenda"))
            if not devices and not amendment_notes:
                warnings.append("Comando da emenda sem dispositivo identificável")
    exclude = {source_id} | ({linked} if linked else set())
    for device in devices:
        target = device.parent() if device.operation == "acrescimo" else device
        if not target.has_device():
            notes.append(f"{device.label()}: dispositivo novo; não há redação atual a recortar")
            continue
        found, device_warnings, device_notes = locate(target, documents, extracted, failed, exclude)
        case_evidence += found
        warnings += device_warnings
        notes += device_notes
        if device.expression and found:
            hit = expression_evidence(found[0]["source_id"], extracted[found[0]["source_id"]], found[0], device.expression)
            if hit:
                case_evidence.append(hit)
            else:
                warnings.append(f'Expressão "{device.expression}" não consta literalmente do trecho localizado')
        case_evidence += current_law(target, documents, extracted)
    if numbering[(doc["kind"], doc["number"])] > 1:
        notes.append("Numeração repetida no lote (outra rodada de destaques); o source_id distingue os objetos")
    if raw:
        warnings += raw.warnings
    if doc.get("metadata", {}).get("inferred_role"):
        warnings.append("Papel do documento inferido pelo nome do arquivo; conferir")
    target = devices[0].as_dict() if devices else Device().as_dict()
    return {
        "case_id": stable_hash([manifest["run_id"], source_id])[:20], "source_id": source_id,
        "role": doc["role"], "object_type": doc["kind"], "object_number": doc["number"],
        "mode": mode, "operation": operation, "ricd_basis": basis,
        "target": target, "additional_targets": [d.as_dict() for d in devices[1:]],
        "linked_amendment_ref": ref, "linked_amendment_id": linked,
        "evidence": list({x["evidence_id"]: x for x in case_evidence}.values()),
        "warnings": list(dict.fromkeys(warnings)), "notes": list(dict.fromkeys(notes)),
        "review_required": bool(warnings),
    }
