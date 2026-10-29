"""Prompts for the agents. One prompt = one file in `<work>/prompts/`; the router passes only its path.

The agent files (`agents/*.md`) hold the standing role instructions. A prompt holds what is specific to the slot:
paths, the batch, the notebook delegation block (`nlm_protocol.md` §8) and the exact output shape.
"""

from __future__ import annotations

import os
from pathlib import Path

from . import config, docs as docs_mod, evidence, ledger
from .nlm_block import delegation_block
from .vocab import DOC_KINDS_UK, QUALITY_UK, SEVERITY_DEF_UK, SEVERITY_UK

KEY_KINDS = {"statut", "corp_decision", "corp_register", "registry_extract", "title_doc", "permit",
             "contract_financing", "court_doc"}


def ddf_cmd(task_id: str) -> str:
    root = config.plugin_root()
    launcher = root / "scripts" / ("ddf.cmd" if os.name == "nt" else "ddf")
    return f'"{launcher}" --task {task_id}'


def _header(state: dict, work: Path, slot: dict) -> list[str]:
    return [
        f"# Завдання: {slot['description']}",
        "",
        f"Слот: {slot['slot_id']}. Об'єкт перевірки: {state['object']}. Угода: {state['transaction']}.",
        f"Команда керування: `{ddf_cmd(state['task_id'])}` (далі `ddf`). Усі виклики `ddf` — через Bash.",
        f"Спільні правила, обов'язкові до виконання: `{config.plugin_root() / 'lib' / 'agent-core'}` — "
        "`untrusted-content.md`, `output-contract.md`, `tooling.md` (прочитай їх до початку).",
        f"Результат запиши одним JSON-об'єктом (без обгортки markdown) у файл `{work / slot['output_file']}`. "
        "Після запису відповідай одним рядком: «готово». Додаткових файлів не створюй.",
        "",
    ]


def _notebook(state: dict, key: str) -> dict:
    return state["notebooks"][key]


def _doc_row(doc: dict) -> str:
    source = "—" if doc["origin"] == "project" else doc["source_id"]
    return (f"| {doc['doc_id']} | {source} | {doc['title'][:70]} | {DOC_KINDS_UK.get(doc.get('kind'), doc.get('kind'))} | "
            f"{QUALITY_UK[doc['text_quality']] if doc.get('fetch_state') != 'not_fetched' else 'текст не завантажено'} | "
            f"{doc.get('chars', 0)} | {doc.get('units', 0)} |")


# --- scout ------------------------------------------------------------------------------------------


def scout_prompt(state, work, slot, notebook_key: str) -> str:
    nb = _notebook(state, notebook_key)
    lines = _header(state, work, slot)
    lines += [
        "Склади перелік джерел блокнота. Тексти джерел не читай і не завантажуй.",
        "",
        delegation_block(nb, nb["role"], ddf_cmd(state["task_id"])),
        "",
        "## Що зробити",
        f"1. Виклич notebook_get для блокнота `{nb['ref']}`.",
        "2. Якщо результат збережено у файл, побудуй результат скриптом з цього файлу, не переписуючи записи вручну.",
        "3. Запиши у файл результату об'єкт:",
        "```",
        '{"notebook_key": "%s", "sources": [{"source_id": "...", "title": "..."}], "blocker": null}' % notebook_key,
        "```",
        "У `sources` — усі джерела блокнота, по одному запису на джерело, з точним ідентифікатором і назвою.",
        'Помилка автентифікації: `{"notebook_key": "%s", "sources": [], "blocker": "auth_expired"}`.' % notebook_key,
    ]
    return "\n".join(lines)


# --- block reviewer ---------------------------------------------------------------------------------


def read_plan(state: dict, scope: list[dict], upgrade: bool, options: dict) -> tuple[list[str], list[str]]:
    """Split the docs in scope into (read, screen). Key documents, project documents and small scopes are read in
    full; the mass of notebook contracts is screened with narrow queries. In the upgrade pass everything is read."""
    read, screen = [], []
    small = len(scope) <= options["small_scope_docs"]
    for doc in scope:
        if upgrade or small or doc["origin"] == "project" or doc.get("kind") in KEY_KINDS:
            read.append(doc["doc_id"])
        else:
            screen.append(doc["doc_id"])
    return read, screen


