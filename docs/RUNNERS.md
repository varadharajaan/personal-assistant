# Runner Scripts And Dev Control

The personal-assistant repo uses `devctl.py` as the local control plane for OpenClaw, logs, mobile command capture, and repeatable runner recipes.

Python commands log through `shared/python/pa_logging.py`. PowerShell wrappers log through `shared/ps1/_log_helper.ps1` without adding wrapper log noise to stdout.

Runner configuration lives in `config/settings.toml`. Do not edit Python or PowerShell to change paths, models, aliases, recipes, timeouts, log limits, or mobile defaults.

## Entry Points

```powershell
python .\devctl.py smoke
.\scripts\pa.ps1 smoke
```

Convenience wrappers:

```powershell
.\scripts\openclaw-control.ps1 status
.\scripts\summarize-example-app.ps1 --dry-run
.\scripts\latest-openclaw-errors.ps1
.\scripts\model-routes.ps1
.\scripts\openclaw-doctor-triage.ps1
.\scripts\start.ps1
```

These wrappers resolve their behavior through `[aliases]` in `config/settings.toml`.

## OpenClaw Control

```powershell
.\scripts\pa.ps1 openclaw start
.\scripts\pa.ps1 openclaw stop
.\scripts\pa.ps1 openclaw restart
.\scripts\pa.ps1 openclaw status
.\scripts\pa.ps1 openclaw doctor
.\scripts\pa.ps1 openclaw doctor-report
.\scripts\pa.ps1 openclaw system-status --all
.\scripts\pa.ps1 openclaw models-auth
.\scripts\pa.ps1 openclaw models-list
.\scripts\pa.ps1 openclaw models-validate
.\scripts\pa.ps1 openclaw doctor-triage
.\scripts\pa.ps1 openclaw doctor-review
.\scripts\pa.ps1 openclaw doctor-fix-safe --confirm
```

Use `--target node` or `--target both` for node host service actions:

```powershell
.\scripts\pa.ps1 openclaw status --target both
```

Note: `openclaw models-status` is also available, but it may hang before local onboarding is complete. Prefer `models-auth` and `models-list` during setup.

`doctor-report` saves the OpenClaw doctor output to `data/runs/openclaw/` before printing it. Use it when tracking doctor warnings across sessions.

`doctor-triage` reads the latest saved doctor report by default and classifies known warnings using `[doctor.issue_patterns]` in `config/settings.toml`. Use `--refresh` to run a new doctor report first. The triage command is read-only.

`doctor-review` refreshes a doctor report, classifies it, and prints recommendations without applying fixes. `doctor-fix-safe --confirm` runs the configured non-interactive OpenClaw repair command and saves an artifact; it must stay confirmation-gated.

`models-validate` runs the configured provider model list, compares it to `[models.validation.targets]`, saves an artifact, and reports which desired/required model routes are visible. Current policy validates `github-copilot/gpt-5.4` as default, `github-copilot/gpt-5.3-codex` for agentic/coding workflows, and `github-copilot/claude-opus-4.7` for maximum-complexity work.

## OpenClaw Watchdog

The OpenClaw watchdog detects the gateway failure mode where Telegram polling stalls, the gateway stops listening on `127.0.0.1:18789`, and an orphan OpenClaw node host remains.

```powershell
python .\devctl.py watchdog status
python .\devctl.py watchdog run
python .\devctl.py watchdog schedule-plan
python .\devctl.py watchdog schedule-install --confirm
python .\devctl.py watchdog schedule-status
python .\devctl.py watchdog schedule-run-now --confirm
python .\devctl.py watchdog schedule-delete --confirm
```

The scheduled task is `PersonalAssistantOpenClawWatchdog`. It runs every 10 minutes through `wscript.exe //B //Nologo scripts/openclaw-watchdog.vbs`, so it should remain windowless. It only matches configured OpenClaw gateway/node command lines and refuses protected ClipSync markers. Its default liveness check is a fast local HTTP probe so recovery does not depend on the slower OpenClaw CLI probe path when the gateway event loop is already starved.

Details are in [OPENCLAW-RELIABILITY.md](./OPENCLAW-RELIABILITY.md).

## Telegram Bridge

Independent long-poll Telegram receiver. Replaces OpenClaw's native Telegram channel as the inbound path; OpenClaw native Telegram polling is now disabled.

```powershell
python .\devctl.py bridge status
python .\devctl.py bridge rules
python .\devctl.py bridge run
python .\devctl.py bridge run --once
python .\devctl.py bridge schedule-plan
python .\devctl.py bridge schedule-install --confirm
python .\devctl.py bridge schedule-status
python .\devctl.py bridge schedule-delete --confirm
python .\devctl.py bridge schedule-run-now --confirm
```

