"""Task creation from a brief, and the preflight digest shown to the user before any review starts."""

from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timezone

from . import checklists, config, docs as docs_mod, evidence, ledger
from .vocab import DEPTH_UK, DEPTHS, DOC_KINDS_UK, MODULES, UNDETERMINED

LAW_KEY = "law"

_TRANSLIT = {
    "а": "a", "б": "b", "в": "v", "г": "h", "ґ": "g", "д": "d", "е": "e", "є": "ie", "ж": "zh", "з": "z",
    "и": "y", "і": "i", "ї": "i", "й": "i", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p",
    "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f", "х": "kh", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "shch",
    "ь": "", "ю": "iu", "я": "ia", "'": "", "’": "", "ʼ": "",
}


class BriefError(ValueError):
    pass


def slugify(text: str, limit: int = 40) -> str:
    out = []
    for ch in unicodedata.normalize("NFC", (text or "").lower()):
        if ch in _TRANSLIT:
            out.append(_TRANSLIT[ch])
        elif ch.isascii() and ch.isalnum():
            out.append(ch)
        else:
            out.append("-")
    slug = re.sub(r"-+", "-", "".join(out)).strip("-")
    return (slug[:limit].strip("-")) or "object"


def _notebook_key(ref: str, known: dict) -> str:
    for key, nb in known.items():
        if nb["ref"] == ref:
            return key
    return f"nb{len([k for k in known if k != LAW_KEY]) + 1}"


def new_state(brief: dict, options: dict, source: dict, task_id: str) -> tuple[dict, dict]:
    """Validate the brief and build the initial state plus `items.json` content. Raises BriefError."""
    problems = []
    obj = (brief.get("object") or "").strip()
    if not obj:
        problems.append("brief.object: потрібна назва об'єкта перевірки")
    depth = brief.get("depth", "full")
    if depth not in DEPTHS:
        problems.append(f"brief.depth: одне з {DEPTHS}")
    modules = brief.get("modules") or ["general"]
    if any(m not in MODULES for m in modules):
        problems.append(f"brief.modules: допустимі {MODULES}")
    if "general" not in modules:
        modules = ["general"] + list(modules)
    legislation = brief.get("legislation") or {}
    if not legislation.get("ref"):
        problems.append("brief.legislation.ref: потрібен блокнот законодавства")
    raw_blocks = brief.get("blocks") or []
    if not raw_blocks:
        problems.append("brief.blocks: потрібен хоча б один блок")
    notebooks = {LAW_KEY: {"key": LAW_KEY, "ref": legislation.get("ref"),
                           "title": legislation.get("title") or legislation.get("ref"), "role": "legislation"}}
    blocks, items_data, order = {}, {"blocks": {}}, []
    for raw in raw_blocks:
        errs = checklists.validate_block(raw)
        if errs:
            problems += errs
            continue
        if raw["id"] in blocks:
            problems.append(f"block {raw['id']}: повторний ідентифікатор")
            continue
        nb = raw["notebook"]
        if nb["ref"] == legislation.get("ref"):
            problems.append(f"block {raw['id']}: блокнот документів збігається з блокнотом законодавства")
            continue
        key = _notebook_key(nb["ref"], notebooks)
        notebooks.setdefault(key, {"key": key, "ref": nb["ref"], "title": nb.get("title") or nb["ref"],
                                   "role": "documents"})
        block = {"id": raw["id"], "title": raw["title"], "checklist": raw.get("checklist", "custom"),
                 "notebook_key": key, "source_filter": raw.get("source_filter"),
                 "custom_items": raw.get("custom_items", [])}
        try:
            items, norms = checklists.resolve_block_items(block, depth, modules)
        except checklists.ChecklistError as exc:
            problems.append(str(exc))
            continue
        blocks[block["id"]] = {k: v for k, v in block.items() if k != "custom_items"}
        items_data["blocks"][block["id"]] = {"items": items, "norms": norms}
        order.append(block["id"])
    if problems:
        raise BriefError("\n".join(problems))
    now = datetime.now(timezone.utc)
    state = {
        "task_id": task_id,
        "slug": f"dd-{now.strftime('%Y%m%d')}-{slugify(obj, 28)}",
        "object": obj,
        "buyer": (brief.get("buyer") or "").strip() or None,
        "transaction": (brief.get("transaction") or "купівля корпоративних прав").strip(),
        "context": (brief.get("context") or "").strip(),
        "created_at": now.isoformat(timespec="seconds"),
        "depth": depth,
        "modules": modules,
        "options": options,
        "option_sources": source,
        "notebooks": notebooks,
        "blocks": blocks,
        "block_order": order,
        "phase": "preflight_scout",
        "pause": None,
        "wave": None,
        "gate": None,
        "scout": {"listed": [], "rounds": 0},
        "item_attempts": {},
        "upgrade_done": False,
        "audit_reopened": False,
        "review_round": 0,
        "revision": None,
        "counters": {"slot": 0, "wave": 0},
        "outputs": {},
        "terminal": None,
        "degraded": [],
        "log": [],
    }
    return state, items_data


# --- estimate ---------------------------------------------------------------------------------------


