# Working Guide

## Operating Rule

Plan mode is part of execution mode.

For every non-trivial change:

1. State the intent.
2. Check current state.
3. Make the change.
4. Run a smoke check.
5. Update docs.
6. Update `CHECKPOINT.md` if the change affects state, workflow, architecture, model policy, or remaining work.
7. Log the checkpoint/doc update.

Protected-service reminder:

- ClipSync is already working for local Wi-Fi clipboard/text sync between machine 1 and peer machines.
- Treat ClipSync as hands-off unless the user explicitly asks for ClipSync work.
- OpenClaw device registration/pairing must not change ClipSync pairing, peers, ports, startup entries, process state, or runtime config.

## Daily Commands

Show current repo state:

```powershell
python .\devctl.py latest
```

Smoke test the control layer:

```powershell
.\scripts\start.ps1
python .\devctl.py smoke
.\scripts\pa.ps1 smoke
```

## Telegram Bot Operator Guide

The personal assistant accepts commands from the approved Telegram owner. Inbound is handled by the independent Telegram bridge (`bridge run`), not OpenClaw's native channel.

### What's working

- **Receive (Telegram → laptop):** working via `bridge run`.
- **Reply (laptop → Telegram):** working via Bot API.
- **Shortcut phrases** (no OpenClaw): ~2–6 seconds round-trip.
- **`task <name>`** (no OpenClaw): ~2–8 seconds round-trip.
- **Natural language `ask <text>` or fallback**: routed through OpenClaw agent `main` with `thinking=medium`. ~60–90 seconds round-trip. Supports OpenClaw tool use.

### Sending messages

From your phone's Telegram client, send any of:

| Phrase | Outcome |
|---|---|
| `ping` | `pong` |
| `help` / `?` / `/help` | full command list |
| `screenshot` / `screen` / `take screenshot` / `screen shot` | primary-screen PNG delivered to Telegram |
| `start ultracode` / `restart ultracode` / `ultracode` / `start ahk` / `restart ahk` / `ahk` | runs UltraCode launcher; replies with `DONE All AHK scripts launched \| delay=...ms` |
| `ultracode errors` / `ultracode latest errors` / `ultracode logs` | latest UltraCode warning/error log lines |
| `task <name>` (optionally followed by `confirm`) | runs the named approved laptop task |
| `ask <text>` | sends `<text>` to OpenClaw `main` |
| anything else | treated as `ask` |

### Adding a new shortcut

Edit `[[telegram_bridge.rules]]` in `config/settings.toml`:

```toml
[[telegram_bridge.rules]]
id = "shortcut-example"
match = "exact"
patterns = ["my phrase", "another phrase"]
description = "What this does."
kind = "task"
task_name = "<approved-laptop-task-name>"
task_confirm = true
task_send_telegram = true
```

Restart the bridge so the new rule loads:

```powershell
python .\devctl.py bridge run
```

### Day-to-day procedure

1. Make sure the bridge is running. Either start it once with `python .\devctl.py bridge run` (foreground) or install it as a scheduled task: `python .\devctl.py bridge schedule-install --confirm`.
2. Send Telegram messages as above.
3. If a reply does not arrive within the expected latency, read the audit files (see Debugging below). Do **not** restart things without first checking the logs.

### Debugging Telegram round-trips

All bridge activity is logged. Read in this order:

```powershell
# 1. Inbound audit (full text up to 4000 chars, dispatch latency, matched rule)
type data\telegram-bridge\inbound.jsonl | Select-Object -Last 10

# 2. Outbound audit (reply text up to 4000 chars, dispatch_ms, send_ms, total_ms)
type data\telegram-bridge\outbound.jsonl | Select-Object -Last 10

# 3. Per-flow log (narrative)
type logs\unified\telegram-bridge.log | Select-Object -Last 50

# 4. Session log (interleaved with all other flows; good for cross-flow debugging)
type logs\unified\_session.log | Select-Object -Last 100

# 5. OpenClaw run artifacts (only for ask/fallback paths)
Get-ChildItem data\runs\openclaw -Filter '*_ask_*.json' | Sort-Object LastWriteTime -Descending | Select-Object -First 5
```

To answer "what did the bot reply to message X?" read `data\telegram-bridge\outbound.jsonl` — `reply_preview` carries up to 4000 chars of the actual reply text.

To answer "how long did message X take?" read the `dispatch_ms`, `send_ms`, and `total_ms` fields on the outbound JSONL entry.

To answer "did the bridge match the right rule?" read the `rule_id` field on the inbound JSONL entry.

### Common scenarios

