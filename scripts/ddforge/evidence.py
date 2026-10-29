"""Document texts, structure indexes, reads, scans and quotation checks.

Every claim of the form «прочитано» or «збігів немає» that the ledger accepts is backed by a record
written here: the program logs each cut and each scan, so the report never relies on a model saying
that it read something.
"""

from __future__ import annotations

import bisect
import difflib
import hashlib
import json
import re
from pathlib import Path

from . import indexer, quality, store
from .vocab import Q_NONE, Q_OK, UNDETERMINED

# --- layout -------------------------------------------------------------------------------------


def docs_path(work) -> Path:
    return Path(work) / "docs.json"


def reads_path(work) -> Path:
    return Path(work) / "evidence" / "reads.jsonl"


def scan_path(work, scan_id: str) -> Path:
    return Path(work) / "evidence" / "scans" / f"{scan_id}.json"


def text_file(work, doc_id: str) -> Path:
    return Path(work) / "texts" / f"{doc_id}.txt"


def index_file(work, doc_id: str) -> Path:
    return Path(work) / "index" / f"{doc_id}.json"


# --- registry of documents ---------------------------------------------------------------------

_PREFIX = {"notebook": "d", "legislation": "l", "project": "p"}


def load_docs(work) -> dict:
    return store.read_json(docs_path(work), {"docs": {}, "counters": {}}) or {"docs": {}, "counters": {}}


def get_doc(work, doc_id: str) -> dict | None:
    return load_docs(work)["docs"].get(doc_id)


def _next_id(registry: dict, prefix: str) -> str:
    counters = registry.setdefault("counters", {})
    counters[prefix] = counters.get(prefix, 0) + 1
    return f"{prefix}-{counters[prefix]:03d}"


def unwrap(raw: str) -> str:
    """The connector may wrap a text in {"content": "..."} (nlm_protocol.md §3 script does the same)."""
    stripped = raw.lstrip("﻿")
    if stripped[:1] in "{[":
        try:
            payload = json.loads(stripped)
        except ValueError:
            return raw
        if isinstance(payload, dict):
            for key in ("content", "text"):
                if isinstance(payload.get(key), str):
                    return payload[key]
    return raw


