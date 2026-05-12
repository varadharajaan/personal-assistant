# Central Configuration

All tunable values for Python and PowerShell runner code must live in:

```text
config/settings.toml
```

This includes:

- paths and runtime directories
- model ids
- model validation targets and availability status labels
- OpenClaw executable candidates
- OpenClaw output encoding
- Desktop sync include/exclude policy
- command aliases
- timeouts and limits
- mobile command defaults and statuses
- console output encoding behavior
- protected local service guardrails
- log sources, scan rules, regexes, and limits
- prompt recipes
- artifact naming rules
- flow/log names
- file scope include/exclude lists
- local tool paths, statuses, limits, approved scopes, and file suffixes
- mobile bridge host, port, path, auth header, token behavior, field mapping, and body limits
- external mobile exposure, OpenClaw channel defaults, command-owner policy, and startup actions
- doctor triage issue patterns, severities, recommendations, and refresh behavior
- agent role routing and local/S3 archive policy
- approved laptop tasks, screenshot capture, and Telegram delivery fallback
- OpenClaw watchdog health checks, process markers, stale-lock handling, direct fallback, and scheduled task settings

## Rule

Do not hardcode configurable values in `.py` or `.ps1` files.

Python and PowerShell may contain code structure, parser wiring, and bootstrap logic, but values that a user might reasonably change must be read from `config/settings.toml`.

Examples that belong in config:

- a model id
- a directory path
- a timeout
- a log line limit
- a mobile command status
- a recipe prompt
- an include/exclude rule
- a Desktop sync folder, extension list, alias, or ignore pattern
- a protected local service rule
- an OpenClaw provider
- a wrapper alias
- a local notes/todos/file-search/brief limit or status
- a mobile webhook field name, host, port, token setting, or payload limit
- an OpenClaw command-owner path, owner id list, redaction setting, or restart policy

## Desktop Sync

The Desktop sync onboarding for this repo is controlled by:

```toml
[desktop_sync]
```

`ultracode-launcher\desktop-sync\ps1\sync-to-personal-desktop-v3.ps1` reads that section and adds this repo as an external sync folder. Change sync aliases, formats, relative path, or ignore patterns in `config/settings.toml`, not inside the sync engine.

Current policy:

- Include only source, docs, scripts, config, `.env.example`, and OpenClaw workspace markdown/text files.
- Exclude generated/runtime/private folders such as `logs`, `data`, `__pycache__`, `.venv`, `node_modules`, and secret-like files.
- Plain `sync-to-personal-desktop.bat` includes this repo automatically with the normal configured folder set.
- `sync-to-personal-desktop.bat --pa -n` previews only the personal-assistant payload when a targeted preview is wanted.
- `--pa` / `-pa` is an optional include selector, not a required mode.

## Override Config Path

By default, Python loads:

```text
config/settings.toml
```

For experiments, set:

```powershell
$env:PERSONAL_ASSISTANT_CONFIG = "C:\path\to\settings.toml"
```

## PowerShell Wrappers

PowerShell wrapper scripts should not hardcode recipe names or log sources.

Wrappers derive their alias from their own filename and call:

```powershell
.\scripts\pa.ps1 alias <alias-name>
```

The alias is resolved from:

```toml
[aliases]
```

## OpenClaw Invocation

OpenClaw execution is configured, not hardcoded.

On Windows, `devctl.py` prefers direct Node invocation when enabled:

```toml
[paths]
openclaw_node_entrypoint = "{appdata}/npm/node_modules/openclaw/openclaw.mjs"

[openclaw]
windows_direct_node_entrypoint = true
node_executable_candidates = ["node.exe", "node"]
executable_candidates = ["openclaw.cmd", "openclaw.exe", "openclaw"]
output_encoding = "utf-8"
output_encoding_errors = "replace"
secret_value_markers = ["commands.ownerAllowFrom", "commands.ownerDisplaySecret"]
```

This runs `node.exe <configured openclaw.mjs>` before falling back to npm or PATH shims. Keep this behavior enabled unless OpenClaw changes its Windows install layout. It avoids brittle `.cmd` argument forwarding for long, multi-line recipe prompts.

