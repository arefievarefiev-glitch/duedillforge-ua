"""Risk register: findings, their norms, severities and the request list for missing documents."""

from __future__ import annotations

from pathlib import Path

from . import evidence, store
from .vocab import (
    CRITICAL, DOC_KINDS_UK, LDH_MARKER_UK, MISSING, REC_TYPES, SEVERITIES, SEVERITY_ORDER, UNVERIFIED,
)


def register_path(work) -> Path:
    return Path(work) / "register.json"


def load(work) -> dict:
    return store.read_json(register_path(work), {"findings": {}, "counters": {}}) or {"findings": {}, "counters": {}}


def save(work, data: dict) -> None:
    store.write_json(register_path(work), data)


def _short(block_id: str) -> str:
    return "".join(ch for ch in block_id.upper() if ch.isalnum())[:8] or "B"


def _confirmation(checks: list[dict], doc_quality: str) -> str:
    verified = [c for c in checks if c.get("verified") in ("exact", "fuzzy")]
    if not verified:
        return "unverified"
    if all(c.get("verified") == "exact" for c in checks) and doc_quality == "ok":
        return "verified"
    return "partial"


def add_finding(work, block_id: str, item_id: str, raw: dict, doc: dict | None) -> tuple[str, list[str]]:
    """Create a finding from an agent's draft. Returns (finding_id, warnings)."""
    warnings: list[str] = []
    checks = []
    for ev in raw.get("evidence", []) or []:
        quote = ev.get("cited_text", "")
        outcome = evidence.verify_quote(work, doc["doc_id"], quote) if doc else {"verified": None}
        checks.append({
            "cited_text": quote,
            "mode": ev.get("mode", "text"),
            "verified": outcome.get("verified"),
            "match": outcome.get("match"),
            "unit": outcome.get("unit_label"),
        })
    quality = doc["text_quality"] if doc else "none"
    confirmation = _confirmation(checks, quality)
    quote_mismatch = bool(checks) and all(c["verified"] == "not_found" for c in checks)
    if quote_mismatch:
        warnings.append("цитату не знайдено в тексті джерела")
    severity = raw.get("severity_proposal")
    if severity not in SEVERITIES:
        severity = "significant"
    with store.locked(work):
        data = load(work)
        for existing in data["findings"].values():
            if (existing["doc_id"], existing["pinpoint"], existing["issue"]) == (
                    raw.get("doc_id"), raw.get("pinpoint", ""), raw.get("issue", "")):
                if item_id not in existing["item_ids"]:
                    existing["item_ids"].append(item_id)
                    save(work, data)
                return existing["id"], warnings
        short = _short(block_id)
        data["counters"][short] = data["counters"].get(short, 0) + 1
        finding_id = f"R-{short}-{data['counters'][short]:02d}"
        data["findings"][finding_id] = {
            "id": finding_id,
            "block_id": block_id,
            "item_ids": [item_id],
            "doc_id": raw.get("doc_id"),
            "doc_title": doc["title"] if doc else None,
            "doc_date": doc.get("doc_date") if doc else None,
            "pinpoint": raw.get("pinpoint", ""),
            "fact": raw.get("fact", ""),
            "issue": raw.get("issue", ""),
            "evidence": checks,
            "confirmation": confirmation,
            "quote_mismatch": quote_mismatch,
            "text_quality": quality,
            "severity_proposal": severity,
            "severity": None,
            "consequence_hypothesis": raw.get("consequence_hypothesis", ""),
            "consequence": None,
            "norm": None,
            "rationale": None,
            "recommendation": None,
            "seller_request": raw.get("seller_request", ""),
            "intertemporal": bool(raw.get("validity_depends_on_law_at_signing")),
            "overridden": False,
            "user_note": None,
            "created_at": store.now_iso(),
        }
        save(work, data)
    return finding_id, warnings


def findings(work) -> list[dict]:
    return list(load(work)["findings"].values())


def final_severity(finding: dict) -> str:
    return finding.get("severity") or finding.get("severity_proposal") or "significant"


def sorted_findings(work) -> list[dict]:
    return sorted(findings(work), key=lambda f: (SEVERITY_ORDER[final_severity(f)], f["block_id"], f["id"]))


def apply_norms(work, items: list[dict]) -> list[str]:
    errors = []
    with store.locked(work):
        data = load(work)
        for item in items:
            finding = data["findings"].get(item.get("finding_id"))
            if finding is None:
                errors.append(f"unknown finding {item.get('finding_id')!r}")
                continue
            norm = dict(item.get("norm") or {})
            source = norm.get("source", "none")
            if source not in ("notebook", "ldh", "none"):
                errors.append(f"{finding['id']}: norm.source must be notebook, ldh or none")
                continue
            norm["source"] = source
            if source == "notebook":
                outcome = evidence.verify_quote(work, norm.get("doc_id", ""), norm.get("cited_text", ""))
                norm["verified"] = outcome.get("verified")
                if outcome.get("verified") not in ("exact", "fuzzy"):
                    norm["note"] = "цитату норми не знайдено в тексті блокнота законодавства; звірити"
            elif source == "ldh":
                norm["verified"] = None
                norm["marker"] = LDH_MARKER_UK
            else:
                norm["verified"] = None
            finding["norm"] = norm
            consequence = item.get("consequence")
            if consequence:
                finding["consequence"] = consequence
        save(work, data)
    return errors


