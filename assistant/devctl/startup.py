"""Startup orchestration for the local personal assistant."""

from __future__ import annotations

from dataclasses import asdict, dataclass

from .agent_roles import ensure_configured_agents
from .archive import build_archive_plan
from .config import get_bool, get_int, get_list, get_str
from .mobile_bridge import bridge_url, health_url
from .openclaw_runner import OpenClawRunner
from .openclaw_watchdog import run_watchdog
from .paths import ensure_runtime_dirs


@dataclass(frozen=True)
class StartupStep:
    action: str
    status: str
    message: str


def run_startup(*, runner: OpenClawRunner, log) -> list[StartupStep]:
    steps: list[StartupStep] = []
    continue_on_error = get_bool("startup.continue_on_error")

    for action in [str(item) for item in get_list("startup.actions")]:
        status = "ok"
        message = ""
        try:
            if action == "ensure-runtime-dirs":
                ensure_runtime_dirs()
                message = "runtime directories ensured"
            elif action == "ensure-agent-roles":
                if get_bool("agent.routing.ensure_roles_on_start"):
                    results = ensure_configured_agents(
                        runner=runner,
                        log=log,
                        timeout_seconds=get_int("openclaw.default_control_timeout_seconds"),
                    )
                    failures = [item for item in results if item.returncode != 0]
                    status = "ok" if not failures else "warn"
                    message = f"agent roles checked: {len(results)}, failures: {len(failures)}"
                else:
                    status = "skipped"
                    message = "agent role startup ensure disabled"
            elif action == "openclaw-gateway-start":
                result = runner.run(["gateway", "start"], timeout_seconds=get_int("startup.openclaw_start_timeout_seconds"))
                status = "ok" if result.returncode == 0 else "warn"
                message = f"gateway start returncode={result.returncode}"
            elif action == "openclaw-watchdog-run":
                result = run_watchdog(runner=runner, log=log)
                status = "ok" if result.returncode == 0 else "warn"
                message = f"watchdog action={result.action} returncode={result.returncode}"
            elif action == "openclaw-gateway-status":
                result = runner.run(["status"], timeout_seconds=get_int("startup.openclaw_status_timeout_seconds"))
                status = "ok" if result.returncode == 0 else "warn"
                message = f"status returncode={result.returncode}"
            elif action == "mobile-webhook-info":
                message = f"capture={bridge_url()} health={health_url()}"
            elif action == "archive-plan":
                plan = build_archive_plan()
                message = f"archive candidate files={len(plan.files)} bytes={plan.total_bytes}"
            elif action == "models-validate" and get_bool("startup.run_model_validation"):
                result = runner.run(
                    ["models", "list", "--provider", get_str("openclaw.default_provider"), "--plain"],
                    timeout_seconds=get_int("openclaw.default_models_list_timeout_seconds"),
                )
                status = "ok" if result.returncode == 0 else "warn"
                message = f"model list returncode={result.returncode}"
            else:
                status = "skipped"
                message = f"unknown or disabled startup action: {action}"
        except Exception as exc:
            status = "error"
            message = str(exc)

        steps.append(StartupStep(action=action, status=status, message=message))
        log("startup step finished", level="OK" if status == "ok" else "WARN", action=action, status=status)
        if status == "error" and not continue_on_error:
            break

    return steps


def startup_steps_as_dicts(steps: list[StartupStep]) -> list[dict[str, str]]:
    return [asdict(step) for step in steps]
