"""Plugin locations and option resolution (flag > environment > options.json > default)."""

from __future__ import annotations

import os
from pathlib import Path

from . import store

PLUGIN_NAME = "duedillforge"

DEFAULTS = {
    "max_parallel_queries": 2,
    "items_per_batch": 8,
    "findings_gate": "auto",
    "review_iterations": 2,
    "writer_model": "opus",
    "queries_per_item_estimate": 2,
    "seconds_per_query": 100,
    "scan_hit_cap": 50,
    "audit_sample_size": 12,
    "max_slot_attempts": 3,
    "cut_max_chars": 12000,
    "max_agents_per_wave": 4,
    "upgrade_max_docs": 25,
    "small_scope_docs": 5,
    "findings_per_batch": 10,
}

INT_KEYS = (
    "max_parallel_queries", "items_per_batch", "review_iterations", "queries_per_item_estimate",
    "seconds_per_query", "scan_hit_cap", "audit_sample_size", "max_slot_attempts", "cut_max_chars",
    "max_agents_per_wave", "upgrade_max_docs", "small_scope_docs", "findings_per_batch",
)
WRITER_MODELS = ("opus", "fable", "sonnet")
GATE_MODES = ("auto", "on", "off")


def plugin_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _usable(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".ddf-write-probe"
        probe.write_text("ok")
        probe.unlink()
        return True
    except OSError:
        return False


def plugin_data_dir() -> Path:
    candidates = []
    env = os.environ.get("CLAUDE_PLUGIN_DATA")
    if env:
        candidates.append(Path(env))
    local = os.environ.get("LOCALAPPDATA")
    if local:
        candidates.append(Path(local) / "claude" / "plugin-data" / PLUGIN_NAME)
    else:
        candidates.append(Path.home() / ".claude" / "plugin-data" / PLUGIN_NAME)
    candidates.append(plugin_root() / ".data")
    for candidate in candidates:
        if _usable(candidate):
            return candidate
    return candidates[-1]


def _options_file() -> dict:
    try:
        return store.read_json(plugin_data_dir() / "options.json", {}) or {}
    except (OSError, ValueError):
        return {}


def resolve_options(overrides: dict | None = None) -> dict:
    """Return the effective options and where each value came from."""
    overrides = overrides or {}
    file_opts = _options_file()
    result, source = {}, {}
    for key, default in DEFAULTS.items():
        value, origin = default, "default"
        env_value = os.environ.get(f"CLAUDE_PLUGIN_OPTION_{key.upper()}")
        if key in file_opts:
            value, origin = file_opts[key], "options.json"
        if env_value not in (None, ""):
            value, origin = env_value, "env"
        if key in overrides and overrides[key] not in (None, ""):
            value, origin = overrides[key], "flag"
        if key in INT_KEYS:
            try:
                value = int(value)
            except (TypeError, ValueError):
                value, origin = default, "default"
            if value < 1:
                value = default
        result[key], source[key] = value, origin
    for key in ("findings_gate", "writer_model"):
        env_value = os.environ.get(f"CLAUDE_PLUGIN_OPTION_{key.upper()}")
        if key in overrides and overrides[key]:
            result[key], source[key] = overrides[key], "flag"
        elif env_value:
            result[key], source[key] = env_value, "env"
        elif key in file_opts:
            result[key], source[key] = file_opts[key], "options.json"
    if result["findings_gate"] not in GATE_MODES:
        result["findings_gate"] = DEFAULTS["findings_gate"]
    if result["writer_model"] not in WRITER_MODELS:
        result["writer_model"] = DEFAULTS["writer_model"]
    for key in ("output_folder", "publish_folder"):
        env_value = os.environ.get(f"CLAUDE_PLUGIN_OPTION_{key.upper()}")
        value = overrides.get(key) or env_value or file_opts.get(key)
        result[key] = value or None
    return {"options": result, "source": source}


def resolve_output_folder(explicit: str | None) -> Path:
    chain = [explicit, os.environ.get("CLAUDE_PLUGIN_OPTION_OUTPUT_FOLDER"), _options_file().get("output_folder")]
    for candidate in chain:
        if candidate and _usable(Path(candidate).expanduser()):
            return Path(candidate).expanduser()
    for fallback in (Path.home() / "Documents" / "duedillforge", Path.cwd() / "outputs" / "duedillforge-work"):
        if _usable(fallback):
            return fallback
    return Path.cwd()


def resolve_publish_folder(explicit: str | None, slug: str) -> Path | None:
    chain = [explicit, os.environ.get("CLAUDE_PLUGIN_OPTION_PUBLISH_FOLDER"), _options_file().get("publish_folder")]
    for candidate in chain:
        if candidate:
            target = Path(candidate).expanduser() / slug
            if _usable(target):
                return target
    cowork = Path("/mnt/user-data/outputs")
    if cowork.is_dir():
        target = cowork / "duedillforge" / slug
        if _usable(target):
            return target
    return None
