"""Heuristic assessment of the quality of a document text (scans, bad recognition).

The connector returns text only; what NotebookLM does with scans is not known to the plugin. This
module judges the text it gets: «читається» / «з помилками» / «нечитабельний». A text with errors can
carry a finding (with a note) but can never carry «Не виявлено».
"""

from __future__ import annotations

import re

from .vocab import Q_DEGRADED, Q_NONE, Q_OK

CYR = "абвгґдеєжзиіїйклмнопрстуфхцчшщьюяАБВГҐДЕЄЖЗИІЇЙКЛМНОПРСТУФХЦЧШЩЬЮЯ'’ʼ"
LAT = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
CYR_RE = re.compile(f"[{CYR}]")
LAT_RE = re.compile(f"[{LAT}]")
VOWELS = set("аеєиіїоуюяАЕЄИІЇОУЮЯ")
SINGLE_OK = set("вуійзаяоеєиВУІЙЗАЯОЕЄИabcdeABCDE")
TOKEN_RE = re.compile(r"[^\W\d_]+", re.UNICODE)

MIN_CHARS = 200


def assess(text: str) -> dict:
    """Return {"quality": ok|degraded|none, "metrics": {...}, "reasons": [...]}."""
    compact = re.sub(r"\s+", "", text or "")
    chars = len(compact)
    if chars < MIN_CHARS:
        return {"quality": Q_NONE, "metrics": {"chars": chars}, "reasons": ["too_short"]}
    letters = sum(1 for ch in compact if ch.isalpha())
    letters_ratio = letters / chars
    tokens = TOKEN_RE.findall(text)
    n_tokens = max(1, len(tokens))

    mixed = 0
    novowel = 0
    cyr_long = 0
    junk_single = 0
    for token in tokens:
        has_cyr = bool(CYR_RE.search(token))
        has_lat = bool(LAT_RE.search(token))
        if has_cyr and has_lat:
            mixed += 1
        if has_cyr and len(token) >= 5:
            cyr_long += 1
            if not any(ch in VOWELS for ch in token):
                novowel += 1
        if len(token) == 1 and token not in SINGLE_OK:
            junk_single += 1
    replacement = text.count("�")
    metrics = {
        "chars": chars,
        "letters_ratio": round(letters_ratio, 3),
        "mixed_script_ratio": round(mixed / n_tokens, 4),
        "no_vowel_ratio": round(novowel / max(1, cyr_long), 4),
        "junk_single_ratio": round(junk_single / n_tokens, 4),
        "replacement_ratio": round(replacement / chars, 5),
    }
    reasons = []
    if letters_ratio < 0.45:
        return {"quality": Q_NONE, "metrics": metrics, "reasons": ["low_letter_ratio"]}
    if metrics["mixed_script_ratio"] > 0.03:
        reasons.append("mixed_script")
    if metrics["no_vowel_ratio"] > 0.08:
        reasons.append("no_vowel_words")
    if metrics["junk_single_ratio"] > 0.08:
        reasons.append("junk_single_letters")
    if metrics["replacement_ratio"] > 0.005:
        reasons.append("replacement_chars")
    if reasons:
        severe = (
            metrics["mixed_script_ratio"] > 0.25
            or metrics["no_vowel_ratio"] > 0.4
            or metrics["junk_single_ratio"] > 0.35
            or metrics["replacement_ratio"] > 0.05
        )
        return {"quality": Q_NONE if severe else Q_DEGRADED, "metrics": metrics, "reasons": reasons}
    return {"quality": Q_OK, "metrics": metrics, "reasons": []}
