"""The verification ledger: one row per checklist item, and the rules that decide what a row may say.

An agent *claims* a status; this module *grants* one. «Не виявлено» is granted only when the program's
own logs (cuts, scans, quotation checks) show that the check was complete. Otherwise the row is written
down to «Не підтверджено» with the exact reason. The wording of statuses in the report comes from
`vocab.py`, never from a model.
"""

from __future__ import annotations

from pathlib import Path

from . import docs as docs_mod
from . import evidence, indexer, register, store
from .vocab import (
    CLAIMABLE, CLEAR, DOC_KINDS, MISSING, NA, PENDING, Q_NONE, Q_OK, REASONS_UK, RISK, SCREENED, STATUSES,
    UNVERIFIED,
)


def items_path(work) -> Path:
    return Path(work) / "items.json"


def ledger_path(work, block_id: str) -> Path:
    return Path(work) / "ledger" / f"{block_id}.json"


def load_items(work) -> dict:
    return store.read_json(items_path(work), {"blocks": {}}) or {"blocks": {}}


def save_items(work, data: dict) -> None:
    store.write_json(items_path(work), data)


def block_items(work, block_id: str) -> list[dict]:
    return load_items(work)["blocks"].get(block_id, {}).get("items", [])


def block_norms(work, block_id: str) -> list[dict]:
    return load_items(work)["blocks"].get(block_id, {}).get("norms", [])


def load_ledger(work, block_id: str) -> dict:
    return store.read_json(ledger_path(work, block_id), {"block_id": block_id, "items": {}}) or {
        "block_id": block_id, "items": {}}


def save_ledger(work, ledger: dict) -> None:
    store.write_json(ledger_path(work, ledger["block_id"]), ledger)


def all_ledgers(work, state: dict) -> dict:
    return {bid: load_ledger(work, bid) for bid in state["blocks"]}


def _new_row(item: dict, block_id: str) -> dict:
    return {
        "item_id": item["id"], "block_id": block_id, "status": PENDING, "reason": None, "detail": None,
        "basis": None, "basis_by_doc": {}, "scope_docs": [], "problem_docs": [], "units_read": {},
        "finding_ids": [], "queries": 0, "missing": [], "evidence": [], "downgraded_from": None,
        "na_reason": None, "history": [], "updated_at": store.now_iso(),
    }


def _hard_unreadable(doc: dict) -> bool:
    """Text is present but unusable, or could not be fetched. A listed-only source (`not_fetched`) is not a
    problem yet: its text is fetched on demand."""
    return doc["text_quality"] != Q_OK and doc.get("fetch_state") != "not_fetched"


def _doc_problem_reason(docs: list[dict]) -> str:
    if any(not d.get("text_path") for d in docs):
        return "no_full_text"
    if any(d["text_quality"] == Q_NONE for d in docs):
        return "scan_unreadable"
    return "text_degraded"


def init_block(work, state: dict, block_id: str) -> dict:
    """Create `pending` rows for every item of the block; settle the rows the program can settle itself."""
    items = block_items(work, block_id)
    docs = docs_mod.block_docs(work, state, block_id)
    with store.locked(work):
        ledger = load_ledger(work, block_id)
        for item in items:
            if item["id"] in ledger["items"]:
                continue
            row = _new_row(item, block_id)
            scope = evidence.block_docs_in_scope(docs, item["doc_kinds"], item["scope"])
            row["scope_docs"] = [d["doc_id"] for d in scope]
            if not scope:
                row["status"] = MISSING
                row["missing"] = [{"kind": kind, "what": ""} for kind in item["doc_kinds"]]
                row["detail"] = "у блокноті та документах проєкту немає документів потрібного виду"
            elif all(d["text_quality"] == Q_NONE and _hard_unreadable(d) for d in scope):
                row["status"] = UNVERIFIED
                row["reason"] = _doc_problem_reason(scope)
                row["problem_docs"] = [d["doc_id"] for d in scope]
                row["detail"] = "усі документи в межах позиції нечитабельні або без тексту"
            ledger["items"][item["id"]] = row
        save_ledger(work, ledger)
    return ledger


def pending_item_ids(work, block_id: str) -> list[str]:
    ledger = load_ledger(work, block_id)
    order = [item["id"] for item in block_items(work, block_id)]
    return [iid for iid in order if ledger["items"].get(iid, {}).get("status") == PENDING]


def reopen(work, block_id: str, item_ids: list[str], reason: str) -> int:
    count = 0
    with store.locked(work):
        ledger = load_ledger(work, block_id)
        for iid in item_ids:
            row = ledger["items"].get(iid)
            if not row or row["status"] not in (CLEAR, SCREENED):
                continue
            row["history"].append({"status": row["status"], "reason": row["reason"], "at": store.now_iso(),
                                   "reopened_because": reason})
            row.update({"status": PENDING, "reason": None, "detail": reason, "basis": None, "basis_by_doc": {},
                        "units_read": {}, "downgraded_from": None, "updated_at": store.now_iso()})
            count += 1
        save_ledger(work, ledger)
    return count


