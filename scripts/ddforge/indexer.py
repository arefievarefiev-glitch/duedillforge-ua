"""Index of structural units of a document (articles, sections, annexes, numbered clauses).

The index is the only basis for «this unit is in the document» / «it is not» (nlm_protocol.md §3).
A unit runs from its heading to the next heading of the same or a higher rank.
"""

from __future__ import annotations

import re

ARTICLE_RE = re.compile(r"^[ \t]*Стаття[ \t]+(\d+(?:-\d+)*)[ \t]*\.", re.M | re.I)
SECTION_RE = re.compile(r"^[ \t]*Розділ[ \t]+([IVXLC]+|\d+)\b[ \t.:]*", re.M | re.I)
ANNEX_RE = re.compile(r"^[ \t]*Додаток[ \t]*(?:№[ \t]*)?(\d+|[А-ЯA-Z])\b", re.M | re.I)
CLAUSE_RE = re.compile(r"^[ \t]*(\d{1,3}(?:\.\d{1,3}){0,3})\.?[ \t]+(?=\S)", re.M)
DATE_LIKE_RE = re.compile(r"^\d{1,2}\.\d{1,2}\.\d{2,4}")
INLINE_CLAUSE_RE = re.compile(r"(?:(?<=[.;:])|(?<=\s))(\d{1,3}(?:\.\d{1,3}){1,3})\.?[ \t]+(?=[А-ЯІЇЄҐA-Z])")

PREFIX_WORDS = ("пункт", "п.", "п", "стаття", "ст.", "ст", "розділ", "розд.", "розд", "додаток", "дод.", "дод")


_ARTICLE_RE = re.compile(r"^(?:статт\w*|ст(?=[\s.\d])\.?)\s*", re.U)
_SECTION_RE = re.compile(r"^(?:розділ\w*|розд(?=[\s.\d])\.?)\s*", re.U)
_ANNEX_RE = re.compile(r"^(?:додат\w*|дод(?=[\s.\d])\.?)\s*", re.U)
_POINT_RE = re.compile(r"^(?:пункт\w*|п(?=[\s.\d])\.?)\s*", re.U)


def normalize_key(value: str) -> str:
    """Canonical form of a unit reference: «п. 5.2» → «5.2», «Стаття 257», «статті 257» → «ст.257»,
    «Додаток №1» → «дод.1»."""
    raw = (value or "").strip().lower().replace("№", " ")
    occurrence = ""
    if "#" in raw:
        raw, _, occurrence = raw.partition("#")
        raw = raw.strip()
        occurrence = "#" + occurrence.strip()
    kind = ""
    for prefix, regex in (("ст.", _ARTICLE_RE), ("розд.", _SECTION_RE), ("дод.", _ANNEX_RE), ("", _POINT_RE)):
        match = regex.match(raw)
        if match:
            kind, raw = prefix, raw[match.end():]
            break
    raw = raw.strip().rstrip(".").replace(" ", "")
    return f"{kind}{raw}{occurrence}"


def _label(kind: str, key: str) -> str:
    if kind == "article":
        return f"Стаття {key}"
    if kind == "section":
        return f"Розділ {key}"
    if kind == "annex":
        return f"Додаток {key}"
    return f"п. {key}"


def _collect(text: str, patterns: list[str] | None) -> list[dict]:
    found = []
    for match in ARTICLE_RE.finditer(text):
        found.append({"start": match.start(), "kind": "article", "key": match.group(1), "rank": 1})
    for match in SECTION_RE.finditer(text):
        found.append({"start": match.start(), "kind": "section", "key": match.group(1).upper(), "rank": 1})
    for match in ANNEX_RE.finditer(text):
        found.append({"start": match.start(), "kind": "annex", "key": match.group(1).upper(), "rank": 1})
    clause_found = []
    for match in CLAUSE_RE.finditer(text):
        key = match.group(1)
        line_end = text.find("\n", match.start())
        line = text[match.start(): line_end if line_end != -1 else len(text)].strip()
        if DATE_LIKE_RE.match(line):
            continue
        depth = key.count(".") + 1
        clause_found.append({"start": match.start(), "kind": "clause", "key": key, "rank": 1 + depth})
    for extra in patterns or []:
        try:
            regex = re.compile(extra, re.M)
        except re.error:
            continue
        for match in regex.finditer(text):
            key = match.group(1) if match.groups() else match.group(0).strip()
            found.append({"start": match.start(), "kind": "custom", "key": key.strip(), "rank": 1})
    found.extend(clause_found)
    return found


def build_index(text: str, patterns: list[str] | None = None) -> dict:
    """Return {"units": [...], "mode": "lines"|"inline"|"none"}."""
    found = _collect(text, patterns)
    mode = "lines"
    if len(found) < 3 and len(text) > 3000:
        inline = []
        for match in INLINE_CLAUSE_RE.finditer(text):
            key = match.group(1)
            inline.append({"start": match.start(), "kind": "clause", "key": key, "rank": 1 + key.count(".") + 1})
        for match in re.finditer(r"(?<![\w])Стаття[ \t]+(\d+(?:-\d+)*)\.", text):
            inline.append({"start": match.start(), "kind": "article", "key": match.group(1), "rank": 1})
        if len(inline) > len(found):
            found, mode = inline, "inline"
    if not found:
        return {"units": [], "mode": "none"}
    found.sort(key=lambda unit: (unit["start"], unit["rank"]))
    deduped = []
    for unit in found:
        if deduped and deduped[-1]["start"] == unit["start"]:
            if unit["rank"] < deduped[-1]["rank"]:
                deduped[-1] = unit
            continue
        deduped.append(unit)
    units = []
    for position, unit in enumerate(deduped):
        end = len(text)
        for later in deduped[position + 1:]:
            if later["rank"] <= unit["rank"]:
                end = later["start"]
                break
        heading = text[unit["start"]: unit["start"] + 90].split("\n", 1)[0].strip()
        short_key = f"ст.{unit['key']}" if unit["kind"] == "article" else (
            f"розд.{unit['key']}" if unit["kind"] == "section" else (
                f"дод.{unit['key']}" if unit["kind"] == "annex" else unit["key"]))
        units.append({
            "label": _label(unit["kind"], unit["key"]),
            "key": short_key.lower(),
            "kind": unit["kind"],
            "rank": unit["rank"],
            "start": unit["start"],
            "end": end,
            "heading": heading,
        })
    return {"units": units, "mode": mode}


def find_unit(index: dict, reference: str) -> tuple[dict | None, int]:
    """Locate a unit by a human reference. Returns (unit, number_of_candidates)."""
    wanted = normalize_key(reference)
    occurrence = 1
    if "#" in wanted:
        wanted, _, occ = wanted.partition("#")
        try:
            occurrence = max(1, int(occ))
        except ValueError:
            occurrence = 1
    candidates = [unit for unit in index.get("units", []) if unit["key"] == wanted]
    if not candidates:
        return None, 0
    if occurrence > len(candidates):
        return None, len(candidates)
    return candidates[occurrence - 1], len(candidates)


def unit_at(index: dict, offset: int) -> dict | None:
    """The deepest unit containing the offset."""
    best = None
    for unit in index.get("units", []):
        if unit["start"] <= offset < unit["end"]:
            if best is None or unit["rank"] >= best["rank"]:
                best = unit
    return best
