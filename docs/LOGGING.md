# Logging Contract

Personal Assistant logging follows the same shape as `example-app-launcher` and `jar`.

## Log Layout

```text
personal-assistant/
  logs/
    unified/
      _session.log
      <flow>.log
    ps1/
      <flow>.log
    py/
      <flow>.log
```

## Line Format

Unified and session logs use:

```text
[YYYY-MM-DD HH:MM:SS] [LANG] [flow] [LEVEL] message
```

Examples:

```text
[2026-05-10 19:45:00] [PS1] [openclaw-setup] [INFO] setup started
[2026-05-10 19:45:03] [PY] [memory-sync] [OK] archive complete | records=42
```

## Rules

- All scripts and skills must use the shared logging helpers.
- `devctl.py` logs every parsed command at the control-plane boundary with a start record and a terminal `OK` or `ERROR` record.
- Unified per-flow logs are always written to `logs/unified/<flow>.log`.
- Cross-flow session logs are always mirrored to `logs/unified/_session.log`.
- Per-language logs are written to `logs/<lang>/<flow>.log`.
- `TRACE` and `DEBUG` logs are only written when `PERSONAL_ASSISTANT_DEBUG_LOGS=1` or the PowerShell helper is initialized with `-VerboseLog`.
- Log files roll at 10 MB.
- Log files older than 90 days are cleaned up during helper initialization.
- Logs should not contain secrets, tokens, full auth headers, or private message bodies unless the flow is explicitly a local private note flow.
- PowerShell wrapper logs are file-only by default because `[logging].ps1_console_enabled = false`; this keeps command and JSON output clean.

## Python Devctl Modules

The logging boundary is deliberate:

- `devctl.py` logs every CLI invocation through the `devctl` flow.
- Modules with side effects, such as OpenClaw execution, mobile capture, archive creation, startup, and agent role repair, either receive a logger from the caller or emit through that logged runner.
- Pure modules, such as config loading, path constants, recipe parsing, model-list parsing, and doctor text classification, do not write logs on every function call. They are logged by the command or service that invokes them.

This keeps reusable functions testable and avoids noisy logs while still ensuring every custom tool flow has an auditable start and finish line.

## Telegram Bridge Logging

The independent Telegram bridge logs under flow name `telegram-bridge` (configured by `flows.telegram_bridge`). Three sinks:

1. **Per-flow narrative log** — `logs/unified/telegram-bridge.log`
   - records bridge start, poll cycle outcome, owner cache refresh, inbound message receipt, rule match, dispatch latency, reply send result.
   - mirrors to `logs/unified/_session.log`.

2. **Inbound audit JSONL** — `data/telegram-bridge/inbound.jsonl`
   - fields: `processed_at`, `update_id`, `sender_hash`, `chat_id_hash`, `char_count`, `rule_id`, `dispatch_ms`, `text_preview` (up to `[telegram_bridge].inbound_text_max_chars_for_log` chars, default 4000).
   - sender ids are hashed; raw text body is present up to the configured cap for historic bot-query investigation.

3. **Outbound audit JSONL** — `data/telegram-bridge/outbound.jsonl`
   - fields: `sent_at`, `update_id`, `sender_hash`, `rule_id`, `dispatch_returncode`, `send_ok`, `send_returncode`, `send_detail`, `reply_chars`, `reply_preview` (up to `[telegram_bridge].outbound_text_max_chars_for_log` chars, default 4000), `dispatch_ms`, `send_ms`, `total_ms`.

Tokens are never logged. Token-redaction patterns in `[logs]` apply to any log inspector path that might surface bridge output.

For historic queries ("what did the bot say to X?", "how long did message X take?", "which rule matched X?") use the audit JSONL files; they are the source of truth.

## Approved Levels

- `TRACE`: very fine-grained diagnostics, gated.
- `DEBUG`: implementation details, gated.
- `INFO`: normal progress.
- `OK`: successful completion or milestone.
- `WARN`: recoverable issue.
- `ERROR`: failed operation.

## Archive Direction

Local archive support is implemented through `python .\devctl.py archive create`. S3 upload is configured for a dedicated bucket, but upload execution must still be explicit and logged:

- Local storage remains the source of truth.
- S3 archive writes must log bucket/key prefix and return code, but not credentials or AWS secret material.
- Archive creation must log source bytes, compressed archive bytes, compression backend, and compression profile.
- Restore flows must write a summary to `logs/unified/memory-archive.log` and `_session.log`.
- Archive uploads require confirmation through `--confirm`.
- The configured S3 bucket is private and dedicated to personal-assistant/OpenClaw archives: `<your-archive-bucket>`.
- The bucket has delete-protection policy, Object Lock governance retention, and a 60-day lifecycle rule; verification failures for attempted deletes or blocked lifecycle mutation are expected and should be logged/summarized as successful protection checks, not runtime failures.
- `archive lifecycle-apply --confirm` must log the temporary lifecycle-mutation guard relax and the guard restore.
- `archive schedule-*` commands must log task install/status/delete/run-now operations.
- Scheduled archive runs enter through `scripts/archive-to-s3.vbs`, which logs the silent launcher start/finish, then starts `scripts/archive-to-s3.ps1` hidden so the PowerShell wrapper and Python archive upload path also write to unified logs.

## Log Viewer Integration

The existing example-app log viewer on `http://127.0.0.1:7000` includes personal-assistant and safe OpenClaw log roots by default.

Included roots:

- `projects/personal-assistant/logs`
- `~/.openclaw/logs`
- `%TEMP%/openclaw`

The viewer discovers `.log`, `.json`, and `.jsonl` files and skips dotfiles. Do not add broad OpenClaw state folders such as credentials, devices, agents, tasks, or canvas to the viewer unless a future review confirms they are safe to expose.

## VS Code Colors

Workspace VS Code settings force `.log` files to use the built-in `log` language and map log token scopes to the project severity colors:

- `ERROR`: red and bold.
- `WARN`: amber and bold.
- `INFO`: blue.
- `DEBUG`: gray.

The setting is present at the Desktop workspace level and inside `projects/personal-assistant/.vscode` so the colors work whether the full Desktop folder or only this repo is opened.
