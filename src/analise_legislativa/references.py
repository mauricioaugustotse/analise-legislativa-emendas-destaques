"""Leitura determinística de referências a dispositivos: art., §, inciso, alínea, item e expressão.

Transforma frases como "o inciso II e os §§ 2º, 2º-A e 11 do art. 144 da Constituição
Federal, no art. 1º da PEC" em dispositivos completos. Não decide se o dispositivo existe:
essa conferência é feita depois, contra o texto literal das fontes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from typing import Iterator

LEVELS = ("article", "paragraph", "inciso", "alinea", "item")
RANK = {"article": 0, "paragraph": 1, "caput": 1, "inciso": 2, "alinea": 3, "item": 4, "expression": 5}
ROMAN = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}

_ORD = r"(?:[ºo°]|\s+[º°])?"
# Sufixo colado ("144-A", "2º-F", "1º-1") vale sempre; com espaço ("8º- A", quebra de linha do PDF)
# só antes de pontuação ou de "do/da/de", para "Art. 1º - O art." não virar "1-O".
_LETTER_SFX = r"(?:-[A-Z](?![a-zà-ú\d])|\s*-\s*[A-Z](?=\s*[,.;)]|\s+d[oae]s?\b|\s*$))"
_SFX = rf"(?:{_LETTER_SFX}|-\d{{1,2}}(?!\d))?"
_ANUM = rf"\d{{1,4}}{_ORD}{_SFX}"
_PNUM = rf"\d{{1,3}}{_ORD}{_LETTER_SFX}?"
_RNUM = rf"[IVXLCDM]+{_LETTER_SFX}?(?![A-Za-z])"
_SEP = r"\s*(?:,\s*(?:e\s+)?|\be\b|\ba\b|\baté\b)\s*"
_QO = "\"“'‘«"
_QC = "\"”'’»"

ARTICLE_RE = re.compile(
    rf"(?<![\w-])(?i:arts?|artigos?)(?!\w)\.?\s*({_ANUM}(?:{_SEP}(?:(?i:arts?|artigos?)\.?\s*)?{_ANUM})*)"
)
PARAGRAPH_RE = re.compile(
    rf"(?:§§?|(?i:par[aá]grafos?)(?!\w))\s*º?\s*({_PNUM}(?:{_SEP}(?:§§?\s*º?\s*)?{_PNUM})*)"
)
SOLE_PARAGRAPH_RE = re.compile(r"(?i:par[aá]grafo\s+[uú]nico)")
INCISO_RE = re.compile(rf"(?i:\bincisos?)\s+({_RNUM}(?:{_SEP}(?:(?i:incisos?)\s+)?{_RNUM})*)")
ALINEA_RE = re.compile(
    rf"(?i:\bal[ií]neas?)(?:\s*[{_QO}]|\s+)([a-z])[{_QC}]?(?!\w)((?:{_SEP}(?:[{_QO}][a-z][{_QC}]|[b-df-np-z](?!\w)))*)"
)
_ALINEA_NEXT = re.compile(rf"({_SEP})(?:[{_QO}]([a-z])[{_QC}]|([b-df-np-z])(?!\w))")
ITEM_RE = re.compile(r"(?i:\bite(?:m|ns))\s+(\d{1,3}(?:\.\d+)*)(?=\s*,?\s*(?i:d[oa]s?)\s+(?i:al[ií]nea|inciso))")
CAPUT_RE = re.compile(r"(?i:\bcaput\b)")
EXPRESSION_RE = re.compile(
    rf"(?i:\b(?:express(?:[aã]o|[oõ]es)|palavras?|termos?|voc[aá]bulos?))\s*[{_QO}]([^{_QC}\n]{{1,400}}?)\s*[{_QC}]"
)
NORM_RE = re.compile(
    r"(?<!rojeto de )(?<!ROJETO DE )(?<!roposta de )(?<!ROPOSTA DE )"
    r"\b(?P<kind>(?i:Constitui[cç][aã]o(?:\s+Federal)?|ADCT(?!\w)|Ato\s+das\s+Disposi[cç][oõ]es\s+Constitucionais\s+Transit[oó]rias"
    r"|Emenda\s+Constitucional|Lei\s+Complementar|Decreto\s*-?\s*Lei|Lei|Decreto)|CF(?!\w)|LC(?!\w))"
    r"(?:\s*(?i:n[.ºo°]*)?\s*(?P<number>\d{1,3}(?:\.\d{3})+|\d{1,6}))?"
    r"(?:\s*[/,]?\s*(?:(?i:de)\s+(?:\d{1,2}(?:º|o)?\s+(?i:de)\s+[A-Za-zç]+\s+(?i:de)\s+)?)?(?P<year>(?:19|20)\d{2})(?!\d))?"
)
_PROPOSITION = (
    r"(?:presente\s+)?(?:proposta(?:\s+de\s+emenda\s+(?:(?:à|a)\s+)?constitu\w+)?\b|PEC\b|projeto(?:\s+de\s+lei)?(?:\s+complementar|\s+de\s+convers[aã]o)?\b"
    r"|PLV\b|PLP\b|PL\b|medida\s+provis[oó]ria|MPV\b|substitutiv[oa]\b|subemenda\s+substitutiva|emenda(?!\s+constitucional)\b)"
)
PROPOSITION_AFTER_RE = re.compile(rf"(?i:\s*,?\s*(?:d[oa]s?|n[oa]s?|pel[oa]|à|ao)\s+{_PROPOSITION})")
SUBSTITUTIVE_RE = re.compile(
    r"(?i:\b(?:a[os]?|à|d[oa]s?|n[oa]s?|pel[oa])\s+(?:presente\s+)?(?:substitutiv[oa]|subemenda\s+substitutiva|projeto\s+de\s+lei\s+de\s+convers[aã]o|PLV)\b)"
)
CHAIN_RE = re.compile(
    r"(?i:\b(?:d[oa]s?|de|a[os]|à|n[oa]s?|pel[oa]s?|constantes?|contid[oa]s?|previst[oa]s?|inserid[oa]s?|integrantes?|acrescid[oa]s?)\b)"
)
DEFINITE_RE = re.compile(r"(?i:(?:^|[\s,;(])(?:o|os|a|as|seus?|suas?)\s*$|\be\b)")
SAME_ARTICLE_RE = re.compile(r"(?i:^\W*(?:deste|desse|do)\s+(?:mesmo\s+)?(?:artigo|dispositivo)\b)")
DECLARATIVE_SUBJECT_RE = re.compile(
    r"\b(?:O|A|Os|As)\s+(?:art|§|inciso|al[ií]nea|Lei|Decreto|Constitui|Medida|caput|par[aá]grafo|reda[cç]|Se[cç][aã]o|Cap[ií]tulo|T[ií]tulo|item|Anexo|Emenda)"
)
ITEM_LABEL_RE = re.compile(r"^\s*[\"“]?\s*(?:Art|At)\.?\s*\d+\s*[ºo°]?\s*\.?\s*[-–]?\s*(?=[A-ZÁÉÍÓÚ])")
COMMAND_RE = re.compile(
    r"(?<![A-Za-zÀ-ÿ])(?i:(?P<verb>D[êeá]\s*-\s*se|D[êe]em\s*-\s*se|Acrescente[m]?\s*-\s*se|Acrescenta[m]?\s*-\s*se|Acresça[m]?\s*-\s*se|"
    r"Acrescente[m]?(?=\s+(?:o|os|a|as|ao|aos|à|às|§|art|inciso|al[ií]nea|par[aá]grafo)\b)|"
    r"Inclua[m]?\s*-\s*se|Inclui[m]?\s*-\s*se|Insira[m]?\s*-\s*se|Altere[m]?\s*-\s*se|Altera[m]?\s*-\s*se|"
    r"Modifique[m]?\s*-\s*se|Suprima[m]?\s*-\s*se|Suprime[m]?\s*-\s*se|Exclua[m]?\s*-\s*se|Retire[m]?\s*-\s*se|"
    r"Revogue[m]?\s*-\s*se|Substitua[m]?\s*-\s*se|Substitui[m]?\s*-\s*se|Renumere[m]?\s*-\s*se|"
    r"passa[m]?\s+a\s+vigorar(?:\s+(?:acrescid[oa]s?|com\s+(?:a|as)\s+seguintes?\s+(?:reda[cç](?:[aã]o|[oõ]es)|altera[cç](?:[aã]o|[oõ]es))))?|"
    r"fica[m]?\s+(?:acrescid[oa]s?|suprimid[oa]s?|revogad[oa]s?|inclu[ií]d[oa]s?)))"
)
COMMAND_OPERATIONS = (
    (re.compile(r"(?i:suprim|exclu|retir|revog)"), "supressao"),
    (re.compile(r"(?i:acresc|acresç|inclu|insir)"), "acrescimo"),
    (re.compile(r"(?i:substitu)"), "substituicao"),
    (re.compile(r"(?i:renumer)"), "renumeracao"),
)
# Fim da cláusula de comando: início da redação proposta, fim de frase ou "; e" antes de outro comando.
# O ponto de "art." e de "nº" não encerra a cláusula.
CLAUSE_END_RE = re.compile(
    r":\s*(?:\n|[\"“'‘«]|$)|(?<![Aa]rt)(?<!rts)(?<!nº)(?<!\bn)\.(?:\s*[”\"]?\s*(?:\n|$)|\s+(?=[A-ZÁÉÍÓÚ“\"]))"
    r"|;\s*(?=e\s)|\n\s*\n"
)
NEW_ARTICLE_RE = re.compile(r"(?i:\bseguintes?\s+(?:artigos?|dispositivos?)\b|\bartigo\s+nos\s+termos\b|\bonde\s+couber\b)")


@dataclass
class Device:
    """Dispositivo atingido. Campo vazio significa "não informado", nunca "inexistente"."""

    article: str = ""
    paragraph: str = ""
    caput: bool = False
    inciso: str = ""
    alinea: str = ""
    item: str = ""
    expression: str = ""
    container_article: str = ""
    amended_norm: str = ""
    text_role: str = "texto_principal"
    operation: str = ""
    raw: str = ""
    start: int = field(default=0, repr=False)
    own: bool = field(default=False, repr=False)

    def as_dict(self) -> dict:
        return {
            "article": self.article, "paragraph": self.paragraph, "caput": self.caput,
            "inciso": self.inciso, "alinea": self.alinea, "item": self.item,
            "expression": self.expression, "container_article": self.container_article,
            "amended_norm": self.amended_norm, "text_role": self.text_role,
            "operation": self.operation, "raw": self.raw,
        }

    def levels(self) -> list[int]:
        found = [RANK[name] for name in LEVELS if getattr(self, name)]
        if self.caput:
            found.append(RANK["caput"])
        if self.expression:
            found.append(RANK["expression"])
        return found

    def deepest(self) -> int:
        return max(self.levels(), default=-1)

    def shallowest_sub(self) -> int:
        return min((x for x in self.levels() if x > 0), default=9)

    def has_device(self) -> bool:
        return bool(self.article or self.paragraph or self.inciso or self.alinea or self.item or self.caput)

    def has_sub(self) -> bool:
        return bool(self.paragraph or self.caput or self.inciso or self.alinea or self.item or self.expression)

    def parent(self) -> "Device":
        """Nível imediatamente superior: o contexto de um acréscimo ("inciso III ao § 2º-B")."""
        up = replace(self, expression="")
        for name in ("item", "alinea", "inciso", "paragraph"):
            if getattr(up, name):
                setattr(up, name, "")
                return up
        if up.caput:
            up.caput = False
            return up
        return replace(up, article="")

    def label(self) -> str:
        parts = []
        if self.expression:
            parts.append(f'expressão "{self.expression}"')
        if self.item:
            parts.append(f"item {self.item}")
        if self.alinea:
            parts.append(f'alínea "{self.alinea}"')
        if self.inciso:
            parts.append(f"inciso {self.inciso}")
        if self.paragraph:
            parts.append("parágrafo único" if self.paragraph == "unico" else f"§ {paragraph_label(self.paragraph)}")
        elif self.caput:
            parts.append("caput")
        if self.article:
            parts.append(f"art. {article_label(self.article)}")
        text = " do ".join(parts) if parts else "dispositivo não identificado"
        return f"{text} ({self.amended_norm})" if self.amended_norm else text


def paragraph_label(value: str) -> str:
    digits, _, suffix = value.partition("-")
    return f"{digits}º-{suffix}" if suffix else f"{digits}º"


def article_label(value: str) -> str:
    digits, _, suffix = value.partition("-")
    head = f"{digits}º" if digits.isdigit() and int(digits) < 10 else digits
    return f"{head}-{suffix}" if suffix else head


def clean_number(raw: str, *, roman: bool = False) -> str:
    """'2º-F' -> '2-F'; '1o' -> '1'; 'XLVI- A' -> 'XLVI-A'; '1º-1' -> '1-1'."""
    raw = re.sub(r"\s+", "", raw).replace("º", "").replace("°", "")
    if not roman:
        raw = re.sub(r"^(\d+)o(?=$|-)", r"\1", raw)
    head, _, suffix = raw.partition("-")
    head = head.upper() if roman else head
    return f"{head}-{suffix.upper()}" if suffix else head


def roman_to_int(value: str) -> int:
    total, previous = 0, 0
    for char in reversed(value.upper()):
        number = ROMAN.get(char, 0)
        total = total - number if number < previous else total + number
        previous = max(previous, number)
    return total


def int_to_roman(number: int) -> str:
    pairs = ((1000, "M"), (900, "CM"), (500, "D"), (400, "CD"), (100, "C"), (90, "XC"),
             (50, "L"), (40, "XL"), (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I"))
    out = []
    for value, symbol in pairs:
        while number >= value:
            out.append(symbol)
            number -= value
    return "".join(out)


def _range(lower: str, upper: str, *, roman: bool) -> list[str]:
    low_head, _, low_suffix = lower.partition("-")
    high_head, _, high_suffix = upper.partition("-")
    if low_suffix and high_suffix and low_head == high_head:
        if low_suffix.isdigit() and high_suffix.isdigit() and int(low_suffix) <= int(high_suffix) <= int(low_suffix) + 30:
            return [f"{low_head}-{n}" for n in range(int(low_suffix), int(high_suffix) + 1)]
        if low_suffix.isalpha() and high_suffix.isalpha() and low_suffix <= high_suffix:
            return [f"{low_head}-{chr(c)}" for c in range(ord(low_suffix), ord(high_suffix) + 1)]
        return []
    if low_suffix or high_suffix:
        return []
    if roman:
        low, high = roman_to_int(low_head), roman_to_int(high_head)
        return [int_to_roman(n) for n in range(low, high + 1)] if 0 < low <= high <= low + 40 else []
    if low_head.isdigit() and high_head.isdigit() and int(low_head) <= int(high_head) <= int(low_head) + 40:
        return [str(n) for n in range(int(low_head), int(high_head) + 1)]
    if low_head.isalpha() and high_head.isalpha() and len(low_head) == len(high_head) == 1 and low_head <= high_head:
        return [chr(c) for c in range(ord(low_head), ord(high_head) + 1)]
    return []


def expand(values: list[str], connectors: list[str], *, roman: bool = False) -> list[str]:
    """Expande intervalos ("10 a 13", "2-C a 2-E", "1-1 a 1-4", "I a VII"); listas ficam como estão."""
    out: list[str] = []
    index = 0
    while index < len(values):
        is_range = index < len(connectors) and re.fullmatch(r"\s*(?:a|até)\s*", connectors[index], re.I)
        if is_range and index + 1 < len(values):
            out.extend(_range(values[index], values[index + 1], roman=roman) or values[index:index + 2])
            index += 2
            continue
        out.append(values[index])
        index += 1
    return list(dict.fromkeys(out))


def _split_list(body: str, number: str, *, roman: bool = False) -> list[str]:
    pieces = list(re.finditer(number, body))
    values = [clean_number(m.group(0), roman=roman) for m in pieces]
    connectors = [re.sub(r"(?i:arts?|artigos?|incisos?|§§?|º)\.?", " ", body[a.end():b.start()])
                  for a, b in zip(pieces, pieces[1:])]
    return expand(values, connectors, roman=roman)


@dataclass
class Token:
    level: str
    values: list[str]
    start: int
    end: int


def mask_expressions(text: str) -> tuple[str, list[tuple[int, int, str]]]:
    """Troca o conteúdo entre aspas por espaços: números citados em expressão não viram dispositivos."""
    found = []
    chars = list(text)
    for match in EXPRESSION_RE.finditer(text):
        value = " ".join(match.group(1).split())
        if value:
            found.append((match.start(), match.end(), value))
            for pos in range(match.start(1), match.end(1)):
                chars[pos] = " "
    return "".join(chars), found


def tokens(text: str) -> list[Token]:
    masked, expressions = mask_expressions(text)
    found: list[Token] = []
    for match in ARTICLE_RE.finditer(masked):
        found.append(Token("article", _split_list(match.group(1), _ANUM), match.start(), match.end()))
    for match in PARAGRAPH_RE.finditer(masked):
        found.append(Token("paragraph", _split_list(match.group(1), _PNUM), match.start(), match.end()))
    for match in SOLE_PARAGRAPH_RE.finditer(masked):
        found.append(Token("paragraph", ["unico"], match.start(), match.end()))
    for match in INCISO_RE.finditer(masked):
        found.append(Token("inciso", _split_list(match.group(1), _RNUM, roman=True), match.start(), match.end()))
    for match in ALINEA_RE.finditer(masked):
        letters, connectors = [match.group(1).lower()], []
        for nxt in _ALINEA_NEXT.finditer(match.group(2) or ""):
            connectors.append(nxt.group(1))
            letters.append((nxt.group(2) or nxt.group(3)).lower())
        found.append(Token("alinea", expand(letters, connectors), match.start(), match.end()))
    for match in ITEM_RE.finditer(masked):
        found.append(Token("item", [match.group(1)], match.start(), match.end()))
    for match in CAPUT_RE.finditer(masked):
        found.append(Token("caput", ["caput"], match.start(), match.end()))
    for start, end, value in expressions:
        found.append(Token("expression", [value], start, end))
    found.sort(key=lambda t: (t.start, -t.end))
    result: list[Token] = []
    for token in found:
        if result and token.start < result[-1].end:
            continue
        result.append(token)
    return [t for t in result if t.values]


def normalize_norm(match: re.Match[str]) -> str:
    kind = " ".join(match.group("kind").split())
    low = kind.lower().replace(" -", "-").replace("- ", "-")
    if low.startswith("constitui") or low == "cf":
        return "Constituição Federal"
    if low == "adct" or low.startswith("ato das"):
        return "ADCT"
    number = (match.group("number") or "").replace(".", "")
    if not number:
        return ""
    canonical = {"decreto-lei": "Decreto-Lei", "decretolei": "Decreto-Lei", "decreto lei": "Decreto-Lei",
                 "lei complementar": "Lei Complementar", "lc": "Lei Complementar",
                 "emenda constitucional": "Emenda Constitucional", "lei": "Lei", "decreto": "Decreto"}.get(low, kind)
    year = match.group("year") or ""
    return f"{canonical} {number}/{year}" if year else f"{canonical} {number}"


def norms(text: str) -> list[tuple[int, int, str]]:
    return [(m.start(), m.end(), value) for m in NORM_RE.finditer(text) if (value := normalize_norm(m))]


def same_norm(a: str, b: str) -> bool:
    """'Decreto-Lei 1804/1980' equivale a 'Decreto-Lei 1804': ano ausente num dos lados não diverge."""
    if not a or not b:
        return False
    head_a, _, year_a = a.partition("/")
    head_b, _, year_b = b.partition("/")
    return head_a == head_b and (year_a == year_b or not year_a or not year_b)


def resolve(text: str, *, operation: str = "", text_role: str = "texto_principal") -> list[Device]:
    """Monta dispositivos completos a partir dos tokens de uma cláusula.

    - Subdispositivos pendentes recebem o artigo seguinte ligado por preposição ("o inciso II do art. 5º").
    - Depois de artigo citado de frente, vírgula ou preposição introduz descendente ("art. 5º, § 2º").
    - Artigo seguido de "da PEC", "do substitutivo" etc. é contenedor quando já há dispositivo na frase
      ("inciso VIII ao art. 144 da CF, no art. 1º da PEC"); sem dispositivo anterior, ele é o próprio alvo.
    - "deste mesmo artigo" reaproveita o último artigo citado.
    """
    masked, _ = mask_expressions(text)
    devices: list[Device] = []
    group: list[Device] = []
    open_article = last_article = ""
    last_closed: list[Device] = []
    container = ""
    previous_end = 0
    for token in tokens(text):
        connector = masked[previous_end:token.start]
        previous_end = token.end
        chain = bool(CHAIN_RE.search(connector))
        definite = bool(DEFINITE_RE.search(connector))
        if token.level == "article":
            followed_by_proposition = bool(PROPOSITION_AFTER_RE.match(masked, token.end))
            if group and (chain or not devices):
                for partial in group:
                    partial.article = token.values[0]
                devices.extend(group)
                last_closed, group = list(group), []
                open_article, last_article = "", token.values[0]
                continue
            if followed_by_proposition and devices and not group:
                container = container or token.values[0]
                continue
            if group:
                _close_pending(group, devices, last_article or open_article)
                group = []
            created = [Device(article=value, start=token.start, own=followed_by_proposition) for value in token.values]
            devices.extend(created)
            last_closed = created
            open_article = last_article = token.values[-1]
            continue
        rank = RANK[token.level]
        comma_only = bool(re.fullmatch(r"\s*,?\s*", connector))
        if token.level == "expression":
            if group and not definite and not chain:
                group = [replace(p, expression=token.values[0]) for p in group]
            elif group:
                group.extend(_partials(token))
            else:
                group = _partials(token)
            continue
        if group:
            if chain and not definite and rank < min(p.shallowest_sub() for p in group):
                for partial in group:
                    _set(partial, token.level, token.values[0])
            else:
                group.extend(_partials(token))
            continue
        if last_article and SAME_ARTICLE_RE.match(masked[token.end:token.end + 40]):
            created = _partials(token)
            for partial in created:
                partial.article = last_article
            devices.extend(created)
            last_closed = created
            continue
        if last_closed and comma_only and not definite and all(not _get(p, token.level) for p in last_closed):
            refined = [_with(p, token.level, value) for p in last_closed for value in token.values]
            for old in last_closed:
                if old in devices:
                    devices.remove(old)
            devices.extend(refined)
            last_closed = refined
            continue
        if open_article and not definite and (comma_only or chain):
            parents = [d for d in devices if d.article == open_article and d.deepest() < rank]
            parent = parents[-1] if parents else Device(article=open_article)
            created = [_with(parent, token.level, value, start=token.start) for value in token.values]
            if parents:
                devices.remove(parent)
            devices.extend(created)
            last_closed = created
            continue
        group = _partials(token)
    if group:
        _close_pending(group, devices, last_article or open_article)
    norm_marks = norms(masked)
    for device in devices:
        device.operation = device.operation or operation
        device.text_role = text_role
        if not device.own:
            later = [n for start, _, n in norm_marks if start >= device.start]
            earlier = [n for start, _, n in norm_marks if start < device.start]
            device.amended_norm = (later or earlier or [""])[0]
        if container and not device.own:
            device.container_article = device.container_article or container
    return _unique(devices)


def _close_pending(group: list[Device], devices: list[Device], article: str) -> None:
    """Pendentes sem artigo posterior pertencem ao último artigo citado ("ao art. 144 o inciso VIII")."""
    if article:
        for old in [d for d in devices if d.article == article and not d.has_sub()]:
            devices.remove(old)
    for partial in group:
        partial.article = partial.article or article
    devices.extend(group)


def _partials(token: Token) -> list[Device]:
    return [_with(Device(start=token.start), token.level, value) for value in token.values]


def _with(base: Device, level: str, value: str, *, start: int | None = None) -> Device:
    copy = replace(base, start=base.start if start is None else start)
    _set(copy, level, value)
    return copy


def _set(device: Device, level: str, value: str) -> None:
    if level == "caput":
        device.caput = True
    elif level == "expression":
        device.expression = value
    else:
        setattr(device, level, value)


def _get(device: Device, level: str) -> str | bool:
    return device.caput if level == "caput" else getattr(device, level, "")


def _unique(devices: list[Device]) -> list[Device]:
    seen, out = set(), []
    for device in devices:
        key = (device.article, device.paragraph, device.caput, device.inciso, device.alinea, device.item,
               device.expression, device.amended_norm)
        if key in seen or not any(key[:7]):
            continue
        seen.add(key)
        out.append(device)
    return out


def operation_for(verb: str) -> str:
    for pattern, name in COMMAND_OPERATIONS:
        if pattern.search(verb):
            return name
    return "nova_redacao"


def commands(text: str) -> Iterator[tuple[str, str, int, int]]:
    """Gera (verbo, cláusula, início, fim) de cada comando de técnica legislativa no texto.

    A cláusula vai do verbo até a redação proposta (dois-pontos seguido de citação), o fim da frase
    ou o próximo comando. Em comandos declarativos ("O art. 22 da CF passa a vigorar acrescido do
    inciso XXXII"), o sujeito anterior ao verbo integra a cláusula.
    """
    matches = list(COMMAND_RE.finditer(text))
    for index, match in enumerate(matches):
        limit = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        tail = text[match.start():limit]
        cut = CLAUSE_END_RE.search(tail)
        clause = tail[:cut.start()] if cut else tail[:700]
        start = match.start()
        if match.group("verb").lower().startswith("passa"):
            before = text[max(0, match.start() - 320):match.start()]
            subjects = list(DECLARATIVE_SUBJECT_RE.finditer(before))
            # Sem sujeito do tipo "O art. ..."/"A Lei ...", vale o início da linha ("Acrescente o § 11 no
            # artigo 144 da CF, que passa a vigorar..."): o comando anterior já foi lido, e a deduplicação
            # dos dispositivos evita contagem dupla.
            subject = before[subjects[-1].start():] if subjects else before[before.rfind("\n") + 1:]
            start = match.start() - len(subject)
            clause = subject + clause
        clause = ITEM_LABEL_RE.sub("", clause)
        yield match.group("verb"), clause[:900], start, start + len(clause)


def text_role_for(text: str) -> str:
    """"substitutivo" só quando o próprio documento atribui a incidência ao substitutivo ou ao PLV."""
    return "substitutivo" if SUBSTITUTIVE_RE.search(text) else "texto_principal"


def is_new_article(clause: str) -> bool:
    """"Acrescente-se, onde couber, o seguinte artigo": acréscimo sem dispositivo correspondente no texto-base."""
    return bool(NEW_ARTICLE_RE.search(clause))


PROPOSED_ARTICLE_RE = re.compile(rf"^[\s\"“'‘«]*(?i:art)\.?\s*({_ANUM})\s*[.\-–]?\s*(.*)$")
PROPOSED_PARAGRAPH_RE = re.compile(rf"^[\s\"“'‘«]*(?:§\s*º?\s*({_PNUM})|((?i:par[aá]grafo\s+[uú]nico)))\s*[.\-–]?\s*(.*)$")
PROPOSED_INCISO_RE = re.compile(rf"^[\s\"“'‘«]*({_RNUM})\s*[-–—]\s*(.*)$")
PROPOSED_ALINEA_RE = re.compile(r"^[\s\"“'‘«]*([a-z])\)\s*(.*)$")


def _has_text(value: str) -> bool:
    return bool(re.search(r"[A-Za-zÀ-ú]{2,}", re.sub(r"[.…_\s]+", " ", value)))


def proposed_devices(text: str, *, limit: int = 30) -> list[Device]:
    """Dispositivos efetivamente redigidos numa redação proposta.

    Linhas pontilhadas ("Art. 144. ........") indicam dispositivo mantido; só entra o que tem texto novo.
    Usado quando o comando não nomeia os dispositivos ("A Constituição passa a vigorar com as seguintes
    alterações: ...").
    """
    out: list[Device] = []
    article = paragraph = inciso = ""
    for line in text.splitlines():
        if len(out) >= limit:
            break
        if match := PROPOSED_ARTICLE_RE.match(line):
            article, paragraph, inciso = clean_number(match.group(1)), "", ""
            if _has_text(match.group(2)):
                out.append(Device(article=article, caput=True))
        elif article and (match := PROPOSED_PARAGRAPH_RE.match(line)):
            paragraph, inciso = ("unico" if match.group(2) else clean_number(match.group(1))), ""
            if _has_text(match.group(3)):
                out.append(Device(article=article, paragraph=paragraph))
        elif article and (match := PROPOSED_INCISO_RE.match(line)):
            inciso = clean_number(match.group(1), roman=True)
            if _has_text(match.group(2)):
                out.append(Device(article=article, paragraph=paragraph, caput=not paragraph, inciso=inciso))
        elif article and inciso and (match := PROPOSED_ALINEA_RE.match(line)):
            if _has_text(match.group(2)):
                out.append(Device(article=article, paragraph=paragraph, caput=not paragraph, inciso=inciso,
                                  alinea=match.group(1)))
    return _unique(out)
