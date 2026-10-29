"""Checklists of review positions («позиції перевірки») per block.

A checklist is a JSON file in `lib/checklists/`. It holds questions, not legal assertions: the norms a
position relies on are named at the level of the act only (`norms`), and the articles are found by the
legal-basis agent in the legislation notebook. The files are drafts until the lawyer approves them.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

from . import config
from .vocab import DOC_KINDS, SCOPES, TIERS

BLOCK_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{1,31}$")
ITEM_REQUIRED = ("id", "title", "question", "tier", "scope", "doc_kinds")


class ChecklistError(ValueError):
    pass


def checklist_dir() -> Path:
    return config.plugin_root() / "lib" / "checklists"


@lru_cache(maxsize=1)
def load_all() -> dict:
    """{checklist_id: data}; overlay files are folded in under the key `overlays`."""
    import json

    result = {}
    for path in sorted(checklist_dir().glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        if "id" in data:
            result[data["id"]] = data
    return result


def validate_item(item: dict) -> list[str]:
    errors = []
    for key in ITEM_REQUIRED:
        if key not in item:
            errors.append(f"item {item.get('id', '?')}: missing {key}")
    if errors:
        return errors
    if item["tier"] not in TIERS:
        errors.append(f"item {item['id']}: tier must be one of {TIERS}")
    if item["scope"] not in SCOPES:
        errors.append(f"item {item['id']}: scope must be one of {SCOPES}")
    if not isinstance(item["doc_kinds"], list) or not item["doc_kinds"]:
        errors.append(f"item {item['id']}: doc_kinds must be a non-empty list")
    else:
        for kind in item["doc_kinds"]:
            if kind not in DOC_KINDS:
                errors.append(f"item {item['id']}: unknown doc kind {kind!r}")
    for probe in item.get("probes", []) or []:
        try:
            re.compile(probe)
        except re.error as exc:
            errors.append(f"item {item['id']}: bad probe {probe!r}: {exc}")
    if item["scope"] == "whole_doc" and not item.get("probes"):
        errors.append(f"item {item['id']}: scope whole_doc needs probes")
    return errors


def resolve_block_items(block: dict, depth: str, modules: list[str]) -> tuple[list, list]:
    """(items, norms) of one block for this task: checklist + agro overlay, `core` only for red-flag."""
    checklist_id = block.get("checklist", "custom")
    items: list[dict] = []
    norms: list[dict] = []
    if checklist_id == "custom":
        items = [dict(item) for item in block.get("custom_items", [])]
        if not items:
            raise ChecklistError(f"block {block['id']}: custom checklist has no custom_items")
    else:
        catalog = load_all()
        if checklist_id not in catalog:
            raise ChecklistError(f"block {block['id']}: unknown checklist {checklist_id!r}")
        data = catalog[checklist_id]
        items = [dict(item) for item in data.get("items", [])]
        norms = list(data.get("norms", []))
        if "agro" in modules:
            overlay = catalog.get("agro", {}).get("overlays", {}).get(checklist_id)
            if overlay:
                items += [dict(item) for item in overlay.get("items", [])]
                norms += list(overlay.get("norms", []))
    seen = set()
    for item in items:
        problems = validate_item(item)
        if problems:
            raise ChecklistError("; ".join(problems))
        if item["id"] in seen:
            raise ChecklistError(f"block {block['id']}: duplicate item id {item['id']}")
        seen.add(item["id"])
        item.setdefault("probes", [])
        item.setdefault("weight", "normal")
        item.setdefault("norm_ids", [])
        item.setdefault("unit_hint", "")
    if depth == "redflag":
        items = [item for item in items if item["tier"] == "core"]
    norm_index = {norm["id"]: norm for norm in norms}
    block_norms = [norm_index[nid] for nid in sorted({n for item in items for n in item["norm_ids"]})
                   if nid in norm_index]
    return items, block_norms


def validate_block(block: dict) -> list[str]:
    errors = []
    if not BLOCK_ID_RE.match(block.get("id", "")):
        errors.append(f"block id {block.get('id')!r}: use lowercase latin letters, digits, - or _")
    if not block.get("title"):
        errors.append(f"block {block.get('id')}: title is required")
    nb = block.get("notebook")
    if not isinstance(nb, dict) or not nb.get("ref"):
        errors.append(f"block {block.get('id')}: notebook.ref is required")
    return errors
