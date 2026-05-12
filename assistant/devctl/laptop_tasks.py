"""Config-driven local laptop tasks for OpenClaw-triggered workflows.

OpenClaw stays the assistant runtime and channel layer. This module only
exposes safe, named local actions that the agent can invoke through devctl.
"""

from __future__ import annotations

import json
import mimetypes
import os
import re
import shutil
import subprocess
import time
import urllib.parse
import urllib.request
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from . import log_inspector
from .config import get_bool, get_int, get_list, get_path, get_str
from .mobile_owner import configured_owner_values, redacted_owners
from .openclaw_runner import OpenClawRunner

LogFn = Callable[..., None]


@dataclass(frozen=True)
class LaptopTaskResult:
    name: str
    kind: str
    status: str
    returncode: int
    message: str
    artifact: str = ""
    sent_to_telegram: bool = False
    telegram_returncode: int | None = None


def _task_tables() -> list[dict[str, Any]]:
    tables: list[dict[str, Any]] = []
    for item in get_list("laptop_tasks.tasks"):
        if isinstance(item, dict):
            tables.append(item)
    return tables


def task_names() -> list[str]:
    return [str(item["name"]) for item in _task_tables()]


def task_definitions_as_dicts() -> list[dict[str, object]]:
    definitions: list[dict[str, object]] = []
    for item in _task_tables():
        definitions.append(
            {
                "name": str(item.get("name", "")),
                "kind": str(item.get("kind", "")),
                "description": str(item.get("description", "")),
                "safety_level": str(item.get("safety_level", "")),
                "requires_confirm": bool(item.get("requires_confirm", False)),
                "requires_confirm_when_sending": bool(item.get("requires_confirm_when_sending", False)),
            }
        )
    return definitions


def task_definition(name: str) -> dict[str, Any]:
    for item in _task_tables():
        if str(item.get("name", "")) == name:
            return item
    available = ", ".join(task_names())
    raise ValueError(f"Unknown laptop task '{name}'. Available: {available}")


def _result(
    *,
    task: dict[str, Any],
    status: str,
    returncode: int,
    message: str,
    artifact: Path | str | None = None,
    sent_to_telegram: bool = False,
    telegram_returncode: int | None = None,
) -> LaptopTaskResult:
    return LaptopTaskResult(
        name=str(task.get("name", "")),
        kind=str(task.get("kind", "")),
        status=status,
        returncode=returncode,
        message=message,
        artifact=str(artifact or ""),
        sent_to_telegram=sent_to_telegram,
        telegram_returncode=telegram_returncode,
    )


def _success_status() -> str:
    return get_str("laptop_tasks.success_status")


def _failed_status() -> str:
    return get_str("laptop_tasks.failed_status")


def _refused_status() -> str:
    return get_str("laptop_tasks.refused_status")


def _dry_run_status() -> str:
    return get_str("laptop_tasks.dry_run_status")


def _task_timeout(task: dict[str, Any]) -> int:
    return int(task.get("timeout_seconds") or get_int("laptop_tasks.default_timeout_seconds"))


def _trim_output(text: str) -> str:
    limit = get_int("laptop_tasks.max_output_chars")
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "\n..."


def _format_message_template(template: str, **values: object) -> str:
    return template.format_map({key: str(value) for key, value in values.items()})


def _command_fallback_message(returncode: int) -> str:
    return _format_message_template(
        get_str("laptop_tasks.command_fallback_message_template"),
        returncode=returncode,
    )


def _sanitize_args(args: list[str]) -> list[str]:
    sanitized: list[str] = []
    redact_next = False
    markers = {str(item) for item in get_list("laptop_tasks.redacted_argument_markers")}
    max_chars = get_int("laptop_tasks.command_arg_log_max_chars")
    for raw_arg in args:
        arg = str(raw_arg)
        if redact_next:
            sanitized.append("<redacted>")
            redact_next = False
            continue
        if arg in markers:
            sanitized.append(arg)
            redact_next = True
            continue
        if len(arg) > max_chars:
            sanitized.append(arg[:max_chars].rstrip() + "...")
            continue
        sanitized.append(arg)
    return sanitized


