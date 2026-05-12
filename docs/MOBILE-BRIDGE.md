# Mobile Bridge

There are now two distinct local components:

1. **Local HTTP webhook bridge** (`mobile webhook`) — loopback-only command capture. Does not send replies. Kept for completeness; not used as the primary mobile path.
2. **Independent Telegram bridge** (`bridge run`) — long-polls Telegram directly, dispatches to configured rules, and replies through the Bot API. This is the primary inbound path for the personal assistant. It does **not** depend on OpenClaw's gateway being healthy for receive/send; OpenClaw is only invoked when the matched rule asks for it.

OpenClaw's native Telegram channel is now disabled (`channels.telegram.enabled = false`) so the bridge is the sole Telegram receiver. Outbound from laptop tasks uses the Bot API directly via `[laptop_tasks.telegram].delivery_order = ["telegram-bot-api"]`.

## Independent Telegram Bridge

Long-running daemon (`python .\devctl.py bridge run`) that:

- reads the bot token from the existing OpenClaw credentials token file,
- long-polls `getUpdates` directly,
- filters senders against OpenClaw's `commands.ownerAllowFrom` (raw ids resolved at startup, kept in-memory only, never logged),
- dispatches each message through configured `[[telegram_bridge.rules]]`,
- replies via `sendMessage`,
- persists offset to `data/telegram-bridge/offset.json` so restarts neither replay nor drop updates,
- writes audit JSONL to `data/telegram-bridge/inbound.jsonl` and `outbound.jsonl` (sender id hashed, no message bodies by default).

### Commands

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

The scheduled task name is `PersonalAssistantTelegramBridge`. Schedule is `ONLOGON`, so the bridge starts at next login.

### Telegram Shortcut Commands

These phrases are matched **exactly** (case-insensitive). They bypass OpenClaw entirely, so replies are typically 2–6 seconds instead of 70–90 seconds.

| Phrase | What runs | Confirm needed | Reply |
|---|---|---|---|
| `ping` | canned reply | no | `pong` |
| `help`, `?`, `/help` | help text built from this rule list | no | command summary |
| `screenshot`, `screen`, `take screenshot`, `screen shot` | `task screen-primary-screenshot` with `--send-telegram --confirm` | implicit | screenshot delivered to Telegram + status line |
| `start ultracode`, `restart ultracode`, `ultracode`, `start ahk`, `restart ahk`, `ahk` | `task ultracode-start-hotkeys` with `--send-telegram --confirm` | implicit | UltraCode launcher result, e.g. `DONE All AHK scripts launched | delay=...ms` |
| `ultracode errors`, `ultracode latest errors`, `ultracode logs` | `task ultracode-latest-errors` with `--send-telegram` | no | recent warning/error log lines |

The shortcut rules are config-driven under `[[telegram_bridge.rules]]` with `kind = "task"`, `task_name`, `task_confirm`, `task_send_telegram`. Add new shortcuts there; do not hardcode them in Python.

### Generic Command Forms

- `task <name>` — run any approved laptop task by name. Append `confirm` if the task requires it. Examples:
  - `task ultracode-latest-errors`
  - `task screen-primary-screenshot confirm`
  - `task ultracode-start-hotkeys confirm`
- `ask <text>` — explicitly route text to OpenClaw agent `main` (`thinking=medium`). Slow (~70–90s) but supports natural language and tool use through the OpenClaw agent.
- Any other text falls through to the `fallback-ask` rule, which routes to OpenClaw the same way as `ask`.

### Latency Expectations

| Path | Typical |
|---|---|
| Shortcut → local task → Bot API reply | 2–6 s |
| `task <name>` → local task → Bot API reply | 2–8 s |
| `ask <text>` / fallback → OpenClaw `main` (`thinking=medium`) → reply | 60–90 s |

Audit JSONL captures `dispatch_ms` (model/task time), `send_ms` (Telegram send time), and `total_ms` so latency is observable per message.

### Owner Authorization

The bridge reads `commands.ownerAllowFrom` from OpenClaw at startup (and refreshes on cache miss every 60 s). Each value is normalised by stripping the `telegram:` / `tg:` prefix, leaving the raw numeric Telegram user id. Senders that don't match are logged at `WARN` with a hashed sender id and **no reply is sent** (`unauthorized_reply_enabled = false`).