| Symptom | Likely cause | Action |
|---|---|---|
| No reply at all | Bridge process not running. | `python .\devctl.py bridge status`; if disabled or missing token, fix; otherwise `python .\devctl.py bridge run`. |
| Reply takes 60–90s | `ask`/fallback path through OpenClaw with `thinking=medium`. | Expected. Use a shortcut phrase or `task <name>` if you want fast. |
| `Unknown task '<name>'` reply | `task <name>` used a name not in `[laptop_tasks.tasks]`. | `python .\devctl.py task list`. |
| `requires confirm` reply | Task has `requires_confirm = true` and you didn't include `confirm`. | Send `task <name> confirm`. |
| Telegram shows truncated message | Reply truncated to ≤3500 chars by bridge before send. | Read `outbound.jsonl` for the full preview. |
| Unauthorized sender (no reply) | Sender id not in OpenClaw `commands.ownerAllowFrom`. | `python .\devctl.py mobile owner set --owner "telegram:<numeric-id>" --confirm`. |

### Latency expectations

| Path | Typical |
|---|---|
| Shortcut → local task → Bot API reply | 2–6 s |
| `task <name>` → local task → Bot API reply | 2–8 s |
| `ask <text>` / fallback → OpenClaw `main` (`thinking=medium`) → reply | 60–90 s |

If `ask` latency starts running far longer than 90s, OpenClaw is likely unhealthy. Read `python .\devctl.py openclaw doctor-triage` and the watchdog log before assuming the bridge is at fault.

## OpenClaw and Recipes

List configured recipes:

```powershell
python .\devctl.py recipes
```

Preview a recipe:

```powershell
python .\devctl.py run summarize-ultracode --dry-run
```

Inspect errors:

```powershell
python .\devctl.py logs errors --source all --limit 80
python .\devctl.py logs errors --source ultracode --limit 80
.\scripts\latest-openclaw-errors.ps1 --limit 20
```

Check or recover OpenClaw reliability:

```powershell
python .\devctl.py watchdog status
python .\devctl.py watchdog run
python .\devctl.py watchdog schedule-status
```

Validate model routing:

```powershell
python .\devctl.py openclaw models-validate
python .\devctl.py agents list --actual
python .\devctl.py agents smoke --role agentic
python .\devctl.py agents smoke --role max
.\scripts\model-routes.ps1
```

Triage OpenClaw doctor warnings:

```powershell
python .\devctl.py openclaw doctor-triage
python .\devctl.py openclaw doctor-triage --refresh
python .\devctl.py openclaw doctor-review
.\scripts\openclaw-doctor-triage.ps1
```

Use local tools:

```powershell
python .\devctl.py notes add --title "Idea" --body "Short local note" --tag personal
python .\devctl.py notes list
python .\devctl.py todos add --title "Review logs"
python .\devctl.py todos list
python .\devctl.py files search --scope personal --query "OpenClaw"
python .\devctl.py brief daily --show
```

Use approved laptop tasks:

```powershell
python .\devctl.py task list
python .\devctl.py task run ultracode-latest-errors
python .\devctl.py task run ultracode-latest-errors --send-telegram --confirm
python .\devctl.py task run screen-primary-screenshot
python .\devctl.py task run screen-primary-screenshot --send-telegram --confirm
python .\devctl.py task run ultracode-start-hotkeys --confirm
python .\devctl.py task run ultracode-start-hotkeys --send-telegram --confirm
```

Telegram phrases for the bot:

```text
check ultracode latest errors
send my current screen screenshot to telegram confirm
start ultracode hotkeys confirm
```

The hotkey task uses UltraCode's configured backend script instead of simulating
keyboard input. It is confirmation-gated because the existing Ctrl+Shift+W
backend can also background helper services.

Preview normal full Desktop sync:

```powershell
$env:DESKSYNC_CI = "1"
..\..\sync-to-personal-desktop.bat -n
```

Do not run the real Desktop sync automatically. When the user explicitly asks for normal sync, run `..\..\sync-to-personal-desktop.bat`; personal-assistant is included automatically. Use `--pa` / `-pa` only for personal-assistant-only sync, and `--no-pa` / `-npa` only to exclude it.

Check the local log viewer:

```powershell
Invoke-WebRequest -Uri "http://127.0.0.1:7000/api/health" -UseBasicParsing
Invoke-WebRequest -Uri "http://127.0.0.1:7000/api/files?refresh=1" -UseBasicParsing
```

## Mobile Command Workflow

Capture a command:

```powershell
python .\devctl.py mobile capture --source whatsapp --sender "<sender>" --channel personal --message "summarize ultracode launcher"
```

List pending commands:

```powershell
python .\devctl.py mobile list --status pending
```

Show local webhook endpoint:

