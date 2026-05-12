"""OpenClaw command execution helpers.

This module intentionally keeps OpenClaw as the runtime boundary. The local
assistant code calls OpenClaw through its CLI, logs the call, and captures
outputs into local run artifacts when requested.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable

from .config import get_bool, get_int, get_list, get_path, get_str
from .paths import OPENCLAW_RUNS_DIR, ensure_runtime_dirs

LogFn = Callable[..., None]


def _resolve_openclaw_invocation() -> list[str]:
    """Resolve the OpenClaw invocation.

    On Windows, prefer Node + openclaw.mjs directly. The npm .cmd shim forwards
    args through `%*`, which is fragile for multi-line recipe prompts.
    """

    if os.name == "nt" and get_bool("openclaw.windows_direct_node_entrypoint"):
        entrypoint = get_path("paths.openclaw_node_entrypoint")
        if entrypoint.is_file():
            for candidate in get_list("openclaw.node_executable_candidates"):
                resolved_node = shutil.which(str(candidate))
                if resolved_node:
                    return [resolved_node, str(entrypoint)]

    for candidate in get_list("openclaw.executable_candidates"):
        resolved = shutil.which(str(candidate))
        if resolved:
            return [resolved]
    return [get_str("openclaw.display_name")]


def _read_windows_registry_env(name: str) -> str:
    if os.name != "nt":
        return ""
    try:
        import winreg
    except ImportError:
        return ""

    locations = [
        (winreg.HKEY_CURRENT_USER, get_str("openclaw.windows_user_env_registry_key")),
        (winreg.HKEY_LOCAL_MACHINE, get_str("openclaw.windows_machine_env_registry_key")),
    ]
    for root, key_path in locations:
        try:
            with winreg.OpenKey(root, key_path) as key:
                value, _ = winreg.QueryValueEx(key, name)
        except OSError:
            continue
        if isinstance(value, str) and value.strip():
            return value
    return ""


def _openclaw_subprocess_env() -> dict[str, str]:
    env = os.environ.copy()
    for raw_name in get_list("openclaw.forward_env_vars"):
        name = str(raw_name).strip()
        if not name:
            continue
        if env.get(name):
            continue
        registry_value = _read_windows_registry_env(name)
        if registry_value:
            env[name] = registry_value
    return env


@dataclass(frozen=True)
class CommandResult:
    args: list[str]
    returncode: int
    stdout: str
    stderr: str
    elapsed_seconds: float
    timed_out: bool = False


def sanitize_args(args: Iterable[str]) -> list[str]:
    """Redact secret-like CLI values before logging command arguments."""

    sanitized: list[str] = []
    redact_next = False
    secret_flags = {str(flag) for flag in get_list("openclaw.secret_flags")}
    secret_value_markers = {str(marker) for marker in get_list("openclaw.secret_value_markers")}
    for raw_arg in args:
        arg = str(raw_arg)
        if redact_next:
            sanitized.append("<redacted>")
            redact_next = False
            continue

        if arg in secret_flags or arg in secret_value_markers:
            sanitized.append(arg)
            redact_next = True
            continue

        if any(arg.startswith(f"{flag}=") for flag in secret_flags):
            key = arg.split("=", 1)[0]
            sanitized.append(f"{key}=<redacted>")
            continue

        sanitized.append(arg)

    return sanitized


class OpenClawRunner:
    """Thin, logged wrapper around the OpenClaw CLI."""

    def __init__(self, log: LogFn, *, cwd: Path | None = None) -> None:
        self._log = log
        self._cwd = cwd if cwd is not None else Path(get_str("openclaw.cwd"))
        ensure_runtime_dirs()

    def run(self, args: list[str], *, timeout_seconds: int | None = None) -> CommandResult:
        timeout = timeout_seconds if timeout_seconds is not None else get_int("openclaw.default_agent_timeout_seconds")
        invocation = _resolve_openclaw_invocation()
        full_args = [*invocation, *args]
        safe_args = sanitize_args([get_str("openclaw.display_name"), *args])
        started = time.monotonic()
        self._log(
            "openclaw command started",
            command=" ".join(safe_args),
            cwd=str(self._cwd),
        )

        try:
            creationflags = 0
            if os.name == "nt" and get_bool("openclaw.windows_process_group"):
                creationflags = subprocess.CREATE_NEW_PROCESS_GROUP

            process = subprocess.Popen(
                full_args,
                cwd=str(self._cwd),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=_openclaw_subprocess_env(),
                text=True,
                encoding=get_str("openclaw.output_encoding"),
                errors=get_str("openclaw.output_encoding_errors"),
                creationflags=creationflags,
            )
            try:
                stdout, stderr = process.communicate(timeout=timeout)
                returncode = process.returncode
                timed_out = False
            except subprocess.TimeoutExpired:
                timed_out = True
                if os.name == "nt":
                    kill_command = [
                        str(part).format(pid=str(process.pid))
                        for part in get_list("openclaw.windows_tree_kill_command")
                    ]
                    subprocess.run(
                        kill_command,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        check=False,
                    )
                else:
                    process.kill()
                try:
                    process.kill()
                except OSError:
                    pass
                try:
                    stdout, stderr = process.communicate(timeout=get_int("openclaw.post_kill_wait_seconds"))
                except subprocess.TimeoutExpired:
                    stdout = ""
                    stderr = "command timed out and did not exit cleanly after termination"
                returncode = get_int("openclaw.timeout_returncode")

            elapsed = time.monotonic() - started
            result = CommandResult(
                args=safe_args,
                returncode=returncode,
                stdout=stdout,
                stderr=stderr,
                elapsed_seconds=elapsed,
                timed_out=timed_out,
            )
        except FileNotFoundError:
            elapsed = time.monotonic() - started
            result = CommandResult(
                args=safe_args,
                returncode=get_int("openclaw.not_found_returncode"),
                stdout="",
                stderr=f"{get_str('openclaw.display_name')} was not found on PATH",
                elapsed_seconds=elapsed,
            )

        level = "OK" if result.returncode == 0 else "ERROR"
        self._log(
            "openclaw command finished",
            level=level,
            command=" ".join(result.args),
            returncode=result.returncode,
            elapsed=f"{result.elapsed_seconds:.2f}s",
            stdout_chars=len(result.stdout),
            stderr_chars=len(result.stderr),
            timed_out=result.timed_out,
        )
        return result

    def agent(
        self,
        message: str,
        *,
        model: str | None = None,
        local: bool = False,
        agent: str | None = None,
        session_id: str | None = None,
        to: str | None = None,
        deliver: bool = False,
        thinking: str | None = None,
        timeout_seconds: int | None = None,
    ) -> CommandResult:
        args = ["agent", "--message", message, "--json"]
        if model:
            args.extend(["--model", model])
        if local:
            args.append("--local")
        if agent:
            args.extend(["--agent", agent])
        if session_id:
            args.extend(["--session-id", session_id])
        if to:
            args.extend(["--to", to])
        if deliver:
            args.append("--deliver")
        if thinking:
            args.extend(["--thinking", thinking])
        return self.run(args, timeout_seconds=timeout_seconds)

    def save_artifact(
        self,
        *,
        label: str,
        prompt: str | None,
        result: CommandResult,
        extra: dict[str, object] | None = None,
    ) -> Path:
        stamp = datetime.now().strftime(get_str("artifacts.timestamp_format"))
        safe_label = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in label)
        artifact_id = f"{stamp}_{safe_label}_{uuid.uuid4().hex[: get_int('artifacts.id_random_chars')]}"
        path = OPENCLAW_RUNS_DIR / f"{artifact_id}{get_str('artifacts.extension')}"
        payload: dict[str, object] = {
            "id": artifact_id,
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "label": label,
            "prompt": prompt,
            "result": asdict(result),
        }
        if extra:
            payload["extra"] = extra
        path.write_text(json.dumps(payload, indent=get_int("artifacts.json_indent")), encoding="utf-8")
        self._log("openclaw run artifact saved", level="OK", label=label, path=str(path))
        return path
