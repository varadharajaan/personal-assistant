# Personal Assistant

Laptop-first personal assistant for daily work automation, notes, reminders, local file help, OpenClaw control, mobile command capture, and a Telegram bot interface.

Maintained as a principal-engineer quality repo: configuration-driven, logged, documented, safe by default.

## What's Working Today

| Capability | Path | Status |
|---|---|---|
| Telegram inbound (receive) | Independent Telegram bridge (long-poll `getUpdates`) | working |
| Telegram outbound (reply) | Telegram Bot API direct | working |
| Owner-only command filtering | OpenClaw `commands.ownerAllowFrom` resolved at startup | working |
| Shortcut phrases (`screenshot`, `ultracode`, `ultracode errors`, `ping`, `help`) | Bridge `kind=task`/`canned`/`help` rules | working, ~2–6s |
| `task <name>` explicit dispatch | Bridge `kind=task-by-name` | working, ~2–8s |
| Natural language `ask <text>` / fallback | Bridge → OpenClaw agent `main` (`thinking=medium`) | working, ~60–90s |
| OpenClaw native Telegram polling | Disabled (`channels.telegram.enabled = false`) | intentionally off |
| OpenClaw native Telegram send | Disabled in `[laptop_tasks.telegram].delivery_order` | bot-api only |
| Local laptop tasks | `devctl.py task run …` | working |
| Notes / todos / file search / daily brief | `devctl.py notes|todos|files|brief …` | working |
| Memory archive (local + S3 daily upload) | `PersonalAssistantArchiveToS3` task | working |
| OpenClaw watchdog (kept for safety) | `PersonalAssistantOpenClawWatchdog` task | working |
| Telegram bridge as scheduled task | `PersonalAssistantTelegramBridge` (ONLOGON) | configured, install with `bridge schedule-install --confirm` |

For the exact current checkpoint, read [CHECKPOINT.md](./CHECKPOINT.md).

## Telegram Bot Usage (operator quick reference)

The bot username is the one paired during onboarding. Send messages directly from your Telegram client. Only the approved owner id is accepted; other senders are silently ignored.

### Shortcut commands (fast, no OpenClaw — typical 2–6 seconds)

| Send | What happens |
|---|---|
| `ping` | Reply `pong`. Health check. |
| `help`, `?`, `/help` | Reply with the full command list built from the live rule set. |
| `screenshot`, `screen`, `take screenshot`, `screen shot` | Capture primary screen; send PNG to Telegram. |
| `start ultracode`, `restart ultracode`, `ultracode`, `start ahk`, `restart ahk`, `ahk` | Run UltraCode launcher; reply with the `DONE All AHK scripts launched …` log line. |
| `ultracode errors`, `ultracode latest errors`, `ultracode logs` | Reply with the latest UltraCode warning/error log lines. |

### Generic forms

| Send | What happens | Latency |
|---|---|---|
| `task <name>` | Run any approved laptop task. Append `confirm` if it requires confirm. e.g. `task screen-primary-screenshot confirm`. | ~2–8s |
| `ask <text>` | Route text to OpenClaw agent `main` (model: `gpt-5.4`, `thinking=medium`). Supports natural language and OpenClaw tool use. | ~60–90s |
| anything else | Treated as `ask` (same path). | ~60–90s |

### Natural language status

Natural-language input **works** and is handled by OpenClaw agent `main`. It can choose to call tools (laptop tasks, file search, etc.) when the agent harness permits. Trade-off: ~60–90s reply latency because of `thinking=medium` plus tool round-trips.

For frequent commands, prefer the shortcut phrases above; they bypass OpenClaw entirely and are an order of magnitude faster.

To add a new shortcut, edit `[[telegram_bridge.rules]]` in `config/settings.toml` with `kind = "task"` and the desired `patterns`, `task_name`, `task_confirm`, `task_send_telegram`. Restart the bridge.

### Running the bridge

```powershell
# Smoke check (does not poll)
python .\devctl.py bridge status

# Show all dispatch rules
python .\devctl.py bridge rules

# Foreground run (Ctrl+C to stop)
python .\devctl.py bridge run

# Single poll-and-exit (for diagnostics)
python .\devctl.py bridge run --once

# Install as silent Windows scheduled task (runs at logon)
python .\devctl.py bridge schedule-install --confirm
python .\devctl.py bridge schedule-status
```

### Debugging a slow or missing reply

Every inbound message and every outbound reply is logged. To trace a single message:

```powershell
# 1. Inbound audit — full message text up to 4000 chars, dispatch latency, matched rule
type data\telegram-bridge\inbound.jsonl | Select-Object -Last 5

# 2. Outbound audit — reply text up to 4000 chars, dispatch_ms, send_ms, total_ms, send_ok
type data\telegram-bridge\outbound.jsonl | Select-Object -Last 5

# 3. Per-flow narrative log
type logs\unified\telegram-bridge.log | Select-Object -Last 50

# 4. Session-wide log (all flows interleaved)
type logs\unified\_session.log | Select-Object -Last 100

# 5. Latest OpenClaw artifacts (for ask/fallback-ask paths)
Get-ChildItem data\runs\openclaw -Filter '*_ask_*.json' | Sort-Object LastWriteTime -Descending | Select-Object -First 5
```