def review_prompt(state, work, slot, block_id: str, item_ids: list[str], parallel: int, upgrade_ids: set) -> str:
    block = state["blocks"][block_id]
    nb = _notebook(state, block["notebook_key"])
    options = state["options"]
    all_items = {item["id"]: item for item in ledger.block_items(work, block_id)}
    bdocs = docs_mod.block_docs(work, state, block_id)
    docs_by_id = {d["doc_id"]: d for d in bdocs}
    lines = _header(state, work, slot)
    lines += [
        f"Блок: «{block['title']}» (`{block_id}`). Глибина: {state['depth']}. "
        f"Паралельних запитів до блокнота: не більше {parallel}.",
        "",
        delegation_block(nb, "documents", ddf_cmd(state["task_id"])),
        "",
        "## Позиції перевірки",
        "",
    ]
    used_docs: dict = {}
    groups: dict = {}

    def listed(ids: list[str]) -> str:
        if not ids:
            return "—"
        if len(ids) <= 8:
            return ", ".join(ids)
        label = groups.setdefault(tuple(ids), f"Г{len(groups) + 1}")
        return f"група {label} ({len(ids)} документів)"

    for iid in item_ids:
        item = all_items[iid]
        scope = evidence.block_docs_in_scope(bdocs, item["doc_kinds"], item["scope"])
        read, screen = read_plan(state, scope, iid in upgrade_ids, options)
        for did in read + screen:
            used_docs[did] = docs_by_id[did]
        lines += [
            f"### {iid} · {item['title']} · {item['tier']} · область: {item['scope']}",
            f"Питання: {item['question']}",
        ]
        if item.get("unit_hint"):
            lines.append(f"Де шукати: {item['unit_hint']}")
        if item.get("probes"):
            lines.append("Ключові слова (регулярні вирази) для `ddf evidence scan`: "
                         + " ; ".join(f"`{p}`" for p in item["probes"]))
        lines.append(f"Читати повністю (doc_id): {listed(read)}")
        lines.append(f"Скринінг запитами: {listed(screen)}")
        if iid in upgrade_ids:
            lines.append("Позиція повторна: попередній скринінг не дав підстав для «Не виявлено»; прочитай документи повністю.")
        lines.append("")
    if groups:
        lines += ["## Групи документів", ""]
        lines += [f"- {label}: {', '.join(ids)}" for ids, label in groups.items()]
        lines.append("")
    lines += ["## Документи, згадані в позиціях", "",
              "| doc_id | source_id | назва | вид | текст | знаків | одиниць |", "|---|---|---|---|---|---|---|"]
    lines += [_doc_row(d) for d in used_docs.values()]
    lines += [
        "",
        "Документ зі станом «текст не завантажено» перед читанням завантаж: source_get_content за "
        "ідентифікатором джерела (стовпець source_id), потім "
        "`ddf evidence register-text --doc <doc_id> --file <шлях>`.",
        "",
        "## Результат",
        "",
        "```",
        '{"results": [ {позиція}, ... ], "blocker": null, "notes": ""}',
        "```",
        "Одна позиція на кожен `item_id` зі списку. Форма й правила статусів — у стандартній інструкції агента. "
        "Позицію, яку не встиг опрацювати, не включай: вона піде в наступний слот.",
    ]
    return "\n".join(lines)


# --- legal basis ------------------------------------------------------------------------------------


def legal_prompt(state, work, slot, findings: list[dict]) -> str:
    law = state["notebooks"]["law"]
    lines = _header(state, work, slot)
    lines += [delegation_block(law, "legislation", ddf_cmd(state["task_id"])), "", "## Знахідки без правової основи", ""]
    for f in findings:
        lines += [f"### {f['id']} · блок {f['block_id']} · документ {f['doc_id']} ({f['doc_title']})",
                  f"Положення: {f['pinpoint']}", f"Факт: {f['fact']}", f"Питання: {f['issue']}",
                  "Чинність закону на дату підписання впливає на висновок: "
                  + ("так" if f.get("intertemporal") else "ні"), ""]
    hints = {}
    for bid in state["block_order"]:
        for norm in ledger.block_norms(work, bid):
            hints[norm["id"]] = norm
    if hints:
        lines += ["## Акти, названі в переліках позицій (підказка, не підстава)", ""]
        lines += [f"- {n.get('act', n['id'])}" for n in hints.values()]
        lines.append("")
    laws = docs_mod.law_docs(work)
    lines += ["## Джерела блокнота законодавства (з останнього огляду)", "",
              "| doc_id | назва | текст | одиниць |", "|---|---|---|---|"]
    for d in laws[:200]:
        state_txt = "не завантажено" if d.get("fetch_state") == "not_fetched" else f"{d['chars']} знаків"
        lines.append(f"| {d['doc_id']} | {d['title'][:70]} | {state_txt} | {d.get('units', 0)} |")
    lines += ["", "## Результат", "", "```",
              '{"items": [{"finding_id": "...", "norm": {"source": "notebook|ldh|none", "doc_id": "...", '
              '"pinpoint": "...", "cited_text": "...", "act": "...", "need": "..."}, "consequence": "..."}], '
              '"blocker": null}', "```"]
    return "\n".join(lines)