`secret_value_markers` redacts sensitive positional values that follow config paths such as `commands.ownerAllowFrom`; keep owner ids and secret display salts out of logs and artifacts.
`secret_flags` must include OpenClaw provider and channel credential flags such as `--bot-token`, `--token-file`, `--access-token`, and `--secret` before any channel setup is routed through logged runner commands. It also redacts `--target` / `-t` so Telegram chat ids are not written into normal command logs.

## Model Override Guard

The desired and practical model names live in `[models]`.

Current model roles:

```toml
[models]
primary = "github-copilot/gpt-5.4"
agentic = "github-copilot/gpt-5.3-codex"
max_complex = "github-copilot/claude-opus-4.7"
```

`devctl.py` only passes a configured model by default when both switches allow it:

```toml
[models]
openclaw_agent_default_override_enabled = false

[agent]
use_configured_model_by_default = false
```

Keep these disabled until OpenClaw authorizes provider/model overrides for this caller. The global OpenClaw default is set through `openclaw models set github-copilot/gpt-5.4`; role-specific overrides should wait until OpenClaw permits them cleanly.

Role-specific routing is implemented through configured OpenClaw agents instead:

```toml
[agent.routing]
default_role = "default"

[[agent.routing.roles]]
name = "agentic"
agent = "pa-codex"
model = "github-copilot/gpt-5.3-codex"

[[agent.routing.roles]]
name = "max"
agent = "pa-max"
model = "github-copilot/claude-opus-4.7"
```

Use `python .\devctl.py agents ensure` after changing role config.

## Model Validation

Configured model diagnostics live under:

```toml
[models.validation]
[[models.validation.targets]]
```

`python .\devctl.py openclaw models-validate` reads these targets, runs the configured provider model list, and saves a validation artifact under `data/runs/openclaw/`. Use this instead of manually scanning model output when deciding whether the default, agentic, max-complex, max-intelligence, or fallback routes are actually visible.

The model-list timeout is controlled by:

```toml
[openclaw]
default_models_list_timeout_seconds = 180
models_list_timeout_actions = ["models-list", "models-validate"]
```

## Doctor Triage

OpenClaw doctor triage is configured under:

```toml
[doctor]
[[doctor.issue_patterns]]
```

`python .\devctl.py openclaw doctor-triage` reads the latest saved doctor report by default. `--refresh` runs a new doctor report first. Patterns, severities, and recommendations must stay in TOML so warnings can be reclassified without editing Python.

The triage command is read-only and does not apply `openclaw doctor --fix`. Safe repair is separately gated through:

```toml
[doctor]
safe_fix_requires_confirm = true
safe_fix_command = ["doctor", "--fix", "--non-interactive"]
```

## Local Tools

Local tool config lives under:

```toml
[tools.notes]
[tools.todos]
[tools.file_search]
[tools.daily_brief]
```

These sections control local storage ids, statuses, priorities, limits, approved file-search scopes, safe suffixes, exclude directories, sensitive-name patterns, and brief output naming. Change those values in TOML, not in Python.

## Laptop Tasks

Approved laptop actions live under:

```toml
[laptop_tasks]
[laptop_tasks.telegram]
[laptop_tasks.screenshot]
[[laptop_tasks.tasks]]
```

Current tasks:

- `ultracode-latest-errors`: reads UltraCode Launcher logs through generic configured log source roots.
- `screen-primary-screenshot`: captures the primary display into `paths.screenshots_dir`.
- `ultracode-start-hotkeys`: runs the configured UltraCode `start-all-ahk.vbs` command and requires confirmation.

Do not hardcode new task paths, commands, timeouts, output limits, screenshot directories, or Telegram delivery options in Python. Add them to TOML and keep `assistant/devctl/laptop_tasks.py` generic.

Command tasks can also build success messages from a configured latest log
line. `ultracode-start-hotkeys` uses:

```toml
success_message_source = "latest-log-line"
success_log_path = "{ultracode_launcher_dir}/logs/unified/start-all.log"
success_log_contains = "DONE All AHK scripts launched"
success_log_strip_regexes = ["^(?:\\[[^\\]]+\\]\\s*)+"]
success_message_template = "{line}"
```

