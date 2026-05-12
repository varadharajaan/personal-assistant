"""Windows scheduled task orchestration for the Telegram bridge.

Mirrors the layout of ``openclaw_watchdog_schedule.py`` so a future operator
can install/inspect/delete/run-now the bridge through the same shape.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

from .config import get_bool, get_int, get_list, get_path, get_str


@dataclass(frozen=True)
class TelegramBridgeSchedulePlan:
    enabled: bool
    task_name: str
    schedule: str
    modifier: int
    start_time: str
    run_level: str
    start_when_available: bool
    allow_start_on_batteries: bool
    do_not_stop_on_batteries: bool
    multiple_instances: str
    execution_time_limit: str
    wrapper_script: str
    powershell_wrapper: str
    task_runner_executable: str
    powershell_executable: str
    schtasks_executable: str
    task_run_command: str
    create_args: list[str]
    query_args: list[str]
    delete_args: list[str]
    run_now_args: list[str]


def _resolve_candidate(key_path: str) -> str:
    for candidate in get_list(key_path):
        resolved = shutil.which(str(candidate))
        if resolved:
            return resolved
    return ""


def _quote_command_part(value: str) -> str:
    escaped = value.replace('"', '\\"')
    return f'"{escaped}"' if any(char.isspace() for char in escaped) else escaped


def _task_run_command(executable: str, wrapper_script: Path) -> str:
    parts = [
        executable,
        *[str(item) for item in get_list("telegram_bridge_schedule.task_runner_arguments")],
        str(wrapper_script),
    ]
    return " ".join(_quote_command_part(part) for part in parts)


def build_telegram_bridge_schedule_plan() -> TelegramBridgeSchedulePlan:
    wrapper_script = get_path("telegram_bridge_schedule.wrapper_script")
    powershell_wrapper = get_path("telegram_bridge_schedule.powershell_wrapper")
    task_runner = _resolve_candidate("telegram_bridge_schedule.task_runner_executable_candidates")
    powershell = _resolve_candidate("telegram_bridge_schedule.powershell_executable_candidates")
    schtasks = _resolve_candidate("telegram_bridge_schedule.schtasks_executable_candidates")
    task_name = get_str("telegram_bridge_schedule.task_name")
    task_run_command = _task_run_command(
        task_runner or get_str("telegram_bridge_schedule.task_runner_fallback"),
        wrapper_script,
    )

    create_args = [
        "/Create",
        "/TN",
        task_name,
        "/SC",
        get_str("telegram_bridge_schedule.schedule"),
        "/TR",
        task_run_command,
        "/RL",
        get_str("telegram_bridge_schedule.run_level"),
    ]
    modifier = get_int("telegram_bridge_schedule.modifier")
    if modifier > 0:
        create_args.extend(["/MO", str(modifier)])
    start_time = get_str("telegram_bridge_schedule.start_time")
    if start_time:
        create_args.extend(["/ST", start_time])
    if get_bool("telegram_bridge_schedule.force_create"):
        create_args.append("/F")

    query_args = ["/Query", "/TN", task_name]
    if get_bool("telegram_bridge_schedule.query_verbose"):
        query_args.append("/V")
    query_args.extend(["/FO", get_str("telegram_bridge_schedule.query_format")])

    return TelegramBridgeSchedulePlan(
        enabled=get_bool("telegram_bridge_schedule.enabled"),
        task_name=task_name,
        schedule=get_str("telegram_bridge_schedule.schedule"),
        modifier=modifier,
        start_time=start_time,
        run_level=get_str("telegram_bridge_schedule.run_level"),
        start_when_available=get_bool("telegram_bridge_schedule.start_when_available"),
        allow_start_on_batteries=get_bool("telegram_bridge_schedule.allow_start_on_batteries"),
        do_not_stop_on_batteries=get_bool("telegram_bridge_schedule.do_not_stop_on_batteries"),
        multiple_instances=get_str("telegram_bridge_schedule.multiple_instances"),
        execution_time_limit=get_str("telegram_bridge_schedule.execution_time_limit"),
        wrapper_script=str(wrapper_script),
        powershell_wrapper=str(powershell_wrapper),
        task_runner_executable=task_runner,
        powershell_executable=powershell,
        schtasks_executable=schtasks,
        task_run_command=task_run_command,
        create_args=create_args,
        query_args=query_args,
        delete_args=["/Delete", "/TN", task_name, "/F"],
        run_now_args=["/Run", "/TN", task_name],
    )


def format_telegram_bridge_schedule_plan() -> str:
    return json.dumps(asdict(build_telegram_bridge_schedule_plan()), indent=get_int("archive.json_indent"))


def _run_schtasks(args: list[str], *, log, operation: str) -> int:
    plan = build_telegram_bridge_schedule_plan()
    if not plan.schtasks_executable:
        print("Task Scheduler CLI was not found.")
        return 2

    command = [plan.schtasks_executable, *args]
    log("telegram bridge schedule command started", operation=operation, task=plan.task_name)
    result = subprocess.run(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    if result.stdout:
        print(result.stdout.rstrip())
    if result.stderr:
        print(result.stderr.rstrip())
    log(
        "telegram bridge schedule command finished",
        level="OK" if result.returncode == 0 else "ERROR",
        operation=operation,
        task=plan.task_name,
        returncode=result.returncode,
    )
    return result.returncode


def _powershell_bool(value: bool) -> str:
    return "$true" if value else "$false"


def _powershell_string(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _apply_task_settings(*, plan: TelegramBridgeSchedulePlan, log) -> int:
    script = "; ".join(
        [
            "$ErrorActionPreference = 'Stop'",
            f"$task = Get-ScheduledTask -TaskName {_powershell_string(plan.task_name)}",
            f"$task.Settings.StartWhenAvailable = {_powershell_bool(plan.start_when_available)}",
            f"$task.Settings.DisallowStartIfOnBatteries = {_powershell_bool(not plan.allow_start_on_batteries)}",
            f"$task.Settings.StopIfGoingOnBatteries = {_powershell_bool(not plan.do_not_stop_on_batteries)}",
            f"$task.Settings.MultipleInstances = {_powershell_string(plan.multiple_instances)}",
            f"$task.Settings.ExecutionTimeLimit = {_powershell_string(plan.execution_time_limit)}",
            "$task | Set-ScheduledTask | Out-Null",
        ]
    )
    command = [
        plan.powershell_executable,
        *[str(item) for item in get_list("telegram_bridge_schedule.powershell_arguments")],
        script,
    ]
    log("telegram bridge schedule settings apply started", task=plan.task_name)
    result = subprocess.run(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    if result.stdout:
        print(result.stdout.rstrip())
    if result.stderr:
        print(result.stderr.rstrip())
    log(
        "telegram bridge schedule settings apply finished",
        level="OK" if result.returncode == 0 else "ERROR",
        task=plan.task_name,
        returncode=result.returncode,
    )
    return result.returncode


def install_telegram_bridge_schedule(*, log, confirm: bool) -> int:
    plan = build_telegram_bridge_schedule_plan()
    if sys.platform != get_str("telegram_bridge_schedule.platform"):
        print(get_str("telegram_bridge_schedule.unsupported_platform_message"))
        return 2
    if not plan.enabled:
        print(get_str("telegram_bridge_schedule.disabled_message"))
        return 2
    if get_bool("telegram_bridge_schedule.install_requires_confirm") and not confirm:
        print(get_str("telegram_bridge_schedule.install_requires_confirm_message"))
        return 2
    if not plan.task_runner_executable:
        print("Configured task runner executable was not found.")
        return 2
    if not plan.powershell_executable:
        print("PowerShell executable was not found for task settings maintenance.")
        return 2
    if not Path(plan.wrapper_script).is_file():
        print(f"Telegram bridge schedule wrapper was not found: {plan.wrapper_script}")
        return 2
    if not Path(plan.powershell_wrapper).is_file():
        print(f"Telegram bridge PowerShell wrapper was not found: {plan.powershell_wrapper}")
        return 2

    create_returncode = _run_schtasks(plan.create_args, log=log, operation="install")
    if create_returncode != 0 or not get_bool("telegram_bridge_schedule.apply_settings_after_install"):
        return create_returncode
    return _apply_task_settings(plan=plan, log=log)


def query_telegram_bridge_schedule(*, log) -> int:
    return _run_schtasks(build_telegram_bridge_schedule_plan().query_args, log=log, operation="status")


def delete_telegram_bridge_schedule(*, log, confirm: bool) -> int:
    plan = build_telegram_bridge_schedule_plan()
    if get_bool("telegram_bridge_schedule.delete_requires_confirm") and not confirm:
        print(get_str("telegram_bridge_schedule.delete_requires_confirm_message"))
        return 2
    return _run_schtasks(plan.delete_args, log=log, operation="delete")


def run_telegram_bridge_schedule_now(*, log, confirm: bool) -> int:
    plan = build_telegram_bridge_schedule_plan()
    if get_bool("telegram_bridge_schedule.run_now_requires_confirm") and not confirm:
        print(get_str("telegram_bridge_schedule.run_now_requires_confirm_message"))
        return 2
    return _run_schtasks(plan.run_now_args, log=log, operation="run-now")
