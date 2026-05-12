from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from assistant.devctl.config import get_bool, get_int, get_list, get_path, get_str
from assistant.devctl.agent_roles import get_role, resolve_agent_call, role_names
from assistant.devctl.archive import (
    build_archive_plan,
    build_s3_lifecycle_configuration,
    build_s3_object_lock_configuration,
)
from assistant.devctl.archive_schedule import build_archive_schedule_plan
from assistant.devctl.doctor_triage import triage_text
from assistant.devctl.log_inspector import redact_text
from assistant.devctl.laptop_tasks import run_laptop_task, task_definition, task_names
from assistant.devctl import laptop_tasks as laptop_task_module
from assistant.devctl.mobile_external import readiness_checks
from assistant.devctl.mobile_owner import owner_payload, redacted_owners
from assistant.devctl.model_diagnostics import parse_model_list, validate_configured_models
from assistant.devctl.openclaw_runner import sanitize_args
from assistant.devctl.openclaw_watchdog import ProcessRole, process_matches_role
from assistant.devctl.openclaw_watchdog_schedule import build_openclaw_watchdog_schedule_plan


class DiagnosticTests(unittest.TestCase):
    def test_model_list_parser_filters_plain_model_ids(self) -> None:
        models = parse_model_list(
            "\n".join(
                [
                    "github-copilot/gpt-5.4",
                    "not a model heading",
                    "\x1b[7mgithub-copilot/claude-opus-4.7\x1b[0m",
                ]
            )
        )

        self.assertEqual(
            models,
            ["github-copilot/claude-opus-4.7", "github-copilot/gpt-5.4"],
        )

    def test_configured_model_validation_marks_configured_routes_available(self) -> None:
        checks = validate_configured_models(
            provider="github-copilot",
            visible_models=[
                "github-copilot/claude-opus-4.7",
                "github-copilot/gpt-5.3-codex",
                "github-copilot/gpt-5.4",
                "github-copilot/gpt-5.4-mini",
                "github-copilot/gemini-3-flash",
            ],
        )

        by_key = {(check.key, check.model): check.status for check in checks}

        self.assertEqual(
            by_key[("models.primary", "github-copilot/gpt-5.4")],
            get_str("models.validation.available_status"),
        )
        self.assertEqual(
            by_key[("models.agentic", "github-copilot/gpt-5.3-codex")],
            get_str("models.validation.available_status"),
        )
        self.assertEqual(
            by_key[("models.max_complex", "github-copilot/claude-opus-4.7")],
            get_str("models.validation.available_status"),
        )
        self.assertEqual(
            by_key[("models.openclaw_agent_default", "github-copilot/gpt-5.4")],
            get_str("models.validation.available_status"),
        )

    def test_doctor_triage_classifies_known_warnings(self) -> None:
        issues = triage_text(
            "\n".join(
                [
                    "No command owner is configured.",
                    "Found 1 orphan transcript file in sessions.",
                    "Missing requirements: 43",
                    "Error: EPERM: operation not permitted, symlink",
                ]
            )
        )

        self.assertEqual(
            [issue.id for issue in issues],
            [
                "command-owner-missing",
                "orphan-transcript",
                "optional-skills-missing",
                "windows-symlink-eperm",
            ],
        )

    def test_doctor_triage_ignores_zero_missing_requirements(self) -> None:
        issues = triage_text("Missing requirements: 0")
        self.assertNotIn("optional-skills-missing", [issue.id for issue in issues])

    def test_agent_roles_resolve_without_model_override_by_default(self) -> None:
        self.assertIn("agentic", role_names())
        role = get_role("agentic")
        self.assertEqual(role.agent, "pa-codex")
        resolved = resolve_agent_call(
            role_name="agentic",
            explicit_agent=None,
            explicit_model=None,
            explicit_thinking=None,
        )
        self.assertEqual(resolved.agent, "pa-codex")
        self.assertIsNone(resolved.model)

    def test_mobile_external_loopback_is_token_safe(self) -> None:
        checks = {check.id: check.status for check in readiness_checks()}
        self.assertEqual(checks["exposure-mode"], "ok")
        self.assertEqual(checks["token-required"], "ok")

    def test_mobile_owner_payload_is_json_list(self) -> None:
        self.assertEqual(owner_payload(["whatsapp:+15551234567"]), '["whatsapp:+15551234567"]')

    def test_mobile_owner_display_redacts_by_default(self) -> None:
        owners = redacted_owners(["whatsapp:+15551234567"])
        self.assertEqual(len(owners), 1)
        self.assertTrue(owners[0].startswith("owner#"))
        self.assertNotIn("+15551234567", owners[0])

    def test_openclaw_runner_redacts_owner_allowlist_values(self) -> None:
        sanitized = sanitize_args(
            [
                "openclaw",
                "config",
                "set",
                "commands.ownerAllowFrom",
                '["whatsapp:+15551234567"]',
                "--strict-json",
            ]
        )

        self.assertIn("<redacted>", sanitized)
        self.assertNotIn('["whatsapp:+15551234567"]', sanitized)

    def test_openclaw_runner_redacts_message_targets(self) -> None:
        sanitized = sanitize_args(["openclaw", "message", "send", "--target", "telegram:123", "--message", "hi"])

        self.assertIn("<redacted>", sanitized)
        self.assertNotIn("telegram:123", sanitized)

    def test_laptop_tasks_are_config_driven(self) -> None:
        names = task_names()

        self.assertIn("start-app-hotkeys", names)
        self.assertIn("app-latest-errors", names)
        self.assertIn("screen-primary-screenshot", names)
        self.assertTrue(task_definition("start-app-hotkeys")["requires_confirm"])

    def test_laptop_task_dry_run_does_not_require_confirm(self) -> None:
        result = run_laptop_task(
            "start-app-hotkeys",
            dry_run=True,
            confirm=False,
            send_telegram=False,
            log=lambda *args, **kwargs: None,
        )

        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.status, get_str("laptop_tasks.dry_run_status"))

    def test_command_task_can_build_success_message_from_latest_log_line(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            log_path = Path(temp_dir) / "start-all.log"
            log_path.write_text(
                "\n".join(
                    [
                        "[2026-05-11 15:28:45] [VBS] [start-all] [INFO] older line",
                        "[2026-05-11 15:35:55] [VBS] [start-all] [INFO] DONE All desktop hotkey scripts launched | delay=414ms total=2429ms",
                    ]
                ),
                encoding="utf-8",
            )

            message = laptop_task_module._command_result_message(
                {
                    "name": "start-app-hotkeys",
                    "success_message_source": "latest-log-line",
                    "success_log_path": str(log_path),
                    "success_log_contains": "DONE All desktop hotkey scripts launched",
                    "success_log_strip_regexes": [r"^(?:\[[^\]]+\]\s*)+"],
                    "success_log_require_mtime_after_start": False,
                    "success_message_template": "{line}",
                },
                subprocess.CompletedProcess(["test"], 0, "", ""),
                started_at=0,
                log=lambda *args, **kwargs: None,
            )

        self.assertEqual(message, "DONE All desktop hotkey scripts launched | delay=414ms total=2429ms")

    def test_telegram_token_uses_local_openclaw_token_file(self) -> None:
        self.assertEqual(get_str("mobile_channel.secret_storage"), "openclaw-token-file")
        self.assertTrue(str(get_path("mobile_channel.token_file")).endswith("telegram-bot-token.txt"))
        self.assertTrue(get_bool("mobile_channel.s3_backup_enabled"))
        self.assertEqual(get_str("mobile_channel.s3_backup_bucket"), get_str("archive.s3_bucket"))
        self.assertTrue(get_str("mobile_channel.s3_backup_key").startswith("personal-assistant/secrets/"))

    def test_log_redaction_masks_telegram_secrets(self) -> None:
        raw = (
            "url=https://api.telegram.org/bot123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabc/getMe "
            "sessionKey=agent:main:telegram:direct:123456789 "
            '"chatId":"123456789"'
        )
        redacted = redact_text(raw)

        self.assertIn(get_str("logs.redaction_replacement"), redacted)
        self.assertNotIn("ABCDEFGHIJKLMNOPQRSTUVWXYZabc", redacted)
        self.assertNotIn("telegram:direct:123456789", redacted)
        self.assertNotIn('"chatId":"123456789"', redacted)

    def test_telegram_native_retry_markers_are_config_driven(self) -> None:
        marker = str(get_list("laptop_tasks.telegram.native_retry_markers")[0])

        self.assertTrue(laptop_task_module._telegram_native_retry_requested(f"{marker} 10000ms"))
        self.assertFalse(laptop_task_module._telegram_native_retry_requested("ordinary Telegram API error"))

    def test_archive_plan_has_relative_archive_names(self) -> None:
        plan = build_archive_plan()
        self.assertGreater(len(plan.files), 0)
        self.assertIn("README.md", {item.arcname for item in plan.files})
        self.assertEqual(plan.compression_backend, get_str("archive.compression_backend"))
        self.assertEqual(plan.compression_profile, get_str("archive.compression_profile"))
        self.assertTrue(plan.destination.endswith(".7z"))

    def test_archive_uses_configured_ppmd_profile(self) -> None:
        arguments = [str(item) for item in get_list("archive.seven_zip_arguments")]

        self.assertEqual(get_str("archive.compression_profile"), "ppmd-text-ultra-no-times")
        self.assertIn("-m0=PPMd", arguments)
        self.assertIn("-mtm=off", arguments)
        self.assertIn("-mtc=off", arguments)
        self.assertIn("-mta=off", arguments)

    def test_s3_lifecycle_policy_uses_configured_archive_retention(self) -> None:
        lifecycle = build_s3_lifecycle_configuration()
        rules = {str(rule["ID"]): rule for rule in lifecycle["Rules"]}
        archive_rule = rules[get_str("archive.s3_lifecycle_rule_id")]

        self.assertEqual(archive_rule["Filter"]["Prefix"], get_str("archive.s3_lifecycle_filter_prefix"))
        self.assertEqual(archive_rule["Expiration"]["Days"], get_int("archive.s3_lifecycle_expiration_days"))
        self.assertEqual(
            archive_rule["NoncurrentVersionExpiration"]["NoncurrentDays"],
            get_int("archive.s3_lifecycle_noncurrent_expiration_days"),
        )

    def test_s3_object_lock_policy_uses_configured_retention(self) -> None:
        object_lock = build_s3_object_lock_configuration()

        self.assertEqual(object_lock["Rule"]["DefaultRetention"]["Mode"], get_str("archive.s3_object_lock_mode"))
        self.assertEqual(object_lock["Rule"]["DefaultRetention"]["Days"], get_int("archive.s3_object_lock_days"))

    def test_archive_schedule_uses_configured_task_and_wrapper(self) -> None:
        plan = build_archive_schedule_plan()

        self.assertEqual(plan.task_name, get_str("archive_schedule.task_name"))
        self.assertEqual(plan.schedule, get_str("archive_schedule.schedule"))
        self.assertEqual(plan.start_time, get_str("archive_schedule.start_time"))
        self.assertEqual(plan.start_when_available, get_bool("archive_schedule.start_when_available"))
        self.assertEqual(plan.allow_start_on_batteries, get_bool("archive_schedule.allow_start_on_batteries"))
        self.assertEqual(plan.wrapper_script, str(get_path("archive_schedule.wrapper_script")))
        self.assertTrue(plan.task_runner_executable.lower().endswith(("wscript.exe", "wscript")))
        self.assertIn("{script}", get_str("archive_schedule.hidden_powershell_command_template"))
        self.assertIn(get_str("archive_schedule.task_name"), plan.create_args)

    def test_openclaw_watchdog_process_matching_is_config_driven(self) -> None:
        role_config = next(
            item for item in get_list("openclaw_watchdog.process_roles") if isinstance(item, dict) and item["name"] == "gateway"
        )
        role = ProcessRole(
            name=str(role_config["name"]),
            required_markers=[str(item) for item in role_config["required_markers"]],
            start_command=[str(item) for item in role_config["start_command"]],
            start_timeout_seconds=int(role_config["start_timeout_seconds"]),
            direct_start_enabled=bool(role_config["direct_start_enabled"]),
            direct_start_command=[str(item) for item in role_config["direct_start_command"]],
            direct_start_wait_seconds=int(role_config["direct_start_wait_seconds"]),
        )

        gateway_command = (
            r'"C:\Program Files\nodejs\node.exe" '
            r"<HOME>/AppData\Roaming\npm\node_modules\openclaw\dist\index.js gateway --port 18789"
        )
        node_command = gateway_command.replace(" gateway ", " node run ")

        self.assertTrue(process_matches_role(gateway_command, role))
        self.assertFalse(process_matches_role(node_command, role))
        self.assertIn("clipsync", [str(item).lower() for item in get_list("openclaw_watchdog.protected_process_markers")])

    def test_openclaw_watchdog_schedule_uses_configured_task_and_wrapper(self) -> None:
        plan = build_openclaw_watchdog_schedule_plan()

        self.assertEqual(plan.task_name, get_str("openclaw_watchdog_schedule.task_name"))
        self.assertEqual(plan.schedule, get_str("openclaw_watchdog_schedule.schedule"))
        self.assertEqual(plan.modifier, get_int("openclaw_watchdog_schedule.modifier"))
        self.assertEqual(plan.wrapper_script, str(get_path("openclaw_watchdog_schedule.wrapper_script")))
        self.assertTrue(plan.task_runner_executable.lower().endswith(("wscript.exe", "wscript")))
        self.assertIn(str(get_int("openclaw_watchdog_schedule.modifier")), plan.create_args)


if __name__ == "__main__":
    unittest.main()
