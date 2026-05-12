# Architecture

## Goal

Build a laptop-first personal assistant that uses OpenClaw as the assistant runtime, while this repo owns local configuration, logging, runner commands, mobile command capture, safety rules, and custom tools.

## System Shape

```text
User / Mobile / CLI
  -> scripts/pa.ps1 or devctl.py
  -> assistant/devctl/*
  -> local tools or OpenClaw CLI / Gateway / Agent
  -> local data stores or model providers
  -> local logs, artifacts, memory, and future tools
```

## Boundaries

OpenClaw owns:

- assistant runtime
- agent sessions
- model routing
- gateway/node host
- provider auth
- future chat channels

This repo owns:

- central config
- local runner/control plane
- local audit logs
- local mobile command inbox
- prompt recipes
- setup/checkpoint docs
- custom laptop tools
- safety gates around local automation

## Native-First Rule

Prefer OpenClaw-native capabilities for runtime behavior: onboarding, agents, model routing, channels, gateway/node control, skills, memory, doctor checks, backup/restore, and future UI/channel surfaces. This repo should wrap or configure those features, not rebuild them.

Custom code is allowed when it adds one of these local concerns:

- central TOML configuration
- local audit logging in the personal-assistant format
- safety gates and redaction
- project-specific notes, todos, briefs, and approved file search
- thin runner scripts or glue that call OpenClaw-native commands
- config-driven laptop tasks with confirmation gates where OpenClaw needs local Windows actions

## Core Components

`config/settings.toml`

- Single source of truth for configurable values.
- Stores paths, models, aliases, timeouts, log limits, mobile defaults, recipes, scopes, and OpenClaw command settings.

`devctl.py`

- Main local control plane.
- Provides OpenClaw control, recipe execution, log inspection, mobile command capture/drain, and status summaries.

`assistant/devctl/config.py`

- Loads TOML config.
- Resolves placeholders such as `{project_root}`, `{desktop_root}`, and `{home}`.

`assistant/devctl/openclaw_runner.py`

- Logged OpenClaw CLI wrapper.
- Redacts configured secret flags.
- Captures run artifacts under configured artifact paths.

`assistant/devctl/openclaw_watchdog.py`

- Detects OpenClaw gateway/node failure states using config-driven probes and process markers.
- Recovers only configured OpenClaw gateway/node processes and refuses protected ClipSync markers.
- Renames stale gateway lock files only when the recorded PID is gone.
- Tries native OpenClaw start first, then a configured direct hidden gateway fallback if native start leaves the gateway unhealthy.

`assistant/devctl/openclaw_watchdog_schedule.py`

- Owns Windows Task Scheduler integration for the silent OpenClaw watchdog.
- Reads cadence, task name, wrapper path, executable candidates, and confirmation gates from `[openclaw_watchdog_schedule]`.
- Uses a windowless VBS launcher and keeps all actions logged.

`assistant/devctl/mobile_inbox.py`

- Append-only local command inbox.
- Stores full mobile commands locally in `data/mobile/commands.jsonl`.
- Logs metadata only.

`assistant/devctl/mobile_bridge.py`

- Local HTTP webhook bridge for mobile command capture.
- Binds to configured host/port; default is loopback only.
- Validates optional token settings from config.
- Captures accepted payloads into the mobile inbox.

`assistant/devctl/log_inspector.py`

- Reads configured personal/OpenClaw/ExampleApp log sources.
- Filters warning/error/failure lines using config-driven rules.

`assistant/devctl/laptop_tasks.py`

- Runs approved laptop tasks from `[laptop_tasks]`.
- Supports command tasks, log-summary tasks, and primary-screen screenshot capture.
- Uses OpenClaw native Telegram `message send` first for delivery.
- Falls back to Telegram Bot API only when native delivery fails; the token file remains outside this repo.
- Keeps task values, delivery order, output limits, screenshot paths, and confirmation policy in config.

`assistant/devctl/model_diagnostics.py`

- Parses OpenClaw provider model lists.
- Compares visible models against config-driven desired/required model targets.
- Keeps model-route decisions auditable through saved artifacts.

`assistant/devctl/agent_roles.py`

- Maps assistant roles to configured OpenClaw agents.
- Avoids per-call model overrides by using agent-specific model config.
- Ensures role workspaces exist and can seed them from the main workspace.

`assistant/devctl/doctor_triage.py`

- Reads saved OpenClaw doctor reports or freshly captured output.
- Classifies known doctor warnings using config-driven patterns.
- Stays read-only; cleanup/fix commands remain explicit operator decisions.

`assistant/devctl/mobile_external.py`

- Reports external mobile/channel readiness.
- Keeps LAN/tunnel exposure blocked until token and channel configuration are explicit.
- Current selected channel is Telegram through OpenClaw native channels; this repo wraps readiness, logging, redaction, and owner gates only.

`assistant/devctl/mobile_owner.py`

- Reads and sets the OpenClaw command-owner allowlist through the OpenClaw CLI.
- Avoids direct edits to OpenClaw state files.
- Redacts owner ids in local logs unless config deliberately allows raw display.

`assistant/devctl/mobile_token_backup.py`

- Manages Telegram token local/S3 fallback status, backup, and restore.
- Keeps local token file primary and treats S3 as a recovery source.
- Logs metadata only; token values are never printed or stored in repo artifacts.

`assistant/devctl/archive.py`

