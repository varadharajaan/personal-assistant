# OpenClaw Config Notes

OpenClaw should be treated as the main assistant runtime for this project.

## Why Use It

- It already handles the hard assistant shell: onboarding, persistent memory, chat surfaces, tools, skills, background work, and provider switching.
- It runs on Windows, macOS, and Linux.
- It can use cloud model providers or local models.
- It can be extended with custom skills, which fits this project better than rebuilding the whole assistant from scratch.

## Initial Setup Preference

Recommended setup:

1. Install OpenClaw via npm because Node.js is already installed on this laptop.
2. Run `openclaw onboard`.
3. Use GitHub Copilot Enterprise as the primary model route:
   - GitHub Copilot provider for general model access.
   - Prefer this because the user has enterprise-specialized Copilot token/API access through Microsoft.
   - OpenAI Codex OAuth for Codex/ChatGPT subscription access.
   - Claude CLI reuse where available and policy-compliant.
4. Start with CLI/local usage before connecting personal messaging apps.
5. Add only low-risk skills first:
   - notes
   - todos
   - read-only file search
   - daily brief
6. Make every custom skill/tool use `shared/python/pa_logging.py` or `shared/ps1/_log_helper.ps1`.
7. Keep email, calendar, browser, and shell automation gated behind confirmation.

## Decision

Use OpenClaw-first, not custom-only.

Use native OpenClaw features before adding custom code. Do not rebuild OpenClaw runtime features such as channels, agents, model routing, memory, gateway/node control, doctor checks, or backup flows inside this repo. Prefer configuring or wrapping OpenClaw-native commands, with custom code limited to local logging, TOML-driven policy, redaction, safety gates, project-specific tools, and narrow integration glue.

Custom Python/PowerShell code should exist only as local skills/tools that OpenClaw can call. This avoids rebuilding the assistant runtime while still giving full control over laptop-specific workflows, memory, project context, and safety gates.

Primary stack:

- Runtime: OpenClaw.
- Already-authenticated practical provider: GitHub Copilot Enterprise.
- Working model policy:
  - default/normal work: `github-copilot/gpt-5.4`
  - agentic/coding workflows: `github-copilot/gpt-5.3-codex`
  - maximum-complexity specialist work: `github-copilot/claude-opus-4.7`
  - fast/simple work: `github-copilot/gpt-5.4-mini` or `github-copilot/gemini-3-flash`
- Future model names should be added only when the user explicitly asks to change routes; current validation stays limited to the configured Copilot routes.
- Actual OpenClaw default is now set to `github-copilot/gpt-5.4`.
- Agent smoke confirmed the runtime route `github-copilot / gpt-5.4`.
- Per-call provider/model override is still config-gated because OpenClaw currently rejects overrides for this caller.
- Custom layer: local skills for notes, todos, project context, file search, reminders, and approved laptop automation.
- Logging layer: all custom tools write to `logs/unified/<flow>.log` and `logs/unified/_session.log`, matching `ultracode-launcher` and `jar` conventions.
- Engineering standard: principal-engineer repo quality, SOLID design, and Liskov-safe provider/tool adapters. See `docs/ENGINEERING-STANDARD.md`.
- Config standard: all tunable values must live in `config/settings.toml`; Python/PowerShell should not hardcode paths, models, aliases, timeouts, recipes, or log limits.
- Documentation standard: every meaningful change must update relevant docs and checkpoint when current state changes. See `docs/DOCUMENTATION-STANDARD.md`.

## Commands To Run Later

```powershell
npm i -g openclaw
openclaw onboard
```

Network access and global package install may need explicit approval before running.

Current setup status:

- Node.js upgraded to `v24.15.0`.
- OpenClaw installed globally as `openclaw@2026.5.7`.
- GitHub Copilot provider auth is complete:
  - `github-copilot:github [github-copilot/token]`
- User model preference is:
  - default: `github-copilot/gpt-5.4`
  - agentic/coding: `github-copilot/gpt-5.3-codex`
  - maximum-complexity specialist: `github-copilot/claude-opus-4.7`
- `python .\devctl.py openclaw models-validate` confirms:
  - `github-copilot/gpt-5.4` is visible through GitHub Copilot.
  - `github-copilot/gpt-5.3-codex` is visible through GitHub Copilot.
  - `github-copilot/claude-opus-4.7` is visible through GitHub Copilot.
- `openclaw models status --plain` returns `github-copilot/gpt-5.4`.
- Agent smoke passed on `github-copilot / gpt-5.4` after setting the default.
- `openclaw health` currently times out against the gateway, while `openclaw status --target gateway` and agent calls work.
- Forced per-call `--model github-copilot/claude-opus-4.7` was rejected with `provider/model overrides are not authorized for this caller`; keep the runner's default model override disabled until OpenClaw config permits it.
- `devctl.py` can now call OpenClaw commands, capture mobile commands, inspect logs, and run prompt recipes.
- `devctl.py` now reads runner paths, aliases, models, timeouts, mobile defaults, log rules, and recipes from `config/settings.toml`.
- `devctl.py` now exposes Phase 1 local tools:
  - notes
  - todos
  - approved-folder file search
  - daily brief
- Phase 1 local tool storage is under `data/notes`, `data/todos`, and `data/briefs`, with settings under `[tools.*]` in `config/settings.toml`.
- A local loopback mobile webhook bridge is available for command capture:
  - `python .\devctl.py mobile webhook info`
  - `python .\devctl.py mobile webhook serve`
  - default binding is `127.0.0.1`, not LAN.
- OpenClaw local workspace onboarding is complete under `openclaw/workspace`.
- Gateway and node host are running locally.
- Local device pairing is repaired with `operator.read`, `operator.pairing`, and `operator.write`; `operator.admin` remains intentionally absent.
- Agent smoke through `devctl.py` passed using the practical Copilot route.
- OpenClaw command-owner status/set is available through `python .\devctl.py mobile owner status --json` and `python .\devctl.py mobile owner set --owner "<channel-native-id>" --confirm`.
- Latest `openclaw doctor` report still needs cleanup:
  - command owner not configured
  - plugin skill symlink creation hit Windows `EPERM`
- Doctor output can be captured with `python .\devctl.py openclaw doctor-report`.
- Doctor warnings can be triaged with `python .\devctl.py openclaw doctor-triage`; triage is read-only and does not apply `openclaw doctor --fix`.
- Desktop sync and the port `7000` log viewer now include safe personal-assistant/OpenClaw roots.
- ClipSync is a protected local service:
  - It already runs locally over Wi-Fi to share clipboard/text between machine 1 and peer machines.
  - OpenClaw device registration/pairing is separate and must not touch ClipSync pairing, peers, ports, startup entries, process state, or runtime config unless the user explicitly asks for ClipSync work.

## Safety Defaults

- No deletion without explicit approval.
- No sending messages or emails without explicit approval.
- No purchases, credential changes, or account changes.
- Read access should start with this project folder only, then expand deliberately.
- Do not disturb protected local services such as ClipSync. Read-only docs/log/status checks are okay; stopping, restarting, pairing, port changes, or state/config edits require an explicit ClipSync-specific request.
- No custom skill should run without writing an INFO/OK/WARN/ERROR trail to the personal-assistant logs.
- S3 archive for memory/context is allowed as a later feature only after explicit confirmation and credential-safe logging.
- After setup is stable and the user says things are looking good, add a simple local OpenClaw agent UI page. It should call logged runner/control flows rather than bypassing `devctl.py`.