def apply_calibration(work, items: list[dict]) -> list[str]:
    errors = []
    with store.locked(work):
        data = load(work)
        for item in items:
            finding = data["findings"].get(item.get("finding_id"))
            if finding is None:
                errors.append(f"unknown finding {item.get('finding_id')!r}")
                continue
            if finding.get("overridden"):
                continue
            if item.get("severity") not in SEVERITIES:
                errors.append(f"{finding['id']}: severity must be one of {SEVERITIES}")
                continue
            rec = item.get("recommendation") or {}
            if rec.get("type") not in REC_TYPES:
                errors.append(f"{finding['id']}: recommendation.type must be one of {REC_TYPES}")
                continue
            if not item.get("rationale"):
                errors.append(f"{finding['id']}: rationale is required")
                continue
            finding["severity"] = item["severity"]
            finding["rationale"] = item["rationale"]
            finding["recommendation"] = {"type": rec["type"], "text": rec.get("text", "")}
            if item.get("seller_request"):
                finding["seller_request"] = item["seller_request"]
        save(work, data)
    return errors


def critical_unconfirmed(finding: dict) -> bool:
    return final_severity(finding) == CRITICAL and finding.get("confirmation") != "verified"


def override(work, finding_id: str, severity: str | None = None, note: str | None = None) -> dict:
    with store.locked(work):
        data = load(work)
        finding = data["findings"].get(finding_id)
        if finding is None:
            return {"error": "unknown_finding"}
        if severity:
            if severity not in SEVERITIES:
                return {"error": "bad_severity"}
            finding["severity"] = severity
            finding["overridden"] = True
        if note:
            finding["user_note"] = note
        save(work, data)
    return {"finding_id": finding_id, "severity": finding.get("severity"), "user_note": finding.get("user_note")}


def uncalibrated(work) -> list[dict]:
    return [f for f in findings(work) if f.get("severity") is None]


def without_norm(work) -> list[dict]:
    return [f for f in findings(work) if f.get("norm") is None]


# --- request list for missing documents ---------------------------------------------------------------


def build_rfi(work, state: dict, ledgers: dict, items_by_block: dict) -> list[dict]:
    """Rows for the request of additional documents, derived from the ledger and the register."""
    rows: dict = {}
    docs = evidence.load_docs(work)["docs"]

    def add(key, kind, what, why, block_id, item_id, high):
        row = rows.setdefault(key, {"kind": kind, "what": what, "why": why, "blocks": [], "items": [],
                                    "priority": "звичайний"})
        if block_id not in row["blocks"]:
            row["blocks"].append(block_id)
        if item_id and item_id not in row["items"]:
            row["items"].append(item_id)
        if high:
            row["priority"] = "високий"

    for block_id, ledger in ledgers.items():
        tiers = {item["id"]: item["tier"] for item in items_by_block.get(block_id, [])}
        for item_id, row in ledger["items"].items():
            high = tiers.get(item_id) == "core"
            if row["status"] == MISSING:
                for entry in row.get("missing") or []:
                    label = DOC_KINDS_UK.get(entry.get("kind"), entry.get("kind", ""))
                    what = entry.get("what") or label
                    add(("m", entry.get("kind"), what), "document", what,
                        "потрібен для перевірки позицій блоку", block_id, item_id, high)
            elif row["status"] == UNVERIFIED and row.get("reason") in (
                    "scan_unreadable", "text_degraded", "no_full_text"):
                for doc_id in row.get("problem_docs", []):
                    doc = docs.get(doc_id)
                    if not doc:
                        continue
                    add(("q", doc_id), "quality",
                        f"Якісна копія або текстова версія документа «{doc['title']}»",
                        "текст документа нечитабельний або розпізнаний з помилками",
                        block_id, item_id, high)
    for finding in findings(work):
        norm = finding.get("norm")
        if norm and norm.get("source") == "none":
            need = norm.get("need") or norm.get("act") or "норма, на яку спирається знахідка"
            add(("n", need), "norm", f"Додати до блокнота законодавства: {need}",
                f"потрібна для правової основи знахідки {finding['id']}", finding["block_id"], None,
                final_severity(finding) != "info")
    result = []
    for number, (_key, row) in enumerate(sorted(rows.items(), key=lambda kv: (kv[1]["priority"] != "високий",
                                                                               kv[1]["kind"], kv[1]["what"])), 1):
        result.append({"id": f"Q-{number:02d}", **row})
    return result