def upgrade_candidates(work, block_id: str) -> list[str]:
    ledger = load_ledger(work, block_id)
    tiers = {item["id"]: item["tier"] for item in block_items(work, block_id)}
    return [iid for iid, row in ledger["items"].items() if row["status"] == SCREENED and tiers.get(iid) == "core"]


def finalize_pending(work, state: dict) -> int:
    count = 0
    with store.locked(work):
        for block_id in state["blocks"]:
            ledger = load_ledger(work, block_id)
            for row in ledger["items"].values():
                if row["status"] == PENDING:
                    row.update({"status": UNVERIFIED, "reason": "not_reached", "updated_at": store.now_iso()})
                    count += 1
            save_ledger(work, ledger)
    return count


# --- evaluation of an agent's claim ------------------------------------------------------------------


def _fail(reason: str, detail: str):
    return False, reason, detail, {}, {}


def _check_clear(work, item: dict, scope: list[dict], res: dict, cap: int):
    """Return (ok, reason_code, detail, basis_by_doc, coverage_by_doc)."""
    if not scope:
        return _fail("clear_not_supported", "немає документів у межах позиції")
    unreadable = [d for d in scope if d["text_quality"] != Q_OK]
    if unreadable:
        return _fail(_doc_problem_reason(unreadable),
                     "документи без придатного тексту: " + ", ".join(d["doc_id"] for d in unreadable))
    default_basis = res.get("basis")
    by_doc_in = res.get("basis_by_doc") or {}
    used, coverage = {}, {}
    for doc in scope:
        doc_id = doc["doc_id"]
        basis = by_doc_in.get(doc_id, default_basis)
        if item["scope"] == "whole_doc" and basis != "scan":
            return _fail("clear_not_supported", f"{doc_id}: для позиції по всьому документу потрібен повнотекстовий пошук")
        if basis == "scan":
            if not item.get("probes"):
                return _fail("clear_not_supported", f"{doc_id}: у позиції немає ключових слів для пошуку")
            scan_state = evidence.latest_scan_state(work, doc_id, evidence.probes_sha(item["probes"]))
            if scan_state is None:
                return _fail("clear_not_supported", f"{doc_id}: повнотекстовий пошук за ключовими словами позиції не виконано")
            if scan_state["truncated"] or scan_state["hits_total"] > cap:
                return _fail("too_many_hits", f"{doc_id}: збігів {scan_state['hits_total']}, понад ліміт {cap}")
            if scan_state["unreviewed"]:
                return _fail("clear_not_supported", f"{doc_id}: непереглянуті збіги {scan_state['unreviewed']}")
            coverage[doc_id] = {"basis": "scan", "hits": scan_state["hits_total"]}
        elif basis == "units":
            if not doc.get("units"):
                return _fail("no_structure", f"{doc_id}: структурних одиниць не виділено")
            claimed = {indexer.normalize_key(u) for u in (res.get("units_read") or {}).get(doc_id, [])}
            logged = evidence.cut_unit_keys(work, doc_id)
            if not claimed:
                return _fail("clear_not_supported", f"{doc_id}: не названо прочитаних одиниць")
            if not claimed <= logged:
                return _fail("clear_not_supported",
                             f"{doc_id}: одиниці {sorted(claimed - logged)} не читалися через `evidence cut`")
            coverage[doc_id] = {"basis": "units", "units_read": len(claimed), "units_total": doc["units"]}
        elif basis == "chain":
            entries = [c for c in (res.get("chain") or []) if c.get("doc_id") == doc_id]
            if not entries:
                return _fail("chain_gap", f"{doc_id}: ланцюг цитат не подано")
            for entry in entries:
                chain = evidence.chain_coverage(work, doc_id, entry.get("unit", ""), entry.get("quotes", []))
                if not chain["ok"]:
                    return _fail("chain_gap", f"{doc_id}: ланцюг цитат має розриви ({chain.get('reason')})")
            coverage[doc_id] = {"basis": "chain", "units_read": len(entries), "units_total": doc["units"]}
        else:
            return _fail("clear_not_supported", f"{doc_id}: не вказано підставу (scan, units або chain)")
        used[doc_id] = basis
    return True, None, None, used, coverage


