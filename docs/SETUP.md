# Setup

## Current Verified Machine State

Checked on 2026-05-11.

- Node.js: `v24.15.0`
- npm: `11.6.2`
- OpenClaw: `2026.5.7`
- Python: `3.13.13`
- PowerShell: `7.6.1`
- GitHub Copilot auth for OpenClaw: present
- OpenClaw local workspace onboarding: complete
- OpenClaw gateway local service: recovered and guarded by the scheduled watchdog
- Desktop log viewer: running on `http://127.0.0.1:7000`

See `LOCAL-SETUP.md` for detailed local scan notes.

## Prerequisites

- Windows laptop.
- PowerShell 7.
- Python available as `python`.
- Node.js 24 LTS.
- npm.
- OpenClaw installed globally.
- GitHub Copilot Enterprise auth through OpenClaw.

## Verify Basics

```powershell
node --version
npm --version
python --version
openclaw --version
python .\devctl.py smoke
.\scripts\pa.ps1 smoke
```

## Verify OpenClaw Auth And Models

```powershell
python .\devctl.py openclaw models-auth --timeout 30
python .\devctl.py openclaw models-list
python .\devctl.py openclaw models-validate
```

Configured working model policy:

```text
default:     github-copilot/gpt-5.4
agentic:     github-copilot/gpt-5.3-codex
max-complex: github-copilot/claude-opus-4.7
```

Per-call provider/model overrides are still disabled because OpenClaw rejected them for this caller. Role-specific routing uses configured OpenClaw agents instead:

```powershell
python .\devctl.py agents list --actual
python .\devctl.py agents smoke --role agentic
python .\devctl.py agents smoke --role max
```

OpenClaw default model has been set with:

```powershell
openclaw models set github-copilot/gpt-5.4
openclaw models status --plain
```

Current verification:

- `openclaw models status --plain` returns `github-copilot/gpt-5.4`.
- Agent smoke passed with provider `github-copilot` and model `gpt-5.4`.
- `pa-codex` role smoke passed with provider `github-copilot` and model `gpt-5.3-codex`.
- `pa-max` role smoke passed with provider `github-copilot` and model `claude-opus-4.7`.
- `openclaw status --target gateway` passes after restarting the OpenClaw gateway.
- `python .\devctl.py startup run --json` passes.

Current `models-validate` result:

- `models.primary = github-copilot/gpt-5.4`: visible.
- `models.agentic = github-copilot/gpt-5.3-codex`: visible.
- `models.max_complex = github-copilot/claude-opus-4.7`: visible.
- Fast/simple fallbacks are visible: `gpt-5.4-mini` and `gemini-3-flash`.
- Model validation scope is limited to configured Copilot routes.

## Verify OpenClaw Runtime

Current working setup uses a local OpenClaw workspace:

```text
openclaw/workspace
```

Gateway and node host are expected to be running locally. Prefer these checks:

```powershell
python .\devctl.py openclaw status --timeout 20
python .\devctl.py openclaw models-auth --timeout 30
python .\devctl.py openclaw models-list
python .\devctl.py openclaw health --timeout 60
```

If OpenClaw state commands hit Windows sandbox permissions from Codex, run them outside the sandbox or approve the specific command.

## Local Onboarding Command

The local workspace was onboarded with:

```powershell
openclaw onboard --non-interactive --accept-risk --flow quickstart --mode local --auth-choice github-copilot --workspace "<repo-root>\openclaw\workspace" --skip-channels --skip-ui --skip-health --no-install-daemon --json
```

After onboarding:

```powershell
openclaw doctor
python .\devctl.py openclaw doctor-report --timeout 150
python .\devctl.py openclaw doctor-triage
python .\devctl.py openclaw status --timeout 30
```

Latest `openclaw doctor` observation:

- command owner is not configured.
- orphan transcript was archived by recoverable rename.
- optional skill missing-requirement noise was cleared by non-interactive doctor safe fix.
- plugin skill symlink creation hit Windows `EPERM`.
- `doctor-report` saves the doctor output as an artifact under `data/runs/openclaw/`.
- `doctor-triage` classifies the latest report and is read-only.
- `doctor-fix-safe --confirm` runs the configured non-interactive OpenClaw repair command.

## Start Or Check Gateway

If the gateway/node host is not running:

```powershell
.\scripts\start.ps1
python .\devctl.py watchdog status
python .\devctl.py watchdog run
python .\devctl.py openclaw start --target gateway --timeout 60
python .\devctl.py openclaw status --target gateway --timeout 30
```

If service installation is required, use OpenClaw gateway install/start commands only after reviewing `openclaw doctor`.

The preferred recovery command is now `python .\devctl.py watchdog run`. It uses OpenClaw-native start first and falls back to the configured direct hidden gateway start only if native start reports success but the gateway still does not bind.

## Desktop Sync

Preview the normal full Desktop sync. This includes personal-assistant automatically:

```powershell
$env:DESKSYNC_CI = "1"
..\..\sync-to-personal-desktop.bat -n
```

Preview only the assistant sync payload:

```powershell
$env:DESKSYNC_CI = "1"
..\..\sync-to-personal-desktop.bat --pa -n
```

Run the normal full Desktop sync:

```powershell
$env:DESKSYNC_CI = "1"
..\..\sync-to-personal-desktop.bat
```

Run only the assistant sync:

```powershell
$env:DESKSYNC_CI = "1"
..\..\sync-to-personal-desktop.bat --pa
```

The sync policy is in `config/settings.toml` under `[desktop_sync]`. `--pa` / `-pa` is an optional personal-assistant-only selector, not a required mode.

## Log Viewer

The log viewer on port `7000` includes:

- example-app logs
- jar logs
- personal-assistant logs
- `~/.openclaw/logs`
- `%TEMP%/openclaw`

Verify:

```powershell
Invoke-WebRequest -Uri "http://127.0.0.1:7000/api/health" -UseBasicParsing
Invoke-WebRequest -Uri "http://127.0.0.1:7000/api/files?refresh=1" -UseBasicParsing
```

## Runner Smoke Checks

```powershell
python -m compileall .\assistant .\devctl.py
python -m unittest discover -s tests -v
python .\devctl.py recipes
python .\devctl.py run summarize-example-app --dry-run
python .\devctl.py notes list
python .\devctl.py todos list
python .\devctl.py files search --scope personal --query OpenClaw --limit 5
python .\devctl.py brief daily --show
python .\devctl.py mobile webhook info
python .\devctl.py mobile external info
python .\devctl.py mobile token status --check-s3
python .\devctl.py mobile owner status --json
python .\devctl.py archive plan
python .\devctl.py archive create --dry-run
python .\devctl.py archive lifecycle-verify
python .\devctl.py archive schedule-status
python .\devctl.py watchdog status
python .\devctl.py watchdog schedule-status
.\scripts\summarize-example-app.ps1 --dry-run
.\scripts\latest-openclaw-errors.ps1 --limit 20
```

`mobile owner status` can report `configured = false` until Telegram is linked and the approved numeric Telegram user id is known. The bot token is stored in the local OpenClaw credentials token file and backed up to the private S3 fallback key. Restart the OpenClaw gateway after token changes, then set the owner:

```powershell
python .\devctl.py mobile owner set --owner "telegram:<numeric-user-id>" --confirm
```

## Config

Primary config:

```text
config/settings.toml
```

Optional override:

```powershell
$env:PERSONAL_ASSISTANT_CONFIG = "C:\path\to\settings.toml"
```

## Recovery Notes

If `openclaw` is not found by Python but works in PowerShell, confirm npm shims:

```powershell
where.exe openclaw
```

The runner resolves configured executable candidates from `config/settings.toml`.

If OpenClaw status commands hang, use short `--timeout` values and inspect logs:

```powershell
python .\devctl.py logs errors --source all --limit 80
```