This makes Telegram confirmations show the launcher outcome, such as
`DONE All AHK scripts launched | delay=414ms total=2429ms`, instead of only a
process return code.

Telegram task delivery is ordered by:

```toml
[laptop_tasks.telegram]
delivery_order = ["openclaw-native", "telegram-bot-api"]
native_retry_attempts = 2
native_restart_gateway_before_retry = true
```

The first provider calls OpenClaw native `message send`. If the native path returns a configured retry marker such as `gateway timeout after`, the runner can restart only the OpenClaw gateway and retry native delivery once before using the second provider. The second provider is a fallback for the observed OpenClaw gateway message-send timeout; it reads the same local OpenClaw Telegram token file from `bot_api_token_file`. Keep this fallback enabled while native message-send can still hit short gateway timeout budgets on this laptop.

For Telegram-originated laptop actions, OpenClaw workspace routing should call the task with `--send-telegram --confirm`. That keeps the local action, logging, and independent Telegram confirmation inside this config-driven task runner instead of relying only on OpenClaw's final chat reply.

Log redaction is also config-driven:

```toml
[logs]
redaction_enabled = true
redaction_secret_file_keys = ["mobile_channel.token_file", "laptop_tasks.telegram.bot_api_token_file"]
redaction_patterns = [...]
```

These patterns protect Bot API URLs, token values, Telegram direct-session owner ids, and common JSON target fields in log-inspection output. Add new patterns here instead of adding redaction literals to Python.

Log source roots are generic:

```toml
[logs.source_roots]
personal = ["{logs_dir}"]
openclaw = ["{user_openclaw_dir}/logs", "{user_openclaw_dir}/agents", "{openclaw_temp_logs_dir}"]
ultracode = ["{ultracode_launcher_dir}/logs"]
```

Add future log sources here instead of editing `log_inspector.py`.

## Mobile Bridge

The local webhook bridge is controlled by:

```toml
[mobile_bridge]
```

Default binding is loopback only:

```text
http://127.0.0.1:8765/mobile/command
```

Before exposing it to LAN or a tunnel, deliberately change `host`, set `require_token = true`, configure the token environment variable from `token_env_var`, and keep the auth header in config. Payload field names are also config-driven through `message_fields`, `sender_fields`, `source_fields`, and `channel_fields`.

External exposure and channel defaults are controlled by:

```toml
[mobile_external]
[mobile_channel]
[mobile_owner]
```

Current native channel direction is Telegram through OpenClaw:

```toml
[mobile_external]
enabled = true
mode = "native-channel"
exposure = "loopback"

[mobile_channel]
enabled = true
provider = "openclaw"
channel = "telegram"
secret_storage = "openclaw-token-file"
token_file = "{user_openclaw_dir}/credentials/telegram-bot-token.txt"
s3_backup_enabled = true
s3_backup_bucket = "<your-archive-bucket>"
s3_backup_key = "personal-assistant/secrets/telegram-bot-token.txt"
```

OpenClaw stores Telegram auth through `channels.telegram.tokenFile`, pointing at the local-only token file under the user OpenClaw credentials directory. The same token is backed up to the private project S3 bucket as a fallback object under `personal-assistant/secrets/telegram-bot-token.txt`; local token file remains primary. Do not put raw bot tokens in repo TOML, Python, PowerShell, markdown, logs, command-line flags, Desktop sync, or memory archive inputs.

`[mobile_owner]` stores the configured command path for `commands.ownerAllowFrom`, the optional owner list, redaction behavior, confirmation requirement, and whether the OpenClaw gateway should restart after setting owners. Do not hardcode or hand-edit owner ids in scripts.

`[mobile_channel].status_timeout_ms` is set to a longer 30-second budget because OpenClaw's default 3-second gateway probe can fail during Windows gateway warm-up even when the channel and deeper health calls are working.

## OpenClaw Watchdog

Gateway reliability settings are controlled by:

```toml
[openclaw_watchdog]
[[openclaw_watchdog.process_roles]]
[openclaw_watchdog_schedule]
```

These sections own the gateway host/port, probe strategy, HTTP probe path, CLI probe command, process query command, gateway/node command-line markers, protected process markers, stale-lock behavior, native restart commands, direct hidden fallback command, and Windows scheduled task settings.