# --- calibration ------------------------------------------------------------------------------------


def calibration_prompt(state, work, slot, findings: list[dict]) -> str:
    lines = _header(state, work, slot)
    lines += ["Шкала: " + "; ".join(f"{SEVERITY_UK[k]} — {v}" for k, v in SEVERITY_DEF_UK.items()), "",
              f"Повна шкала і приклади: `{config.plugin_root() / 'lib' / 'risk-scale.md'}`.", "",
              "## Знахідки", ""]
    for f in findings:
        norm = f.get("norm") or {}
        lines += [f"### {f['id']} · блок {f['block_id']} · пропозиція рівня: {f['severity_proposal']}",
                  f"Документ: {f['doc_title']} ({f['pinpoint']})", f"Факт: {f['fact']}", f"Питання: {f['issue']}",
                  f"Гіпотеза наслідку: {f.get('consequence_hypothesis') or f.get('consequence') or '—'}",
                  f"Норма: {norm.get('act') or norm.get('pinpoint') or norm.get('source', 'none')}",
                  f"Підтвердження цитати: {f['confirmation']}", ""]
    lines += ["## Результат", "", "```",
              '{"items": [{"finding_id": "...", "severity": "critical|significant|info", "rationale": "...", '
              '"recommendation": {"type": "price|specific_indemnity|condition_precedent|warranty|'
              'pre_closing_undertaking|post_closing_action|further_review|none", "text": "..."}, '
              '"seller_request": "..."}]}', "```"]
    return "\n".join(lines)


# --- coverage audit ---------------------------------------------------------------------------------


def audit_prompt(state, work, slot, sample: list[dict]) -> str:
    lines = _header(state, work, slot)
    lines += ["Перевір вибірку рядків реєстру покриття на достатність підстави. Дивись `ddf state ledger-row <block> <item>`.",
              "", "## Вибірка", ""]
    for row in sample:
        lines.append(f"- блок {row['block_id']}, позиція {row['item_id']} · статус {row['status']} · підстава {row.get('basis') or '—'}"
                     f" · документів у межах: {len(row['scope_docs'])}")
    lines += ["", "## Результат", "", "```",
              '{"verdicts": [{"block_id": "...", "item_id": "...", "ok": true, "reason": "..."}]}', "```"]
    return "\n".join(lines)


# --- writer and reviewers -----------------------------------------------------------------------------


def writer_prompt(state, work, slot, input_path: Path, revision: list[dict] | None) -> str:
    lines = _header(state, work, slot)
    lines += [f"Вхідні дані звіту: `{input_path}` (прочитай повністю).", ""]
    if revision:
        lines += ["## Зауваження рецензентів до попередньої версії (виправ кожне)", ""]
        for r in revision:
            lines.append(f"- {r.get('where', '')}: {r.get('problem', '')} → {r.get('fix', '')}")
        lines.append("")
    lines += ["## Результат", "", "```",
              '{"summary_points": ["..."], "signing_questions": [{"text": "...", "finding_ids": ["R-..."]}], '
              '"block_summaries": {"<block_id>": "..."}, "limitations_note": "..."}', "```"]
    return "\n".join(lines)


def reviewer_prompt(state, work, slot, draft_path: Path, register_path: Path, kind: str) -> str:
    lines = _header(state, work, slot)
    lines += [f"Чернетка звіту: `{draft_path}`. Реєстр знахідок: `{register_path}`.", "", "## Результат", "", "```",
              '{"issues": [{"where": "розділ або id знахідки", "problem": "...", "fix": "...", "must": true}]}', "```",
              "`must: true` — виправлення обов'язкове; `false` — побажання. Якщо зауважень немає, `issues` порожній."]
    return "\n".join(lines)