def estimate(work, state: dict) -> dict:
    options = state["options"]
    items = 0
    for bid in state["block_order"]:
        items += len(ledger.block_items(work, bid))
    queries = items * options["queries_per_item_estimate"]
    parallel = max(1, options["max_parallel_queries"])
    hours = queries * options["seconds_per_query"] / parallel / 3600
    return {"items": items, "queries": queries, "parallel": parallel,
            "seconds_per_query": options["seconds_per_query"], "hours": round(hours, 1)}


# --- legislation hints ----------------------------------------------------------------------------


def norm_gaps(work, state: dict) -> list[dict]:
    """Acts named in checklists (act level only) that no source of the legislation notebook matches by title."""
    laws = docs_mod.law_docs(work)
    titles = [(d.get("title") or "").lower() for d in laws]
    gaps, seen = [], set()
    for bid in state["block_order"]:
        for norm in ledger.block_norms(work, bid):
            if norm["id"] in seen:
                continue
            seen.add(norm["id"])
            needles = [n.lower() for n in (norm.get("match") or [norm.get("act", "")]) if n]
            if not any(any(needle in title for needle in needles) for title in titles):
                gaps.append({"id": norm["id"], "act": norm.get("act", norm["id"]), "block": bid})
    return gaps


# --- gate digest ----------------------------------------------------------------------------------


def _doc_kind_label(kind: str) -> str:
    return DOC_KINDS_UK.get(kind, kind)


def digest(work, state: dict) -> dict:
    """Structured preflight picture and its Ukrainian text."""
    all_docs = docs_mod.all_docs(work)
    law = [d for d in all_docs if d["origin"] == "legislation"]
    blocks_info, missing_rows, undetermined = [], [], []
    for bid in state["block_order"]:
        block = state["blocks"][bid]
        bdocs = docs_mod.block_docs(work, state, bid)
        items = ledger.block_items(work, bid)
        no_scope = []
        for item in items:
            scope = evidence.block_docs_in_scope(bdocs, item["doc_kinds"], item["scope"])
            if not scope:
                no_scope.append(item)
        for item in no_scope:
            missing_rows.append({"block": bid, "item": item["id"], "title": item["title"],
                                 "kinds": [_doc_kind_label(k) for k in item["doc_kinds"]]})
        blocks_info.append({
            "id": bid, "title": block["title"], "notebook": state["notebooks"][block["notebook_key"]]["title"],
            "docs": len(bdocs), "project_docs": sum(1 for d in bdocs if d["origin"] == "project"),
            "items": len(items), "items_without_docs": len(no_scope),
        })
        for d in bdocs:
            if d.get("kind") == UNDETERMINED and d["doc_id"] not in {u["doc_id"] for u in undetermined}:
                undetermined.append({"doc_id": d["doc_id"], "title": d["title"]})
    gaps = norm_gaps(work, state)
    est = estimate(work, state)
    return {"blocks": blocks_info, "law_docs": len(law), "missing_rows": missing_rows,
            "undetermined": undetermined, "norm_gaps": gaps, "estimate": est}


def digest_text(info: dict, state: dict) -> str:
    lines = [f"## Готовність до перевірки: {state['object']}", "",
             f"Режим глибини: {DEPTH_UK[state['depth']]}. Модулі: {', '.join(state['modules'])}. "
             f"Джерел у блокноті законодавства: {info['law_docs']}.", "",
             "| Блок | Блокнот | Документів | з них у проєкті | Позицій | Позицій без документів |",
             "|---|---|---|---|---|---|"]
    for b in info["blocks"]:
        lines.append(f"| {b['title']} | {b['notebook']} | {b['docs']} | {b['project_docs']} | {b['items']} | "
                     f"{b['items_without_docs']} |")
    if info["missing_rows"]:
        lines += ["", "### Немає документів для позицій", ""]
        for row in info["missing_rows"][:40]:
            lines.append(f"- {row['block']}: {row['title']} — потрібні: {'; '.join(row['kinds'])}")
        if len(info["missing_rows"]) > 40:
            lines.append(f"- ще {len(info['missing_rows']) - 40} позицій")
        lines.append("Ці позиції отримають статус «Документ відсутній», а документи потраплять до запиту додаткових документів.")
    if info["undetermined"]:
        lines += ["", "### Документи невизначеного виду", ""]
        for d in info["undetermined"][:30]:
            lines.append(f"- {d['doc_id']}: {d['title']}")
        lines.append("Невизначений документ входить до обсягу кожної позиції блоку, тому звужує ефективність перевірки. "
                     "Вид можна задати командою `ddf docs set-kind`.")
    if info["norm_gaps"]:
        lines += ["", "### Акти, яких немає в блокноті законодавства", ""]
        for g in info["norm_gaps"]:
            lines.append(f"- {g['act']} (блок {g['block']})")
        lines.append("Для таких норм буде використано Legal Data Hunter з маркером [Перевірити чинну редакцію]; "
                     "краще додати акт до блокнота.")
    est = info["estimate"]
    lines += ["", "### Обсяг і час", "",
              f"Позицій перевірки: {est['items']}. Орієнтовно запитів до блокнотів: {est['queries']}. "
              f"При {est['parallel']} паралельних запитах і {est['seconds_per_query']} с на запит це близько "
              f"{est['hours']} год (оцінка, не вимір)."]
    return "\n".join(lines)
