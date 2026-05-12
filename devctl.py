#!/usr/bin/env python
"""Personal Assistant dev control.

Examples:
    python devctl.py openclaw status
    python devctl.py run summarize-ultracode --dry-run
    python devctl.py mobile capture --source whatsapp --sender me --message "summarize ultracode"
    python devctl.py mobile drain --limit 1
    python devctl.py logs errors --source all
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

from assistant.devctl.doctor_triage import (
    format_issues,
    issues_as_dicts,
    latest_doctor_artifact,
    text_from_artifact,
    text_from_result,
    triage_text,
)
from assistant.devctl import log_inspector
from assistant.devctl.agent_roles import (
    configured_roles,
    ensure_configured_agents,
    ensure_results_as_dicts,
    resolve_agent_call,
    role_names,
    roles_as_dicts,
)
from assistant.devctl.archive import (
    apply_s3_archive_policy,
    build_archive_plan,
    create_archive,
    format_archive_plan,
    format_s3_archive_policy_plan,
    s3_archive_policy_status_as_dict,
    upload_archive_to_s3,
)
from assistant.devctl.archive_schedule import (
    delete_archive_schedule,
    format_archive_schedule_plan,
    install_archive_schedule,
    query_archive_schedule,
    run_archive_schedule_now,
)
from assistant.devctl.config import get_bool, get_int, get_list, get_str, get_table
from assistant.devctl.laptop_tasks import (
    format_task_result,
    run_laptop_task,
    task_definition,
    task_definitions_as_dicts,
    task_names,
    task_result_as_dict,
)
from assistant.devctl.mobile_external import external_info, readiness_as_dicts
from assistant.devctl.model_diagnostics import (
    checks_as_dicts,
    format_model_checks,
    parse_model_list,
    returncode_for_checks,
    validate_configured_models,
)
from assistant.devctl.mobile_bridge import bridge_url, health_url, serve_mobile_bridge
from assistant.devctl.mobile_inbox import capture_command, list_commands, mark_command, pending_commands
from assistant.devctl.mobile_owner import (
    command_owner_set_result_as_dict,
    command_owner_status,
    command_owner_status_as_dict,
    configured_owner_values,
    set_command_owners,
)
from assistant.devctl.mobile_token_backup import (
    backup_token_to_s3,
    restore_token_from_s3,
    status_as_dict as token_status_as_dict,
    token_status,
)
from assistant.devctl.openclaw_runner import CommandResult, OpenClawRunner
from assistant.devctl.openclaw_watchdog import (
    check_watchdog_status,
    format_watchdog_run,
    format_watchdog_status,
    run_watchdog,
    watchdog_run_as_dict,
    watchdog_status_as_dict,
)
from assistant.devctl.openclaw_watchdog_schedule import (
    delete_openclaw_watchdog_schedule,
    format_openclaw_watchdog_schedule_plan,
    install_openclaw_watchdog_schedule,
    query_openclaw_watchdog_schedule,
    run_openclaw_watchdog_schedule_now,
)
from assistant.devctl.telegram_bridge import (
    bridge_status_as_dict,
    build_bridge,
    check_bridge_status,
    configured_rules_as_dicts,
    cycle_outcome_as_dict,
)
from assistant.devctl.telegram_bridge_schedule import (
    delete_telegram_bridge_schedule,
    format_telegram_bridge_schedule_plan,
    install_telegram_bridge_schedule,
    query_telegram_bridge_schedule,
    run_telegram_bridge_schedule_now,
)
from assistant.devctl.paths import CHECKPOINT_FILE, ensure_runtime_dirs
from assistant.devctl.recipes import get_recipe, list_recipes
from assistant.devctl.startup import run_startup, startup_steps_as_dicts
from assistant.tools.brief import configured_daily_brief_builder
from assistant.tools.file_search import configured_file_searcher
from assistant.tools.notes import configured_note_store
from assistant.tools.todos import configured_todo_store
from shared.python.pa_logging import get_logger


def _configure_console() -> None:
    errors = get_str("console.output_encoding_errors")
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors=errors)


_configure_console()


def _print_result(result: CommandResult) -> int:
    if result.stdout:
        print(result.stdout.rstrip())
    if result.stderr:
        print(result.stderr.rstrip(), file=sys.stderr)
    return result.returncode


def _runner(flow: str | None = None) -> OpenClawRunner:
    selected_flow = flow or get_str("flows.openclaw_runner")
    return OpenClawRunner(get_logger(selected_flow))


def _config_choices(key_path: str) -> list[str]:
    return [str(item) for item in get_list(key_path)]


def _default_model() -> str | None:
    if not get_bool("agent.use_configured_model_by_default"):
        return None
    if not get_bool("models.openclaw_agent_default_override_enabled"):
        return None
    return get_str("models.openclaw_agent_default") or None


def _control_timeout() -> int:
    return get_int("openclaw.default_control_timeout_seconds")


def _openclaw_action_timeout(action: str, requested_timeout: int | None) -> int:
    if requested_timeout is not None:
        return requested_timeout
    if action in {str(item) for item in get_list("openclaw.models_list_timeout_actions")}:
        return get_int("openclaw.default_models_list_timeout_seconds")
    if action in {str(item) for item in get_list("openclaw.doctor_report_timeout_actions")}:
        return get_int("openclaw.default_doctor_report_timeout_seconds")
    return _control_timeout()


def _agent_timeout() -> int:
    return get_int("openclaw.default_agent_timeout_seconds")


def _build_model_args(model: str | None) -> str | None:
    return model or None


def _resolve_agent_options(args: argparse.Namespace) -> tuple[str | None, str | None, str | None]:
    resolved = resolve_agent_call(
        role_name=getattr(args, "role", None),
        explicit_agent=getattr(args, "agent", None),
        explicit_model=_build_model_args(getattr(args, "model", None)),
        explicit_thinking=getattr(args, "thinking", None),
    )
    return resolved.agent, resolved.model, resolved.thinking


def _resolve_alias(alias_name: str) -> list[str]:
    aliases = get_table("aliases")
    if alias_name not in aliases:
        available = ", ".join(sorted(aliases))
        raise ValueError(f"Unknown alias '{alias_name}'. Available: {available}")
    value = aliases[alias_name]
    if not isinstance(value, list):
        raise TypeError(f"Alias '{alias_name}' must be a list in config.")
    return [str(item) for item in value]


def _devctl_command_path(args: argparse.Namespace) -> str:
    pieces: list[str] = []
    for field in [str(item) for item in get_list("devctl_logging.command_path_fields")]:
        value = getattr(args, field, None)
        if value:
            pieces.append(str(value))
    return " ".join(pieces) or get_str("devctl_logging.unknown_command_label")


def _run_logged_command(args: argparse.Namespace) -> int:
    if not get_bool("devctl_logging.enabled"):
        return int(args.func(args))

    log = get_logger(get_str("flows.devctl"))
    command_path = _devctl_command_path(args)
    log("devctl command started", command=command_path)
    try:
        returncode = int(args.func(args))
    except Exception as exc:
        message = str(exc)[: get_int("devctl_logging.exception_message_max_chars")]
        log("devctl command failed", level="ERROR", command=command_path, error=message)
        raise

    log(
        "devctl command finished",
        level="OK" if returncode == 0 else "ERROR",
        command=command_path,
        returncode=returncode,
    )
    return returncode


def cmd_openclaw(args: argparse.Namespace) -> int:
    log = get_logger(get_str("flows.openclaw_control"))
    runner = OpenClawRunner(log)
    timeout = _openclaw_action_timeout(args.action, args.timeout)

    if args.action == "doctor":
        return _print_result(runner.run(["doctor"], timeout_seconds=timeout))
    if args.action == "doctor-report":
        result = runner.run(["doctor"], timeout_seconds=timeout)
        artifact = runner.save_artifact(
            label=get_str("openclaw.doctor_report_label"),
            prompt=None,
            result=result,
            extra={"purpose": "OpenClaw doctor report capture"},
        )
        print(f"Artifact: {artifact}")
        return _print_result(result)
    if args.action == "health":
        return _print_result(runner.run(["health"], timeout_seconds=timeout))
    if args.action == "system-status":
        command = ["status"]
        if args.all:
            command.append("--all")
        if args.usage:
            command.append("--usage")
        return _print_result(runner.run(command, timeout_seconds=timeout))
    if args.action == "models-status":
        return _print_result(runner.run(["models", "status"], timeout_seconds=timeout))
    if args.action == "models-auth":
        return _print_result(runner.run(["models", "auth", "list"], timeout_seconds=timeout))
    if args.action == "models-list":
        return _print_result(
            runner.run(
                ["models", "list", "--provider", args.provider, "--plain"],
                timeout_seconds=timeout,
            )
        )
    if args.action == "models-validate":
        result = runner.run(
            ["models", "list", "--provider", args.provider, "--plain"],
            timeout_seconds=timeout,
        )
        visible_models = parse_model_list(result.stdout)
        checks = validate_configured_models(provider=args.provider, visible_models=visible_models)
        artifact = runner.save_artifact(
            label=get_str("models.validation.artifact_label"),
            prompt=None,
            result=result,
            extra={
                "provider": args.provider,
                "visible_model_count": len(visible_models),
                "checks": checks_as_dicts(checks),
            },
        )
        payload = {
            "artifact": str(artifact),
            "provider": args.provider,
            "visible_model_count": len(visible_models),
            "checks": checks_as_dicts(checks),
        }
        print(f"Artifact: {artifact}")
        if args.json:
            print(json.dumps(payload, indent=get_int("artifacts.json_indent")))
        else:
            print(format_model_checks(provider=args.provider, visible_models=visible_models, checks=checks))
        return result.returncode or returncode_for_checks(checks)
    if args.action == "doctor-triage":
        source = ""
        artifact = None
        if args.artifact:
            artifact = Path(args.artifact).expanduser().resolve()
            source = str(artifact)
            text = text_from_artifact(artifact)
        elif args.refresh:
            result = runner.run(["doctor"], timeout_seconds=timeout)
            artifact = runner.save_artifact(
                label=get_str("openclaw.doctor_report_label"),
                prompt=None,
                result=result,
                extra={"purpose": "OpenClaw doctor report capture"},
            )
            source = str(artifact)
            text = text_from_result(result.stdout, result.stderr)
        else:
            artifact = latest_doctor_artifact()
            if artifact is None and get_bool("doctor.refresh_when_no_artifact"):
                result = runner.run(["doctor"], timeout_seconds=timeout)
                artifact = runner.save_artifact(
                    label=get_str("openclaw.doctor_report_label"),
                    prompt=None,
                    result=result,
                    extra={"purpose": "OpenClaw doctor report capture"},
                )
                source = str(artifact)
                text = text_from_result(result.stdout, result.stderr)
            elif artifact is not None:
                source = str(artifact)
                text = text_from_artifact(artifact)
            else:
                print("No doctor report artifact found.", file=sys.stderr)
                return 2

        issues = triage_text(text)
        payload = {
            "source": source,
            "issue_count": len(issues),
            "issues": issues_as_dicts(issues),
        }
        if args.json:
            print(json.dumps(payload, indent=get_int("doctor.json_indent")))
        else:
            print(format_issues(source=source, issues=issues))
        return 0
    if args.action == "doctor-review":
        result = runner.run(["doctor"], timeout_seconds=timeout)
        artifact = runner.save_artifact(
            label=get_str("openclaw.doctor_report_label"),
            prompt=None,
            result=result,
            extra={"purpose": "OpenClaw doctor review capture"},
        )
        issues = triage_text(text_from_result(result.stdout, result.stderr))
        payload = {
            "artifact": str(artifact),
            "issue_count": len(issues),
            "issues": issues_as_dicts(issues),
            "automatic_fix_applied": False,
        }
        if args.json:
            print(json.dumps(payload, indent=get_int("doctor.json_indent")))
        else:
            print(format_issues(source=str(artifact), issues=issues))
            print()
            print("No fixes were applied. Use doctor-fix-safe --confirm only after reviewing this output.")
        return 0
    if args.action == "doctor-fix-safe":
        if get_bool("doctor.safe_fix_requires_confirm") and not args.confirm:
            print(get_str("doctor.safe_fix_refusal_message"))
            return 2
        result = runner.run([str(item) for item in get_list("doctor.safe_fix_command")], timeout_seconds=timeout)
        artifact = runner.save_artifact(
            label=get_str("doctor.safe_fix_artifact_label"),
            prompt=None,
            result=result,
            extra={"purpose": "OpenClaw non-interactive doctor safe fix"},
        )
        print(f"Artifact: {artifact}")
        return _print_result(result)

    targets = ["gateway", "node"] if args.target == "both" else [args.target]
    rc = 0
    for target in targets:
        result = runner.run([target, args.action], timeout_seconds=timeout)
        rc = max(rc, _print_result(result))
    return rc


def cmd_ask(args: argparse.Namespace) -> int:
    message = " ".join(args.message).strip()
    if not message:
        print("No message provided.", file=sys.stderr)
        return 2

    runner = _runner(get_str("flows.openclaw_ask"))
    agent, model, thinking = _resolve_agent_options(args)
    result = runner.agent(
        message,
        model=model,
        local=args.local,
        agent=agent,
        session_id=args.session_id,
        to=args.to,
        deliver=args.deliver,
        thinking=thinking,
        timeout_seconds=args.timeout,
    )
    artifact = runner.save_artifact(label="ask", prompt=message, result=result)
    print(f"Artifact: {artifact}")
    return _print_result(result)


def cmd_run_recipe(args: argparse.Namespace) -> int:
    recipe = get_recipe(args.recipe)
    if args.dry_run:
        print(recipe.prompt)
        return 0

    runner = _runner(get_str("flows.openclaw_recipe"))
    agent, model, thinking = _resolve_agent_options(args)
    result = runner.agent(
        recipe.prompt,
        model=model,
        local=args.local,
        agent=agent,
        session_id=args.session_id,
        to=args.to,
        deliver=args.deliver,
        thinking=thinking,
        timeout_seconds=args.timeout,
    )
    artifact = runner.save_artifact(
        label=recipe.name,
        prompt=recipe.prompt,
        result=result,
        extra={"description": recipe.description},
    )
    print(f"Recipe: {recipe.name}")
    print(f"Artifact: {artifact}")
    return _print_result(result)


def cmd_recipes(_: argparse.Namespace) -> int:
    for recipe in list_recipes():
        print(f"{recipe.name}: {recipe.description}")
    return 0


def cmd_agents(args: argparse.Namespace) -> int:
    log = get_logger(get_str("flows.openclaw_agents"))
    runner = OpenClawRunner(log)
    if args.agents_command == "list":
        payload: dict[str, object] = {"configured": roles_as_dicts()}
        if args.actual:
            result = runner.run(["agents", "list", "--json"], timeout_seconds=args.timeout)
            payload["actual_returncode"] = result.returncode
            payload["actual_stdout"] = result.stdout
            payload["actual_stderr"] = result.stderr
        print(json.dumps(payload, indent=get_int("artifacts.json_indent")))
        log("agent roles listed", level="OK", count=len(configured_roles()), actual=args.actual)
        return 0
    if args.agents_command == "ensure":
        results = ensure_configured_agents(runner=runner, log=log, timeout_seconds=args.timeout)
        print(json.dumps(ensure_results_as_dicts(results), indent=get_int("artifacts.json_indent")))
        return max([result.returncode for result in results] or [0])
    if args.agents_command == "smoke":
        rc = 0
        for role in configured_roles():
            if args.role and role.name != args.role:
                continue
            prompt = get_str("agent.routing.smoke_message_template").format(role=role.name)
            result = runner.agent(
                prompt,
                agent=role.agent,
                thinking=role.thinking,
                timeout_seconds=args.timeout or get_int("agent.routing.smoke_timeout_seconds"),
            )
            artifact = runner.save_artifact(label=f"agent-role-{role.name}", prompt=prompt, result=result)
            print(f"{role.name}: returncode={result.returncode} artifact={artifact}")
            _print_result(result)
            rc = max(rc, result.returncode)
        return rc
    return 2


def cmd_mobile_capture(args: argparse.Namespace) -> int:
    log = get_logger(get_str("flows.mobile_command"))
    command = capture_command(
        source=args.source,
        sender=args.sender,
        channel=args.channel or get_str("mobile.default_channel"),
        text=args.message,
        log=log,
    )
    print(f"Captured {command.id} from {command.source}; status={command.status}")
    return 0


def cmd_mobile_list(args: argparse.Namespace) -> int:
    commands = list_commands(status=args.status)
    if args.json:
        print(json.dumps([command.__dict__ for command in commands], indent=2))
        return 0

    for command in commands[: args.limit]:
        preview = command.text.replace("\r", " ").replace("\n", " ")[: get_int("mobile.preview_chars")]
        print(
            f"{command.id} | {command.status} | {command.received_at} | "
            f"{command.source}/{command.channel or '-'} | {preview}"
        )
    if not commands:
        print("No mobile commands found.")
    return 0


def cmd_mobile_drain(args: argparse.Namespace) -> int:
    log = get_logger(get_str("flows.mobile_command"))
    runner = OpenClawRunner(log)
    processed = 0
    rc = 0

    for command in pending_commands(args.limit):
        processed += 1
        if args.dry_run:
            print(f"DRY RUN {command.id}: {command.text}")
            continue

        mark_command(command.id, status=get_str("mobile.processing_status"), log=log)
        agent, model, thinking = _resolve_agent_options(args)
        result = runner.agent(
            command.text,
            model=model,
            local=args.local,
            agent=agent,
            session_id=args.session_id,
            to=args.to,
            deliver=args.deliver,
            thinking=thinking,
            timeout_seconds=args.timeout,
        )
        artifact = runner.save_artifact(
            label=f"mobile-{command.id}",
            prompt=command.text,
            result=result,
            extra={
                "source": command.source,
                "channel": command.channel,
                "received_at": command.received_at,
            },
        )
        if result.returncode == 0:
            mark_command(command.id, status=get_str("mobile.completed_status"), log=log, artifact=str(artifact))
        else:
            rc = max(rc, result.returncode)
            mark_command(
                command.id,
                status=get_str("mobile.failed_status"),
                log=log,
                artifact=str(artifact),
                error=result.stderr or result.stdout,
            )
        _print_result(result)

    if processed == 0:
        print("No pending mobile commands.")
    return rc


def cmd_mobile_mark(args: argparse.Namespace) -> int:
    log = get_logger(get_str("flows.mobile_command"))
    mark_command(args.command_id, status=args.status, log=log, artifact=args.artifact, error=args.error)
    print(f"Marked {args.command_id} as {args.status}")
    return 0


def cmd_mobile_webhook_info(_: argparse.Namespace) -> int:
    payload = {
        "enabled": get_bool("mobile_bridge.enabled"),
        "capture_url": bridge_url(),
        "health_url": health_url(),
        "token_required": get_bool("mobile_bridge.require_token"),
        "auth_header": get_str("mobile_bridge.auth_header"),
        "token_env_var": get_str("mobile_bridge.token_env_var"),
        "bind_warning": get_str("mobile_bridge.bind_warning"),
    }
    print(json.dumps(payload, indent=get_int("mobile_bridge.json_indent")))
    get_logger(get_str("flows.mobile_bridge"))("mobile bridge info requested", level="OK", url=payload["capture_url"])
    return 0


def cmd_mobile_webhook_serve(args: argparse.Namespace) -> int:
    if not get_bool("mobile_bridge.enabled"):
        print("Mobile bridge is disabled in config.", file=sys.stderr)
        return 2
    serve_mobile_bridge(log=get_logger(get_str("flows.mobile_bridge")), once=args.once)
    return 0


def cmd_mobile_external_info(args: argparse.Namespace) -> int:
    payload = external_info()
    if args.json:
        print(json.dumps(payload, indent=get_int("mobile_bridge.json_indent")))
    else:
        for check in readiness_as_dicts():
            print(f"{check['id']}: {check['status']} - {check['message']}")
    get_logger(get_str("flows.mobile_external"))("mobile external readiness requested", level="OK")
    return 0


def cmd_mobile_channel_status(args: argparse.Namespace) -> int:
    log = get_logger(get_str("flows.mobile_external"))
    runner = OpenClawRunner(log)
    command = ["channels", "status", "--timeout", str(args.timeout_ms)]
    if args.json:
        command.append("--json")
    return _print_result(runner.run(command, timeout_seconds=max(30, int(args.timeout_ms / 1000) + 10)))


def cmd_mobile_channel_login(args: argparse.Namespace) -> int:
    if get_bool("mobile_channel.login_requires_confirm") and not args.confirm:
        print("Channel login can open an interactive auth/QR flow. Re-run with --confirm after choosing the channel.")
        return 2
    channel = args.channel or get_str("mobile_channel.channel")
    if not channel:
        print("No mobile channel configured. Pass --channel or set [mobile_channel].channel.", file=sys.stderr)
        return 2
    log = get_logger(get_str("flows.mobile_external"))
    runner = OpenClawRunner(log)
    command = ["channels", "login", "--channel", channel]
    account = args.account or get_str("mobile_channel.account")
    if account:
        command.extend(["--account", account])
    if args.verbose:
        command.append("--verbose")
    return _print_result(runner.run(command, timeout_seconds=args.timeout))


def cmd_mobile_channel_qr(args: argparse.Namespace) -> int:
    log = get_logger(get_str("flows.mobile_external"))
    runner = OpenClawRunner(log)
    command = ["qr", "--json" if args.json else "--setup-code-only"]
    public_url = args.public_url or get_str("mobile_channel.qr_public_url") or get_str("mobile_external.public_url")
    if public_url:
        command.extend(["--public-url", public_url])
    return _print_result(runner.run(command, timeout_seconds=args.timeout))


def cmd_mobile_token(args: argparse.Namespace) -> int:
    log = get_logger(get_str("flows.mobile_secret"))
    if args.mobile_token_command == "status":
        status = token_status(check_s3=args.check_s3)
        print(json.dumps(token_status_as_dict(status), indent=get_int("artifacts.json_indent")))
        log(
            "telegram token status requested",
            level="OK",
            local_exists=status.local_exists,
            s3_checked=status.s3_checked,
            s3_exists=status.s3_exists,
        )
        return 0
    if args.mobile_token_command == "backup-s3":
        return backup_token_to_s3(log=log, confirm=args.confirm)
    if args.mobile_token_command == "restore-s3":
        return restore_token_from_s3(log=log, confirm=args.confirm, overwrite=args.overwrite)
    print("Unknown mobile token command.", file=sys.stderr)
    return 2


def cmd_mobile_owner(args: argparse.Namespace) -> int:
    log = get_logger(get_str("flows.mobile_external"))
    runner = OpenClawRunner(log)
    if args.mobile_owner_command == "status":
        status = command_owner_status(runner=runner, timeout_seconds=args.timeout)
        if args.json:
            print(json.dumps(command_owner_status_as_dict(status), indent=get_int("artifacts.json_indent")))
        else:
            print(f"configured: {status.configured}")
            print(f"owner_count: {status.owner_count}")
            print(f"owners: {', '.join(status.owners) if status.owners else '-'}")
            if status.message:
                print(f"message: {status.message}")
        log("mobile command owner status requested", level="OK", configured=status.configured)
        return 0
    if args.mobile_owner_command == "set":
        owners = configured_owner_values(args.owner)
        try:
            result = set_command_owners(
                runner=runner,
                owners=owners,
                log=log,
                confirm=args.confirm,
                restart=not args.no_restart,
                timeout_seconds=args.timeout,
            )
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        if result is None:
            return 2
        if args.json:
            print(json.dumps(command_owner_set_result_as_dict(result), indent=get_int("artifacts.json_indent")))
        else:
            print(f"owners: {', '.join(result.owners)}")
            print(f"returncode: {result.returncode}")
        return result.returncode
    return 2


def cmd_archive(args: argparse.Namespace) -> int:
    log = get_logger(get_str("flows.archive"))
    if args.archive_command == "plan":
        print(format_archive_plan(build_archive_plan()))
        log("archive plan requested", level="OK")
        return 0
    if args.archive_command == "create":
        if args.dry_run:
            print(format_archive_plan(build_archive_plan()))
            log("archive dry run requested", level="OK")
            return 0
        plan = create_archive(log=log)
        print(format_archive_plan(plan))
        if args.upload_s3:
            return upload_archive_to_s3(plan, log=log, confirm=args.confirm)
        return 0
    if args.archive_command == "lifecycle-plan":
        print(format_s3_archive_policy_plan())
        log("s3 archive policy plan requested", level="OK")
        return 0
    if args.archive_command == "lifecycle-apply":
        return apply_s3_archive_policy(log=log, confirm=args.confirm)
    if args.archive_command == "lifecycle-verify":
        payload = s3_archive_policy_status_as_dict(log=log)
        print(json.dumps(payload, indent=get_int("archive.json_indent")))
        return 0 if all(payload["checks"].values()) else 1
    if args.archive_command == "schedule-plan":
        print(format_archive_schedule_plan())
        log("archive schedule plan requested", level="OK")
        return 0
    if args.archive_command == "schedule-install":
        return install_archive_schedule(log=log, confirm=args.confirm)
    if args.archive_command == "schedule-status":
        return query_archive_schedule(log=log)
    if args.archive_command == "schedule-delete":
        return delete_archive_schedule(log=log, confirm=args.confirm)
    if args.archive_command == "schedule-run-now":
        return run_archive_schedule_now(log=log, confirm=args.confirm)
    return 2


def cmd_watchdog(args: argparse.Namespace) -> int:
    log = get_logger(get_str("flows.openclaw_watchdog"))
    runner = OpenClawRunner(log)
    if args.watchdog_command == "status":
        status = check_watchdog_status(runner=runner, log=log)
        if args.json:
            print(json.dumps(watchdog_status_as_dict(status), indent=get_int("artifacts.json_indent")))
        else:
            print(format_watchdog_status(status))
        log("watchdog status requested", level="OK" if status.healthy else "WARN", healthy=status.healthy)
        return get_int("openclaw_watchdog.status_returncode_healthy") if status.healthy else get_int("openclaw_watchdog.status_returncode_unhealthy")
    if args.watchdog_command == "run":
        result = run_watchdog(runner=runner, log=log)
        if args.json:
            print(json.dumps(watchdog_run_as_dict(result), indent=get_int("artifacts.json_indent")))
        else:
            print(format_watchdog_run(result))
        return result.returncode
    if args.watchdog_command == "schedule-plan":
        print(format_openclaw_watchdog_schedule_plan())
        log("watchdog schedule plan requested", level="OK")
        return 0
    if args.watchdog_command == "schedule-install":
        return install_openclaw_watchdog_schedule(log=log, confirm=args.confirm)
    if args.watchdog_command == "schedule-status":
        return query_openclaw_watchdog_schedule(log=log)
    if args.watchdog_command == "schedule-delete":
        return delete_openclaw_watchdog_schedule(log=log, confirm=args.confirm)
    if args.watchdog_command == "schedule-run-now":
        return run_openclaw_watchdog_schedule_now(log=log, confirm=args.confirm)
    return 2


def cmd_bridge(args: argparse.Namespace) -> int:
    log = get_logger(get_str("flows.telegram_bridge"))
    if args.bridge_command == "status":
        status = check_bridge_status(log=log)
        if args.json:
            print(json.dumps(bridge_status_as_dict(status), indent=get_int("artifacts.json_indent")))
        else:
            print(f"enabled: {status.enabled}")
            print(f"token_file_exists: {status.token_file_exists}")
            print(f"token_file_path: {status.token_file_path}")
            print(f"rule_count: {status.rule_count}")
            print(f"owner_source: {status.owner_source}")
            print(f"owner_ids_configured: {status.owner_ids_configured}")
            print(f"offset_state_path: {status.offset_state_path}")
            print(f"offset: {status.offset}")
        return get_int("telegram_bridge.status_returncode_healthy") if (status.enabled and status.token_file_exists) else get_int("telegram_bridge.status_returncode_unhealthy")
    if args.bridge_command == "rules":
        payload = configured_rules_as_dicts()
        if args.json:
            print(json.dumps(payload, indent=get_int("artifacts.json_indent")))
        else:
            for rule in payload:
                patterns = ", ".join(rule.get("patterns", []) or [])
                print(f"{rule['id']} ({rule['kind']}) match={rule['match']} patterns=[{patterns}] - {rule.get('description', '')}")
        return 0
    if args.bridge_command == "run":
        if not get_bool("telegram_bridge.enabled"):
            print("Telegram bridge is disabled in config.", file=sys.stderr)
            return 2
        bridge = build_bridge(log=log)
        if args.once:
            outcome = bridge.poll_once()
            if args.json:
                print(json.dumps(cycle_outcome_as_dict(outcome), indent=get_int("artifacts.json_indent")))
            else:
                print(
                    f"polled={outcome.polled} updates={outcome.updates} "
                    f"processed={outcome.processed} skipped={outcome.skipped_unauthorized} "
                    f"sent_ok={outcome.sent_ok} sent_failed={outcome.sent_failed} "
                    f"error={outcome.error}"
                )
            return 0 if outcome.polled and outcome.sent_failed == 0 else 1
        return bridge.run_forever()
    if args.bridge_command == "schedule-plan":
        print(format_telegram_bridge_schedule_plan())
        log("telegram bridge schedule plan requested", level="OK")
        return 0
    if args.bridge_command == "schedule-install":
        return install_telegram_bridge_schedule(log=log, confirm=args.confirm)
    if args.bridge_command == "schedule-status":
        return query_telegram_bridge_schedule(log=log)
    if args.bridge_command == "schedule-delete":
        return delete_telegram_bridge_schedule(log=log, confirm=args.confirm)
    if args.bridge_command == "schedule-run-now":
        return run_telegram_bridge_schedule_now(log=log, confirm=args.confirm)
    return 2


def cmd_startup(args: argparse.Namespace) -> int:
    log = get_logger(get_str("flows.startup"))
    runner = OpenClawRunner(log)
    steps = run_startup(runner=runner, log=log)
    if args.json:
        print(json.dumps(startup_steps_as_dicts(steps), indent=get_int("artifacts.json_indent")))
    else:
        for step in steps:
            print(f"{step.action}: {step.status} - {step.message}")
    return 0 if all(step.status in {"ok", "skipped"} for step in steps) else 1


def cmd_logs_tail(args: argparse.Namespace) -> int:
    log = get_logger(get_str("flows.log_inspector"))
    lines = log_inspector.tail_lines(
        source=args.source,
        lines=args.lines,
        contains=args.contains,
        errors_only=args.errors_only,
        max_files=args.max_files,
    )
    log(
        "log tail requested",
        level="OK",
        source=args.source,
        lines=args.lines,
        returned=len(lines),
        errors_only=args.errors_only,
    )
    print(log_inspector.format_log_lines(lines) or "No matching log lines.")
    return 0


def cmd_logs_errors(args: argparse.Namespace) -> int:
    log = get_logger(get_str("flows.log_inspector"))
    lines = log_inspector.error_lines(source=args.source, limit=args.limit)
    log("log error summary requested", level="OK", source=args.source, returned=len(lines))
    print(log_inspector.format_log_lines(lines) or "No warning/error lines found.")
    return 0


def cmd_logs_summary(args: argparse.Namespace) -> int:
    summary = log_inspector.summarize_logs(source=args.source)
    get_logger(get_str("flows.log_inspector"))(
        "log structured summary requested",
        level="OK",
        source=args.source,
        file_count=summary["file_count"],
        warn_error_count=summary["warn_error_count_in_tails"],
    )
    print(json.dumps(summary, indent=2))
    return 0


def cmd_task(args: argparse.Namespace) -> int:
    log = get_logger(get_str("flows.laptop_task"))
    if args.task_command == "list":
        payload = task_definitions_as_dicts()
        if args.json:
            print(json.dumps(payload, indent=get_int("laptop_tasks.json_indent")))
        else:
            for task in payload:
                confirmation = " confirm" if task["requires_confirm"] else ""
                print(f"{task['name']} ({task['kind']}{confirmation}): {task['description']}")
        log("laptop tasks listed", level="OK", count=len(payload))
        return 0

    if args.task_command == "show":
        payload = task_definition(args.task_name)
        print(json.dumps(payload, indent=get_int("laptop_tasks.json_indent")))
        log("laptop task shown", level="OK", task=args.task_name)
        return 0

    if args.task_command == "run":
        result = run_laptop_task(
            args.task_name,
            dry_run=args.dry_run,
            confirm=args.confirm,
            send_telegram=args.send_telegram,
            log=log,
        )
        if args.json:
            print(json.dumps(task_result_as_dict(result), indent=get_int("laptop_tasks.json_indent")))
        else:
            print(format_task_result(result))
        return result.returncode

    return 2


def cmd_notes_add(args: argparse.Namespace) -> int:
    log = get_logger(get_str("flows.notes_tool"))
    record = configured_note_store().add(title=args.title, body=args.body, tags=args.tag, log=log)
    print(f"Added note {record.id}")
    print(record.path)
    return 0


def cmd_notes_list(args: argparse.Namespace) -> int:
    records = configured_note_store().list(limit=args.limit, tag=args.tag)
    if args.json:
        print(json.dumps([asdict(record) for record in records], indent=2))
        return 0
    for record in records:
        print(f"{record.id} | {record.created_at} | {','.join(record.tags)} | {record.title}")
    if not records:
        print("No notes found.")
    get_logger(get_str("flows.notes_tool"))("notes listed", level="OK", count=len(records), tag=args.tag or "")
    return 0


def cmd_notes_show(args: argparse.Namespace) -> int:
    record, content = configured_note_store().get(args.note_id)
    print(content.rstrip())
    get_logger(get_str("flows.notes_tool"))("note shown", level="OK", note_id=record.id, chars=len(content))
    return 0


def cmd_notes_search(args: argparse.Namespace) -> int:
    matches = configured_note_store().search(query=args.query, limit=args.limit)
    if args.json:
        print(
            json.dumps(
                [{"note": asdict(record), "preview": preview} for record, preview in matches],
                indent=2,
            )
        )
        return 0
    for record, preview in matches:
        print(f"{record.id} | {record.created_at} | {record.title}")
        print(f"  {preview}")
    if not matches:
        print("No matching notes found.")
    get_logger(get_str("flows.notes_tool"))("notes searched", level="OK", query_chars=len(args.query), matches=len(matches))
    return 0


def cmd_todos_add(args: argparse.Namespace) -> int:
    log = get_logger(get_str("flows.todos_tool"))
    record = configured_todo_store().add(
        title=args.title,
        details=args.details or "",
        priority=args.priority,
        due=args.due or "",
        log=log,
    )
    print(f"Added todo {record.id}")
    return 0


def cmd_todos_list(args: argparse.Namespace) -> int:
    records = configured_todo_store().list(status=args.status, limit=args.limit)
    if args.json:
        print(json.dumps([asdict(record) for record in records], indent=2))
        return 0
    for record in records:
        due = f" due={record.due}" if record.due else ""
        print(f"{record.id} | {record.status} | {record.priority}{due} | {record.title}")
    if not records:
        print("No todos found.")
    get_logger(get_str("flows.todos_tool"))("todos listed", level="OK", status=args.status, count=len(records))
    return 0


def cmd_todos_done(args: argparse.Namespace) -> int:
    log = get_logger(get_str("flows.todos_tool"))
    record = configured_todo_store().set_status(
        todo_id=args.todo_id,
        status=get_str("tools.todos.completed_status"),
        log=log,
    )
    print(f"Marked {record.id} as {record.status}")
    return 0


def cmd_todos_reopen(args: argparse.Namespace) -> int:
    log = get_logger(get_str("flows.todos_tool"))
    record = configured_todo_store().set_status(
        todo_id=args.todo_id,
        status=get_str("tools.todos.default_status"),
        log=log,
    )
    print(f"Marked {record.id} as {record.status}")
    return 0


def cmd_todos_cancel(args: argparse.Namespace) -> int:
    log = get_logger(get_str("flows.todos_tool"))
    record = configured_todo_store().set_status(
        todo_id=args.todo_id,
        status=get_str("tools.todos.cancelled_status"),
        log=log,
    )
    print(f"Marked {record.id} as {record.status}")
    return 0


def cmd_files_search(args: argparse.Namespace) -> int:
    log = get_logger(get_str("flows.file_search"))
    matches = configured_file_searcher().search(scope=args.scope, query=args.query, limit=args.limit, log=log)
    if args.json:
        print(json.dumps([asdict(match) for match in matches], indent=2))
        return 0
    for match in matches:
        print(f"{match.path}:{match.line_number}: {match.line}")
    if not matches:
        print("No matching files found.")
    return 0


def cmd_brief_daily(args: argparse.Namespace) -> int:
    result = configured_daily_brief_builder().build(log=get_logger(get_str("flows.daily_brief")))
    print(f"Generated {result.title}")
    print(result.path)
    if args.show:
        with open(result.path, "r", encoding="utf-8") as handle:
            print(handle.read().rstrip())
    return 0


def cmd_latest(args: argparse.Namespace) -> int:
    if CHECKPOINT_FILE.exists():
        print("# Checkpoint")
        print("\n".join(CHECKPOINT_FILE.read_text(encoding="utf-8").splitlines()[: args.checkpoint_lines]))
        print()

    print("# Recent Personal Assistant Log Lines")
    lines = log_inspector.tail_lines(
        source=get_str("logs.default_source"),
        lines=args.log_lines,
        max_files=get_int("logs.latest_log_max_files"),
    )
    print(log_inspector.format_log_lines(lines) or "No recent log lines found.")
    get_logger(get_str("flows.devctl"))("latest update requested", level="OK", log_lines=args.log_lines)
    return 0


def cmd_smoke(_: argparse.Namespace) -> int:
    ensure_runtime_dirs()
    log = get_logger(get_str("flows.devctl_smoke"))
    log("smoke started")
    runner = OpenClawRunner(log)
    result = runner.run(["--version"], timeout_seconds=get_int("openclaw.default_smoke_timeout_seconds"))
    log("smoke finished", level="OK" if result.returncode == 0 else "ERROR", returncode=result.returncode)
    return _print_result(result)


def cmd_alias(args: argparse.Namespace) -> int:
    return main([*_resolve_alias(args.alias_name), *args.alias_args])


def _add_agent_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--role", choices=role_names(), default=get_str("agent.routing.default_role"), help="Configured assistant role")
    parser.add_argument("--model", default=_default_model(), help=get_str("agent.model_help"))
    parser.add_argument("--local", action="store_true", help="Run embedded local agent mode")
    parser.add_argument("--agent", help="OpenClaw agent id override")
    parser.add_argument("--session-id", help="OpenClaw session id")
    parser.add_argument("--to", help="Recipient/session target for routed agent sessions")
    parser.add_argument("--deliver", action="store_true", help="Deliver reply to the selected channel/target")
    parser.add_argument(
        "--thinking",
        choices=_config_choices("agent.thinking_levels"),
        help="OpenClaw thinking level when supported",
    )
    parser.add_argument("--timeout", type=int, default=_agent_timeout(), help="Timeout in seconds")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pa", description="Personal Assistant control plane")
    subparsers = parser.add_subparsers(dest="command", required=True)

    openclaw = subparsers.add_parser("openclaw", help="Control OpenClaw gateway/node/status commands")
    openclaw.add_argument(
        "action",
        choices=[
            *_config_choices("openclaw.control_actions"),
        ],
    )
    openclaw.add_argument(
        "--target",
        choices=_config_choices("openclaw.service_targets"),
        default=get_str("openclaw.default_service_target"),
    )
    openclaw.add_argument("--provider", default=get_str("openclaw.default_provider"), help="Provider for models-list")
    openclaw.add_argument("--all", action="store_true", help="Use openclaw status --all with system-status")
    openclaw.add_argument("--usage", action="store_true", help="Use openclaw status --usage with system-status")
    openclaw.add_argument("--refresh", action="store_true", help="Refresh source data for diagnostic actions")
    openclaw.add_argument("--artifact", help="Use a specific saved artifact for diagnostic actions")
    openclaw.add_argument("--json", action="store_true", help="Print diagnostic output as JSON when supported")
    openclaw.add_argument("--confirm", action="store_true", help="Confirm a gated safe action")
    openclaw.add_argument("--timeout", type=int)
    openclaw.set_defaults(func=cmd_openclaw)

    agents = subparsers.add_parser("agents", help="Manage configured OpenClaw agent roles")
    agents_sub = agents.add_subparsers(dest="agents_command", required=True)

    agents_list = agents_sub.add_parser("list", help="List configured agent roles")
    agents_list.add_argument("--actual", action="store_true", help="Also query OpenClaw actual agents")
    agents_list.add_argument("--timeout", type=int, default=get_int("openclaw.default_control_timeout_seconds"))
    agents_list.set_defaults(func=cmd_agents)

    agents_ensure = agents_sub.add_parser("ensure", help="Create or repair configured OpenClaw agent roles")
    agents_ensure.add_argument("--timeout", type=int, default=get_int("openclaw.default_control_timeout_seconds"))
    agents_ensure.set_defaults(func=cmd_agents)

    agents_smoke = agents_sub.add_parser("smoke", help="Run a tiny prompt through configured agent roles")
    agents_smoke.add_argument("--role", choices=role_names())
    agents_smoke.add_argument("--timeout", type=int, default=get_int("agent.routing.smoke_timeout_seconds"))
    agents_smoke.set_defaults(func=cmd_agents)

    ask = subparsers.add_parser("ask", help="Send one prompt to OpenClaw agent")
    ask.add_argument("message", nargs="+")
    _add_agent_options(ask)
    ask.set_defaults(func=cmd_ask)

    run_recipe = subparsers.add_parser("run", help="Run a named OpenClaw prompt recipe")
    run_recipe.add_argument("recipe", choices=[recipe.name for recipe in list_recipes()])
    run_recipe.add_argument("--dry-run", action="store_true", help="Print the generated prompt only")
    _add_agent_options(run_recipe)
    run_recipe.set_defaults(func=cmd_run_recipe)

    recipes = subparsers.add_parser("recipes", help="List available prompt recipes")
    recipes.set_defaults(func=cmd_recipes)

    alias = subparsers.add_parser("alias", help="Run a configured command alias")
    alias.add_argument("alias_name", choices=sorted(get_table("aliases")))
    alias.add_argument("alias_args", nargs=argparse.REMAINDER)
    alias.set_defaults(func=cmd_alias)

    mobile = subparsers.add_parser("mobile", help="Capture and process mobile-originated commands")
    mobile_sub = mobile.add_subparsers(dest="mobile_command", required=True)

    mobile_capture = mobile_sub.add_parser("capture", help="Capture a command from a mobile bridge")
    mobile_capture.add_argument("--source", default=get_str("mobile.default_source"))
    mobile_capture.add_argument("--sender", default=get_str("mobile.default_sender"))
    mobile_capture.add_argument("--channel", default=get_str("mobile.default_channel"))
    mobile_capture.add_argument("--message", required=True)
    mobile_capture.set_defaults(func=cmd_mobile_capture)

    mobile_list = mobile_sub.add_parser("list", help="List captured mobile commands")
    mobile_list.add_argument("--status", choices=_config_choices("mobile.statuses"), default=_config_choices("mobile.statuses")[0])
    mobile_list.add_argument("--limit", type=int, default=get_int("mobile.list_limit_default"))
    mobile_list.add_argument("--json", action="store_true")
    mobile_list.set_defaults(func=cmd_mobile_list)

    mobile_drain = mobile_sub.add_parser("drain", help="Send pending mobile commands to OpenClaw")
    mobile_drain.add_argument("--limit", type=int, default=get_int("mobile.drain_limit_default"))
    mobile_drain.add_argument("--dry-run", action="store_true")
    _add_agent_options(mobile_drain)
    mobile_drain.set_defaults(func=cmd_mobile_drain)

    mobile_mark = mobile_sub.add_parser("mark", help="Manually update a captured mobile command status")
    mobile_mark.add_argument("command_id")
    mobile_mark.add_argument("--status", choices=_config_choices("mobile.mark_statuses"), required=True)
    mobile_mark.add_argument("--artifact")
    mobile_mark.add_argument("--error")
    mobile_mark.set_defaults(func=cmd_mobile_mark)

    mobile_webhook = mobile_sub.add_parser("webhook", help="Run or inspect the local mobile webhook bridge")
    mobile_webhook_sub = mobile_webhook.add_subparsers(dest="mobile_webhook_command", required=True)

    mobile_webhook_info = mobile_webhook_sub.add_parser("info", help="Print mobile webhook bridge endpoint details")
    mobile_webhook_info.set_defaults(func=cmd_mobile_webhook_info)

    mobile_webhook_serve = mobile_webhook_sub.add_parser("serve", help="Serve the local mobile webhook bridge")
    mobile_webhook_serve.add_argument("--once", action="store_true", help="Handle one request and exit")
    mobile_webhook_serve.set_defaults(func=cmd_mobile_webhook_serve)

    mobile_external = mobile_sub.add_parser("external", help="Inspect external mobile exposure readiness")
    mobile_external_sub = mobile_external.add_subparsers(dest="mobile_external_command", required=True)

    mobile_external_info = mobile_external_sub.add_parser("info", help="Show external mobile readiness")
    mobile_external_info.add_argument("--json", action="store_true")
    mobile_external_info.set_defaults(func=cmd_mobile_external_info)

    mobile_channel = mobile_sub.add_parser("channel", help="Inspect or start OpenClaw mobile channel flows")
    mobile_channel_sub = mobile_channel.add_subparsers(dest="mobile_channel_command", required=True)

    mobile_channel_status = mobile_channel_sub.add_parser("status", help="Run OpenClaw channel status")
    mobile_channel_status.add_argument("--json", action="store_true")
    mobile_channel_status.add_argument("--timeout-ms", type=int, default=get_int("mobile_channel.status_timeout_ms"))
    mobile_channel_status.set_defaults(func=cmd_mobile_channel_status)

    mobile_channel_login = mobile_channel_sub.add_parser("login", help="Start an OpenClaw channel login flow")
    mobile_channel_login.add_argument("--channel", choices=_config_choices("mobile_channel.supported_channels"))
    mobile_channel_login.add_argument("--account")
    mobile_channel_login.add_argument("--verbose", action="store_true")
    mobile_channel_login.add_argument("--confirm", action="store_true")
    mobile_channel_login.add_argument("--timeout", type=int, default=get_int("openclaw.default_agent_timeout_seconds"))
    mobile_channel_login.set_defaults(func=cmd_mobile_channel_login)

    mobile_channel_qr = mobile_channel_sub.add_parser("qr", help="Generate OpenClaw mobile setup QR/code")
    mobile_channel_qr.add_argument("--json", action="store_true")
    mobile_channel_qr.add_argument("--public-url")
    mobile_channel_qr.add_argument("--timeout", type=int, default=get_int("openclaw.default_control_timeout_seconds"))
    mobile_channel_qr.set_defaults(func=cmd_mobile_channel_qr)

    mobile_token = mobile_sub.add_parser("token", help="Inspect or restore Telegram token local/S3 fallback")
    mobile_token_sub = mobile_token.add_subparsers(dest="mobile_token_command", required=True)

    mobile_token_status = mobile_token_sub.add_parser("status", help="Show local token and optional S3 fallback status")
    mobile_token_status.add_argument("--check-s3", action="store_true")
    mobile_token_status.set_defaults(func=cmd_mobile_token)

    mobile_token_backup = mobile_token_sub.add_parser("backup-s3", help="Upload local Telegram token file to configured S3 fallback")
    mobile_token_backup.add_argument("--confirm", action="store_true")
    mobile_token_backup.set_defaults(func=cmd_mobile_token)

    mobile_token_restore = mobile_token_sub.add_parser("restore-s3", help="Restore Telegram token file from configured S3 fallback")
    mobile_token_restore.add_argument("--confirm", action="store_true")
    mobile_token_restore.add_argument("--overwrite", action="store_true")
    mobile_token_restore.set_defaults(func=cmd_mobile_token)

    mobile_owner = mobile_sub.add_parser("owner", help="Inspect or set OpenClaw command owner allowlist")
    mobile_owner_sub = mobile_owner.add_subparsers(dest="mobile_owner_command", required=True)

    mobile_owner_status = mobile_owner_sub.add_parser("status", help="Show OpenClaw command-owner status")
    mobile_owner_status.add_argument("--json", action="store_true")
    mobile_owner_status.add_argument("--timeout", type=int, default=get_int("mobile_owner.command_timeout_seconds"))
    mobile_owner_status.set_defaults(func=cmd_mobile_owner)

    mobile_owner_set = mobile_owner_sub.add_parser("set", help="Set approved OpenClaw command owner ids")
    mobile_owner_set.add_argument("--owner", action="append", default=[])
    mobile_owner_set.add_argument("--confirm", action="store_true")
    mobile_owner_set.add_argument("--no-restart", action="store_true")
    mobile_owner_set.add_argument("--json", action="store_true")
    mobile_owner_set.add_argument("--timeout", type=int, default=get_int("mobile_owner.command_timeout_seconds"))
    mobile_owner_set.set_defaults(func=cmd_mobile_owner)

    archive = subparsers.add_parser("archive", help="Create local/S3 memory and context archives")
    archive_sub = archive.add_subparsers(dest="archive_command", required=True)

    archive_plan = archive_sub.add_parser("plan", help="Preview archive contents")
    archive_plan.set_defaults(func=cmd_archive)

    archive_create = archive_sub.add_parser("create", help="Create a local archive")
    archive_create.add_argument("--dry-run", action="store_true")
    archive_create.add_argument("--upload-s3", action="store_true")
    archive_create.add_argument("--confirm", action="store_true")
    archive_create.set_defaults(func=cmd_archive)

    archive_lifecycle_plan = archive_sub.add_parser("lifecycle-plan", help="Preview desired S3 lifecycle and object-lock policy")
    archive_lifecycle_plan.set_defaults(func=cmd_archive)

    archive_lifecycle_apply = archive_sub.add_parser("lifecycle-apply", help="Apply configured S3 lifecycle and object-lock policy")
    archive_lifecycle_apply.add_argument("--confirm", action="store_true")
    archive_lifecycle_apply.set_defaults(func=cmd_archive)

    archive_lifecycle_verify = archive_sub.add_parser("lifecycle-verify", help="Verify configured S3 lifecycle and object-lock policy")
    archive_lifecycle_verify.set_defaults(func=cmd_archive)

    archive_schedule_plan = archive_sub.add_parser("schedule-plan", help="Preview configured S3 archive upload schedule")
    archive_schedule_plan.set_defaults(func=cmd_archive)

    archive_schedule_install = archive_sub.add_parser("schedule-install", help="Install the configured S3 archive upload schedule")
    archive_schedule_install.add_argument("--confirm", action="store_true")
    archive_schedule_install.set_defaults(func=cmd_archive)

    archive_schedule_status = archive_sub.add_parser("schedule-status", help="Show the configured S3 archive upload scheduled task")
    archive_schedule_status.set_defaults(func=cmd_archive)

    archive_schedule_delete = archive_sub.add_parser("schedule-delete", help="Delete the configured S3 archive upload scheduled task")
    archive_schedule_delete.add_argument("--confirm", action="store_true")
    archive_schedule_delete.set_defaults(func=cmd_archive)

    archive_schedule_run_now = archive_sub.add_parser("schedule-run-now", help="Trigger the configured S3 archive upload scheduled task")
    archive_schedule_run_now.add_argument("--confirm", action="store_true")
    archive_schedule_run_now.set_defaults(func=cmd_archive)

    watchdog = subparsers.add_parser("watchdog", help="Detect and recover stuck OpenClaw gateway/node processes")
    watchdog_sub = watchdog.add_subparsers(dest="watchdog_command", required=True)

    watchdog_status = watchdog_sub.add_parser("status", help="Show current OpenClaw watchdog health view")
    watchdog_status.add_argument("--json", action="store_true")
    watchdog_status.set_defaults(func=cmd_watchdog)

    watchdog_run = watchdog_sub.add_parser("run", help="Recover OpenClaw if the gateway is dead or stuck")
    watchdog_run.add_argument("--json", action="store_true")
    watchdog_run.set_defaults(func=cmd_watchdog)

    watchdog_schedule_plan = watchdog_sub.add_parser("schedule-plan", help="Preview the configured OpenClaw watchdog scheduled task")
    watchdog_schedule_plan.set_defaults(func=cmd_watchdog)

    watchdog_schedule_install = watchdog_sub.add_parser("schedule-install", help="Install the silent OpenClaw watchdog scheduled task")
    watchdog_schedule_install.add_argument("--confirm", action="store_true")
    watchdog_schedule_install.set_defaults(func=cmd_watchdog)

    watchdog_schedule_status = watchdog_sub.add_parser("schedule-status", help="Show the OpenClaw watchdog scheduled task")
    watchdog_schedule_status.set_defaults(func=cmd_watchdog)

    watchdog_schedule_delete = watchdog_sub.add_parser("schedule-delete", help="Delete the OpenClaw watchdog scheduled task")
    watchdog_schedule_delete.add_argument("--confirm", action="store_true")
    watchdog_schedule_delete.set_defaults(func=cmd_watchdog)

    watchdog_schedule_run_now = watchdog_sub.add_parser("schedule-run-now", help="Trigger the OpenClaw watchdog scheduled task now")
    watchdog_schedule_run_now.add_argument("--confirm", action="store_true")
    watchdog_schedule_run_now.set_defaults(func=cmd_watchdog)

    bridge = subparsers.add_parser("bridge", help="Independent Telegram bridge (long-poll receiver)")
    bridge_sub = bridge.add_subparsers(dest="bridge_command", required=True)

    bridge_status = bridge_sub.add_parser("status", help="Show Telegram bridge readiness")
    bridge_status.add_argument("--json", action="store_true")
    bridge_status.set_defaults(func=cmd_bridge)

    bridge_rules = bridge_sub.add_parser("rules", help="List configured dispatch rules")
    bridge_rules.add_argument("--json", action="store_true")
    bridge_rules.set_defaults(func=cmd_bridge)

    bridge_run = bridge_sub.add_parser("run", help="Run the Telegram bridge poll loop")
    bridge_run.add_argument("--once", action="store_true", help="Poll once and exit")
    bridge_run.add_argument("--json", action="store_true")
    bridge_run.set_defaults(func=cmd_bridge)

    bridge_schedule_plan = bridge_sub.add_parser("schedule-plan", help="Preview the configured Telegram bridge scheduled task")
    bridge_schedule_plan.set_defaults(func=cmd_bridge)

    bridge_schedule_install = bridge_sub.add_parser("schedule-install", help="Install the silent Telegram bridge scheduled task")
    bridge_schedule_install.add_argument("--confirm", action="store_true")
    bridge_schedule_install.set_defaults(func=cmd_bridge)

    bridge_schedule_status = bridge_sub.add_parser("schedule-status", help="Show the Telegram bridge scheduled task")
    bridge_schedule_status.set_defaults(func=cmd_bridge)

    bridge_schedule_delete = bridge_sub.add_parser("schedule-delete", help="Delete the Telegram bridge scheduled task")
    bridge_schedule_delete.add_argument("--confirm", action="store_true")
    bridge_schedule_delete.set_defaults(func=cmd_bridge)

    bridge_schedule_run_now = bridge_sub.add_parser("schedule-run-now", help="Trigger the Telegram bridge scheduled task now")
    bridge_schedule_run_now.add_argument("--confirm", action="store_true")
    bridge_schedule_run_now.set_defaults(func=cmd_bridge)

    startup = subparsers.add_parser("startup", help="Run local personal-assistant startup orchestration")
    startup_sub = startup.add_subparsers(dest="startup_command", required=True)
    startup_run = startup_sub.add_parser("run", help="Start and check the configured assistant runtime")
    startup_run.add_argument("--json", action="store_true")
    startup_run.set_defaults(func=cmd_startup)

    logs = subparsers.add_parser("logs", help="Inspect local personal-assistant and OpenClaw logs")
    logs_sub = logs.add_subparsers(dest="logs_command", required=True)

    logs_tail = logs_sub.add_parser("tail", help="Tail recent matching log lines")
    logs_tail.add_argument("--source", choices=_config_choices("logs.sources"), default=get_str("logs.default_source"))
    logs_tail.add_argument("--lines", type=int, default=get_int("logs.tail_lines_default"))
    logs_tail.add_argument("--contains")
    logs_tail.add_argument("--errors-only", action="store_true")
    logs_tail.add_argument("--max-files", type=int, default=get_int("logs.max_files_default"))
    logs_tail.set_defaults(func=cmd_logs_tail)

    logs_errors = logs_sub.add_parser("errors", help="Show recent warning/error/failure log lines")
    logs_errors.add_argument("--source", choices=_config_choices("logs.sources"), default=get_str("logs.default_error_source"))
    logs_errors.add_argument("--limit", type=int, default=get_int("logs.errors_limit_default"))
    logs_errors.set_defaults(func=cmd_logs_errors)

    logs_summary = logs_sub.add_parser("summary", help="Print structured log summary as JSON")
    logs_summary.add_argument("--source", choices=_config_choices("logs.sources"), default=get_str("logs.default_error_source"))
    logs_summary.set_defaults(func=cmd_logs_summary)

    task = subparsers.add_parser("task", help="Run approved, config-driven laptop tasks")
    task_sub = task.add_subparsers(dest="task_command", required=True)

    task_list = task_sub.add_parser("list", help="List approved laptop tasks")
    task_list.add_argument("--json", action="store_true")
    task_list.set_defaults(func=cmd_task)

    task_show = task_sub.add_parser("show", help="Show one laptop task definition")
    task_show.add_argument("task_name", choices=task_names())
    task_show.set_defaults(func=cmd_task)

    task_run = task_sub.add_parser("run", help="Run one approved laptop task")
    task_run.add_argument("task_name", choices=task_names())
    task_run.add_argument("--dry-run", action="store_true")
    task_run.add_argument("--confirm", action="store_true", help="Confirm a gated task or external Telegram send")
    task_run.add_argument("--send-telegram", action="store_true", help="Send supported task output to the approved Telegram owner")
    task_run.add_argument("--json", action="store_true")
    task_run.set_defaults(func=cmd_task)

    notes = subparsers.add_parser("notes", help="Manage local markdown notes")
    notes_sub = notes.add_subparsers(dest="notes_command", required=True)

    notes_add = notes_sub.add_parser("add", help="Add a local note")
    notes_add.add_argument("--title", required=True)
    notes_add.add_argument("--body", required=True)
    notes_add.add_argument("--tag", action="append", default=[])
    notes_add.set_defaults(func=cmd_notes_add)

    notes_list = notes_sub.add_parser("list", help="List local notes")
    notes_list.add_argument("--limit", type=int, default=get_int("tools.notes.list_limit_default"))
    notes_list.add_argument("--tag")
    notes_list.add_argument("--json", action="store_true")
    notes_list.set_defaults(func=cmd_notes_list)

    notes_show = notes_sub.add_parser("show", help="Show one local note")
    notes_show.add_argument("note_id")
    notes_show.set_defaults(func=cmd_notes_show)

    notes_search = notes_sub.add_parser("search", help="Search local notes")
    notes_search.add_argument("--query", required=True)
    notes_search.add_argument("--limit", type=int, default=get_int("tools.notes.search_limit_default"))
    notes_search.add_argument("--json", action="store_true")
    notes_search.set_defaults(func=cmd_notes_search)

    todos = subparsers.add_parser("todos", help="Manage local todos")
    todos_sub = todos.add_subparsers(dest="todos_command", required=True)

    todos_add = todos_sub.add_parser("add", help="Add a todo")
    todos_add.add_argument("--title", required=True)
    todos_add.add_argument("--details")
    todos_add.add_argument("--priority", choices=_config_choices("tools.todos.priorities"), default=get_str("tools.todos.default_priority"))
    todos_add.add_argument("--due")
    todos_add.set_defaults(func=cmd_todos_add)

    todos_list = todos_sub.add_parser("list", help="List todos")
    todos_list.add_argument("--status", choices=_config_choices("tools.todos.list_statuses"), default=get_str("tools.todos.default_status"))
    todos_list.add_argument("--limit", type=int, default=get_int("tools.todos.list_limit_default"))
    todos_list.add_argument("--json", action="store_true")
    todos_list.set_defaults(func=cmd_todos_list)

    todos_done = todos_sub.add_parser("done", help="Mark a todo completed")
    todos_done.add_argument("todo_id")
    todos_done.set_defaults(func=cmd_todos_done)

    todos_reopen = todos_sub.add_parser("reopen", help="Reopen a todo")
    todos_reopen.add_argument("todo_id")
    todos_reopen.set_defaults(func=cmd_todos_reopen)

    todos_cancel = todos_sub.add_parser("cancel", help="Cancel a todo")
    todos_cancel.add_argument("todo_id")
    todos_cancel.set_defaults(func=cmd_todos_cancel)

    files = subparsers.add_parser("files", help="Read-only approved-folder file tools")
    files_sub = files.add_subparsers(dest="files_command", required=True)

    files_search = files_sub.add_parser("search", help="Search approved text files")
    files_search.add_argument("--scope", choices=_config_choices("tools.file_search.scopes"), default=get_str("tools.file_search.default_scope"))
    files_search.add_argument("--query", required=True)
    files_search.add_argument("--limit", type=int, default=get_int("tools.file_search.default_limit"))
    files_search.add_argument("--json", action="store_true")
    files_search.set_defaults(func=cmd_files_search)

    brief = subparsers.add_parser("brief", help="Generate local briefs from notes, todos, logs, and mobile commands")
    brief_sub = brief.add_subparsers(dest="brief_command", required=True)

    brief_daily = brief_sub.add_parser("daily", help="Generate a local daily brief")
    brief_daily.add_argument("--show", action="store_true")
    brief_daily.set_defaults(func=cmd_brief_daily)

    latest = subparsers.add_parser("latest", help="Show checkpoint plus recent local log activity")
    latest.add_argument("--checkpoint-lines", type=int, default=get_int("logs.latest_checkpoint_lines_default"))
    latest.add_argument("--log-lines", type=int, default=get_int("logs.latest_log_lines_default"))
    latest.set_defaults(func=cmd_latest)

    smoke = subparsers.add_parser("smoke", help="Smoke-test devctl paths, logger, and OpenClaw CLI")
    smoke.set_defaults(func=cmd_smoke)

    return parser


def main(argv: list[str] | None = None) -> int:
    ensure_runtime_dirs()
    parser = build_parser()
    args = parser.parse_args(argv)
    return _run_logged_command(args)


if __name__ == "__main__":
    raise SystemExit(main())