The default probe strategy is layered because the observed OpenClaw failure can leave the canvas HTTP endpoint responding while the OpenClaw gateway command path times out. The current liveness probe is:

```toml
probe_strategy = "http_and_cli"
http_probe_path = "/__openclaw__/canvas/"
http_probe_timeout_seconds = 6
probe_command = ["channels", "status", "--timeout", "30000", "--json"]
probe_timeout_seconds = 45
```

The watchdog must stay narrow:

- It may kill only processes matching configured OpenClaw gateway/node role markers.
- It must refuse configured protected process markers such as ClipSync.
- It may rename stale OpenClaw gateway lock files only when the recorded PID no longer exists.
- It should try OpenClaw-native start commands first, then use the configured direct fallback only when native start reports success but the gateway remains unhealthy.

Task Scheduler settings are also config-only:

```toml
[openclaw_watchdog_schedule]
task_name = "PersonalAssistantOpenClawWatchdog"
schedule = "MINUTE"
modifier = 10
execution_time_limit = "PT15M"
wrapper_script = "{project_root}/scripts/openclaw-watchdog.vbs"
powershell_wrapper = "{project_root}/scripts/openclaw-watchdog.ps1"
```

Change cadence, timeout, wrapper, process markers, or fallback commands in TOML, not in Python or PowerShell.

Startup and archive behavior are controlled by:

```toml
[startup]
[archive]
[archive_schedule]
```

Current archive S3 settings use a dedicated private bucket for this repo:

```toml
[archive]
s3_enabled = true
s3_bucket = "<your-archive-bucket>"
s3_prefix = "personal-assistant/archive"
s3_region = "ap-south-1"
archive_name_template = "personal-assistant-memory-{timestamp}.7z"
compression_backend = "7z"
compression_format = "7z"
compression_profile = "ppmd-text-ultra-no-times"
seven_zip_executable_candidates = ["7z.exe", "...", "C:/Program Files/7-Zip/7z.exe"]
seven_zip_arguments = ["a", "-t7z", "-m0=PPMd", "-mx=9", "-mtm=off", "..."]
s3_delete_protection_policy_enabled = true
s3_object_lock_enabled = true
s3_object_lock_mode = "GOVERNANCE"
s3_object_lock_days = 60
s3_lifecycle_enabled = true
s3_lifecycle_rule_id = "expire-archive-after-60-days"
s3_lifecycle_filter_prefix = "personal-assistant/archive/"
s3_lifecycle_expiration_days = 60
s3_lifecycle_noncurrent_expiration_days = 60
s3_lifecycle_abort_rule_id = "abort-incomplete-multipart-uploads"
s3_lifecycle_abort_multipart_days = 7
s3_lifecycle_mutation_deny_sid = "DenyPersonalAssistantLifecycleMutation"
s3_lifecycle_apply_requires_confirm = true
s3_policy_propagation_wait_seconds = 10
aws_profile = "account01"
s3_requires_confirm = true
```

The bucket is not shared with ultracode. It was created with public access blocked, bucket-owner-enforced object ownership, versioning enabled, S3-managed AES256 default encryption, Object Lock governance retention for 60 days, and a lifecycle rule that expires current and noncurrent archive objects after 60 days. Incomplete multipart uploads are cleaned after 7 days.

Archives are created as `.7z` using the config-named `ppmd-text-ultra-no-times` profile. The current profile uses solid 7-Zip PPMd compression and strips archive timestamp metadata because the assistant memory archive is mostly text. 7-Zip is resolved from config, including the explicit Windows install path `C:/Program Files/7-Zip/7z.exe` because it may not be on `PATH`.

The bucket policy explicitly denies object deletion, bucket deletion, ad hoc lifecycle mutation, and governance-retention bypass. The config-driven lifecycle apply command temporarily relaxes only the lifecycle-mutation deny, waits for policy propagation, applies the configured lifecycle/object-lock policy, and restores the deny rule. Uploads still require the explicit `--upload-s3 --confirm` command gate.