def _evaluate(work, state: dict, block_id: str, item: dict, res: dict, cap: int):
    """Return (row_patch, warnings)."""
    warnings: list[str] = []
    docs = docs_mod.block_docs(work, state, block_id)
    docs_by_id = {d["doc_id"]: d for d in docs}
    scope = evidence.block_docs_in_scope(docs, item["doc_kinds"], item["scope"])
    claimed = res.get("status")
    patch = {"queries": int(res.get("queries") or 0), "scope_docs": [d["doc_id"] for d in scope],
             "units_read": res.get("units_read") or {}, "detail": (res.get("detail") or "")[:500] or None}
    if claimed not in CLAIMABLE:
        return {**patch, "status": UNVERIFIED, "reason": "agent_failed", "detail": f"невідомий статус {claimed!r}"}, warnings
    if claimed == RISK:
        drafts = res.get("findings") or []
        valid = [d for d in drafts if d.get("doc_id") in docs_by_id and d.get("fact") and d.get("issue")]
        if not valid:
            return {**patch, "status": UNVERIFIED, "reason": "agent_failed",
                    "detail": "статус «Виявлено» без чинної знахідки (потрібні doc_id, fact, issue, evidence)",
                    "downgraded_from": RISK}, warnings
        ids = []
        for draft in valid:
            fid, warn = register.add_finding(work, block_id, item["id"], draft, docs_by_id[draft["doc_id"]])
            ids.append(fid)
            warnings += [f"{fid}: {w}" for w in warn]
        return {**patch, "status": RISK, "finding_ids": ids, "reason": None}, warnings
    if claimed == CLEAR:
        ok, reason, detail, used, coverage = _check_clear(work, item, scope, res, cap)
        if ok:
            return {**patch, "status": CLEAR, "basis": res.get("basis") or "mixed", "basis_by_doc": used,
                    "coverage": coverage, "reason": None, "detail": None}, warnings
        if not scope:
            return {**patch, "status": MISSING, "downgraded_from": CLEAR, "detail": detail,
                    "missing": [{"kind": k, "what": ""} for k in item["doc_kinds"]]}, warnings
        problems = [d["doc_id"] for d in scope if _hard_unreadable(d)]
        return {**patch, "status": UNVERIFIED, "reason": reason, "downgraded_from": CLEAR, "detail": detail,
                "problem_docs": problems}, warnings
    if claimed == SCREENED:
        if int(res.get("queries") or 0) < 1:
            return {**patch, "status": UNVERIFIED, "reason": "query_failed", "downgraded_from": SCREENED,
                    "detail": "«Скринінг» без жодного запиту до блокнота"}, warnings
        return {**patch, "status": SCREENED, "reason": None}, warnings
    if claimed == MISSING:
        entries = [m for m in (res.get("missing") or []) if m.get("kind") in DOC_KINDS]
        if not entries:
            entries = [{"kind": k, "what": ""} for k in item["doc_kinds"]]
        return {**patch, "status": MISSING, "missing": entries}, warnings
    if claimed == NA:
        reason = (res.get("na_reason") or "").strip()
        if not reason:
            return {**patch, "status": UNVERIFIED, "reason": "agent_failed", "downgraded_from": NA,
                    "detail": "«Не застосовується» без причини"}, warnings
        return {**patch, "status": NA, "na_reason": reason[:300]}, warnings
    reason = res.get("reason") if res.get("reason") in REASONS_UK else "agent_failed"
    return {**patch, "status": UNVERIFIED, "reason": reason,
            "problem_docs": [d["doc_id"] for d in scope if _hard_unreadable(d)]}, warnings


def apply_results(work, state: dict, block_id: str, results: list[dict], cap: int, agent: str = "") -> dict:
    """Merge an agent's results into the ledger. Returns {"applied": n, "rejected": [...], "warnings": [...]}."""
    items = {item["id"]: item for item in block_items(work, block_id)}
    applied, rejected, warnings = 0, [], []
    with store.locked(work):
        ledger = load_ledger(work, block_id)
        for res in results:
            iid = res.get("item_id")
            if iid not in items:
                rejected.append({"item_id": iid, "why": "позиції немає в переліку блоку"})
                continue
            row = ledger["items"].setdefault(iid, _new_row(items[iid], block_id))
            if row["status"] != PENDING:
                rejected.append({"item_id": iid, "why": f"позиція вже має статус {row['status']}"})
                continue
            patch, warn = _evaluate(work, state, block_id, items[iid], res, cap)
            warnings += [f"{iid}: {w}" for w in warn]
            row.update(patch)
            row["updated_at"] = store.now_iso()
            row["agent"] = agent
            applied += 1
        save_ledger(work, ledger)
    return {"applied": applied, "rejected": rejected, "warnings": warnings}


# --- statistics -----------------------------------------------------------------------------------------


def stats(ledger: dict) -> dict:
    counts = {status: 0 for status in STATUSES}
    for row in ledger["items"].values():
        counts[row["status"]] += 1
    total = sum(counts.values())
    confirmed = counts[RISK] + counts[CLEAR] + counts[NA]
    return {"counts": counts, "total": total, "confirmed": confirmed,
            "confirmed_pct": round(100 * confirmed / total) if total else 0,
            "screened_pct": round(100 * counts[SCREENED] / total) if total else 0,
            "open": counts[UNVERIFIED] + counts[MISSING] + counts[PENDING]}


def overall(work, state: dict) -> dict:
    per_block, totals = {}, {status: 0 for status in STATUSES}
    for bid in state["blocks"]:
        st = stats(load_ledger(work, bid))
        per_block[bid] = st
        for status, n in st["counts"].items():
            totals[status] += n
    total = sum(totals.values())
    confirmed = totals[RISK] + totals[CLEAR] + totals[NA]
    return {"blocks": per_block, "counts": totals, "total": total,
            "confirmed_pct": round(100 * confirmed / total) if total else 0}
