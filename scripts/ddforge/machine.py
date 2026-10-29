"""The task state machine: `next` decides what happens, `report` takes an agent's output, `gate` takes the
user's decision. Deterministic phases run inside `next`; only dispatch, gate and terminal reach the router.

Phases: preflight_scout -> preflight_gate -> block_review -> legal_basis -> calibration -> coverage_audit ->
findings_gate -> drafting -> review -> export -> (done | failed | cancelled). A pause (expired NotebookLM login)
is a flag, not a phase. A failed slot never stops the task: its output degrades to an honest «not confirmed».
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from . import classify, dispatch, docs as docs_mod, evidence, ledger, preflight, register, render, store
from .vocab import CLEAR, CRITICAL, NA, SCREENED

PHASES = ("preflight_scout", "preflight_gate", "block_review", "legal_basis", "calibration", "coverage_audit",
          "findings_gate", "drafting", "review", "export")


# --- state persistence ------------------------------------------------------------------------------


def state_path(work) -> Path:
    return Path(work) / "state.json"


def load(work) -> dict:
    return store.read_json(state_path(work))


def save(work, state: dict) -> None:
    store.write_json(state_path(work), state)


def log(state: dict, message: str) -> None:
    state["log"].append({"at": store.now_iso(), "phase": state.get("phase"), "message": message})
    del state["log"][:-200]


# --- slots and waves --------------------------------------------------------------------------------


def _slot(state, work, agent: str, kind: str, description: str, meta: dict, prompt_fn, model: str | None = None) -> dict:
    state["counters"]["slot"] += 1
    slot_id = f"s-{state['counters']['slot']:03d}"
    slot = {"slot_id": slot_id, "agent": agent, "kind": kind, "description": description, "meta": meta,
            "status": "open", "attempts": 0, "prompt_file": f"prompts/{slot_id}.md",
            "output_file": f"slots/{slot_id}.json", "model": model}
    store.write_text(Path(work) / slot["prompt_file"], prompt_fn(slot))
    return slot


def _start_wave(state, slots: list[dict]) -> None:
    state["counters"]["wave"] += 1
    state["wave"] = {"id": f"w-{state['counters']['wave']:03d}", "phase": state["phase"],
                     "slots": {s["slot_id"]: s for s in slots}}


def _dispatch_action(state, work, open_slots: list[dict]) -> dict:
    work = Path(work)
    return {
        "action": "dispatch",
        "phase": state["phase"],
        "wave_id": state["wave"]["id"],
        "note": "Запусти всіх агентів нижче паралельно в одному повідомленні. Коли агент завершить, виклич "
                "`ddf report --slot <slot_id>` для його слота. Коли всі слоти хвилі опрацьовано, виклич `ddf next`.",
        "slots": [{
            "slot_id": s["slot_id"],
            "agent": f"duedillforge:{s['agent']}",
            "description": s["description"],
            "model": s.get("model"),
            "prompt": f"Виконай завдання з файлу `{work / s['prompt_file']}` (прочитай його повністю). "
                      f"Результат запиши у `{work / s['output_file']}`.",
            "output_file": str(work / s["output_file"]),
        } for s in open_slots],
    }


# --- main entry: next -------------------------------------------------------------------------------


def next_action(work) -> dict:
    work = Path(work)
    with store.locked(work):
        state = load(work)
        for _ in range(200):
            if state.get("terminal"):
                save(work, state)
                return _terminal_action(state)
            if state.get("pause"):
                save(work, state)
                return {"action": "pause", "reason": state["pause"]["reason"],
                        "message": ("Вхід у NotebookLM протермінований. Виконай `nlm login` у терміналі, потім "
                                    "запусти /duedillforge:continue: плагін викличе refresh_auth і поновить роботу.")}
            wave = state.get("wave")
            if wave:
                open_slots = [s for s in wave["slots"].values() if s["status"] == "open"]
                if open_slots:
                    save(work, state)
                    return _dispatch_action(state, work, open_slots)
                state["wave"] = None
                continue
            if state.get("gate"):
                save(work, state)
                return _gate_action(state)
            handler = HANDLERS[state["phase"]]
            result = handler(work, state)
            if result is not None:
                save(work, state)
                return result
        raise RuntimeError("state machine did not settle in 200 steps")


def _terminal_action(state: dict) -> dict:
    t = state["terminal"]
    return {"action": t["status"], "message": t.get("message"), "outputs": state.get("outputs", {}).get("files", []),
            "warnings": state.get("outputs", {}).get("warnings", []), "degraded": state.get("degraded", [])}


def _gate_action(state: dict) -> dict:
    g = state["gate"]
    return {"action": "gate", "gate_id": g["id"], "text": g["text"], "decisions": g["decisions"]}


def _finish(state, status: str, message: str) -> None:
    state["terminal"] = {"status": status, "message": message, "at": store.now_iso()}
    log(state, f"{status}: {message}")


# --- handlers: preflight ------------------------------------------------------------------------------


def h_preflight_scout(work, state):
    docs_mod.ingest_inbox(work)
    todo = [key for key in state["notebooks"] if key not in state["scout"]["listed"]]
    if not todo:
        state["phase"] = "preflight_gate"
        return None
    cap = state["options"]["max_agents_per_wave"]
    slots = []
    for key in todo[:cap]:
        nb = state["notebooks"][key]
        slots.append(_slot(state, work, "scout", "scout_list", f"Перелік джерел блокнота «{nb['title']}»",
                           {"notebook_key": key},
                           lambda slot, key=key: dispatch.scout_prompt(state, Path(work), slot, key)))
    _start_wave(state, slots)
    return None


def h_preflight_gate(work, state):
    docs_mod.ingest_inbox(work)
    info = preflight.digest(work, state)
    state["gate"] = {"id": "g-preflight", "kind": "preflight", "decisions": ["proceed", "rescan", "cancel"],
                     "text": preflight.digest_text(info, state)}
    return None


def gate_reply(work, decision: str, text: str | None = None) -> dict:
    work = Path(work)
    with store.locked(work):
        state = load(work)
        gate = state.get("gate")
        if not gate:
            return {"error": "no_open_gate"}
        if decision not in gate["decisions"]:
            return {"error": "bad_decision", "allowed": gate["decisions"]}
        if text:
            gate["reply_text"] = text
        if decision == "cancel":
            state["gate"] = None
            _finish(state, "cancelled", "завдання скасовано користувачем")
        elif gate["kind"] == "preflight":
            state["gate"] = None
            if decision == "rescan":
                state["scout"]["listed"] = []
                state["phase"] = "preflight_scout"
            else:
                state["phase"] = "block_review"
        elif gate["kind"] == "findings":
            state["gate"] = None
            state["findings_gate_done"] = True
            state["phase"] = "drafting"
        log(state, f"gate {gate['id']}: {decision}")
        save(work, state)
    return {"ok": True, "decision": decision}


# --- handlers: block review ----------------------------------------------------------------------------


def _batches(work, state) -> list[tuple[str, list[str]]]:
    size = state["options"]["items_per_batch"]
    result = []
    for bid in state["block_order"]:
        pending = ledger.pending_item_ids(work, bid)
        for i in range(0, len(pending), size):
            result.append((bid, pending[i:i + size]))
    return result


def _upgrade(work, state) -> int:
    """Full depth: reopen `screened` core items whose scope is small enough for a full read."""
    options = state["options"]
    reopened = 0
    ids = state.setdefault("upgrade_ids", [])
    for bid in state["block_order"]:
        bdocs = docs_mod.block_docs(work, state, bid)
        items = {i["id"]: i for i in ledger.block_items(work, bid)}
        todo = []
        for iid in ledger.upgrade_candidates(work, bid):
            scope = evidence.block_docs_in_scope(bdocs, items[iid]["doc_kinds"], items[iid]["scope"])
            if 0 < len(scope) <= options["upgrade_max_docs"]:
                todo.append(iid)
        if todo:
            reopened += ledger.reopen(work, bid, todo, "повторна перевірка: прочитати документи повністю")
            ids += [f"{bid}:{iid}" for iid in todo]
    return reopened


def h_block_review(work, state):
    for bid in state["block_order"]:
        ledger.init_block(work, state, bid)
    batches = _batches(work, state)
    if not batches:
        if state["depth"] == "full" and not state["upgrade_done"]:
            state["upgrade_done"] = True
            if _upgrade(work, state):
                log(state, "повторний прохід: позиції зі скринінгом переведено на повне читання")
                return None
        state["phase"] = "legal_basis"
        return None
    options = state["options"]
    n_agents = max(1, min(options["max_parallel_queries"], options["max_agents_per_wave"], len(batches)))
    parallel = max(1, options["max_parallel_queries"] // n_agents)
    upgrade = set(state.get("upgrade_ids", []))
    slots = []
    for bid, item_ids in batches[:n_agents]:
        block = state["blocks"][bid]
        up = {i for i in item_ids if f"{bid}:{i}" in upgrade}
        slots.append(_slot(
            state, work, "block-reviewer", "review_batch",
            f"Блок «{block['title']}»: позиції {item_ids[0]}…{item_ids[-1]} ({len(item_ids)})",
            {"block_id": bid, "item_ids": item_ids},
            lambda slot, bid=bid, item_ids=item_ids, up=up: dispatch.review_prompt(
                state, Path(work), slot, bid, item_ids, parallel, up)))
    _start_wave(state, slots)
    return None


# --- handlers: legal basis, calibration, audit ------------------------------------------------------------


def h_legal_basis(work, state):
    pending = register.without_norm(work)
    if not pending:
        state["phase"] = "calibration"
        return None
    options = state["options"]
    size = options["findings_per_batch"]
    chunks = [pending[i:i + size] for i in range(0, len(pending), size)][:options["max_agents_per_wave"]]
    slots = []
    for chunk in chunks:
        ids = [f["id"] for f in chunk]
        slots.append(_slot(state, work, "legal-basis-researcher", "legal_basis",
                           f"Правова основа: {ids[0]}…{ids[-1]} ({len(ids)})", {"finding_ids": ids},
                           lambda slot, chunk=chunk: dispatch.legal_prompt(state, Path(work), slot, chunk)))
    _start_wave(state, slots)
    return None


def h_calibration(work, state):
    pending = register.uncalibrated(work)
    if not pending:
        state["phase"] = "coverage_audit"
        return None
    chunks = [pending[i:i + 40] for i in range(0, len(pending), 40)]
    slots = []
    for chunk in chunks:
        ids = [f["id"] for f in chunk]
        slots.append(_slot(state, work, "severity-calibrator", "calibration",
                           f"Калібрування: {ids[0]}…{ids[-1]} ({len(ids)})", {"finding_ids": ids},
                           lambda slot, chunk=chunk: dispatch.calibration_prompt(state, Path(work), slot, chunk)))
    _start_wave(state, slots)
    return None


def _audit_sample(work, state) -> list[dict]:
    rows = []
    for bid in state["block_order"]:
        tiers = {i["id"]: i["tier"] for i in ledger.block_items(work, bid)}
        for row in ledger.load_ledger(work, bid)["items"].values():
            if row["status"] in (CLEAR, NA) or (row["status"] == SCREENED and tiers.get(row["item_id"]) == "core"):
                rows.append(row)
    def partial_units(row):
        return any(c.get("basis") == "units" and c["units_read"] < c["units_total"]
                   for c in (row.get("coverage") or {}).values())

    rows.sort(key=lambda r: (not partial_units(r), hashlib.sha256(f"{r['block_id']}/{r['item_id']}".encode()).hexdigest()))
    return rows[:state["options"]["audit_sample_size"]]


def h_coverage_audit(work, state):
    if state.get("audit_done"):
        state["phase"] = "findings_gate"
        return None
    state["audit_done"] = True
    sample = _audit_sample(work, state)
    if not sample:
        state["phase"] = "findings_gate"
        return None
    slot = _slot(state, work, "coverage-auditor", "audit", f"Аудит покриття: вибірка {len(sample)} позицій",
                 {"sample": [[r["block_id"], r["item_id"]] for r in sample]},
                 lambda slot: dispatch.audit_prompt(state, Path(work), slot, sample))
    _start_wave(state, [slot])
    return None


def h_findings_gate(work, state):
    mode = state["options"]["findings_gate"]
    critical = [f for f in register.findings(work) if register.final_severity(f) == CRITICAL]
    if state.get("findings_gate_done") or mode == "off" or (mode == "auto" and not critical):
        state["phase"] = "drafting"
        return None
    lines = ["## Критичні знахідки до складання звіту", "",
             "Перегляньте їх. Рівень можна змінити командою `ddf finding override <id> --severity ...`; "
             "після цього продовжуйте.", ""]
    for f in critical:
        lines.append(f"- **{f['id']}** · {f['doc_title']} · {f['pinpoint']} · підтвердження: "
                     f"{f['confirmation']} — {f['issue']}")
    state["gate"] = {"id": "g-findings", "kind": "findings", "decisions": ["proceed", "cancel"],
                     "text": "\n".join(lines)}
    return None


# --- handlers: drafting, review, export ---------------------------------------------------------------------


def h_drafting(work, state):
    work = Path(work)
    input_path = work / "report_input.json"
    store.write_json(input_path, render.writer_input(work, state))
    revision = state.get("revision")
    version = state.get("draft_version", 0) + 1
    slot = _slot(state, work, "report-writer", "draft", f"Текст звіту, версія {version}",
                 {"version": version},
                 lambda slot: dispatch.writer_prompt(state, work, slot, input_path, revision),
                 model=state["options"]["writer_model"])
    _start_wave(state, [slot])
    return None


def h_review(work, state):
    work = Path(work)
    version = state.get("draft_version", 0)
    if state.get("review_done_for") == version:
        return _after_review(work, state)
    draft_path = work / "drafts" / f"report-v{version}.md"
    store.write_text(draft_path, render.draft_markdown(work, state, state["outputs"].get("writer")))
    reg_path = register.register_path(work)
    slots = []
    for agent, kind in (("fidelity-reviewer", "fidelity"), ("form-reviewer", "form")):
        slots.append(_slot(state, work, agent, "review", f"Рецензія звіту ({kind})", {"review": kind},
                           lambda slot, kind=kind: dispatch.reviewer_prompt(state, work, slot, draft_path, reg_path, kind)))
    state["reviews"] = {}
    _start_wave(state, slots)
    state["review_done_for"] = version
    return None


def _after_review(work, state):
    issues = [i for r in state.get("reviews", {}).values() for i in r if i.get("must")]
    options = state["options"]
    if issues and state["review_round"] < options["review_iterations"]:
        state["review_round"] += 1
        state["revision"] = issues
        state["phase"] = "drafting"
        log(state, f"рецензія: {len(issues)} обов'язкових зауважень, повторне складання")
        return None
    if issues:
        state["degraded"].append(f"після {state['review_round']} ітерацій лишилося {len(issues)} зауважень рецензентів")
        state["unresolved_review_issues"] = issues
    state["phase"] = "export"
    return None


def h_export(work, state):
    work = Path(work)
    ledger.finalize_pending(work, state)
    try:
        result = render.export(work, state, state["outputs"].get("writer"))
    except Exception as exc:  # last resort: always deliver something
        _finish(state, "failed", f"не вдалося сформувати файли звіту: {exc}")
        return None
    state["outputs"]["files"] = result["files"]
    state["outputs"]["warnings"] = result["warnings"]
    _finish(state, "done", "звіт сформовано")
    return None


HANDLERS = {
    "preflight_scout": h_preflight_scout,
    "preflight_gate": h_preflight_gate,
    "block_review": h_block_review,
    "legal_basis": h_legal_basis,
    "calibration": h_calibration,
    "coverage_audit": h_coverage_audit,
    "findings_gate": h_findings_gate,
    "drafting": h_drafting,
    "review": h_review,
    "export": h_export,
}


# --- report ------------------------------------------------------------------------------------------------


def _parse_output(path: Path):
    if not path.is_file():
        return None, "файл результату не створено"
    raw = path.read_text(encoding="utf-8-sig", errors="replace").strip()
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw)
    try:
        data = json.loads(raw)
    except ValueError as exc:
        return None, f"результат не є чинним JSON: {exc}"
    if not isinstance(data, dict):
        return None, "результат має бути JSON-об'єктом"
    return data, None


def report_slot(work, slot_id: str) -> dict:
    work = Path(work)
    with store.locked(work):
        state = load(work)
        wave = state.get("wave")
        slot = wave["slots"].get(slot_id) if wave else None
        if slot is None:
            return {"error": "unknown_slot", "slot_id": slot_id}
        if slot["status"] != "open":
            return {"slot_id": slot_id, "status": slot["status"], "message": "слот уже опрацьовано"}
        out = work / slot["output_file"]
        data, problem = _parse_output(out)
        if data is not None and data.get("blocker") == "auth_expired":
            state["pause"] = {"reason": "auth_expired", "slot_id": slot_id, "at": store.now_iso()}
            out.unlink(missing_ok=True)
            log(state, f"пауза: протермінований вхід (слот {slot_id})")
            save(work, state)
            return {"slot_id": slot_id, "status": "paused", "message": "протермінований вхід у NotebookLM"}
        if data is not None:
            ok, message = APPLY[slot["kind"]](work, state, slot, data)
        else:
            ok, message = False, problem
        if ok:
            slot["status"] = "done"
            log(state, f"слот {slot_id} ({slot['kind']}): {message}")
        else:
            slot["attempts"] += 1
            if slot["attempts"] >= state["options"]["max_slot_attempts"]:
                slot["status"] = "failed"
                DEGRADE[slot["kind"]](work, state, slot, message)
                log(state, f"слот {slot_id} ({slot['kind']}) не вдався: {message}")
            else:
                prompt_path = work / slot["prompt_file"]
                prompt_path.write_text(prompt_path.read_text(encoding="utf-8") +
                                       f"\n\n## Повторна спроба {slot['attempts'] + 1}\nПопередній результат відхилено: "
                                       f"{message}\nВиправ і запиши результат заново.\n", encoding="utf-8")
                out.unlink(missing_ok=True)
        save(work, state)
        return {"slot_id": slot_id, "status": slot["status"], "message": message}


def resume(work) -> dict:
    work = Path(work)
    with store.locked(work):
        state = load(work)
        if not state.get("pause"):
            return {"ok": True, "message": "завдання не на паузі"}
        state["pause"] = None
        log(state, "поновлення після паузи")
        save(work, state)
    return {"ok": True, "message": "паузу знято; виклич `ddf next`"}


def cancel(work) -> dict:
    work = Path(work)
    with store.locked(work):
        state = load(work)
        if not state.get("terminal"):
            _finish(state, "cancelled", "завдання скасовано")
            save(work, state)
    return {"ok": True}


# --- applying slot outputs ----------------------------------------------------------------------------------


def _apply_scout(work, state, slot, data):
    key = slot["meta"]["notebook_key"]
    nb = state["notebooks"][key]
    sources = data.get("sources")
    if not isinstance(sources, list):
        return False, "поле sources має бути списком"
    clean = [s for s in sources if isinstance(s, dict) and s.get("source_id") and s.get("title") is not None]
    if sources and not clean:
        return False, "жоден запис sources не має source_id і title"
    origin = "legislation" if nb["role"] == "legislation" else "notebook"
    for s in clean:
        kind = classify.classify_origin(origin, str(s["title"]))
        evidence.register_no_text(work, origin=origin, notebook_key=key, source_id=str(s["source_id"]),
                                  title=str(s["title"]), kind=kind, reason="not_fetched", fetch_state="not_fetched")
    state["scout"]["listed"].append(key)
    nb["sources"] = len(clean)
    return True, f"блокнот «{nb['title']}»: джерел {len(clean)}"


def _apply_review(work, state, slot, data):
    block_id = slot["meta"]["block_id"]
    results = data.get("results")
    if not isinstance(results, list):
        return False, "поле results має бути списком"
    outcome = ledger.apply_results(work, state, block_id, results, state["options"]["scan_hit_cap"],
                                   agent=slot["slot_id"])
    if results and outcome["applied"] == 0:
        return False, "жоден результат не прийнято: " + "; ".join(f"{r['item_id']}: {r['why']}" for r in outcome["rejected"][:5])
    _bump_leftovers(work, state, block_id, slot["meta"]["item_ids"])
    for w in outcome["warnings"]:
        log(state, w)
    return True, f"прийнято позицій: {outcome['applied']}, відхилено: {len(outcome['rejected'])}"


def _bump_leftovers(work, state, block_id: str, item_ids: list[str]) -> None:
    pending = set(ledger.pending_item_ids(work, block_id))
    attempts = state["item_attempts"]
    stuck = []
    for iid in item_ids:
        if iid in pending:
            key = f"{block_id}:{iid}"
            attempts[key] = attempts.get(key, 0) + 1
            if attempts[key] >= state["options"]["max_slot_attempts"]:
                stuck.append(iid)
    if stuck:
        _fail_items(work, block_id, stuck, "агент не повернув результат за кілька спроб")


def _fail_items(work, block_id: str, item_ids: list[str], detail: str) -> None:
    with store.locked(work):
        led = ledger.load_ledger(work, block_id)
        for iid in item_ids:
            row = led["items"].get(iid)
            if row and row["status"] == "pending":
                row.update({"status": "unverified", "reason": "agent_failed", "detail": detail,
                            "updated_at": store.now_iso()})
        ledger.save_ledger(work, led)


def _apply_legal(work, state, slot, data):
    items = data.get("items")
    if not isinstance(items, list):
        return False, "поле items має бути списком"
    errors = register.apply_norms(work, items)
    _close_missing_norms(work, slot["meta"]["finding_ids"], "агент не повернув норму")
    return True, f"норм прийнято: {len(items) - len(errors)}, помилок: {len(errors)}"


def _close_missing_norms(work, finding_ids: list[str], note: str) -> None:
    with store.locked(work):
        data = register.load(work)
        for fid in finding_ids:
            f = data["findings"].get(fid)
            if f and f.get("norm") is None:
                f["norm"] = {"source": "none", "verified": None, "note": note}
        register.save(work, data)


def _apply_calibration(work, state, slot, data):
    items = data.get("items")
    if not isinstance(items, list):
        return False, "поле items має бути списком"
    errors = register.apply_calibration(work, items)
    _close_uncalibrated(work, slot["meta"]["finding_ids"])
    return True, f"відкалібровано: {len(items) - len(errors)}, помилок: {len(errors)}"


def _close_uncalibrated(work, finding_ids: list[str]) -> None:
    with store.locked(work):
        data = register.load(work)
        for fid in finding_ids:
            f = data["findings"].get(fid)
            if f and f.get("severity") is None:
                f["severity"] = f.get("severity_proposal") or "significant"
                f["recommendation"] = {"type": "further_review", "text": ""}
                f["rationale"] = None
                f["calibrated"] = False
        register.save(work, data)


def _apply_audit(work, state, slot, data):
    verdicts = data.get("verdicts")
    if not isinstance(verdicts, list):
        return False, "поле verdicts має бути списком"
    disputed: dict = {}
    for v in verdicts:
        if v.get("ok") is False and v.get("block_id") in state["blocks"]:
            disputed.setdefault(v["block_id"], []).append(v.get("item_id"))
    reopened = 0
    for bid, ids in disputed.items():
        reopened += ledger.reopen(work, bid, [i for i in ids if i], "аудитор не підтвердив достатність підстави")
    if reopened:
        state["audit_reopened"] = True
        state["phase"] = "block_review"
    return True, f"оскаржено позицій: {reopened}"


def _apply_draft(work, state, slot, data):
    problems = render.lint_writer(work, state, data)
    if problems:
        return False, "; ".join(problems[:6])
    state["outputs"]["writer"] = data
    state["draft_version"] = slot["meta"]["version"]
    state["revision"] = None
    state["phase"] = "review"
    return True, "текст звіту прийнято"


def _apply_review_slot(work, state, slot, data):
    issues = data.get("issues")
    if not isinstance(issues, list):
        return False, "поле issues має бути списком"
    clean = [{"where": str(i.get("where", "")), "problem": str(i.get("problem", "")), "fix": str(i.get("fix", "")),
              "must": bool(i.get("must"))} for i in issues if isinstance(i, dict) and i.get("problem")]
    state.setdefault("reviews", {})[slot["meta"]["review"]] = clean
    return True, f"зауважень: {len(clean)}"


APPLY = {"scout_list": _apply_scout, "review_batch": _apply_review, "legal_basis": _apply_legal,
         "calibration": _apply_calibration, "audit": _apply_audit, "draft": _apply_draft, "review": _apply_review_slot}


# --- degradation of a failed slot -------------------------------------------------------------------------


def _degrade_scout(work, state, slot, message):
    key = slot["meta"]["notebook_key"]
    state["scout"]["listed"].append(key)
    state["degraded"].append(f"перелік джерел блокнота «{state['notebooks'][key]['title']}» не отримано: {message}")


def _degrade_review(work, state, slot, message):
    _fail_items(work, slot["meta"]["block_id"], slot["meta"]["item_ids"], message)


def _degrade_legal(work, state, slot, message):
    _close_missing_norms(work, slot["meta"]["finding_ids"], "правову основу не встановлено: " + message)
    state["degraded"].append("правову основу частини знахідок не встановлено")


def _degrade_calibration(work, state, slot, message):
    _close_uncalibrated(work, slot["meta"]["finding_ids"])
    state["degraded"].append("частину знахідок не відкалібровано: рівень — пропозиція перевірника, рекомендація — додаткова перевірка")


def _degrade_audit(work, state, slot, message):
    state["degraded"].append("аудит покриття не виконано")


def _degrade_draft(work, state, slot, message):
    state["draft_version"] = slot["meta"]["version"]
    state["phase"] = "export"
    if state["outputs"].get("writer"):
        state["degraded"].append("повторне складання тексту не вдалося: у звіті попередня версія тексту")
    else:
        state["outputs"]["writer"] = None
        state["degraded"].append("текст резюме складено програмою: агент-автор не повернув чинного результату")


def _degrade_review_slot(work, state, slot, message):
    state.setdefault("reviews", {})[slot["meta"]["review"]] = []
    state["degraded"].append(f"рецензію ({slot['meta']['review']}) не виконано")


DEGRADE = {"scout_list": _degrade_scout, "review_batch": _degrade_review, "legal_basis": _degrade_legal,
           "calibration": _degrade_calibration, "audit": _degrade_audit, "draft": _degrade_draft,
           "review": _degrade_review_slot}


# --- status ---------------------------------------------------------------------------------------------------


def status(work) -> dict:
    work = Path(work)
    state = load(work)
    overall = ledger.overall(work, state)
    findings = register.findings(work)
    by_sev: dict = {}
    for f in findings:
        by_sev[register.final_severity(f)] = by_sev.get(register.final_severity(f), 0) + 1
    wave = state.get("wave")
    return {"task_id": state["task_id"], "object": state["object"], "phase": state["phase"],
            "terminal": state.get("terminal"), "pause": state.get("pause"),
            "open_gate": state["gate"]["id"] if state.get("gate") else None,
            "wave": {"id": wave["id"], "slots": {k: v["status"] for k, v in wave["slots"].items()}} if wave else None,
            "ledger": overall, "findings": {"total": len(findings), "by_severity": by_sev},
            "degraded": state.get("degraded", []), "estimate": preflight.estimate(work, state)}