def _resolve_executable(candidates: list[str]) -> str:
    for candidate in candidates:
        value = str(candidate)
        if not value:
            continue
        resolved = shutil.which(value)
        if resolved:
            return resolved
        path = Path(value).expanduser()
        if path.is_file():
            return str(path)
    return str(candidates[0]) if candidates else ""


def _subprocess_creationflags() -> int:
    if os.name == "nt" and get_bool("laptop_tasks.windows_create_no_window"):
        return subprocess.CREATE_NO_WINDOW
    return 0


def _latest_matching_log_line(task: dict[str, Any], *, started_at: float, log: LogFn) -> str:
    path_value = str(task.get("success_log_path", "")).strip()
    if not path_value:
        return ""

    path = Path(path_value).expanduser().resolve()
    if not path.is_file():
        log("success log file not found", level="WARN", task=str(task.get("name", "")), path=str(path))
        return ""

    if bool(task.get("success_log_require_mtime_after_start", False)) and path.stat().st_mtime < started_at:
        log(
            "success log file was not updated by this task run",
            level="WARN",
            task=str(task.get("name", "")),
            path=str(path),
        )
        return ""

    delay_seconds = float(task.get("success_log_read_delay_seconds") or 0)
    if delay_seconds > 0:
        time.sleep(delay_seconds)

    max_bytes = int(task.get("success_log_max_bytes") or get_int("laptop_tasks.latest_log_line_default_max_bytes"))
    with path.open("rb") as handle:
        if max_bytes > 0:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - max_bytes))
        data = handle.read()

    text = data.decode(
        str(task.get("success_log_encoding") or get_str("laptop_tasks.output_encoding")),
        errors=str(task.get("success_log_encoding_errors") or get_str("laptop_tasks.output_encoding_errors")),
    )
    lines = text.splitlines()
    max_lines = int(task.get("success_log_max_lines") or get_int("laptop_tasks.latest_log_line_default_max_lines"))
    if max_lines > 0:
        lines = lines[-max_lines:]

    contains = str(task.get("success_log_contains", "")).strip()
    regex = str(task.get("success_log_regex", "")).strip()
    strip_regexes = [str(item) for item in task.get("success_log_strip_regexes", [])]

    for raw_line in reversed(lines):
        line = raw_line.strip()
        if not line:
            continue
        if contains and contains not in line:
            continue
        if regex and not re.search(regex, line):
            continue
        for strip_regex in strip_regexes:
            line = re.sub(strip_regex, "", line).strip()
        return line

    log("success log line not found", level="WARN", task=str(task.get("name", "")), path=str(path))
    return ""


def _command_result_message(
    task: dict[str, Any],
    completed: subprocess.CompletedProcess[str],
    *,
    started_at: float,
    log: LogFn,
) -> str:
    source = str(task.get("success_message_source", "")).strip()
    if completed.returncode == 0 and source == "latest-log-line":
        line = _latest_matching_log_line(task, started_at=started_at, log=log)
        if line:
            template = str(task.get("success_message_template") or "{line}")
            return _format_message_template(
                template,
                line=line,
                returncode=completed.returncode,
                task=task.get("name", ""),
            )

    output = "\n".join(part for part in [completed.stdout.strip(), completed.stderr.strip()] if part)
    if output:
        return output
    return _command_fallback_message(completed.returncode)


def _run_subprocess(
    *,
    command: list[str],
    cwd: str,
    timeout_seconds: int,
    log: LogFn,
) -> subprocess.CompletedProcess[str] | None:
    safe_command = _sanitize_args(command)
    log("laptop task subprocess started", command=" ".join(safe_command), cwd=cwd)
    try:
        completed = subprocess.run(
            command,
            cwd=cwd or None,
            capture_output=True,
            text=True,
            encoding=get_str("laptop_tasks.output_encoding"),
            errors=get_str("laptop_tasks.output_encoding_errors"),
            timeout=timeout_seconds,
            check=False,
            creationflags=_subprocess_creationflags(),
        )
    except FileNotFoundError:
        log("laptop task executable not found", level="ERROR", command=" ".join(safe_command))
        return None
    except subprocess.TimeoutExpired as exc:
        log(
            "laptop task subprocess timed out",
            level="ERROR",
            command=" ".join(safe_command),
            timeout_seconds=timeout_seconds,
        )
        return subprocess.CompletedProcess(command, get_int("openclaw.timeout_returncode"), exc.stdout or "", exc.stderr or "")

    log(
        "laptop task subprocess finished",
        level="OK" if completed.returncode == 0 else "ERROR",
        command=" ".join(safe_command),
        returncode=completed.returncode,
        stdout_chars=len(completed.stdout or ""),
        stderr_chars=len(completed.stderr or ""),
    )
    return completed


