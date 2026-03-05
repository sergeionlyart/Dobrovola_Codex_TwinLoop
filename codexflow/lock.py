from __future__ import annotations

import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


class LockError(RuntimeError):
    pass


DEFAULT_LOCK_TTL_SEC = 8 * 60 * 60


def _lock_ttl_sec() -> int:
    raw = os.getenv("CODEXFLOW_LOCK_TTL_SEC", str(DEFAULT_LOCK_TTL_SEC)).strip()
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_LOCK_TTL_SEC
    return max(1, value)


def _read_lock_metadata(lock_path: Path) -> dict[str, str]:
    metadata: dict[str, str] = {}
    try:
        text = lock_path.read_text(encoding="utf-8")
    except OSError:
        return metadata
    for line in text.splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        metadata[key.strip()] = value.strip()
    return metadata


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _lock_status(lock_path: Path) -> tuple[bool, int | None, int | None]:
    metadata = _read_lock_metadata(lock_path)
    pid_value = metadata.get("pid")
    created_value = metadata.get("created_at_epoch")
    pid: int | None = None
    age_sec: int | None = None

    if pid_value:
        try:
            pid = int(pid_value)
        except ValueError:
            pid = None

    if created_value:
        try:
            created_at = int(created_value)
            age_sec = max(0, int(time.time()) - created_at)
        except ValueError:
            age_sec = None

    ttl_sec = _lock_ttl_sec()
    if pid is not None and _pid_alive(pid):
        return False, pid, age_sec
    if pid is not None and not _pid_alive(pid):
        return True, pid, age_sec
    if age_sec is not None and age_sec >= ttl_sec:
        return True, None, age_sec
    return False, pid, age_sec


@contextmanager
def file_lock(lock_path: Path) -> Iterator[None]:
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    acquired = False

    for attempt in range(2):
        fd: int | None = None
        try:
            fd = os.open(lock_path, flags)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(f"pid={os.getpid()}\n")
                handle.write(f"created_at_epoch={int(time.time())}\n")
            fd = None
            acquired = True
            break
        except FileExistsError:
            stale, pid, age_sec = _lock_status(lock_path)
            if not stale or attempt == 1:
                details = []
                if pid is not None:
                    details.append(f"pid={pid}")
                if age_sec is not None:
                    details.append(f"age_sec={age_sec}")
                suffix = f" ({', '.join(details)})" if details else ""
                raise LockError(f"Lock already exists: {lock_path}{suffix}") from None
            try:
                lock_path.unlink()
            except OSError as exc:
                raise LockError(f"Cannot clear stale lock {lock_path}: {exc}") from exc
        finally:
            if fd is not None:
                os.close(fd)

    if not acquired:
        raise LockError(f"Lock already exists: {lock_path}")

    try:
        yield
    finally:
        lock_path.unlink(missing_ok=True)
