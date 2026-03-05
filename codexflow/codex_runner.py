from __future__ import annotations

import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path


class CodexExecError(RuntimeError):
    pass


def _is_debug_enabled() -> bool:
    value = os.getenv("CODEXFLOW_DEBUG", "").strip().lower()
    return value in {"1", "true", "yes", "on"}


def _debug_log(message: str) -> None:
    if _is_debug_enabled():
        print(f"[codexflow-debug] {message}")


def _to_text(data: str | bytes | None) -> str:
    if data is None:
        return ""
    if isinstance(data, bytes):
        return data.decode("utf-8", errors="replace")
    return data


def _read_int_env(name: str, default: int, *, minimum: int, maximum: int) -> int:
    raw = os.getenv(name, str(default)).strip()
    try:
        value = int(raw)
    except ValueError:
        return default
    return max(minimum, min(maximum, value))


def _read_float_env(name: str, default: float, *, minimum: float, maximum: float) -> float:
    raw = os.getenv(name, str(default)).strip()
    try:
        value = float(raw)
    except ValueError:
        return default
    return max(minimum, min(maximum, value))


def _render_attempt_log(lines: list[tuple[int, str]]) -> str:
    if not lines:
        return ""
    if len(lines) == 1:
        return lines[0][1]
    rendered: list[str] = []
    for attempt, text in lines:
        rendered.append(f"===== attempt {attempt} =====")
        rendered.append(text)
    return "\n".join(rendered).rstrip() + "\n"


@dataclass
class CodexExecResult:
    command: list[str]
    stdout_log: Path
    stderr_log: Path
    output_file: Path


def run_codex_exec(
    *,
    prompt_text: str,
    sandbox: str,
    schema_path: Path,
    output_file: Path,
    stdout_log: Path,
    stderr_log: Path,
    cwd: Path,
    timeout_sec: int,
) -> CodexExecResult:
    output_file.parent.mkdir(parents=True, exist_ok=True)
    stdout_log.parent.mkdir(parents=True, exist_ok=True)
    stderr_log.parent.mkdir(parents=True, exist_ok=True)

    command = [
        "codex",
        "exec",
        "--config",
        'approval_policy="never"',
        "--sandbox",
        sandbox,
        "--json",
        "--output-schema",
        str(schema_path),
        "--output-last-message",
        str(output_file),
        "-",
    ]

    _debug_log(f"cwd={cwd}")
    _debug_log(f"timeout_sec={timeout_sec}")
    _debug_log(f"schema_path={schema_path}")
    _debug_log(f"output_file={output_file}")
    _debug_log(f"stdout_log={stdout_log}")
    _debug_log(f"stderr_log={stderr_log}")
    _debug_log(f"command_argv={command}")

    max_attempts = _read_int_env(
        "CODEXFLOW_CODEX_EXEC_MAX_ATTEMPTS",
        2,
        minimum=1,
        maximum=5,
    )
    retry_delay_sec = _read_float_env(
        "CODEXFLOW_CODEX_EXEC_RETRY_DELAY_SEC",
        1.0,
        minimum=0.0,
        maximum=30.0,
    )

    stdout_attempts: list[tuple[int, str]] = []
    stderr_attempts: list[tuple[int, str]] = []
    completed: subprocess.CompletedProcess[str] | None = None
    last_timeout: subprocess.TimeoutExpired | None = None
    last_return_code: int | None = None

    for attempt in range(1, max_attempts + 1):
        output_file.unlink(missing_ok=True)
        try:
            completed = subprocess.run(
                command,
                cwd=cwd,
                input=prompt_text,
                text=True,
                capture_output=True,
                check=False,
                timeout=timeout_sec,
            )
        except FileNotFoundError as exc:
            raise CodexExecError("codex CLI not found in PATH") from exc
        except subprocess.TimeoutExpired as exc:
            last_timeout = exc
            stdout_attempts.append((attempt, _to_text(exc.stdout)))
            stderr_attempts.append((attempt, _to_text(exc.stderr)))
            if attempt < max_attempts:
                _debug_log(
                    f"attempt={attempt} timed out after {timeout_sec}s; retrying in "
                    f"{retry_delay_sec * attempt:.1f}s"
                )
                if retry_delay_sec > 0:
                    time.sleep(retry_delay_sec * attempt)
                continue
            break

        stdout_attempts.append((attempt, completed.stdout or ""))
        stderr_attempts.append((attempt, completed.stderr or ""))
        _debug_log(f"attempt={attempt} return_code={completed.returncode}")

        if completed.returncode == 0 and output_file.exists():
            break

        last_return_code = completed.returncode
        if attempt < max_attempts:
            if retry_delay_sec > 0:
                time.sleep(retry_delay_sec * attempt)
            continue

    stdout_log.write_text(_render_attempt_log(stdout_attempts), encoding="utf-8")
    stderr_log.write_text(_render_attempt_log(stderr_attempts), encoding="utf-8")

    if completed is None and last_timeout is not None:
        raise CodexExecError(
            f"codex exec timed out after {timeout_sec}s (attempts={max_attempts}); "
            f"stderr log: {stderr_log}"
        ) from last_timeout

    if completed is None:
        raise CodexExecError("codex exec failed before completion")

    if completed.returncode != 0:
        raise CodexExecError(
            "codex exec failed with return code "
            f"{last_return_code if last_return_code is not None else completed.returncode} "
            f"(attempts={max_attempts}); stderr log: {stderr_log}"
        )

    if not output_file.exists():
        raise CodexExecError(
            f"codex exec did not create output file after {max_attempts} attempt(s): {output_file}"
        )

    return CodexExecResult(
        command=command,
        stdout_log=stdout_log,
        stderr_log=stderr_log,
        output_file=output_file,
    )
