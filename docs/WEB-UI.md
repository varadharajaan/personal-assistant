# Web UI Console (Phase 6)

Local-only operator surface for the assistant. Loopback only. Exposes chat,
recipe buttons, log tails, and a status panel through a single stdlib
`http.server` that delegates real work to existing `assistant.devctl` modules.

## TL;DR

```powershell
python .\devctl.py web info     # show configured surface
python .\devctl.py web serve    # start the server on 127.0.0.1:7100
# then open: http://127.0.0.1:7100
```

The server binds to `127.0.0.1` only and additionally enforces a `Host`
header allowlist. External clients receive a `403`. Stop the server with
Ctrl-C.

## What the page shows

Four panels:

1. **💬 Chat** &mdash; text input bound to `/api/chat`. Each turn calls
   `openclaw agent --message <text> --agent <main> --thinking <off>`
   (parameters resolved by `assistant.devctl.agent_roles.resolve_agent_call`
   so the surface is identical to the Telegram bridge). The reply JSON
   envelope is unwrapped by the same extractor the bridge uses
   (`assistant.devctl.telegram_bridge._extract_openclaw_reply`).
2. **⚡ Recipes** &mdash; clickable buttons sourced from
   `[web_ui].recipe_buttons` in `config/settings.toml`. Each button either
   runs an approved laptop task (`kind = "task"`) or fetches a log tail
   (`kind = "log-tail"`). Tasks re-enter the same
   `run_laptop_task` codepath the Telegram bridge uses; safety/confirm gates
   cannot diverge.
3. **🪵 Recent logs** &mdash; tails the unified flow files configured under
   `[web_ui].log_panels`. Default panels: `_session`, `telegram-bridge`,
   `openclaw-watchdog`. Auto-refreshes every 8 s.
4. **📡 Status** &mdash; runs the checks configured under
   `[web_ui].status_checks`. Supports two kinds:
   - `schtasks`: queries `schtasks /Query /TN <task_name>` and reports
     `ok | fail | unknown`.
   - `openclaw-cmd`: runs an arbitrary `openclaw` subcommand through the
     logged `OpenClawRunner` and reports the first line of stdout.

## API surface (loopback only)

| Method | Path           | Purpose                                       |
| ------ | -------------- | --------------------------------------------- |
| GET    | `/`            | renders the page (`index.html` from template) |
| GET    | `/api/health`  | `{ok: true, ts}`                              |
| GET    | `/api/recipes` | configured recipe buttons                     |
| GET    | `/api/status`  | latest status snapshot                        |
| GET    | `/api/logs`    | unified-log panel snapshot                    |
| POST   | `/api/chat`    | one chat turn -> `main` agent                 |
| POST   | `/api/recipe`  | run one recipe by id                          |

Every request is gated by the loopback + Host check; rejections return
`403` with the configured `[web_ui].unauthorized_message`.

## Logging

Every HTTP request and every dispatched action emits one or more
structured lines through the unified flow `web-ui`
(configured by `[flows].web_ui`):

```
[YYYY-MM-DD HH:MM:SS] [PY] [web-ui] [INFO] web ui chat dispatch | role=default | message_chars=22
[YYYY-MM-DD HH:MM:SS] [PY] [web-ui] [INFO] openclaw command started | command=openclaw agent --message ... --agent main --thinking off | cwd=...
[YYYY-MM-DD HH:MM:SS] [PY] [web-ui] [OK]   openclaw command finished | command=... | returncode=0 | elapsed=NN.NNs | stdout_chars=... | stderr_chars=0 | timed_out=False
```

Logs live under `logs/unified/web-ui.log` (per-flow) and
`logs/unified/_session.log` (cross-flow mirror), like every other surface.

## Configuration

All of the following live in `config/settings.toml -> [web_ui]`. The fields
are intentionally narrow; new panels are config-only:

```toml
[web_ui]
enabled = true
host = "127.0.0.1"
port = 7100
allowed_hosts = ["127.0.0.1", "localhost"]
templates_dir = "{project_root}/assistant/devctl/web_ui_templates"
title = "Personal Assistant Console"
chat_default_role = "default"
chat_default_timeout_seconds = 300
chat_max_message_chars = 4000
chat_reply_max_chars = 8000

recipe_buttons = [
  { id = "screenshot",      label = "📸 Screenshot",          kind = "task", task_name = "screen-primary-screenshot",  task_confirm = true,  task_send_telegram = false },
  { id = "app-errors",      label = "🩺 App: latest errors",  kind = "task", task_name = "app-latest-errors",          task_confirm = true,  task_send_telegram = false },
  { id = "start-app",       label = "🚀 Start app hotkeys",   kind = "task", task_name = "start-app-hotkeys",          task_confirm = true,  task_send_telegram = false },
  { id = "openclaw-errors", label = "📜 OpenClaw recent errors", kind = "log-tail", source = "openclaw", lines = 80, errors_only = true },
  # ...
]

log_panels = [
  { id = "session", label = "Session log", flow = "_session",         max_lines = 200 },
  { id = "bridge",  label = "Bridge log",  flow = "telegram-bridge",  max_lines = 200 },
  { id = "watchdog",label = "Watchdog log",flow = "openclaw-watchdog", max_lines = 200 },
]

status_checks = [
  { id = "bridge-task",      label = "Telegram bridge (scheduled task)", kind = "schtasks", task_name = "PersonalAssistantTelegramBridge" },
  { id = "watchdog-task",    label = "OpenClaw watchdog (scheduled task)", kind = "schtasks", task_name = "PersonalAssistantOpenClawWatchdog" },
  { id = "archive-task",     label = "Daily archive (scheduled task)",   kind = "schtasks", task_name = "PersonalAssistantArchiveToS3" },
  { id = "openclaw-version", label = "OpenClaw CLI version",             kind = "openclaw-cmd", args = ["--version"], timeout = 30 },
]
```

### Adding a recipe button

Append to `recipe_buttons`, restart `python .\devctl.py web serve`. No
Python edits.

```toml
{ id = "daily-brief", label = "🧾 Daily brief", kind = "task", task_name = "daily-brief", task_confirm = true, task_send_telegram = false }
```

### Adding a status check

Append to `status_checks`. Two kinds supported today:

* `kind = "schtasks"` &mdash; pass `task_name = "<name>"`.
* `kind = "openclaw-cmd"` &mdash; pass `args = ["<openclaw-subcommand>", ...]`.

## Security notes

* **Loopback only.** External binds are not supported through config.
* **Host-header allowlist** defends against DNS-rebinding.
* **No auth on top of loopback.** Anyone with a shell on this machine can
  hit `127.0.0.1:7100`. That is the same trust boundary as the Telegram
  bridge and the watchdog.
* **No secrets in HTML.** The page never includes the Telegram token,
  OpenClaw credentials, owner ids, or audit JSONL contents.
* **Same safety gates as Telegram.** A recipe button cannot run a task
  that the Telegram bridge cannot run. Confirm flags come from the recipe
  config; `task_send_telegram` is honored.

## Tests

`tests/test_web_ui.py` covers:

* recipe definition shape from live config
* recipe dispatcher (unknown kind, unknown task, task dispatch)
* status-check dispatcher (unsupported kind, missing schtasks, failed
  query, successful query parsing)
* loopback + Host-header enforcement on the handler
* host header normalisation helper

Live chat / agent calls are intentionally exercised only via the smoke
flow `python .\devctl.py web serve` &rArr; browser, because they require
an authenticated OpenClaw gateway.

## Roadmap

* Optional: `web schedule-install` to register a `PersonalAssistantWebUI`
  scheduled task (config already provisioned at `[web_ui_schedule]`).
* Optional: streamed responses (SSE) for chat so the user sees tokens
  arrive rather than a single 30-80 s wait.
* Optional: dark-only theme toggle, font-size knob.