def _clean(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n").replace("﻿", "")


def register_text(
    work,
    *,
    origin: str,
    notebook_key: str | None,
    source_id: str,
    title: str,
    kind: str,
    text: str | None = None,
    file: str | None = None,
    date: str | None = None,
    patterns: list[str] | None = None,
    extra: dict | None = None,
) -> dict:
    """Register the full text of a source. Idempotent per (origin, notebook_key, source_id)."""
    work = Path(work)
    if text is None:
        if not file:
            raise ValueError("register_text needs text or file")
        text = Path(file).read_text(encoding="utf-8-sig", errors="replace")
    text = _clean(unwrap(text))
    assessment = quality.assess(text)
    index = indexer.build_index(text, patterns)
    sha = store.sha256_text(text)
    with store.locked(work):
        registry = load_docs(work)
        existing = None
        for doc in registry["docs"].values():
            if (doc["origin"], doc.get("notebook_key"), doc["source_id"]) == (origin, notebook_key, source_id):
                existing = doc
                break
        changed = True
        if existing:
            doc_id = existing["doc_id"]
            changed = existing.get("text_sha") != sha
        else:
            doc_id = _next_id(registry, _PREFIX.get(origin, "d"))
        store.write_text(text_file(work, doc_id), text)
        store.write_json(index_file(work, doc_id), {"doc_id": doc_id, **index})
        record = {
            "doc_id": doc_id,
            "origin": origin,
            "notebook_key": notebook_key,
            "source_id": source_id,
            "title": title,
            "kind": kind,
            "doc_date": date,
            "text_path": f"texts/{doc_id}.txt",
            "index_path": f"index/{doc_id}.json",
            "text_sha": sha,
            "chars": len(text),
            "text_quality": assessment["quality"],
            "quality_metrics": assessment["metrics"],
            "quality_reasons": assessment["reasons"],
            "units": len(index["units"]),
            "index_mode": index["mode"],
            "fetch_state": "text",
            "registered_at": store.now_iso(),
        }
        if extra:
            record.update(extra)
        registry["docs"][doc_id] = record
        store.write_json(docs_path(work), registry)
    return {
        "doc_id": doc_id,
        "text_quality": record["text_quality"],
        "chars": record["chars"],
        "units": record["units"],
        "index_mode": record["index_mode"],
        "reused": existing is not None,
        "changed": changed,
    }


def register_no_text(
    work,
    *,
    origin: str,
    notebook_key: str | None,
    source_id: str,
    title: str,
    kind: str,
    reason: str = "no_full_text",
    date: str | None = None,
    fetch_state: str = "failed",
) -> dict:
    """Register a source without text. `fetch_state`: "failed" (text could not be obtained: it cannot carry
    any check) or "not_fetched" (listed only; the text is fetched on demand when a check needs a full read)."""
    work = Path(work)
    with store.locked(work):
        registry = load_docs(work)
        doc_id = None
        for doc in registry["docs"].values():
            if (doc["origin"], doc.get("notebook_key"), doc["source_id"]) == (origin, notebook_key, source_id):
                doc_id = doc["doc_id"]
                break
        if doc_id is None:
            doc_id = _next_id(registry, _PREFIX.get(origin, "d"))
        elif fetch_state == "not_fetched":
            known = registry["docs"][doc_id]
            return {"doc_id": doc_id, "text_quality": known["text_quality"], "reason": "already_registered",
                    "fetch_state": known.get("fetch_state", "text")}
        registry["docs"][doc_id] = {
            "doc_id": doc_id,
            "origin": origin,
            "notebook_key": notebook_key,
            "source_id": source_id,
            "title": title,
            "kind": kind,
            "doc_date": date,
            "text_path": None,
            "index_path": None,
            "text_sha": None,
            "chars": 0,
            "text_quality": Q_NONE,
            "quality_metrics": {},
            "quality_reasons": [reason],
            "units": 0,
            "index_mode": "none",
            "fetch_state": fetch_state,
            "registered_at": store.now_iso(),
        }
        store.write_json(docs_path(work), registry)
    return {"doc_id": doc_id, "text_quality": Q_NONE, "reason": reason, "fetch_state": fetch_state}


def set_kind(work, doc_id: str, kind: str, date: str | None = None) -> None:
    with store.locked(work):
        registry = load_docs(work)
        if doc_id in registry["docs"]:
            registry["docs"][doc_id]["kind"] = kind
            if date:
                registry["docs"][doc_id]["doc_date"] = date
            store.write_json(docs_path(work), registry)


# --- text access ----------------------------------------------------------------------------------

_text_cache: dict = {}


def read_text(work, doc_id: str) -> str | None:
    doc = get_doc(work, doc_id)
    if not doc or not doc.get("text_path"):
        return None
    path = Path(work) / doc["text_path"]
    key = (str(path), doc.get("text_sha"))
    if key not in _text_cache:
        _text_cache.clear()
        _text_cache[key] = path.read_text(encoding="utf-8")
    return _text_cache[key]


def read_index(work, doc_id: str) -> dict:
    return store.read_json(index_file(work, doc_id), {"units": [], "mode": "none"})


# --- normalization and quotation search -----------------------------------------------------------

_WS = set(" \t\r\n      ​﻿  ")
_MAP = {
    "«": '"', "»": '"', "“": '"', "”": '"', "„": '"', "‟": '"', "″": '"',
    "’": "'", "ʼ": "'", "‘": "'", "`": "'", "´": "'",
    "–": "-", "—": "-", "‒": "-", "―": "-", "‑": "-", "−": "-",
}


def normalize_with_map(text: str):
    out, idx = [], []
    prev_space = True
    for i, ch in enumerate(text):
        if ch in _WS:
            if not prev_space:
                out.append(" ")
                idx.append(i)
                prev_space = True
            continue
        if ch == "­":
            continue
        out.append(_MAP.get(ch, ch).lower())
        idx.append(i)
        prev_space = False
    return "".join(out), idx


def normalize(text: str) -> str:
    return normalize_with_map(text)[0].strip()


def _trigram_windows(norm_text: str, quote: str, step: int):
    grams = {quote[i:i + 3] for i in range(max(0, len(quote) - 2))}
    bins: dict = {}
    for gram in grams:
        count = 0
        for match in re.finditer(re.escape(gram), norm_text):
            bins[match.start() // step] = bins.get(match.start() // step, 0) + 1
            count += 1
            if count > 2000:
                break
    return sorted(bins.items(), key=lambda item: -item[1])[:6]


def locate_quote(text: str, quote: str, *, allow_fuzzy: bool = False) -> dict:
    """Find `quote` in `text`. Exact on normalized text; optional fuzzy match for degraded texts."""
    nq = normalize(quote)
    if len(nq) < 8:
        return {"found": False, "match": "none", "reason": "quote_too_short"}
    norm_text, idx = normalize_with_map(text)
    pos = norm_text.find(nq)
    if pos != -1:
        start = idx[pos]
        end = idx[min(pos + len(nq), len(idx)) - 1] + 1
        return {"found": True, "match": "exact", "start": start, "end": end, "ratio": 1.0,
                "occurrences": norm_text.count(nq)}
    if not allow_fuzzy:
        return {"found": False, "match": "none"}
    step = max(20, len(nq) // 2)
    best = None
    for window_no, _score in _trigram_windows(norm_text, nq, step):
        lo = max(0, window_no * step - len(nq))
        hi = min(len(norm_text), (window_no + 1) * step + 2 * len(nq))
        region = norm_text[lo:hi]
        matcher = difflib.SequenceMatcher(None, nq, region, autojunk=False)
        block = matcher.find_longest_match(0, len(nq), 0, len(region))
        if block.size < 8:
            continue
        s = max(0, block.b - block.a)
        e = min(len(region), s + len(nq) + max(4, len(nq) // 10))
        ratio = difflib.SequenceMatcher(None, nq, region[s:e], autojunk=False).ratio()
        if best is None or ratio > best[0]:
            best = (ratio, lo + s, lo + e)
    if best and best[0] >= 0.85:
        start = idx[min(best[1], len(idx) - 1)]
        end = idx[min(best[2], len(idx)) - 1] + 1
        return {"found": True, "match": "fuzzy", "start": start, "end": end, "ratio": round(best[0], 3)}
    return {"found": False, "match": "none", "best_ratio": round(best[0], 3) if best else 0.0}


def verify_quote(work, doc_id: str, quote: str) -> dict:
    """Check a quotation against the registered text of a document."""
    doc = get_doc(work, doc_id)
    if doc is None:
        return {"verified": None, "match": "none", "reason": "doc_unknown"}
    text = read_text(work, doc_id)
    if text is None:
        return {"verified": None, "match": "none", "reason": "no_full_text"}
    result = locate_quote(text, quote, allow_fuzzy=doc.get("text_quality") != Q_OK)
    if result["found"]:
        index = read_index(work, doc_id)
        unit = indexer.unit_at(index, result["start"])
        result["unit_label"] = unit["label"] if unit else None
        result["verified"] = "exact" if result["match"] == "exact" else "fuzzy"
    else:
        result["verified"] = "not_found"
    return result


def chain_coverage(work, doc_id: str, unit_ref: str, quotes: list[str]) -> dict:
    """Does a chain of verbatim quotations cover the whole unit without gaps?"""
    text = read_text(work, doc_id)
    if text is None:
        return {"ok": False, "reason": "no_full_text"}
    unit, _count = indexer.find_unit(read_index(work, doc_id), unit_ref)
    if unit is None:
        return {"ok": False, "reason": "unit_not_found"}
    norm_text, idx = normalize_with_map(text)
    ns = bisect.bisect_left(idx, unit["start"])
    ne = bisect.bisect_left(idx, unit["end"])
    heading_end = norm_text.find(" ", ns + 1)
    first_newline = text.find("\n", unit["start"])
    if first_newline != -1 and first_newline < unit["end"]:
        ns = max(ns, bisect.bisect_left(idx, first_newline))
    spans = []
    for quote in quotes:
        nq = normalize(quote)
        if len(nq) < 8:
            continue
        pos = norm_text.find(nq, ns - 1 if ns > 0 else 0)
        if pos == -1 or pos + len(nq) > ne + 1:
            continue
        spans.append((pos, pos + len(nq)))
    if not spans:
        return {"ok": False, "reason": "no_quote_located", "covered_ratio": 0.0}
    spans.sort()
    merged = [list(spans[0])]
    for start, end in spans[1:]:
        if start <= merged[-1][1] + 3:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    gaps = []
    cursor = ns
    for start, end in merged:
        if start - cursor > 3 and norm_text[cursor:start].strip(" .,;:-") != "":
            gaps.append((cursor, start))
        cursor = max(cursor, end)
    if ne - cursor > 3 and norm_text[cursor:ne].strip(" .,;:-") != "":
        gaps.append((cursor, ne))
    covered = sum(end - start for start, end in merged)
    total = max(1, ne - ns)
    return {
        "ok": not gaps,
        "covered_ratio": round(min(1.0, covered / total), 4),
        "gaps": [{"from": idx[g[0]] if g[0] < len(idx) else None,
                  "to": idx[g[1] - 1] if 0 < g[1] <= len(idx) else None,
                  "preview": norm_text[g[0]: g[0] + 80]} for g in gaps[:10]],
        "reason": None if not gaps else "chain_gap",
    }


# --- reads log -----------------------------------------------------------------------------------


def log_read(work, record: dict) -> None:
    record = {**record, "at": store.now_iso()}
    with store.locked(work):
        store.append_jsonl(reads_path(work), record)


def reads_for(work, doc_id: str, kind: str | None = None) -> list:
    rows = [row for row in store.read_jsonl(reads_path(work)) if row.get("doc_id") == doc_id]
    if kind:
        rows = [row for row in rows if row.get("kind") == kind]
    return rows


def cut_unit_keys(work, doc_id: str) -> set:
    return {row["unit_key"] for row in reads_for(work, doc_id, "cut") if row.get("unit_key")}


# --- cut -----------------------------------------------------------------------------------------


def list_units(work, doc_id: str, limit: int = 400) -> dict:
    doc = get_doc(work, doc_id)
    if not doc:
        return {"error": "doc_unknown"}
    index = read_index(work, doc_id)
    return {
        "doc_id": doc_id,
        "title": doc["title"],
        "text_quality": doc["text_quality"],
        "index_mode": index.get("mode"),
        "units_total": len(index["units"]),
        "units": [{"label": u["label"], "key": u["key"], "rank": u["rank"], "chars": u["end"] - u["start"],
                   "heading": u["heading"]} for u in index["units"][:limit]],
    }


def cut(work, doc_id: str, *, unit: str | None = None, scan_id: str | None = None, hit: int | None = None,
        max_chars: int = 12000, context: int = 600) -> dict:
    work = Path(work)
    doc = get_doc(work, doc_id)
    if not doc:
        return {"error": "doc_unknown", "doc_id": doc_id}
    text = read_text(work, doc_id)
    if text is None:
        return {"error": "no_full_text", "doc_id": doc_id}
    index = read_index(work, doc_id)
    if unit:
        found, count = indexer.find_unit(index, unit)
        if not found:
            return {"error": "unit_not_found", "doc_id": doc_id, "requested": unit,
                    "available": [u["label"] for u in index["units"][:60]], "units_total": len(index["units"])}
        start, end, label, key = found["start"], found["end"], found["label"], found["key"]
        log = {"kind": "cut", "doc_id": doc_id, "unit_key": key, "start": start, "end": end}
        extra = {"candidates_with_this_label": count}
    elif scan_id is not None and hit is not None:
        data = store.read_json(scan_path(work, scan_id))
        if not data or doc_id not in data["docs"]:
            return {"error": "scan_unknown", "scan_id": scan_id}
        hits = data["docs"][doc_id]["hits"]
        if not (1 <= hit <= len(hits)):
            return {"error": "hit_unknown", "hits": len(hits)}
        target = hits[hit - 1]
        start, end = max(0, target["start"] - context), min(len(text), target["end"] + context)
        owner = indexer.unit_at(index, target["start"])
        label, key = (owner["label"] if owner else "поза структурою"), None
        log = {"kind": "cut", "doc_id": doc_id, "unit_key": None, "start": start, "end": end,
               "scan_id": scan_id, "hit": hit}
        mark_reviewed(work, scan_id, doc_id, [hit])
        extra = {}
    else:
        return {"error": "need_unit_or_hit"}
    body = text[start:end]
    truncated = len(body) > max_chars
    log_read(work, log)
    return {"doc_id": doc_id, "unit": label, "start": start, "end": end, "chars": len(body),
            "truncated": truncated, "continues_at": start + max_chars if truncated else None,
            "text_quality": doc["text_quality"], "text": body[:max_chars], **extra}


def cut_span(work, doc_id: str, start: int, end: int, max_chars: int = 12000) -> dict:
    """Continue reading a truncated cut."""
    text = read_text(work, doc_id)
    if text is None:
        return {"error": "no_full_text"}
    end = min(end, len(text))
    body = text[start:end]
    log_read(work, {"kind": "cut", "doc_id": doc_id, "unit_key": None, "start": start, "end": end})
    return {"doc_id": doc_id, "start": start, "end": end, "truncated": len(body) > max_chars,
            "continues_at": start + max_chars if len(body) > max_chars else None, "text": body[:max_chars]}


# --- scan ----------------------------------------------------------------------------------------


def probes_sha(probes: list[str]) -> str:
    return hashlib.sha256("\n".join(sorted(probes)).encode("utf-8")).hexdigest()


def scan(work, doc_ids: list[str], probes: list[str], *, item_id: str | None = None, cap: int = 50) -> dict:
    work = Path(work)
    compiled = []
    for probe in probes:
        try:
            compiled.append(re.compile(probe, re.I | re.U))
        except re.error as exc:
            return {"error": "bad_probe", "probe": probe, "detail": str(exc)}
    with store.locked(work):
        registry = load_docs(work)
        scan_id = _next_id(registry, "sc")
        store.write_json(docs_path(work), registry)
    result_docs, skipped = {}, []
    for doc_id in doc_ids:
        doc = registry["docs"].get(doc_id)
        text = read_text(work, doc_id) if doc else None
        if doc is None or text is None:
            skipped.append({"doc_id": doc_id, "reason": "no_full_text"})
            continue
        spans = []
        for regex in compiled:
            for match in regex.finditer(text):
                if match.end() > match.start():
                    spans.append((match.start(), match.end()))
        spans.sort()
        merged = []
        for start, end in spans:
            if merged and start <= merged[-1][1] + 40:
                merged[-1][1] = max(merged[-1][1], end)
            else:
                merged.append([start, end])
        index = read_index(work, doc_id)
        hits = []
        for number, (start, end) in enumerate(merged[:cap], start=1):
            owner = indexer.unit_at(index, start)
            lo, hi = max(0, start - 80), min(len(text), end + 80)
            hits.append({"n": number, "start": start, "end": end,
                         "unit": owner["label"] if owner else None,
                         "excerpt": text[lo:hi].replace("\n", " ")})
        result_docs[doc_id] = {"hits": hits, "hits_total": len(merged), "reviewed": [],
                               "truncated": len(merged) > cap, "text_quality": doc["text_quality"]}
        log_read(work, {"kind": "scan", "doc_id": doc_id, "scan_id": scan_id, "probes_sha": probes_sha(probes),
                        "hits_total": len(merged), "item_id": item_id})
    payload = {"scan_id": scan_id, "item_id": item_id, "probes": probes, "probes_sha": probes_sha(probes),
               "docs": result_docs, "skipped": skipped, "created_at": store.now_iso()}
    store.write_json(scan_path(work, scan_id), payload)
    return payload


def mark_reviewed(work, scan_id: str, doc_id: str, hits: list[int]) -> dict:
    with store.locked(work):
        data = store.read_json(scan_path(work, scan_id))
        if not data or doc_id not in data["docs"]:
            return {"error": "scan_unknown"}
        entry = data["docs"][doc_id]
        reviewed = set(entry["reviewed"]) | {h for h in hits if 1 <= h <= len(entry["hits"])}
        entry["reviewed"] = sorted(reviewed)
        store.write_json(scan_path(work, scan_id), data)
    return {"scan_id": scan_id, "doc_id": doc_id, "reviewed": entry["reviewed"],
            "unreviewed": [h["n"] for h in entry["hits"] if h["n"] not in reviewed]}


def latest_scan_state(work, doc_id: str, sha: str) -> dict | None:
    """Most recent scan of the doc with the given probe set: {"scan_id","hits_total","unreviewed":[...]}."""
    rows = [r for r in store.read_jsonl(reads_path(work))
            if r.get("kind") == "scan" and r.get("doc_id") == doc_id and r.get("probes_sha") == sha]
    if not rows:
        return None
    scan_id = rows[-1]["scan_id"]
    data = store.read_json(scan_path(work, scan_id))
    if not data or doc_id not in data["docs"]:
        return None
    entry = data["docs"][doc_id]
    unreviewed = [h["n"] for h in entry["hits"] if h["n"] not in entry["reviewed"]]
    return {"scan_id": scan_id, "hits_total": entry["hits_total"], "truncated": entry["truncated"],
            "unreviewed": unreviewed}


def block_docs_in_scope(docs: list[dict], doc_kinds: list[str], scope: str) -> list[dict]:
    """Documents an item must cover. Unclassified documents are always in scope (conservative)."""
    always = {UNDETERMINED} | ({"other"} if scope == "whole_doc" else set())
    wanted = set(doc_kinds) | always
    return [doc for doc in docs if doc.get("kind") in wanted]
