"""Command line `ddf`. Every command prints one JSON object on stdout."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

from . import (config, docs as docs_mod, evidence, ledger, machine, nlm_block, preflight, register, render, store)
from .vocab import DOC_KINDS


def out(payload, code: int = 0) -> int:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    return code


def tasks_root() -> Path:
    path = config.plugin_data_dir() / "tasks"
    path.mkdir(parents=True, exist_ok=True)
    return path


def resolve_work(args) -> Path:
    if getattr(args, "work", None):
        return Path(args.work)
    task = getattr(args, "task", None) or os.environ.get("DDF_TASK")
    root = tasks_root()
    if task:
        return root / task
    candidates = sorted((p for p in root.iterdir() if (p / "state.json").is_file()), key=lambda p: p.stat().st_mtime,
                        reverse=True)
    for p in candidates:
        state = store.read_json(p / "state.json")
        if not state.get("terminal"):
            return p
    if candidates:
        return candidates[0]
    raise SystemExit(json.dumps({"error": "no_task", "message": "завдань немає; створи: ddf task new --brief <файл>"},
                                ensure_ascii=False))


def _overrides(pairs: list[str] | None) -> dict:
    result = {}
    for pair in pairs or []:
        key, _, value = pair.partition("=")
        result[key.strip()] = value.strip()
    return result


# --- task ---------------------------------------------------------------------------------------------------------


def cmd_task_new(args):
    brief = json.loads(Path(args.brief).read_text(encoding="utf-8-sig"))
    resolved = config.resolve_options(_overrides(args.option) | (brief.get("options") or {}))
    task_id = "t-" + datetime.now().strftime("%Y%m%d-%H%M%S")
    try:
        state, items = preflight.new_state(brief, resolved["options"], resolved["source"], task_id)
    except preflight.BriefError as exc:
        return out({"error": "bad_brief", "problems": str(exc).split("\n")}, 2)
    work = tasks_root() / task_id
    (work / "inbox").mkdir(parents=True, exist_ok=True)
    store.write_json(work / "brief.json", brief)
    ledger.save_items(work, items)
    machine.save(work, state)
    return out({"task_id": task_id, "work": str(work), "inbox": str(work / "inbox"), "slug": state["slug"],
                "blocks": {bid: len(items["blocks"][bid]["items"]) for bid in state["block_order"]},
                "notebooks": {k: v["title"] for k, v in state["notebooks"].items()},
                "options": resolved["options"]})


def cmd_task_list(args):
    rows = []
    for p in sorted(tasks_root().iterdir()):
        state = store.read_json(p / "state.json") if (p / "state.json").is_file() else None
        if state:
            rows.append({"task_id": state["task_id"], "object": state["object"], "phase": state["phase"],
                         "terminal": (state.get("terminal") or {}).get("status")})
    return out({"tasks": rows})


def cmd_task_status(args):
    return out(machine.status(resolve_work(args)))


def cmd_task_cancel(args):
    return out(machine.cancel(resolve_work(args)))


def cmd_task_resolve(args):
    work = resolve_work(args)
    return out({"work": str(work), "task_id": work.name})


# --- machine --------------------------------------------------------------------------------------------------------


def cmd_next(args):
    return out(machine.next_action(resolve_work(args)))


def cmd_report(args):
    result = machine.report_slot(resolve_work(args), args.slot)
    return out(result, 1 if result.get("error") else 0)


def cmd_gate(args):
    result = machine.gate_reply(resolve_work(args), args.decision, args.text)
    return out(result, 1 if result.get("error") else 0)


def cmd_resume(args):
    return out(machine.resume(resolve_work(args)))


# --- evidence ---------------------------------------------------------------------------------------------------------


def cmd_register_text(args):
    work = resolve_work(args)
    if args.doc:
        doc = evidence.get_doc(work, args.doc)
        if not doc:
            return out({"error": "doc_unknown", "doc_id": args.doc}, 1)
        identity = dict(origin=doc["origin"], notebook_key=doc.get("notebook_key"), source_id=doc["source_id"],
                        title=doc["title"], kind=doc.get("kind", "undetermined"))
    else:
        if not (args.origin and args.source_id and args.title):
            return out({"error": "need_doc_or_identity"}, 1)
        identity = dict(origin=args.origin, notebook_key=args.notebook, source_id=args.source_id, title=args.title,
                        kind=args.kind or "undetermined")
    if args.kind:
        identity["kind"] = args.kind
    if identity["kind"] not in DOC_KINDS:
        return out({"error": "bad_kind", "allowed": list(DOC_KINDS)}, 1)
    patterns = args.pattern or None
    result = evidence.register_text(work, file=args.file, date=args.date, patterns=patterns, **identity)
    return out(result)


def cmd_units(args):
    return out(evidence.list_units(resolve_work(args), args.doc, args.limit))


def cmd_cut(args):
    work = resolve_work(args)
    options = config.resolve_options()["options"]
    if args.from_ is not None and args.to is not None:
        return out(evidence.cut_span(work, args.doc, args.from_, args.to, options["cut_max_chars"]))
    result = evidence.cut(work, args.doc, unit=args.unit, scan_id=args.scan_id, hit=args.hit,
                          max_chars=options["cut_max_chars"])
    return out(result, 1 if result.get("error") else 0)


def cmd_scan(args):
    work = resolve_work(args)
    options = config.resolve_options()["options"]
    cap = args.cap or options["scan_hit_cap"]
    if args.item:
        block_id, _, item_id = args.item.partition(":")
        state = machine.load(work)
        item = next((i for i in ledger.block_items(work, block_id) if i["id"] == item_id), None)
        if not item or not item.get("probes"):
            return out({"error": "item_has_no_probes"}, 1)
        bdocs = docs_mod.block_docs(work, state, block_id)
        scope = evidence.block_docs_in_scope(bdocs, item["doc_kinds"], item["scope"])
        doc_ids = [d["doc_id"] for d in scope if (not args.docs or d["doc_id"] in args.docs.split(","))]
        probes, item_ref = item["probes"], args.item
    else:
        if not (args.docs and args.probe):
            return out({"error": "need_item_or_docs_and_probes"}, 1)
        doc_ids, probes, item_ref = args.docs.split(","), args.probe, None
    result = evidence.scan(work, doc_ids, probes, item_id=item_ref, cap=cap)
    if result.get("error"):
        return out(result, 1)
    summary = {"scan_id": result["scan_id"], "probes_sha": result["probes_sha"], "skipped": result["skipped"], "docs": {}}
    for doc_id, entry in result["docs"].items():
        summary["docs"][doc_id] = {
            "hits_total": entry["hits_total"], "truncated": entry["truncated"], "text_quality": entry["text_quality"],
            "hits": [{"n": h["n"], "unit": h["unit"], "excerpt": h["excerpt"]} for h in entry["hits"][:30]]}
    summary["next"] = ("Перегляньте кожен збіг: `ddf evidence cut --doc <id> --scan-id <scan> --hit <n>` читає навколишній "
                       "текст і позначає збіг переглянутим.")
    return out(summary)


def cmd_review(args):
    work = resolve_work(args)
    scan = store.read_json(evidence.scan_path(work, args.scan_id))
    if not scan or args.doc not in scan["docs"]:
        return out({"error": "scan_unknown"}, 1)
    hits = ([h["n"] for h in scan["docs"][args.doc]["hits"]] if args.hits == "all"
            else [int(x) for x in args.hits.split(",") if x.strip()])
    return out(evidence.mark_reviewed(work, args.scan_id, args.doc, hits))


def cmd_locate(args):
    return out(evidence.verify_quote(resolve_work(args), args.doc, args.quote))


# --- docs, findings, state -------------------------------------------------------------------------------------------------


def cmd_ingest(args):
    return out(docs_mod.ingest_inbox(resolve_work(args)))


def cmd_set_kind(args):
    if args.kind not in DOC_KINDS:
        return out({"error": "bad_kind", "allowed": list(DOC_KINDS)}, 1)
    evidence.set_kind(resolve_work(args), args.doc, args.kind, args.date)
    return out({"doc_id": args.doc, "kind": args.kind})


def cmd_docs_list(args):
    work = resolve_work(args)
    rows = [{k: d.get(k) for k in ("doc_id", "origin", "notebook_key", "source_id", "title", "kind", "text_quality",
                                    "fetch_state", "chars", "units")} for d in docs_mod.all_docs(work)]
    if args.origin:
        rows = [r for r in rows if r["origin"] == args.origin]
    return out({"docs": rows})


def cmd_override(args):
    result = register.override(resolve_work(args), args.finding_id, args.severity, args.note)
    return out(result, 1 if result.get("error") else 0)


def cmd_state_doc(args):
    doc = evidence.get_doc(resolve_work(args), args.doc)
    return out(doc or {"error": "doc_unknown"}, 0 if doc else 1)


def cmd_state_row(args):
    row = ledger.load_ledger(resolve_work(args), args.block)["items"].get(args.item)
    return out(row or {"error": "row_unknown"}, 0 if row else 1)


def cmd_nlm_block(args):
    work = resolve_work(args)
    state = machine.load(work)
    nb = state["notebooks"][args.notebook]
    return out({"block": nlm_block.delegation_block(nb, nb["role"], "ddf")})


def cmd_export(args):
    work = resolve_work(args)
    state = machine.load(work)
    return out(render.export(work, state, state.get("outputs", {}).get("writer")))


def cmd_deps_install(args):
    import subprocess

    target = config.plugin_data_dir() / "site-packages"
    target.mkdir(parents=True, exist_ok=True)
    req = config.plugin_root() / "requirements.txt"
    proc = subprocess.run([sys.executable, "-m", "pip", "install", "--quiet", "--disable-pip-version-check",
                           "--target", str(target), "-r", str(req)], capture_output=True, text=True, timeout=600)
    return out({"ok": proc.returncode == 0, "target": str(target), "stderr": proc.stderr[-1500:]},
               0 if proc.returncode == 0 else 1)


def cmd_deps(args):
    result = {"python": sys.version.split()[0]}
    for mod in ("docx", "openpyxl", "pypdf"):
        try:
            __import__(mod)
            result[mod] = True
        except ImportError:
            result[mod] = False
    result["plugin_root"] = str(config.plugin_root())
    result["plugin_data"] = str(config.plugin_data_dir())
    return out(result)


# --- parser -------------------------------------------------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="ddf", description="duedillforge: керування перевіркою")
    p.add_argument("--task", help="ідентифікатор завдання (за замовчуванням — останнє активне)")
    p.add_argument("--work", help=argparse.SUPPRESS)
    sub = p.add_subparsers(dest="cmd", required=True)

    task = sub.add_parser("task").add_subparsers(dest="sub", required=True)
    t = task.add_parser("new")
    t.add_argument("--brief", required=True)
    t.add_argument("--option", action="append", help="key=value")
    t.set_defaults(fn=cmd_task_new)
    task.add_parser("list").set_defaults(fn=cmd_task_list)
    task.add_parser("status").set_defaults(fn=cmd_task_status)
    task.add_parser("cancel").set_defaults(fn=cmd_task_cancel)
    task.add_parser("resolve").set_defaults(fn=cmd_task_resolve)

    sub.add_parser("next").set_defaults(fn=cmd_next)
    r = sub.add_parser("report")
    r.add_argument("--slot", required=True)
    r.set_defaults(fn=cmd_report)
    g = sub.add_parser("gate")
    g.add_argument("--decision", required=True)
    g.add_argument("--text")
    g.set_defaults(fn=cmd_gate)
    sub.add_parser("resume").set_defaults(fn=cmd_resume)
    sub.add_parser("status").set_defaults(fn=cmd_task_status)
    sub.add_parser("export").set_defaults(fn=cmd_export)
    deps = sub.add_parser("deps").add_subparsers(dest="sub")
    deps.add_parser("check").set_defaults(fn=cmd_deps)
    deps.add_parser("install").set_defaults(fn=cmd_deps_install)
    sub.choices["deps"].set_defaults(fn=cmd_deps)
    nb = sub.add_parser("nlm-block")
    nb.add_argument("--notebook", required=True)
    nb.set_defaults(fn=cmd_nlm_block)

    ev = sub.add_parser("evidence").add_subparsers(dest="sub", required=True)
    rt = ev.add_parser("register-text")
    rt.add_argument("--doc")
    rt.add_argument("--origin", choices=["notebook", "legislation", "project"])
    rt.add_argument("--notebook")
    rt.add_argument("--source-id")
    rt.add_argument("--title")
    rt.add_argument("--kind")
    rt.add_argument("--date")
    rt.add_argument("--file", required=True)
    rt.add_argument("--pattern", action="append", help="додатковий регулярний вираз заголовка одиниці")
    rt.set_defaults(fn=cmd_register_text)
    u = ev.add_parser("units")
    u.add_argument("--doc", required=True)
    u.add_argument("--limit", type=int, default=400)
    u.set_defaults(fn=cmd_units)
    c = ev.add_parser("cut")
    c.add_argument("--doc", required=True)
    c.add_argument("--unit")
    c.add_argument("--scan-id")
    c.add_argument("--hit", type=int)
    c.add_argument("--from", dest="from_", type=int)
    c.add_argument("--to", type=int)
    c.set_defaults(fn=cmd_cut)
    s = ev.add_parser("scan")
    s.add_argument("--item", help="BLOCK:ITEM — взяти ключові слова й область позиції")
    s.add_argument("--docs", help="doc_id через кому")
    s.add_argument("--probe", action="append")
    s.add_argument("--cap", type=int)
    s.set_defaults(fn=cmd_scan)
    rv = ev.add_parser("review")
    rv.add_argument("--scan-id", required=True)
    rv.add_argument("--doc", required=True)
    rv.add_argument("--hits", required=True, help="номери через кому або all")
    rv.set_defaults(fn=cmd_review)
    lc = ev.add_parser("locate")
    lc.add_argument("--doc", required=True)
    lc.add_argument("--quote", required=True)
    lc.set_defaults(fn=cmd_locate)

    d = sub.add_parser("docs").add_subparsers(dest="sub", required=True)
    d.add_parser("ingest-inbox").set_defaults(fn=cmd_ingest)
    sk = d.add_parser("set-kind")
    sk.add_argument("--doc", required=True)
    sk.add_argument("--kind", required=True)
    sk.add_argument("--date")
    sk.set_defaults(fn=cmd_set_kind)
    dl = d.add_parser("list")
    dl.add_argument("--origin")
    dl.set_defaults(fn=cmd_docs_list)

    f = sub.add_parser("finding").add_subparsers(dest="sub", required=True)
    fo = f.add_parser("override")
    fo.add_argument("finding_id")
    fo.add_argument("--severity")
    fo.add_argument("--note")
    fo.set_defaults(fn=cmd_override)

    st = sub.add_parser("state").add_subparsers(dest="sub", required=True)
    sd = st.add_parser("doc")
    sd.add_argument("doc")
    sd.set_defaults(fn=cmd_state_doc)
    sr = st.add_parser("ledger-row")
    sr.add_argument("block")
    sr.add_argument("item")
    sr.set_defaults(fn=cmd_state_row)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.fn(args)