```powershell
python .\devctl.py mobile webhook info
```

Run local webhook bridge on loopback:

```powershell
python .\devctl.py mobile webhook serve
```

The bridge captures POSTed JSON commands into the same mobile inbox. It is loopback-only by default.

Check external mobile readiness:

```powershell
python .\devctl.py mobile external info
python .\devctl.py mobile channel status --json
python .\devctl.py mobile owner status --json
```

Current selected channel is Telegram through OpenClaw native channels. Store the bot token only in OpenClaw's local credentials token file, with S3 as the configured fallback restore source. Restart the OpenClaw gateway after token changes before checking channel status. Do not expose a tunnel or set command owners until the approved Telegram numeric user id is known.

Dry-run drain:

```powershell
python .\devctl.py mobile drain --limit 3 --dry-run
```

Process commands after OpenClaw is ready:

```powershell
python .\devctl.py mobile drain --limit 3
```

Use `--role agentic` for coding-heavy work and `--role max` for hard reasoning. Add `--model` only after provider/model overrides are authorized for this OpenClaw caller.

## Archive Workflow

Preview or create a local archive:

```powershell
python .\devctl.py archive plan
python .\devctl.py archive create
```

The current archive backend is 7-Zip `.7z` with the `ppmd-text-ultra-no-times` profile. Change compression behavior in `[archive]`, not in Python or wrapper scripts.

S3 upload uses the dedicated private bucket from `[archive]` and still requires a deliberate confirmation flag:

```powershell
python .\devctl.py archive create --upload-s3 --confirm
```

Verify or reapply the configured S3 lifecycle/Object Lock policy:

```powershell
python .\devctl.py archive lifecycle-plan
python .\devctl.py archive lifecycle-apply --confirm
python .\devctl.py archive lifecycle-verify
```

Check or manage the daily S3 archive upload schedule:

```powershell
python .\devctl.py archive schedule-status
python .\devctl.py archive schedule-plan
python .\devctl.py archive schedule-install --confirm
python .\devctl.py archive schedule-delete --confirm
```

The current configured task runs daily at `22:30` and uploads through the logged `archive-to-s3` alias. It uses a `wscript.exe` launcher so the scheduled run stays windowless.

## Documentation Workflow

Use this checklist for every change:

- Did `config/settings.toml` change? Update `docs/CONFIG.md`.
- Did setup or version state change? Update `docs/SETUP.md` and `LOCAL-SETUP.md`.
- Did runner behavior change? Update `docs/RUNNERS.md`.
- Did architecture or folder ownership change? Update `docs/ARCHITECTURE.md`.
- Did safety behavior change? Update `docs/ENGINEERING-STANDARD.md`.
- Did current status or remaining work change? Update `CHECKPOINT.md`.
- Did roadmap change? Update `PLAN.md`.

## Logging Workflow

Custom Python and PowerShell tools must log to:

```text
logs/unified/<flow>.log
logs/unified/_session.log
logs/py/<flow>.log
logs/ps1/<flow>.log
```

Log doc/checkpoint changes with:

```powershell
. .\shared\ps1\_log_helper.ps1
$script:logContext = Initialize-PALogging -FlowName "checkpoint" -ScriptType "ps1"
Write-PALog -Level "OK" -Message "documentation updated"
```

## Config Workflow

Do not edit Python or PowerShell to change:

- model ids
- provider names
- directories
- timeouts
- limits
- log scan rules
- aliases
- prompt recipes
- mobile defaults
- include/exclude scopes

Edit:

```text
config/settings.toml
```

Then run:

```powershell
python -m compileall .\assistant .\shared .\devctl.py .\tests
python -m unittest discover -s tests -v
python .\devctl.py smoke
```

## Current Infrastructure State

OpenClaw local onboarding is complete. Gateway recovery is now guarded by the config-driven watchdog and the scheduled task `PersonalAssistantOpenClawWatchdog`.

Current local tools:

1. Notes, todos, approved-folder search, and daily brief are implemented through `devctl.py`.
2. Tool data lives under `data/` and is excluded from Desktop sync.
3. Tool settings live under `[tools.*]` in `config/settings.toml`.

Remaining infrastructure work:

1. Role-based routing is implemented through `main`, `pa-codex`, and `pa-max`.
2. Keep model validation focused on the configured Copilot routes unless the user explicitly asks to add another model.
3. Decide whether mobile commands should auto-drain or stay manual-review first.
4. Keep Telegram/OpenClaw reliability under observation through `openclaw-watchdog.log`.
5. Resolve Windows browser skill symlink warning only if browser automation is needed.
6. Build the simple local OpenClaw UI after the user confirms the core flow looks good.