The scheduled task name is `PersonalAssistantTelegramBridge`. Schedule mode is `ONLOGON` because the bridge is a long-running daemon, not a periodic job.

Telegram phrases the bridge recognises directly (no OpenClaw call, ~2–6s):

- `ping`, `help`, `?`, `/help`
- `screenshot`, `screen`, `take screenshot`, `screen shot`
- `start example-app`, `restart example-app`, `example-app`, `start hotkey scripts`, `restart hotkey scripts`, `hotkey scripts`
- `example-app errors`, `example-app latest errors`, `example-app logs`

Generic forms:

- `task <name>` — run any approved laptop task. Append `confirm` if the task requires it.
- `ask <text>` — route text to OpenClaw agent `main` (slow; ~60–90s with `thinking=medium`).
- any other text → fallback to OpenClaw the same way as `ask`.

Full reference: [MOBILE-BRIDGE.md](./MOBILE-BRIDGE.md).

## Startup

The single startup command runs the configured startup actions:

```powershell
.\scripts\start.ps1
python .\devctl.py startup run --json
```

Current startup actions ensure runtime directories, run the OpenClaw watchdog, ensure configured OpenClaw role agents, check the gateway, print mobile webhook info, and preview the archive plan. The action list lives under `[startup]`.

## Agent Roles

Use roles instead of per-call model overrides:

```powershell
python .\devctl.py agents list --actual
python .\devctl.py agents ensure
python .\devctl.py agents smoke --role agentic
python .\devctl.py agents smoke --role max
python .\devctl.py ask "implement this" --role agentic
python .\devctl.py ask "think through this hard problem" --role max
```

Configured roles:

- `default`: OpenClaw agent `main`, model `github-copilot/gpt-5.4`.
- `agentic`: OpenClaw agent `pa-codex`, model `github-copilot/gpt-5.3-codex`.
- `max`: OpenClaw agent `pa-max`, model `github-copilot/claude-opus-4.7`.

## Windows OpenClaw Invocation

On Windows, the runner uses the configured direct Node entrypoint by default:

```text
node.exe {appdata}/npm/node_modules/openclaw/openclaw.mjs
```

The exact entrypoint, Node executable candidates, and fallback OpenClaw executable candidates live in `config/settings.toml`. This is intentional: the npm `openclaw.cmd` shim can mishandle long or multi-line `--message` values and make OpenClaw report that no `--agent`, `--session-id`, or `--to` was passed even when `--agent main` is present.

If that error returns, check these first:

```powershell
python .\devctl.py smoke
python .\devctl.py run summarize-example-app --dry-run
```

## Prompt Recipes

Recipes are configured in `config/settings.toml` under `[recipes.*]`.

List recipes:

```powershell
.\scripts\pa.ps1 recipes
```

Print the exact prompt without sending it:

```powershell
.\scripts\pa.ps1 run summarize-example-app --dry-run
```

Send a recipe to OpenClaw:

```powershell
.\scripts\pa.ps1 run summarize-example-app
.\scripts\pa.ps1 run latest-openclaw-errors
.\scripts\pa.ps1 run handoff
```

Use `--model` only after OpenClaw authorizes provider/model overrides for this caller.

Run artifacts are saved under:

```text
data/runs/openclaw/
```

## Local Tools

Notes:

```powershell
.\scripts\pa.ps1 notes add --title "Idea" --body "Short local note" --tag personal
.\scripts\pa.ps1 notes list
.\scripts\pa.ps1 notes search --query "idea"
.\scripts\pa.ps1 notes show <note-id>
```

Todos:

```powershell
.\scripts\pa.ps1 todos add --title "Review logs" --priority normal
.\scripts\pa.ps1 todos list --status pending
.\scripts\pa.ps1 todos done <todo-id>
.\scripts\pa.ps1 todos reopen <todo-id>
.\scripts\pa.ps1 todos cancel <todo-id>
```

Approved-folder search:

```powershell
.\scripts\pa.ps1 files search --scope personal --query "OpenClaw"
```

Daily brief:

```powershell
.\scripts\pa.ps1 brief daily --show
```

Tool data is stored under configured `data/` paths and is excluded from Desktop sync. Tool actions log to `logs/unified/_session.log` and their own flow logs.

## Laptop Tasks

Approved laptop actions are exposed through `devctl.py task` and configured in
`[laptop_tasks]`.