Important limit: a same-account root/admin principal may still be able to remove or replace bucket policy controls. For stronger delete resistance than this, add an AWS Organizations SCP or separate break-glass/admin control outside this repo.

Daily archive upload automation is controlled by:

```toml
[archive_schedule]
enabled = true
task_name = "PersonalAssistantArchiveToS3"
schedule = "DAILY"
start_time = "22:30"
run_level = "LIMITED"
start_when_available = true
allow_start_on_batteries = true
do_not_stop_on_batteries = true
wrapper_script = "{project_root}/scripts/archive-to-s3.vbs"
task_runner_executable_candidates = ["wscript.exe", "wscript"]
task_runner_arguments = ["//B", "//Nologo"]
install_requires_confirm = true
delete_requires_confirm = true
run_now_requires_confirm = true
```

The scheduled task runs through `wscript.exe //B //Nologo` and `scripts/archive-to-s3.vbs` so the archive upload is windowless. The VBS launcher starts the PowerShell archive wrapper hidden, waits for its exit code, and logs start/finish lines to the normal archive log stream. The PowerShell wrapper resolves the `archive-to-s3` alias from `[aliases]`, so changing the scheduled archive action should be done in TOML rather than by editing the wrapper. The current settings also ask Windows to start the missed task when available and to allow the archive upload on battery power.

## Telegram Bridge

The independent Telegram bridge lives under:

```toml
[telegram_bridge]
[[telegram_bridge.rules]]
[telegram_bridge_schedule]
```

`[telegram_bridge]` controls token file path, long-poll timeout, owner source (`openclaw-command-owner` or `explicit`), reply limits, audit JSONL paths, and the dispatch defaults.

`[[telegram_bridge.rules]]` is an ordered list of dispatch rules. Each rule has:

- `id` — log/audit identifier
- `match` — `exact` / `prefix` / `fallback`
- `patterns` — phrases or prefixes to match (case-insensitive)
- `description` — used in the auto-generated help text
- `kind` — `canned` / `help` / `task` / `task-by-name` / `openclaw-ask`
- `reply` — for `canned`
- `role` — for `openclaw-ask`, names the agent-role from `[agent.routing]`
- `task_name`, `task_confirm`, `task_send_telegram` — for `kind = "task"` shortcuts

Add new Telegram shortcuts by appending another `[[telegram_bridge.rules]]` entry. Do not hardcode dispatch rules in Python.

`[telegram_bridge_schedule]` mirrors the watchdog schedule shape for `PersonalAssistantTelegramBridge` (`ONLOGON` so the bridge starts at every user login). Install/inspect/delete/run-now go through `python .\devctl.py bridge schedule-...`.

Audit JSONL paths:

```toml
audit_jsonl_path = "{project_root}/data/telegram-bridge/inbound.jsonl"
audit_outbound_jsonl_path = "{project_root}/data/telegram-bridge/outbound.jsonl"
inbound_text_max_chars_for_log = 4000
outbound_text_max_chars_for_log = 4000
```

Keep both audit files enabled for debugging. They store hashed sender ids and message/reply text up to the configured cap.

## PowerShell Logging

PowerShell wrapper logging is controlled by:

```toml
[logging]
ps1_console_enabled = false
```

Wrappers still write to `logs/unified/` and `logs/ps1/`, but by default they do not print log lines to stdout. This keeps JSON and command output usable while preserving the audit trail.

## Devctl Logging

`devctl.py` command audit logging is controlled by:

```toml
[devctl_logging]
enabled = true
command_path_fields = ["command", "..."]
```

- `enabled`: turns command start/finish audit records on or off.
- `command_path_fields`: parsed argparse fields used to build a safe command label without logging private message bodies.
- `unknown_command_label`: fallback command label.
- `exception_message_max_chars`: maximum exception text stored in logs.

The audit records use the configured `flows.devctl` log stream and mirror into `logs/unified/_session.log`.

## Adding New Behavior

When adding a new runner, recipe, path, model, timeout, or log rule:

1. Add or update `config/settings.toml`.
2. Make Python read that key through `assistant/devctl/config.py`.
3. Keep code generic where possible.
4. Add a smoke check.
5. Update `CHECKPOINT.md` if the behavior affects setup or workflow.