def _confirmation_refusal(task: dict[str, Any], message: str) -> LaptopTaskResult:
    return _result(
        task=task,
        status=_refused_status(),
        returncode=2,
        message=message,
    )


def _ensure_confirmed(task: dict[str, Any], *, confirm: bool, send_telegram: bool) -> LaptopTaskResult | None:
    if bool(task.get("requires_confirm", False)) and not confirm:
        reason = str(task.get("confirm_reason") or get_str("laptop_tasks.command_requires_confirm_message"))
        return _confirmation_refusal(task, reason)
    if send_telegram:
        sending_requires_confirm = get_bool("laptop_tasks.external_send_requires_confirm") or bool(
            task.get("requires_confirm_when_sending", False)
        )
        if sending_requires_confirm and not confirm:
            return _confirmation_refusal(task, get_str("laptop_tasks.external_send_requires_confirm_message"))
    return None


def _run_command_task(task: dict[str, Any], *, dry_run: bool, log: LogFn) -> LaptopTaskResult:
    command = [str(item) for item in task.get("command", [])]
    cwd = str(task.get("cwd", ""))
    if dry_run:
        message = "Would run: " + " ".join(_sanitize_args(command))
        return _result(task=task, status=_dry_run_status(), returncode=0, message=message)

    started_at = time.time()
    completed = _run_subprocess(command=command, cwd=cwd, timeout_seconds=_task_timeout(task), log=log)
    if completed is None:
        return _result(
            task=task,
            status=_failed_status(),
            returncode=get_int("laptop_tasks.not_found_returncode"),
            message="Configured executable was not found.",
        )

    output = _command_result_message(task, completed, started_at=started_at, log=log)
    return _result(
        task=task,
        status=_success_status() if completed.returncode == 0 else _failed_status(),
        returncode=completed.returncode,
        message=_trim_output(output),
    )


def _run_log_summary_task(task: dict[str, Any], *, dry_run: bool) -> LaptopTaskResult:
    source = str(task.get("source", ""))
    lines = int(task.get("lines") or get_int("logs.tail_lines_default"))
    max_files = int(task.get("max_files") or get_int("logs.max_files_default"))
    errors_only = bool(task.get("errors_only", False))
    if dry_run:
        message = f"Would inspect source={source}, lines={lines}, errors_only={errors_only}, max_files={max_files}."
        return _result(task=task, status=_dry_run_status(), returncode=0, message=message)

    results = log_inspector.tail_lines(
        source=source,
        lines=lines,
        errors_only=errors_only,
        max_files=max_files,
    )
    message = log_inspector.format_log_lines(results) or get_str("laptop_tasks.no_matching_logs_message")
    return _result(task=task, status=_success_status(), returncode=0, message=_trim_output(message))


def _screenshot_path() -> Path:
    stamp = datetime.now().strftime(get_str("laptop_tasks.timestamp_format"))
    filename = get_str("laptop_tasks.screenshot.filename_template").format(timestamp=stamp)
    return get_path("laptop_tasks.screenshot.output_dir") / filename