```powershell
python .\devctl.py task list
python .\devctl.py task run app-latest-errors
python .\devctl.py task run app-latest-errors --send-telegram --confirm
python .\devctl.py task run screen-primary-screenshot
python .\devctl.py task run screen-primary-screenshot --send-telegram --confirm
python .\devctl.py task run start-app-hotkeys --confirm
python .\devctl.py task run start-app-hotkeys --send-telegram --confirm
```

Current task map:

- `app-latest-errors`: read-only ExampleApp Launcher warning/error log scan.
- `screen-primary-screenshot`: primary-screen PNG capture under `data/screenshots`.
- `screen-primary-screenshot --send-telegram --confirm`: sends the PNG to the approved Telegram owner.
- `start-app-hotkeys --confirm`: runs the same `start-app.vbs` backend used by the Ctrl+Shift+W shortcut.
- `start-app-hotkeys --send-telegram --confirm`: preferred Telegram-owner route because the task runner sends a separate delivery confirmation. On success, the confirmation comes from the latest configured ExampleApp launcher log line, for example `DONE All desktop hotkey scripts launched | delay=414ms total=2429ms`.

Telegram delivery uses OpenClaw native `message send` first. If the gateway
message-send path returns a configured timeout marker, the task runner can
restart only the OpenClaw gateway and retry native delivery once before using
the configured Telegram Bot API fallback. The fallback reads the same local
OpenClaw token file without storing secrets in this repo.

OpenClaw's normal final Telegram reply can fail independently of the local task.
For Telegram-originated laptop tasks, workspace routing therefore includes
`--send-telegram --confirm` on the task itself.

## Mobile Command Capture

A future Telegram, WhatsApp, or webhook bridge should call:

```powershell
.\scripts\pa.ps1 mobile capture --source whatsapp --sender "<sender>" --channel personal --message "summarize example-app launcher"
```

This stores the full command locally in:

```text
data/mobile/commands.jsonl
```

The normal log records only metadata such as command id, source, channel, sender hash, and character count.

List pending commands:

```powershell
.\scripts\pa.ps1 mobile list --status pending
```

Process pending commands:

```powershell
.\scripts\pa.ps1 mobile drain --limit 3
```

Dry-run processing:

```powershell
.\scripts\pa.ps1 mobile drain --limit 3 --dry-run
```

Manually mark a command that was handled outside OpenClaw:

```powershell
.\scripts\pa.ps1 mobile mark <command-id> --status skipped
```

## Local Mobile Webhook

Show endpoint details:

```powershell
.\scripts\pa.ps1 mobile webhook info
```

Start the loopback webhook bridge:

```powershell
.\scripts\mobile-bridge.ps1
```

Run one request and exit, useful for smoke checks:

```powershell
.\scripts\pa.ps1 mobile webhook serve --once
```

Default capture endpoint:

```text
http://127.0.0.1:8765/mobile/command
```

Example JSON body:

```json
{
  "message": "summarize example-app launcher",
  "sender": "phone",
  "source": "webhook",
  "channel": "local-webhook"
}
```

The default bridge is loopback-only. Before exposing it to LAN or a tunnel, change config deliberately and enable token validation.

External/mobile readiness:

```powershell
python .\devctl.py mobile external info
python .\devctl.py mobile external info --json
python .\devctl.py mobile channel status --json
python .\devctl.py mobile channel login --channel telegram --confirm
python .\devctl.py mobile channel qr --json
python .\devctl.py mobile token status --check-s3
python .\devctl.py mobile token backup-s3 --confirm
python .\devctl.py mobile token restore-s3 --confirm
python .\devctl.py mobile owner status --json
python .\devctl.py mobile owner set --owner "telegram:<numeric-user-id>" --confirm
```

The selected channel is Telegram via OpenClaw native channel support. The bot token is read from OpenClaw's local `channels.telegram.tokenFile`. S3 is only a fallback restore source, configured under `[mobile_channel]`; the token must not be stored in repo files or command-line arguments.

External exposure is disabled by default. Channel login can be interactive and should only run after choosing the channel/account.
Command-owner setup is also gated; set it only after the approved channel-native id is known. Owner ids are redacted in personal-assistant logs by default.

## Archive

Preview and create local memory/context archives:

```powershell
python .\devctl.py archive plan
python .\devctl.py archive create
```

Archives use the configured `7z` backend and currently produce `.7z` files with the `ppmd-text-ultra-no-times` profile. The profile is tuned for text-heavy memory/context snapshots and is controlled by `[archive].seven_zip_arguments`.

S3 upload is enabled for the dedicated private bucket configured in `[archive]`:

```text
<your-archive-bucket>
```

Uploads still require the explicit confirmation gate:

```powershell
python .\devctl.py archive create --upload-s3 --confirm
```

The upload command uses `aws_profile` and `s3_region` from `config/settings.toml`.

