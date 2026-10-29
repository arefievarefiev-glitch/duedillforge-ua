"""File locking, atomic JSON writes and small helpers shared by every module.

Derived in part from memoforge `state_io.py` (MIT, Grigorii Moskalev): the advisory lock and the
tmp + `os.replace` write. The design here is simpler: one lock file per task (`state.lock`).
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path

LOCK_TIMEOUT = 30.0
LOCK_POLL = 0.05
REPLACE_RETRIES = 5
REPLACE_BACKOFF = 0.05

_local = threading.local()


class LockTimeout(TimeoutError):
    code = "lock_timeout"


if os.name == "nt":  # pragma: no cover - platform branch
    import msvcrt

    def _lock_fd(fd: int) -> None:
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)

    def _unlock_fd(fd: int) -> None:
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)

else:  # pragma: no cover - platform branch
    import fcntl

    def _lock_fd(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _unlock_fd(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_UN)


@contextmanager
def locked(work_dir):
    """Re-entrant (per thread) advisory lock on `<work_dir>/state.lock`."""
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    path = str((work_dir / "state.lock").absolute())
    held = getattr(_local, "held", None)
    if held is None:
        held = _local.held = {}
    if path in held:
        held[path]["depth"] += 1
        try:
            yield
        finally:
            held[path]["depth"] -= 1
        return
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    deadline = time.monotonic() + LOCK_TIMEOUT
    while True:
        try:
            _lock_fd(fd)
            break
        except OSError:
            if time.monotonic() >= deadline:
                os.close(fd)
                raise LockTimeout(f"lock_timeout: {path} is held by another process")
            time.sleep(LOCK_POLL)
    held[path] = {"depth": 1}
    try:
        yield
    finally:
        del held[path]
        try:
            _unlock_fd(fd)
        finally:
            os.close(fd)


def read_json(path, default=None):
    path = Path(path)
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8-sig"))


def dumps(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2) + "\n"


def write_bytes_atomic(path, payload: bytes) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.stem}-", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        last = None
        for attempt in range(REPLACE_RETRIES):
            try:
                os.replace(tmp_name, str(path))
                last = None
                break
            except PermissionError as exc:  # Windows sharing violation
                last = exc
                time.sleep(REPLACE_BACKOFF * (2 ** attempt))
        if last is not None:
            raise last
    finally:
        if os.path.exists(tmp_name):
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
    return path


def write_json(path, obj) -> Path:
    return write_bytes_atomic(path, dumps(obj).encode("utf-8"))


def write_text(path, text: str) -> Path:
    return write_bytes_atomic(path, text.encode("utf-8"))


def append_jsonl(path, obj) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(obj, ensure_ascii=False) + "\n")
        handle.flush()


def read_jsonl(path) -> list:
    path = Path(path)
    if not path.exists():
        return []
    rows = []
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    continue
    return rows


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_file(path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