Each inbound JSONL line carries: `processed_at`, `update_id`, `sender_hash`, `chat_id_hash`, `char_count`, `rule_id`, `dispatch_ms`, `text_preview` (up to 4000 chars). Each outbound JSONL line carries: `sent_at`, `update_id`, `sender_hash`, `rule_id`, `dispatch_returncode`, `send_ok`, `send_returncode`, `send_detail`, `reply_chars`, `reply_preview` (up to 4000 chars), `dispatch_ms`, `send_ms`, `total_ms`.

For historic bot queries (e.g. "what did the bot reply to X yesterday?"), `inbound.jsonl` and `outbound.jsonl` are the source of truth. Sender ids are hashed; raw text bodies are present for the configured preview length.

## Repo Layout

- `config/settings.toml` — central configuration (no values hardcoded in Python or PowerShell).
- `assistant/devctl/` — Python control plane modules (OpenClaw runner, mobile owner, watchdog, laptop tasks, **Telegram bridge**, archive).
- `devctl.py` — CLI entry point.
- `scripts/` — PowerShell + VBS wrappers.
- `shared/python/pa_logging.py` — shared logging helper.
- `logs/unified/<flow>.log` — primary per-flow logs.
- `logs/unified/_session.log` — cross-flow mirror.
- `data/telegram-bridge/` — bridge runtime (offset state, inbound + outbound audit JSONL).
- `data/runs/openclaw/` — OpenClaw run artifacts (per `ask`, `task`, `models-validate`, etc.).
- `docs/` — design contracts and operator guides.

## Start Here

1. [CHECKPOINT.md](./CHECKPOINT.md) — current state, completed work, remaining work, cross-chat handoff.
2. [docs/ARCHITECTURE.md](./docs/ARCHITECTURE.md) — system design, boundaries, data flow.
3. [docs/SETUP.md](./docs/SETUP.md) — setup, verification, onboarding, recovery commands.
4. [docs/WORKING-GUIDE.md](./docs/WORKING-GUIDE.md) — day-to-day workflows including Telegram operator guide.
5. [docs/MOBILE-BRIDGE.md](./docs/MOBILE-BRIDGE.md) — full Telegram bridge reference.
6. [PLAN.md](./PLAN.md) — roadmap, phases, open decisions.

## Core Docs

- [docs/DOCUMENTATION-STANDARD.md](./docs/DOCUMENTATION-STANDARD.md) — documentation rules.
- [docs/CONFIG.md](./docs/CONFIG.md) — centralized TOML configuration rule.
- [docs/LOGGING.md](./docs/LOGGING.md) — logging contract.
- [docs/RUNNERS.md](./docs/RUNNERS.md) — `devctl.py`, runner scripts, **bridge commands**, log inspection.
- [docs/LOCAL-TOOLS.md](./docs/LOCAL-TOOLS.md) — local notes, todos, file search, daily brief.
- [docs/LAPTOP-TASKS.md](./docs/LAPTOP-TASKS.md) — approved laptop tasks.
- [docs/MOBILE-BRIDGE.md](./docs/MOBILE-BRIDGE.md) — **Telegram bridge + local webhook**.
- [docs/OPENCLAW-RELIABILITY.md](./docs/OPENCLAW-RELIABILITY.md) — gateway diagnosis, watchdog.
- [docs/ENGINEERING-STANDARD.md](./docs/ENGINEERING-STANDARD.md) — SOLID, plan-mode contract.
- [LOCAL-SETUP.md](./LOCAL-SETUP.md) — verified local machine state.
- [openclaw/config-notes.md](./openclaw/config-notes.md) — OpenClaw provider, safety, setup.

## Useful Commands

```powershell
# Bridge (Telegram)
python .\devctl.py bridge status
python .\devctl.py bridge rules
python .\devctl.py bridge run
python .\devctl.py bridge run --once
python .\devctl.py bridge schedule-plan
python .\devctl.py bridge schedule-install --confirm
python .\devctl.py bridge schedule-status

# Startup / watchdog
.\scripts\start.ps1
python .\devctl.py watchdog status
python .\devctl.py watchdog schedule-status

# Smoke / agents
python .\devctl.py smoke
python .\devctl.py agents list --actual
python .\devctl.py openclaw models-validate
python .\devctl.py openclaw doctor-triage

# Local tools
python .\devctl.py notes list
python .\devctl.py todos list
python .\devctl.py files search --scope personal --query OpenClaw
python .\devctl.py brief daily --show

# Laptop tasks
python .\devctl.py task list
python .\devctl.py task run ultracode-latest-errors
python .\devctl.py task run screen-primary-screenshot --send-telegram --confirm
python .\devctl.py task run ultracode-start-hotkeys --send-telegram --confirm

# Archive
python .\devctl.py archive plan
python .\devctl.py archive lifecycle-verify
python .\devctl.py archive schedule-status

# Mobile owner / channels (kept for completeness)
python .\devctl.py mobile owner status --json
python .\devctl.py mobile channel status --json
python .\devctl.py mobile webhook info

# Wrappers
.\scripts\pa.ps1 smoke
.\scripts\summarize-ultracode.ps1 --dry-run
.\scripts\latest-openclaw-errors.ps1 --limit 20
.\scripts\model-routes.ps1
.\scripts\openclaw-doctor-triage.ps1
```

## Repo Law

Every meaningful code, config, workflow, setup, model, safety, or architecture change must update the relevant docs and, when it changes current state or next steps, `CHECKPOINT.md`.