def _ps_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _screenshot_script(output_path: Path, image_format: str) -> str:
    header = "\n".join(
        [
            "$ErrorActionPreference = 'Stop'",
            f"$outputPath = {_ps_literal(str(output_path))}",
            f"$imageFormatName = {_ps_literal(image_format)}",
        ]
    )
    body = r"""
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$screen = [System.Windows.Forms.Screen]::PrimaryScreen
$bounds = $screen.Bounds
$bitmap = New-Object System.Drawing.Bitmap($bounds.Width, $bounds.Height)
$graphics = [System.Drawing.Graphics]::FromImage($bitmap)
try {
    $graphics.CopyFromScreen($bounds.Location, [System.Drawing.Point]::Empty, $bounds.Size)
    $property = [System.Drawing.Imaging.ImageFormat].GetProperty($imageFormatName)
    if ($null -eq $property) { throw "Unsupported image format: $imageFormatName" }
    $format = $property.GetValue($null, $null)
    $bitmap.Save($outputPath, $format)
    Write-Output $outputPath
}
finally {
    if ($graphics) { $graphics.Dispose() }
    if ($bitmap) { $bitmap.Dispose() }
}
"""
    return header + body


def _run_screenshot_capture(task: dict[str, Any], *, dry_run: bool, log: LogFn) -> LaptopTaskResult:
    output_path = _screenshot_path()
    if dry_run:
        return _result(
            task=task,
            status=_dry_run_status(),
            returncode=0,
            message=f"Would capture primary screen to {output_path}.",
            artifact=output_path,
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    executable = _resolve_executable([str(item) for item in get_list("laptop_tasks.screenshot.powershell_executable_candidates")])
    command = [
        executable,
        *[str(item) for item in get_list("laptop_tasks.screenshot.powershell_arguments")],
        _screenshot_script(output_path, get_str("laptop_tasks.screenshot.image_format")),
    ]
    completed = _run_subprocess(
        command=command,
        cwd=str(output_path.parent),
        timeout_seconds=get_int("laptop_tasks.screenshot.capture_timeout_seconds"),
        log=log,
    )
    if completed is None:
        return _result(
            task=task,
            status=_failed_status(),
            returncode=get_int("laptop_tasks.not_found_returncode"),
            message="Configured PowerShell executable was not found.",
        )
    if completed.returncode != 0:
        message = "\n".join(part for part in [completed.stdout.strip(), completed.stderr.strip()] if part)
        return _result(task=task, status=_failed_status(), returncode=completed.returncode, message=_trim_output(message))
    if not output_path.is_file():
        return _result(task=task, status=_failed_status(), returncode=1, message="Screenshot file was not created.")
    if output_path.stat().st_size > get_int("laptop_tasks.screenshot.max_media_bytes"):
        return _result(task=task, status=_failed_status(), returncode=1, message="Screenshot exceeds configured media size limit.")

    return _result(
        task=task,
        status=_success_status(),
        returncode=0,
        message=f"Captured primary screen screenshot: {output_path}",
        artifact=output_path,
    )


def _raw_owner_values_from_stdout(stdout: str) -> list[str]:
    try:
        parsed = json.loads(stdout)
    except json.JSONDecodeError:
        return []
    if isinstance(parsed, list):
        return [str(item) for item in parsed]
    if isinstance(parsed, dict):
        direct = parsed.get("ownerAllowFrom", [])
        if isinstance(direct, list):
            return [str(item) for item in direct]
        commands = parsed.get("commands", {})
        if isinstance(commands, dict):
            nested = commands.get("ownerAllowFrom", [])
            if isinstance(nested, list):
                return [str(item) for item in nested]
    return []


def _normalize_telegram_target(value: str) -> str:
    target = value.strip()
    for prefix in get_list("laptop_tasks.telegram.target_prefixes_to_strip"):
        raw_prefix = str(prefix)
        if target.lower().startswith(raw_prefix.lower()):
            target = target[len(raw_prefix) :]
            break
    return target.strip()


def _resolve_telegram_target(*, runner: OpenClawRunner, log: LogFn) -> str:
    explicit = get_str("laptop_tasks.telegram.explicit_target").strip()
    if explicit:
        log("telegram target resolved from explicit config", owners=",".join(redacted_owners([explicit])))
        return _normalize_telegram_target(explicit)

    target_source = get_str("laptop_tasks.telegram.target_source")
    owners: list[str] = []
    if target_source == "openclaw-command-owner":
        result = runner.run(
            [str(item) for item in get_list("laptop_tasks.telegram.owner_config_command")],
            timeout_seconds=get_int("laptop_tasks.telegram.owner_command_timeout_seconds"),
        )
        if result.returncode == 0:
            owners = _raw_owner_values_from_stdout(result.stdout)

    owners = configured_owner_values(owners)
    if not owners:
        raise ValueError(get_str("laptop_tasks.telegram_target_missing_message"))

    target = _normalize_telegram_target(owners[0])
    log("telegram target resolved from command owner", owner_count=len(owners), owners=",".join(redacted_owners(owners)))
    return target


def _safe_delivery_text(text: str, target: str) -> str:
    return text.replace(target, "<redacted-target>")


def _send_telegram_openclaw_native(
    *,
    message: str,
    media: str | None,
    log: LogFn,
    target: str,
) -> tuple[bool, int, str]:
    runner = OpenClawRunner(log)
    max_chars = get_int("laptop_tasks.telegram.message_max_chars")
    body = message[:max_chars]
    args = [
        "message",
        "send",
        "--channel",
        get_str("laptop_tasks.telegram.channel"),
        "--target",
        target,
        "--message",
        body,
        "--json",
    ]
    account = get_str("laptop_tasks.telegram.account").strip()
    if account:
        args.extend(["--account", account])
    if media:
        args.extend(["--media", media])
        if get_bool("laptop_tasks.telegram.force_document"):
            args.append("--force-document")
    if get_bool("laptop_tasks.telegram.silent"):
        args.append("--silent")

    result = runner.run(args, timeout_seconds=get_int("laptop_tasks.telegram.send_timeout_seconds"))
    detail = _safe_delivery_text((result.stderr or result.stdout).strip(), target)
    return result.returncode == 0, result.returncode, _trim_output(detail)


def _telegram_native_retry_requested(detail: str) -> bool:
    lowered = detail.lower()
    return any(str(marker).lower() in lowered for marker in get_list("laptop_tasks.telegram.native_retry_markers"))


def _restart_gateway_for_native_retry(*, log: LogFn) -> bool:
    runner = OpenClawRunner(log)
    result = runner.run(
        [str(item) for item in get_list("laptop_tasks.telegram.native_restart_gateway_command")],
        timeout_seconds=get_int("laptop_tasks.telegram.native_restart_gateway_timeout_seconds"),
    )
    ok = result.returncode == 0
    log("openclaw gateway restart for telegram native retry finished", level="OK" if ok else "ERROR", returncode=result.returncode)
    return ok


def _send_telegram_openclaw_native_with_recovery(
    *,
    message: str,
    media: str | None,
    log: LogFn,
    target: str,
) -> tuple[bool, int, str]:
    attempts = max(1, get_int("laptop_tasks.telegram.native_retry_attempts"))
    last_result: tuple[bool, int, str] = (False, 1, "")
    for attempt in range(1, attempts + 1):
        ok, returncode, detail = _send_telegram_openclaw_native(message=message, media=media, log=log, target=target)
        last_result = (ok, returncode, detail)
        if ok:
            return last_result

        can_retry = attempt < attempts and _telegram_native_retry_requested(detail)
        if not can_retry:
            return last_result

        log("telegram native delivery hit retryable gateway timeout", level="WARN", attempt=attempt, returncode=returncode)
        if get_bool("laptop_tasks.telegram.native_restart_gateway_before_retry"):
            _restart_gateway_for_native_retry(log=log)
        delay_seconds = get_int("laptop_tasks.telegram.native_retry_delay_seconds")
        if delay_seconds > 0:
            time.sleep(delay_seconds)

    return last_result


def _telegram_bot_api_url(token: str, method: str) -> str:
    return f"{get_str('laptop_tasks.telegram.bot_api_base_url').rstrip('/')}/bot{token}/{method}"


def _read_telegram_bot_token() -> str:
    token_path = get_path("laptop_tasks.telegram.bot_api_token_file")
    return token_path.read_text(encoding="utf-8").strip()


def _send_telegram_bot_api_text(*, message: str, target: str, token: str) -> tuple[bool, int, str]:
    method = get_str("laptop_tasks.telegram.bot_api_send_message_method")
    payload = urllib.parse.urlencode({"chat_id": target, "text": message}).encode("utf-8")
    request = urllib.request.Request(
        _telegram_bot_api_url(token, method),
        data=payload,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=get_int("laptop_tasks.telegram.bot_api_timeout_seconds")) as response:
        body = response.read().decode("utf-8", errors="replace")
    if not get_bool("laptop_tasks.telegram.bot_api_parse_json_response"):
        return True, 0, ""
    parsed = json.loads(body)
    return bool(parsed.get("ok")), 0 if parsed.get("ok") else 1, str(parsed.get("description", ""))


def _multipart_body(*, fields: dict[str, str], file_field: str, file_path: Path) -> tuple[bytes, str]:
    boundary = f"pa-{uuid.uuid4().hex}"
    chunks: list[bytes] = []
    for key, value in fields.items():
        chunks.extend(
            [
                f"--{boundary}\r\n".encode("utf-8"),
                f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode("utf-8"),
                str(value).encode("utf-8"),
                b"\r\n",
            ]
        )

    content_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
    chunks.extend(
        [
            f"--{boundary}\r\n".encode("utf-8"),
            (
                f'Content-Disposition: form-data; name="{file_field}"; '
                f'filename="{file_path.name}"\r\n'
            ).encode("utf-8"),
            f"Content-Type: {content_type}\r\n\r\n".encode("utf-8"),
            file_path.read_bytes(),
            b"\r\n",
            f"--{boundary}--\r\n".encode("utf-8"),
        ]
    )
    return b"".join(chunks), boundary


def _send_telegram_bot_api_media(*, message: str, media: str, target: str, token: str) -> tuple[bool, int, str]:
    method = get_str("laptop_tasks.telegram.bot_api_send_media_method")
    media_field = get_str("laptop_tasks.telegram.bot_api_media_field")
    caption_field = get_str("laptop_tasks.telegram.bot_api_caption_field")
    file_path = Path(media)
    body, boundary = _multipart_body(
        fields={"chat_id": target, caption_field: message},
        file_field=media_field,
        file_path=file_path,
    )
    request = urllib.request.Request(
        _telegram_bot_api_url(token, method),
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=get_int("laptop_tasks.telegram.bot_api_timeout_seconds")) as response:
        response_body = response.read().decode("utf-8", errors="replace")
    if not get_bool("laptop_tasks.telegram.bot_api_parse_json_response"):
        return True, 0, ""
    parsed = json.loads(response_body)
    return bool(parsed.get("ok")), 0 if parsed.get("ok") else 1, str(parsed.get("description", ""))


def _send_telegram_bot_api(
    *,
    message: str,
    media: str | None,
    target: str,
    log: LogFn,
) -> tuple[bool, int, str]:
    if not get_bool("laptop_tasks.telegram.bot_api_enabled"):
        return False, 2, "Telegram Bot API fallback is disabled in config."
    try:
        token = _read_telegram_bot_token()
        body = message[: get_int("laptop_tasks.telegram.message_max_chars")]
        if media:
            result = _send_telegram_bot_api_media(message=body, media=media, target=target, token=token)
        else:
            result = _send_telegram_bot_api_text(message=body, target=target, token=token)
        log("telegram bot api fallback finished", level="OK" if result[0] else "ERROR", returncode=result[1])
        return result
    except Exception as exc:
        message_text = str(exc)
        if "token" in locals() and token:
            message_text = message_text.replace(token, "<redacted-token>")
        log("telegram bot api fallback failed", level="ERROR", error=message_text[: get_int("laptop_tasks.max_output_chars")])
        return False, 1, _trim_output(message_text)


def _send_telegram(
    *,
    message: str,
    media: str | None,
    log: LogFn,
) -> tuple[bool, int, str]:
    if not get_bool("laptop_tasks.telegram.enabled"):
        return False, 2, "Telegram delivery is disabled in config."

    target_runner = OpenClawRunner(log)
    target = _resolve_telegram_target(runner=target_runner, log=log)
    attempts: list[str] = []
    for provider in [str(item) for item in get_list("laptop_tasks.telegram.delivery_order")]:
        if provider == "openclaw-native":
            ok, returncode, detail = _send_telegram_openclaw_native_with_recovery(
                message=message,
                media=media,
                log=log,
                target=target,
            )
        elif provider == "telegram-bot-api":
            ok, returncode, detail = _send_telegram_bot_api(
                message=message,
                media=media,
                target=target,
                log=log,
            )
        else:
            ok, returncode, detail = False, 2, f"Unsupported Telegram delivery provider: {provider}"

        attempts.append(f"{provider}: rc={returncode}" + (f" {detail}" if detail else ""))
        if ok:
            return True, returncode, provider
        log("telegram delivery provider failed", level="WARN", provider=provider, returncode=returncode)

    return False, 1, _trim_output("\n".join(attempts))


def _send_result_if_requested(result: LaptopTaskResult, *, send_telegram: bool, log: LogFn) -> LaptopTaskResult:
    if not send_telegram or result.returncode != 0:
        return result

    if result.kind == "screenshot":
        stamp = datetime.now().strftime(get_str("laptop_tasks.timestamp_format"))
        message = get_str("laptop_tasks.telegram.media_message_template").format(timestamp=stamp)
        ok, returncode, detail = _send_telegram(message=message, media=result.artifact, log=log)
    else:
        prefix = get_str("laptop_tasks.telegram.text_message_prefix")
        ok, returncode, detail = _send_telegram(message=f"{prefix}\n\n{result.message}", media=None, log=log)

    if ok:
        return LaptopTaskResult(
            name=result.name,
            kind=result.kind,
            status=result.status,
            returncode=result.returncode,
            message=result.message + "\nTelegram delivery completed.",
            artifact=result.artifact,
            sent_to_telegram=True,
            telegram_returncode=returncode,
        )

    return LaptopTaskResult(
        name=result.name,
        kind=result.kind,
        status=_failed_status(),
        returncode=returncode,
        message=result.message + "\nTelegram delivery failed: " + detail,
        artifact=result.artifact,
        sent_to_telegram=False,
        telegram_returncode=returncode,
    )


def run_laptop_task(
    name: str,
    *,
    dry_run: bool = False,
    confirm: bool = False,
    send_telegram: bool = False,
    log: LogFn,
) -> LaptopTaskResult:
    if not get_bool("laptop_tasks.enabled"):
        task = {"name": name, "kind": "disabled"}
        return _result(task=task, status=_refused_status(), returncode=2, message="Laptop tasks are disabled in config.")

    task = task_definition(name)
    refusal = None if dry_run else _ensure_confirmed(task, confirm=confirm, send_telegram=send_telegram)
    if refusal is not None:
        log("laptop task refused", level="WARN", task=name, reason=refusal.message)
        return refusal

    kind = str(task.get("kind", ""))
    log("laptop task started", task=name, kind=kind, dry_run=dry_run, send_telegram=send_telegram)
    if kind == "command":
        result = _run_command_task(task, dry_run=dry_run, log=log)
    elif kind == "log-summary":
        result = _run_log_summary_task(task, dry_run=dry_run)
    elif kind == "screenshot":
        result = _run_screenshot_capture(task, dry_run=dry_run, log=log)
    else:
        result = _result(task=task, status=_failed_status(), returncode=2, message=f"Unsupported task kind: {kind}")

    result = _send_result_if_requested(result, send_telegram=send_telegram and not dry_run, log=log)
    log(
        "laptop task finished",
        level="OK" if result.returncode == 0 else "ERROR",
        task=name,
        kind=kind,
        status=result.status,
        returncode=result.returncode,
        artifact=bool(result.artifact),
        sent_to_telegram=result.sent_to_telegram,
    )
    return result


def task_result_as_dict(result: LaptopTaskResult) -> dict[str, object]:
    return asdict(result)


def format_task_result(result: LaptopTaskResult) -> str:
    lines = [
        f"Task: {result.name}",
        f"Kind: {result.kind}",
        f"Status: {result.status}",
        f"Returncode: {result.returncode}",
    ]
    if result.artifact:
        lines.append(f"Artifact: {result.artifact}")
    if result.telegram_returncode is not None:
        lines.append(f"Telegram returncode: {result.telegram_returncode}")
    lines.append("")
    lines.append(result.message)
    return "\n".join(lines)
