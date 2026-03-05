from __future__ import annotations

import json
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


def _read_text_file(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


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


def _extract_last_agent_message_from_jsonl(stdout_text: str) -> str | None:
    """Extract the last agent_message text from codex JSONL stdout."""
    last_message: str | None = None
    for raw_line in stdout_text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        item = payload.get("item")
        if not isinstance(item, dict):
            continue
        if item.get("type") != "agent_message":
            continue
        text = item.get("text")
        if isinstance(text, str) and text.strip():
            last_message = text.strip()
    return last_message


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
        attempt_stdout_path = stdout_log.parent / f".{stdout_log.name}.attempt{attempt}.tmp"
        attempt_stderr_path = stderr_log.parent / f".{stderr_log.name}.attempt{attempt}.tmp"
        attempt_stdout_path.unlink(missing_ok=True)
        attempt_stderr_path.unlink(missing_ok=True)
        attempt_stdout = ""
        attempt_stderr = ""
        try:
            with attempt_stdout_path.open(
                "w", encoding="utf-8"
            ) as stdout_handle, attempt_stderr_path.open(
                "w", encoding="utf-8"
            ) as stderr_handle:
                completed = subprocess.run(
                    command,
                    cwd=cwd,
                    input=prompt_text,
                    text=True,
                    stdout=stdout_handle,
                    stderr=stderr_handle,
                    check=False,
                    timeout=timeout_sec,
                )
            attempt_stdout = _read_text_file(attempt_stdout_path)
            attempt_stderr = _read_text_file(attempt_stderr_path)
        except FileNotFoundError as exc:
            raise CodexExecError("codex CLI not found in PATH") from exc
        except subprocess.TimeoutExpired as exc:
            last_timeout = exc
            attempt_stdout = _read_text_file(attempt_stdout_path) or _to_text(exc.stdout)
            attempt_stderr = _read_text_file(attempt_stderr_path) or _to_text(exc.stderr)
            stdout_attempts.append((attempt, attempt_stdout))
            stderr_attempts.append((attempt, attempt_stderr))
            attempt_stdout_path.unlink(missing_ok=True)
            attempt_stderr_path.unlink(missing_ok=True)
            if attempt < max_attempts:
                _debug_log(
                    f"attempt={attempt} timed out after {timeout_sec}s; retrying in "
                    f"{retry_delay_sec * attempt:.1f}s"
                )
                if retry_delay_sec > 0:
                    time.sleep(retry_delay_sec * attempt)
                continue
            break

        attempt_stdout_path.unlink(missing_ok=True)
        attempt_stderr_path.unlink(missing_ok=True)

        stdout_attempts.append((attempt, attempt_stdout))
        stderr_attempts.append((attempt, attempt_stderr))
        _debug_log(f"attempt={attempt} return_code={completed.returncode}")

        # Fallback: some codex CLI/sandbox combinations skip output-last-message
        # file creation even when stdout JSONL contains a valid agent_message.
        if completed.returncode == 0 and not output_file.exists():
            fallback_message = _extract_last_agent_message_from_jsonl(
                attempt_stdout
            )
            if fallback_message:
                output_file.write_text(fallback_message + "\n", encoding="utf-8")
                _debug_log(
                    f"attempt={attempt} output file synthesized from stdout JSONL"
                )

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