- Creates local memory/context archives from config-approved paths.
- Supports S3 upload only when explicitly enabled and confirmed.
- Uses a config-selected archive backend; current backend is 7-Zip `.7z` with the `ppmd-text-ultra-no-times` profile for text-heavy memory/context snapshots.
- Uses the dedicated private S3 archive bucket configured in `[archive]`; bucket name, profile, prefix, and region live in `config/settings.toml`.
- The bucket protection layer denies normal object/bucket deletes, denies ad hoc lifecycle mutation, applies Object Lock governance retention, and manages archive expiration through config-driven lifecycle commands.

`assistant/devctl/archive_schedule.py`

- Owns Windows Task Scheduler integration for daily S3 archive uploads.
- Reads task name, schedule, wrapper path, executable candidates, and confirmation gates from `[archive_schedule]`.
- Uses a windowless VBS launcher plus the same logged PowerShell archive wrapper and does not bypass `devctl.py`.

`assistant/devctl/startup.py`

- Runs configured startup checks such as role-agent ensure, gateway start/status, mobile endpoint info, and archive planning.

`assistant/devctl/recipes.py`

- Loads prompt recipes from `config/settings.toml`.
- No prompt text should be hardcoded in Python.

`assistant/tools/*`

- Local tools exposed through `devctl.py`.
- Current tools: notes, todos, approved-folder file search, and daily brief.
- Tools log via the shared Python logger and keep storage paths/statuses/limits/scopes in config.

`assistant/memory/json_file.py`

- Atomic JSON file storage helper for local structured data.
- Keeps store callers independent from the current file format.

`scripts/*.ps1`

- Thin wrappers only.
- They route to `devctl.py`.
- Wrapper behavior is resolved by config aliases.

## Data Flow

CLI recipe:

```text
PowerShell wrapper -> devctl alias -> configured recipe -> OpenClaw agent -> artifact + logs
```

Mobile command:

```text
mobile bridge -> devctl mobile capture -> data/mobile/commands.jsonl -> devctl mobile drain -> OpenClaw agent -> artifact + status event
```

Mobile webhook:

```text
HTTP POST -> mobile_bridge.py -> mobile_inbox.py -> data/mobile/commands.jsonl -> logs
```

Mobile owner setup:

```text
devctl mobile owner -> OpenClaw config CLI -> commands.ownerAllowFrom -> optional gateway restart -> logs
```

Log inspection:

```text
devctl logs -> configured log sources -> redacted/safe file scan -> console output + audit log
```

Laptop task:

```text
Telegram/OpenClaw/CLI -> devctl task -> configured task -> local command/log/screenshot -> logs -> optional Telegram delivery
```

Model validation:

```text
devctl openclaw models-validate -> OpenClaw models list -> config target comparison -> artifact + logs
```

Doctor triage:

```text
devctl openclaw doctor-triage -> saved or refreshed doctor report -> config issue patterns -> recommendations
```

Agent role routing:

```text
devctl ask/run/mobile --role <role> -> configured OpenClaw agent -> role-specific model without per-call model override
```

Startup:

```text
scripts/start.ps1 -> devctl startup run -> configured startup actions -> logs
```

OpenClaw watchdog:

```text
Task Scheduler or devctl watchdog -> gateway probe + process markers -> narrow OpenClaw recovery -> logs
```

Archive:

```text
devctl archive plan/create -> config-approved files -> local 7z archive -> optional gated S3 upload
devctl archive lifecycle-* -> configured S3 lifecycle/Object Lock policy -> AWS bucket -> logs
devctl archive schedule-* -> Windows Task Scheduler -> wscript archive-to-s3.vbs -> hidden PowerShell wrapper -> devctl alias -> S3 upload
```

Desktop sync:

```text
config/settings.toml [desktop_sync] -> DeskSync v3 external folder spec -> personal OneDrive Desktop
```

Log viewer:

```text
example-app log-viewer server -> example-app + jar + personal-assistant + safe OpenClaw log roots
```

Local notes/todos:

```text
devctl notes/todos -> assistant/tools/* -> data/notes or data/todos -> logs
```

Daily brief:

```text
devctl brief daily -> notes + todos + mobile inbox + logs -> data/briefs -> logs
```

## Safety Model

- Read-only actions can run with minimal friction.
- Writes inside this repo should be logged.
- Shell/app automation needs confirmation.
- Email, messages, credentials, purchases, account changes, deletions, and broad system changes need explicit approval.
- Final UI must be local-only by default.

## Model Policy

- Default model policy: `github-copilot/gpt-5.4` for normal work.
- Agentic/coding model policy: `github-copilot/gpt-5.3-codex`.
- Maximum-complexity model policy: `github-copilot/claude-opus-4.7`.
- Model validation scope is limited to configured Copilot routes.
- Practical authenticated provider: GitHub Copilot Enterprise models.
- Actual OpenClaw default is set and verified as `github-copilot/gpt-5.4`.
- Per-call model overrides are still config-gated because OpenClaw currently rejects provider/model overrides for this caller.
- Role-specific routing is implemented through OpenClaw agents: `main`, `pa-codex`, and `pa-max`.

## Extension Points

Current tool folders:

- `assistant/tools/` for user-facing local tools.
- `assistant/memory/` for local storage helpers.
- `assistant/llm/` only if custom provider abstraction becomes necessary.
- `tests/` for smoke and regression checks.

Future UI:

- Build only after core setup is stable.
- Route actions through `devctl.py` or a thin logged API wrapper.
- Do not bypass config, logging, or safety gates.