To approve a new sender, use the existing `mobile owner set` flow:

```powershell
python .\devctl.py mobile owner set --owner "telegram:<numeric-user-id>" --confirm
```

The bridge picks up the change after its next 60 s cache refresh.

### Safety / Privacy

- The token is read only from `[telegram_bridge].token_file` and never logged.
- Raw owner ids never leave the in-memory `OwnerFilter` cache; the audit JSONL stores only hashed sender ids.
- Message bodies are not stored in general logs. The inbound JSONL stores a `text_preview` only when `inbound_text_max_chars_for_log > 0` (default `0`, off).
- Reply truncation: bridge truncates outbound text to `reply_max_chars` (default 3500) so it fits inside Telegram's per-message limit.
- Token redaction patterns in `[logs]` still apply to any log inspector path that might surface bridge output.

### When To Use Which Path

- **Use shortcuts** (`screenshot`, `ultracode`, …) for daily commands. Fast, deterministic, no model cost.
- **Use `task <name>`** for any approved laptop task whose name you know.
- **Use `ask <text>`** when you actually need OpenClaw's reasoning or tool routing.

If a shortcut you want doesn't exist, add it to `[[telegram_bridge.rules]]` with `kind = "task"` rather than relying on `fallback-ask` through OpenClaw.

## Local HTTP Webhook (loopback only)

```text
http://127.0.0.1:8765/mobile/command
```

```text
http://127.0.0.1:8765/health
```

The default binding is loopback only.

## Commands

Show current endpoint details:

```powershell
python .\devctl.py mobile webhook info
```

Run the bridge:

```powershell
python .\devctl.py mobile webhook serve
```

Run one request and exit:

```powershell
python .\devctl.py mobile webhook serve --once
```

PowerShell wrapper:

```powershell
.\scripts\mobile-bridge.ps1
```

## Payload

Example:

```json
{
  "message": "summarize ultracode launcher",
  "sender": "phone",
  "source": "webhook",
  "channel": "local-webhook"
}
```

Accepted field names are configured in `config/settings.toml` under `[mobile_bridge]`.

## Security

- Keep the bridge on `127.0.0.1` unless you deliberately expose it.
- Before LAN or tunnel exposure, set `require_token = true`.
- Store the token in the configured `token_env_var`.
- Do not log private message bodies; the inbox stores full messages locally under `data/mobile/commands.jsonl`, while normal logs store metadata only.

## External Readiness

External mobile access is now selected as a native OpenClaw Telegram channel. The local webhook bridge remains loopback-only and separate from Telegram. Check readiness:

```powershell
python .\devctl.py mobile external info
python .\devctl.py mobile external info --json
```

OpenClaw channel helpers are available, but login should be explicit because some channels can open an interactive auth or QR flow:

```powershell
python .\devctl.py mobile channel status --json
python .\devctl.py mobile channel login --channel telegram --confirm
python .\devctl.py mobile channel qr --json
```

Telegram uses the native OpenClaw Telegram plugin. Its bot token lives in the local OpenClaw credentials token file referenced by `channels.telegram.tokenFile`. Do not paste the token into repo config files or command arguments.

The configured fallback copy lives in the private project S3 bucket. Check local/S3 status, upload the local token file, or restore it from S3 with:

```powershell
python .\devctl.py mobile token status --check-s3
python .\devctl.py mobile token backup-s3 --confirm
python .\devctl.py mobile token restore-s3 --confirm
```

Do not change the bridge to LAN or tunnel exposure until `mobile_bridge.require_token = true` and the token environment variable is set.

## Command Owner

OpenClaw owner-only commands must be bound to an explicit channel-native id, such as a WhatsApp phone id or Telegram user id. Telegram pairing has been approved for the current owner; repo logs and docs keep the owner id redacted.

Check current owner status:

```powershell
python .\devctl.py mobile owner status --json
```

Set it only after approval:

```powershell
python .\devctl.py mobile owner set --owner "telegram:<numeric-user-id>" --confirm
```

Owner ids are redacted in personal-assistant logs by default. The command path, owner display mode, confirmation requirement, restart behavior, and owner list all live under `[mobile_owner]` in `config/settings.toml`.

## Processing

Captured commands are listed and drained through the normal mobile commands:

```powershell
python .\devctl.py mobile list --status pending
python .\devctl.py mobile drain --limit 3
```
