# Laptop Tasks

Laptop tasks are approved, named local actions exposed through `devctl.py`.
They are the safe bridge between OpenClaw/Telegram natural language and this
Windows laptop.

## Rule

Use OpenClaw native features first. The local task runner only handles:

- task name resolution
- config-driven command execution
- local logging
- confirmation gates
- screenshot capture
- delivery recovery/fallback when OpenClaw native Telegram send times out

All task values live in `config/settings.toml` under `[laptop_tasks]`.

## Commands

List tasks:

```powershell
python .\devctl.py task list
```

Check ExampleApp Launcher warning/error logs:

```powershell
python .\devctl.py task run app-latest-errors
```

Check ExampleApp Launcher warning/error logs and send the result to Telegram:

```powershell
python .\devctl.py task run app-latest-errors --send-telegram --confirm
```

Capture the primary screen locally:

```powershell
python .\devctl.py task run screen-primary-screenshot
```

Capture and send the primary screen to the approved Telegram owner:

```powershell
python .\devctl.py task run screen-primary-screenshot --send-telegram --confirm
```

Start/restart ExampleApp hotkeys using the same backend as Ctrl+Shift+W:

```powershell
python .\devctl.py task run start-app-hotkeys --confirm
```

Start/restart ExampleApp hotkeys and send an independent Telegram confirmation:

```powershell
python .\devctl.py task run start-app-hotkeys --send-telegram --confirm
```

On success, the Telegram confirmation is not the generic return code. The task
reads the latest configured ExampleApp launcher success line from
`example-app-launcher/logs/unified/start-all.log`, strips the configured log
prefixes, and sends a message like:

```text
DONE All desktop hotkey scripts launched | delay=414ms total=2429ms
```

## Telegram Phrases

Ask the OpenClaw Telegram bot naturally, but include explicit confirmation for
external send/system actions:

- `check example-app latest errors`
- `send my current screen screenshot to telegram confirm`
- `start example-app hotkeys confirm`

The OpenClaw workspace tells the agent to map those phrases to `devctl.py task`
commands instead of inventing shell commands.

For Telegram-originated laptop actions, the OpenClaw workspace uses
`--send-telegram --confirm` so the task runner sends its own result through the
configured recovery path. This protects the owner experience when OpenClaw's
normal final channel reply fails after the local action already succeeded.

## Safety

- `app-latest-errors` is read-only.
- `screen-primary-screenshot` writes a local PNG under `data/screenshots`.
- `screen-primary-screenshot --send-telegram` exports screen content and requires `--confirm`.
- `start-app-hotkeys` requires `--confirm` because `start-app.vbs` restarts a desktop automation tool and may background helper services exactly like the desktop shortcut.
- `start-app-hotkeys --send-telegram --confirm` is the preferred Telegram-owner route because it sends a separate task-level confirmation using the latest `DONE All desktop hotkey scripts launched` launcher log line.
- Do not simulate keypresses unless the user explicitly asks for keypress simulation. Prefer the configured backend script.
- ClipSync remains protected. The hotkey task exists because it is the existing ExampleApp shortcut backend; do not modify ClipSync pairing, ports, state, or config.

## Telegram Delivery

Delivery order is config-driven:

```toml
[laptop_tasks.telegram]
delivery_order = ["openclaw-native", "telegram-bot-api"]
```

The runner tries `openclaw message send --channel telegram` first. On this
laptop, that native path can time out through the gateway message-send RPC even
when Telegram itself is reachable. The runner now treats known gateway timeout
signatures as recoverable: it can restart only the OpenClaw gateway once, wait
for the configured delay, and retry native delivery before moving to the
Telegram Bot API fallback. If native delivery still fails, the fallback uses the
same local OpenClaw Telegram token file and Telegram Bot API.

The recovery behavior is config-driven:

```toml
[laptop_tasks.telegram]
native_retry_attempts = 2
native_restart_gateway_before_retry = true
native_retry_markers = ["gateway timeout after", "gateway request timeout", "gateway event loop readiness timeout"]
```

The token stays outside the repo:

```text
~\.openclaw\credentials\telegram-bot-token.txt
```

No token or raw owner id is written to docs or normal logs.

## Logs

Task runs log through the shared Python logger:

```text
logs/unified/laptop-task.log
logs/unified/_session.log
logs/py/laptop-task.log
```

OpenClaw-native delivery attempts, gateway restart retries, and fallback sends
are logged through the same flow. Delivery targets are redacted by
`openclaw.secret_flags` in `config/settings.toml`.
