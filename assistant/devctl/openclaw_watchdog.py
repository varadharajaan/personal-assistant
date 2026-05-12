"""Config-driven OpenClaw gateway watchdog.

The watchdog is deliberately narrow: it checks OpenClaw's configured loopback
gateway, restarts only configured OpenClaw gateway/node roles, and refuses to
touch protected services such as ClipSync.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime
from glob import glob
from pathlib import Path
from typing import Any, Callable

from .config import get_bool, get_int, get_list, get_path, get_str
from .log_inspector import redact_text
from .openclaw_runner import CommandResult, OpenClawRunner

LogFn = Callable[..., None]


@dataclass(frozen=True)
class ProcessRole:
    name: str
    required_markers: list[str]
    start_command: list[str]
    start_timeout_seconds: int
    direct_start_enabled: bool
    direct_start_command: list[str]
    direct_start_wait_seconds: int


@dataclass(frozen=True)
class ProcessInfo:
    pid: int
    name: str
    executable_path: str
    command_line: str
    role: str
    protected: bool


@dataclass(frozen=True)
class ProbeInfo:
    attempted: bool
    ok: bool
    returncode: int | None
    timed_out: bool
    stdout_preview: str
    stderr_preview: str


@dataclass(frozen=True)
class WatchdogStatus:
    enabled: bool
    healthy: bool
    host: str
    port: int
    port_listening: bool
    probe: ProbeInfo
    gateway_processes: list[ProcessInfo]
    node_processes: list[ProcessInfo]
    reasons: list[str]
    message: str


@dataclass(frozen=True)
class KillResult:
    pid: int
    role: str
    skipped: bool
    reason: str
    returncode: int | None


@dataclass(frozen=True)
class StartResult:
    role: str
    command: list[str]
    returncode: int
    timed_out: bool
    pid: int | None = None


@dataclass(frozen=True)
class WatchdogRunResult:
    action: str
    before: WatchdogStatus
    killed: list[KillResult]
    started: list[StartResult]
    after: WatchdogStatus
    returncode: int


def _normalize_text(value: str) -> str:
    normalized = value.replace("\\", "/").lower()
    normalized = re.sub(r"\s+", " ", normalized)
    return f" {normalized.strip()} "


def _resolve_candidate(key_path: str) -> str:
    for candidate in get_list(key_path):
        resolved = shutil.which(str(candidate))
        if resolved:
            return resolved
    return ""


def _configured_roles() -> list[ProcessRole]:
    roles: list[ProcessRole] = []
    for item in get_list("openclaw_watchdog.process_roles"):
        if not isinstance(item, dict):
            continue
        roles.append(
            ProcessRole(
                name=str(item.get("name", "")),
                required_markers=[str(marker) for marker in item.get("required_markers", [])],
                start_command=[str(part) for part in item.get("start_command", [])],
                start_timeout_seconds=int(item.get("start_timeout_seconds", get_int("openclaw.default_control_timeout_seconds"))),
                direct_start_enabled=bool(item.get("direct_start_enabled", False)),
                direct_start_command=[str(part) for part in item.get("direct_start_command", [])],
                direct_start_wait_seconds=int(item.get("direct_start_wait_seconds", 0)),
            )
        )
    return [role for role in roles if role.name]


def _role_by_name(name: str) -> ProcessRole | None:
    for role in _configured_roles():
        if role.name == name:
            return role
    return None


def process_matches_role(command_line: str, role: ProcessRole) -> bool:
    normalized = _normalize_text(command_line)
    return all(_normalize_text(marker).strip() in normalized for marker in role.required_markers)


def _is_protected_process(command_line: str) -> bool:
    normalized = _normalize_text(command_line)
    return any(_normalize_text(str(marker)).strip() in normalized for marker in get_list("openclaw_watchdog.protected_process_markers"))


def _creationflags() -> int:
    flags = 0
    if os.name == "nt" and get_bool("openclaw_watchdog.windows_create_no_window"):
        flags |= getattr(subprocess, "CREATE_NO_WINDOW", 0)
    if os.name == "nt":
        flags |= getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    return flags


def _extract_json_payload(raw: str) -> Any:
    text = raw.strip()
    if not text:
        return []
    for start_char in ("[", "{"):
        index = text.find(start_char)
        if index >= 0:
            return json.loads(text[index:])
    return []


def _query_windows_processes(log: LogFn | None = None) -> list[dict[str, Any]]:
    executable = _resolve_candidate("openclaw_watchdog.powershell_executable_candidates")
    if not executable:
        if log:
            log("watchdog process query skipped", level="ERROR", reason="powershell-not-found")
        return []

    command = [
        executable,
        *[str(item) for item in get_list("openclaw_watchdog.powershell_arguments")],
        get_str("openclaw_watchdog.windows_process_query_script"),
    ]
    try:
        result = subprocess.run(
            command,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            encoding=get_str("openclaw_watchdog.process_output_encoding"),
            errors=get_str("openclaw_watchdog.process_output_encoding_errors"),
            timeout=get_int("openclaw_watchdog.windows_process_query_timeout_seconds"),
            check=False,
            creationflags=_creationflags(),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        if log:
            log("watchdog process query failed", level="ERROR", error=str(exc)[:300])
        return []

    if result.returncode != 0:
        if log:
            log("watchdog process query returned error", level="ERROR", returncode=result.returncode, stderr=redact_text(result.stderr)[:300])
        return []

    try:
        payload = _extract_json_payload(result.stdout)
    except json.JSONDecodeError as exc:
        if log:
            log("watchdog process query json parse failed", level="ERROR", error=str(exc)[:300])
        return []

    if isinstance(payload, dict):
        return [payload]
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    return []


def matching_processes(log: LogFn | None = None) -> list[ProcessInfo]:
    roles = _configured_roles()
    matches: list[ProcessInfo] = []
    if os.name != "nt":
        return matches

    for raw in _query_windows_processes(log=log):
        command_line = str(raw.get("CommandLine") or "")
        if not command_line:
            continue
        for role in roles:
            if process_matches_role(command_line, role):
                matches.append(
                    ProcessInfo(
                        pid=int(raw.get("ProcessId") or 0),
                        name=str(raw.get("Name") or ""),
                        executable_path=redact_text(str(raw.get("ExecutablePath") or "")),
                        command_line=redact_text(command_line),
                        role=role.name,
                        protected=_is_protected_process(command_line),
                    )
                )
                break
    return [item for item in matches if item.pid > 0]


def _running_pids(log: LogFn | None = None) -> set[int]:
    if os.name != "nt":
        return set()
    pids: set[int] = set()
    for raw in _query_windows_processes(log=log):
        try:
            pids.add(int(raw.get("ProcessId") or 0))
        except (TypeError, ValueError):
            continue
    return {pid for pid in pids if pid > 0}


def _cleanup_stale_gateway_locks(log: LogFn) -> list[str]:
    if not get_bool("openclaw_watchdog.cleanup_stale_gateway_locks"):
        return []

    running = _running_pids(log=log)
    touched: list[str] = []
    stamp = datetime.now().strftime(get_str("openclaw_watchdog.stale_lock_timestamp_format"))
    suffix = get_str("openclaw_watchdog.stale_lock_suffix_template").format(timestamp=stamp)
    action = get_str("openclaw_watchdog.stale_lock_action")

    for pattern in [str(item) for item in get_list("openclaw_watchdog.stale_lock_globs")]:
        for raw_path in glob(pattern):
            path = Path(raw_path)
            if not path.is_file():
                continue
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                pid = int(payload.get("pid") or 0) if isinstance(payload, dict) else 0
            except (OSError, json.JSONDecodeError, TypeError, ValueError):
                pid = 0
            if pid <= 0 or pid in running:
                continue
            if action == "rename":
                target = path.with_name(f"{path.name}{suffix}")
                try:
                    path.replace(target)
                    touched.append(str(target))
                    log("watchdog renamed stale gateway lock", level="WARN", pid=pid, path=str(target))
                except OSError as exc:
                    log("watchdog stale gateway lock rename failed", level="ERROR", pid=pid, path=str(path), error=str(exc)[:300])
            else:
                log("watchdog stale gateway lock action skipped", level="WARN", action=action, pid=pid, path=str(path))
    return touched


def _port_listening() -> bool:
    try:
        with socket.create_connection(
            (get_str("openclaw_watchdog.host"), get_int("openclaw_watchdog.port")),
            timeout=max(1, min(10, get_int("openclaw_watchdog.probe_timeout_seconds"))),
        ):
            return True
    except OSError:
        return False


def _preview(value: str) -> str:
    return redact_text(value.replace("\r", " ").replace("\n", " "))[:300]


def _probe_gateway(*, runner: OpenClawRunner, attempt: bool) -> ProbeInfo:
    if not attempt:
        return ProbeInfo(attempted=False, ok=False, returncode=None, timed_out=False, stdout_preview="", stderr_preview="")

    strategy = get_str("openclaw_watchdog.probe_strategy").strip().lower()
    if strategy == "http":
        return _probe_gateway_http()
    if strategy == "http_and_cli":
        http_probe = _probe_gateway_http()
        if not http_probe.ok:
            return http_probe
        cli_probe = _probe_gateway_cli(runner=runner)
        if cli_probe.ok:
            return ProbeInfo(
                attempted=True,
                ok=True,
                returncode=0,
                timed_out=False,
                stdout_preview=f"{http_probe.stdout_preview}; {cli_probe.stdout_preview}",
                stderr_preview=cli_probe.stderr_preview,
            )
        return ProbeInfo(
            attempted=True,
            ok=False,
            returncode=cli_probe.returncode,
            timed_out=cli_probe.timed_out,
            stdout_preview=f"{http_probe.stdout_preview}; {cli_probe.stdout_preview}",
            stderr_preview=cli_probe.stderr_preview,
        )

    return _probe_gateway_cli(runner=runner)


def _probe_gateway_cli(*, runner: OpenClawRunner) -> ProbeInfo:
    result = runner.run(
        [str(item) for item in get_list("openclaw_watchdog.probe_command")],
        timeout_seconds=get_int("openclaw_watchdog.probe_timeout_seconds"),
    )
    ok = result.returncode == 0 and not result.timed_out
    if ok and result.stdout.strip():
        try:
            payload = _extract_json_payload(result.stdout)
            if isinstance(payload, dict) and payload.get("ok") is False:
                ok = False
        except json.JSONDecodeError:
            pass

    return ProbeInfo(
        attempted=True,
        ok=ok,
        returncode=result.returncode,
        timed_out=result.timed_out,
        stdout_preview=_preview(result.stdout),
        stderr_preview=_preview(result.stderr),
    )


def _probe_gateway_http() -> ProbeInfo:
    host = get_str("openclaw_watchdog.host")
    port = get_int("openclaw_watchdog.port")
    path = get_str("openclaw_watchdog.http_probe_path")
    if not path.startswith("/"):
        path = f"/{path}"
    timeout = max(1, get_int("openclaw_watchdog.http_probe_timeout_seconds"))
    min_status = get_int("openclaw_watchdog.http_probe_success_status_min")
    max_status = get_int("openclaw_watchdog.http_probe_success_status_max")
    url = f"http://{host}:{port}{path}"

    request = urllib.request.Request(url, method="GET")
    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status = int(response.status)
            ok = min_status <= status <= max_status
            preview = f"http_status={status} elapsed={time.monotonic() - started:.2f}s"
            return ProbeInfo(
                attempted=True,
                ok=ok,
                returncode=0 if ok else status,
                timed_out=False,
                stdout_preview=preview,
                stderr_preview="",
            )
    except urllib.error.HTTPError as exc:
        status = int(exc.code)
        ok = min_status <= status <= max_status
        preview = f"http_status={status} elapsed={time.monotonic() - started:.2f}s"
        return ProbeInfo(
            attempted=True,
            ok=ok,
            returncode=0 if ok else status,
            timed_out=False,
            stdout_preview=preview,
            stderr_preview=_preview(str(exc)),
        )
    except (TimeoutError, socket.timeout) as exc:
        return ProbeInfo(
            attempted=True,
            ok=False,
            returncode=get_int("openclaw.timeout_returncode"),
            timed_out=True,
            stdout_preview="",
            stderr_preview=_preview(str(exc)),
        )
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", exc)
        timed_out = isinstance(reason, (TimeoutError, socket.timeout)) or "timed out" in str(reason).lower()
        return ProbeInfo(
            attempted=True,
            ok=False,
            returncode=get_int("openclaw.timeout_returncode") if timed_out else 1,
            timed_out=timed_out,
            stdout_preview="",
            stderr_preview=_preview(str(exc)),
        )


def check_watchdog_status(*, runner: OpenClawRunner, log: LogFn | None = None) -> WatchdogStatus:
    if not get_bool("openclaw_watchdog.enabled"):
        probe = ProbeInfo(attempted=False, ok=False, returncode=None, timed_out=False, stdout_preview="", stderr_preview="")
        return WatchdogStatus(
            enabled=False,
            healthy=True,
            host=get_str("openclaw_watchdog.host"),
            port=get_int("openclaw_watchdog.port"),
            port_listening=False,
            probe=probe,
            gateway_processes=[],
            node_processes=[],
            reasons=[],
            message=get_str("openclaw_watchdog.disabled_message"),
        )

    processes = matching_processes(log=log)
    gateway = [item for item in processes if item.role == "gateway"]
    node = [item for item in processes if item.role == "node"]
    port_listening = _port_listening()
    probe_attempt = port_listening or get_bool("openclaw_watchdog.probe_when_port_closed")
    probe = _probe_gateway(runner=runner, attempt=probe_attempt)
    reasons: list[str] = []

    if get_bool("openclaw_watchdog.restart_when_gateway_process_missing") and not gateway:
        reasons.append("gateway-process-missing")
    if get_bool("openclaw_watchdog.restart_when_port_closed") and not port_listening:
        reasons.append("gateway-port-closed")
    if get_bool("openclaw_watchdog.restart_when_probe_fails") and probe_attempt and not probe.ok:
        reasons.append("gateway-probe-failed")
    if (
        get_bool("openclaw_watchdog.restart_when_duplicate_gateway_processes")
        and len(gateway) > get_int("openclaw_watchdog.max_gateway_processes")
    ):
        reasons.append("duplicate-gateway-processes")
    if get_bool("openclaw_watchdog.restart_when_orphan_node_process_exists") and node and not gateway:
        reasons.append("orphan-node-process")

    healthy = not reasons
    return WatchdogStatus(
        enabled=True,
        healthy=healthy,
        host=get_str("openclaw_watchdog.host"),
        port=get_int("openclaw_watchdog.port"),
        port_listening=port_listening,
        probe=probe,
        gateway_processes=gateway,
        node_processes=node,
        reasons=reasons,
        message=get_str("openclaw_watchdog.healthy_message") if healthy else get_str("openclaw_watchdog.unhealthy_message"),
    )


def _kill_process(process: ProcessInfo, log: LogFn) -> KillResult:
    if process.protected:
        log("watchdog refused to kill protected process", level="WARN", pid=process.pid, role=process.role)
        return KillResult(pid=process.pid, role=process.role, skipped=True, reason="protected-process-marker", returncode=None)

    command = [str(part).format(pid=str(process.pid)) for part in get_list("openclaw_watchdog.kill_command")]
    log("watchdog killing openclaw process", pid=process.pid, role=process.role)
    try:
        result = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=get_int("openclaw_watchdog.kill_timeout_seconds"),
            check=False,
            creationflags=_creationflags(),
        )
        log(
            "watchdog kill command finished",
            level="OK" if result.returncode == 0 else "ERROR",
            pid=process.pid,
            role=process.role,
            returncode=result.returncode,
        )
        return KillResult(pid=process.pid, role=process.role, skipped=False, reason="", returncode=result.returncode)
    except (OSError, subprocess.TimeoutExpired) as exc:
        log("watchdog kill command failed", level="ERROR", pid=process.pid, role=process.role, error=str(exc)[:300])
        return KillResult(pid=process.pid, role=process.role, skipped=False, reason="kill-failed", returncode=None)


def _start_role(role: ProcessRole, *, runner: OpenClawRunner, log: LogFn) -> StartResult:
    log("watchdog starting openclaw role", role=role.name)
    result = runner.run(role.start_command, timeout_seconds=role.start_timeout_seconds)
    log(
        "watchdog start role finished",
        level="OK" if result.returncode == 0 else "ERROR",
        role=role.name,
        returncode=result.returncode,
        timed_out=result.timed_out,
    )
    return StartResult(role=role.name, command=role.start_command, returncode=result.returncode, timed_out=result.timed_out)


def _resolve_node_executable() -> str:
    for candidate in get_list("openclaw.node_executable_candidates"):
        resolved = shutil.which(str(candidate))
        if resolved:
            return resolved
    return str(get_list("openclaw.node_executable_candidates")[0])


def _direct_command(role: ProcessRole) -> list[str]:
    replacements = {
        "node_executable": _resolve_node_executable(),
        "openclaw_dist_entrypoint": str(get_path("paths.openclaw_dist_entrypoint")),
    }
    return [str(part).format(**replacements) for part in role.direct_start_command]


def _start_direct_role(role: ProcessRole, *, log: LogFn) -> StartResult:
    if not role.direct_start_enabled or not role.direct_start_command:
        return StartResult(role=f"{role.name}:direct", command=[], returncode=2, timed_out=False)

    command = _direct_command(role)
    log("watchdog direct-starting openclaw role", level="WARN", role=role.name)
    try:
        process = subprocess.Popen(
            command,
            cwd=get_str("openclaw.cwd"),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            creationflags=_creationflags(),
        )
        time.sleep(role.direct_start_wait_seconds)
        returncode = process.poll()
        if returncode is None:
            returncode = 0
        log(
            "watchdog direct-start role finished",
            level="OK" if returncode == 0 else "ERROR",
            role=role.name,
            pid=process.pid,
            returncode=returncode,
        )
        return StartResult(role=f"{role.name}:direct", command=command, returncode=returncode, timed_out=False, pid=process.pid)
    except OSError as exc:
        log("watchdog direct-start role failed", level="ERROR", role=role.name, error=str(exc)[:300])
        return StartResult(role=f"{role.name}:direct", command=command, returncode=1, timed_out=False)


def run_watchdog(*, runner: OpenClawRunner, log: LogFn) -> WatchdogRunResult:
    before = check_watchdog_status(runner=runner, log=log)
    if not before.enabled:
        log("watchdog run skipped disabled", level="OK")
        return WatchdogRunResult(
            action="disabled",
            before=before,
            killed=[],
            started=[],
            after=before,
            returncode=get_int("openclaw_watchdog.run_returncode_disabled"),
        )

    if before.healthy:
        log("watchdog run skipped healthy", level="OK")
        return WatchdogRunResult(
            action="none",
            before=before,
            killed=[],
            started=[],
            after=before,
            returncode=get_int("openclaw_watchdog.run_returncode_healthy"),
        )

    log("watchdog restart started", level="WARN", reasons=",".join(before.reasons))
    killed: list[KillResult] = []
    roles_to_kill = {str(role) for role in get_list("openclaw_watchdog.kill_roles_on_restart")}
    for process in [*before.gateway_processes, *before.node_processes]:
        if process.role in roles_to_kill:
            killed.append(_kill_process(process, log))

    time.sleep(get_int("openclaw_watchdog.post_kill_wait_seconds"))
    _cleanup_stale_gateway_locks(log)

    started: list[StartResult] = []
    for role_name in [str(role) for role in get_list("openclaw_watchdog.start_roles_on_restart")]:
        role = _role_by_name(role_name)
        if role is None:
            log("watchdog start role missing from config", level="ERROR", role=role_name)
            continue
        started.append(_start_role(role, runner=runner, log=log))

    time.sleep(get_int("openclaw_watchdog.startup_warmup_seconds"))

    after = check_watchdog_status(runner=runner, log=log)
    attempts = max(1, get_int("openclaw_watchdog.post_start_probe_attempts"))
    for _ in range(1, attempts):
        if after.healthy:
            break
        time.sleep(get_int("openclaw_watchdog.post_start_probe_delay_seconds"))
        after = check_watchdog_status(runner=runner, log=log)

    if not after.healthy and get_bool("openclaw_watchdog.direct_start_after_native_failure"):
        log("watchdog native restart did not recover gateway; trying direct fallback", level="WARN", reasons=",".join(after.reasons))
        for process in [*after.gateway_processes, *after.node_processes]:
            if process.role in roles_to_kill:
                killed.append(_kill_process(process, log))
        time.sleep(get_int("openclaw_watchdog.post_kill_wait_seconds"))
        _cleanup_stale_gateway_locks(log)
        for role_name in [str(role) for role in get_list("openclaw_watchdog.direct_start_roles_after_native_failure")]:
            role = _role_by_name(role_name)
            if role is None:
                log("watchdog direct-start role missing from config", level="ERROR", role=role_name)
                continue
            started.append(_start_direct_role(role, log=log))
        time.sleep(get_int("openclaw_watchdog.startup_warmup_seconds"))
        after = check_watchdog_status(runner=runner, log=log)
        for _ in range(1, attempts):
            if after.healthy:
                break
            time.sleep(get_int("openclaw_watchdog.post_start_probe_delay_seconds"))
            after = check_watchdog_status(runner=runner, log=log)

    log(
        "watchdog restart finished",
        level="OK" if after.healthy else "ERROR",
        before_reasons=",".join(before.reasons),
        after_reasons=",".join(after.reasons),
        killed=len(killed),
        started=len(started),
    )
    return WatchdogRunResult(
        action="restart",
        before=before,
        killed=killed,
        started=started,
        after=after,
        returncode=get_int("openclaw_watchdog.run_returncode_healthy")
        if after.healthy
        else get_int("openclaw_watchdog.run_returncode_unhealthy"),
    )


def watchdog_status_as_dict(status: WatchdogStatus) -> dict[str, Any]:
    return asdict(status)


def watchdog_run_as_dict(result: WatchdogRunResult) -> dict[str, Any]:
    return asdict(result)


def format_watchdog_status(status: WatchdogStatus) -> str:
    lines = [
        status.message,
        f"enabled: {status.enabled}",
        f"endpoint: {status.host}:{status.port}",
        f"port_listening: {status.port_listening}",
        f"probe_ok: {status.probe.ok if status.probe.attempted else 'not-attempted'}",
        f"gateway_processes: {len(status.gateway_processes)}",
        f"node_processes: {len(status.node_processes)}",
    ]
    if status.reasons:
        lines.append(f"reasons: {', '.join(status.reasons)}")
    for process in [*status.gateway_processes, *status.node_processes]:
        protected = " protected" if process.protected else ""
        lines.append(f"{process.role}: pid={process.pid}{protected}")
    return "\n".join(lines)


def format_watchdog_run(result: WatchdogRunResult) -> str:
    lines = [
        f"action: {result.action}",
        f"returncode: {result.returncode}",
        f"before: {'healthy' if result.before.healthy else 'unhealthy'}",
        f"after: {'healthy' if result.after.healthy else 'unhealthy'}",
    ]
    if result.before.reasons:
        lines.append(f"before_reasons: {', '.join(result.before.reasons)}")
    if result.after.reasons:
        lines.append(f"after_reasons: {', '.join(result.after.reasons)}")
    if result.killed:
        lines.append("killed:")
        for item in result.killed:
            suffix = f" skipped={item.reason}" if item.skipped else f" returncode={item.returncode}"
            lines.append(f"  {item.role} pid={item.pid}{suffix}")
    if result.started:
        lines.append("started:")
        for item in result.started:
            pid = f" pid={item.pid}" if item.pid is not None else ""
            lines.append(f"  {item.role}{pid} returncode={item.returncode} timed_out={item.timed_out}")
    return "\n".join(lines)