Preview, apply, and verify the S3 archive lifecycle/object-lock policy:

```powershell
python .\devctl.py archive lifecycle-plan
python .\devctl.py archive lifecycle-apply --confirm
python .\devctl.py archive lifecycle-verify
```

Install, inspect, remove, or manually trigger the daily S3 archive upload schedule:

```powershell
python .\devctl.py archive schedule-plan
python .\devctl.py archive schedule-install --confirm
python .\devctl.py archive schedule-status
python .\devctl.py archive schedule-delete --confirm
python .\devctl.py archive schedule-run-now --confirm
```

The installed Windows scheduled task is `PersonalAssistantArchiveToS3`. It runs daily at `22:30` and calls `wscript.exe //B //Nologo scripts/archive-to-s3.vbs`, so it should not show a PowerShell console window. The VBS launcher starts `scripts/archive-to-s3.ps1` hidden, and that wrapper resolves the configured `archive-to-s3` alias to `archive create --upload-s3 --confirm`. The current task settings allow battery runs and start the task when available if the laptop missed the exact time.

The bucket has delete-protection controls:

- explicit bucket policy denies object delete and bucket delete
- archive objects under `personal-assistant/archive/` expire after 60 days
- noncurrent archive versions expire after 60 days
- incomplete multipart uploads are aborted after 7 days
- Object Lock default retention is `GOVERNANCE` for 60 days
- lifecycle mutation is denied after the controlled apply command restores the guard
- governance bypass is denied by bucket policy

These controls protect normal archive operation from accidental deletion. An AWS root/admin outside this repo can still change account-level controls; use an Organizations SCP for stronger account-wide enforcement.

## Log Inspection

Personal-assistant logs:

```powershell
.\scripts\pa.ps1 logs tail --source personal --lines 80
.\scripts\pa.ps1 logs errors --source personal
```

OpenClaw logs and session-related log files:

```powershell
.\scripts\pa.ps1 logs tail --source openclaw --lines 80
.\scripts\pa.ps1 logs errors --source openclaw
.\scripts\pa.ps1 logs summary --source all
```

ExampleApp Launcher logs:

```powershell
.\scripts\pa.ps1 logs errors --source example-app
.\scripts\pa.ps1 logs summary --source example-app
```

## Desktop Sync

The Desktop wrapper now knows about this repo through `[desktop_sync]` in `config/settings.toml`.

Preview the normal full Desktop sync. This includes personal-assistant automatically:

```powershell
$env:DESKSYNC_CI = "1"
..\..\sync-to-personal-desktop.bat -n
```

Preview only the required personal-assistant files:

```powershell
$env:DESKSYNC_CI = "1"
..\..\sync-to-personal-desktop.bat --pa -n
```

Run the normal full Desktop sync:

```powershell
$env:DESKSYNC_CI = "1"
..\..\sync-to-personal-desktop.bat
```

Sync only the required personal-assistant files to the personal Desktop:

```powershell
$env:DESKSYNC_CI = "1"
..\..\sync-to-personal-desktop.bat --pa
```

`--pa` / `-pa` is only a personal-assistant-only selector. It is not needed for normal sync. Use `--no-pa` / `-npa` only when personal-assistant should be excluded.

The sync policy excludes runtime/private output: `logs`, `data`, `__pycache__`, `.venv`, `node_modules`, upgrade logs, `.env`, and secret/key/cert-like files.

## Log Viewer

The existing example-app log viewer on port `7000` now includes these roots by default:

```text
personal-assistant -> projects/personal-assistant/logs
openclaw           -> ~/.openclaw/logs
openclaw-temp      -> %TEMP%/openclaw
```

Check it:

```powershell
Invoke-WebRequest -Uri "http://127.0.0.1:7000/api/health" -UseBasicParsing
Invoke-WebRequest -Uri "http://127.0.0.1:7000/api/files?refresh=1" -UseBasicParsing
```

Latest project status:

```powershell
.\scripts\pa.ps1 latest
```

## Safety Notes

- Runner recipes are read-only unless a command explicitly requests writes.
- Mobile command bodies are stored locally in `data/mobile/commands.jsonl`, but are not mirrored into normal logs.
- OpenClaw command output artifacts can contain model responses. Treat `data/runs/openclaw/` as local private data.
- Sending replies back to mobile requires `--deliver` and an explicit target/session configuration.
- The local mobile bridge only captures commands. It does not send replies back to a phone.
- Per-call `--model` overrides can fail until OpenClaw authorizes provider/model overrides for this caller; use configured roles instead.
- A future simple UI page should use this runner layer or a thin logged API wrapper around it, so UI actions stay auditable.
