# Personal Assistant Plan

## Goal

Build a personal assistant that runs on this Windows laptop and can help with daily tasks:

- Chat with context about approved local files and notes.
- Create and retrieve personal memory.
- Manage todos, reminders, and simple routines.
- Run approved laptop actions through PowerShell, Python, or a desktop automation tool.
- Optionally support voice input/output later.

## Recommended Direction

Start with OpenClaw as the main assistant runtime, then add small local tools only where we need custom behavior.

Best MVP:

1. OpenClaw as the assistant runtime and chat/app interface.
2. A local `personal-assistant` workspace for configuration, custom skills, notes, and guardrails.
3. Local JSON/JSONL and markdown storage for personal tasks, notes, and durable memory we control.
4. Optional LLM provider switch:
   - Local mode: Ollama for privacy and offline use.
   - Cloud mode: OpenAI API for stronger reasoning and tool calling.
5. Simple desktop launcher script once the core works.

## Core Principles

- Ask before risky actions such as deleting files, sending messages, buying things, or changing system settings.
- Keep private data local unless cloud mode is explicitly enabled.
- Make every tool explicit and reviewable.
- Use OpenClaw-native features first; do not rebuild channels, agents, model routing, memory, gateway/node control, doctor checks, or backup flows when OpenClaw already provides them.
- Log every custom tool action through the shared logging helpers so it is easy to see what the assistant did.
- Start text-first; add voice only after the core loop is reliable.
- Maintain this as a principal-engineer quality repo: small modules, clear interfaces, documented decisions, and repeatable setup.
- Follow SOLID design. In particular, provider/store/tool adapters must honor Liskov substitution: replacing one adapter with another must not skip logging, weaken permissions, change output contracts, or introduce surprise side effects.
- Keep S3 archive and retrieval as an optional extension, not a hidden dependency of local memory.

## Tooling

Required:

- Python 3.11 or newer.
- Git.
- VS Code or another editor.
- SQLite, via Python standard library.
- PowerShell scripts for Windows actions.

Recommended Python packages:

- `typer` for command-line commands.
- `rich` for readable terminal output.
- `pydantic` for structured tool inputs.
- `python-dotenv` for local secrets.
- `requests` or `httpx` for local/API calls.

LLM options:

- OpenClaw as the primary personal assistant layer.
- GitHub Copilot Enterprise is the practical no-extra-cost route already authenticated on this laptop.
- Working model policy:
  - default/normal work: `github-copilot/gpt-5.4`
  - agentic/coding workflows: `github-copilot/gpt-5.3-codex`
  - maximum-complexity specialist work: `github-copilot/claude-opus-4.7`
  - fast/simple fallback: `github-copilot/gpt-5.4-mini` or `github-copilot/gemini-3-flash`
- Future model upgrades should be added only by changing `config/settings.toml` and rerunning model validation after the user explicitly asks.
- Ollama for local models on Windows.
- OpenAI Responses API for stronger reasoning, structured outputs, and tool/function calling.

Automation options:

- PowerShell for files, apps, Windows settings, scheduled tasks.
- desktop automation for hotkeys and shortcuts.
- PyAutoGUI or pywinauto for UI automation only when no API/script route exists.

Memory/search options:

- Local JSON/JSONL for current lightweight structured memory.
- SQLite later if the memory schema grows beyond simple local tools.
- Local markdown files for human-readable notes.
- Later: embeddings/vector search if plain search becomes limiting.

Voice options for later:

- Speech-to-text: local Whisper/faster-whisper or cloud transcription.
- Text-to-speech: Windows voices, Edge TTS, or another local TTS tool.

## Proposed Architecture

```text
personal-assistant/
  openclaw/
    config-notes.md     # setup decisions, providers, channels, permissions
    skills/             # custom OpenClaw skills we create
    prompts/            # assistant personality and operating instructions
  shared/
    ps1/_log_helper.ps1 # PowerShell logging helper
    python/pa_logging.py # Python logging helper
  config/
    settings.toml       # central config for paths, models, runners, recipes, logs
  assistant/
    devctl/
      doctor_triage.py  # read-only OpenClaw doctor warning classifier
      model_diagnostics.py # configured model route validation
      openclaw_watchdog.py # narrow OpenClaw gateway recovery
    app.py              # optional companion CLI for custom local tools
    config.py           # settings and provider selection
    llm/
      base.py           # provider interface
      ollama.py         # local provider
      openai_provider.py
    tools/
      notes.py          # markdown notes integration
      todos.py          # local todo store and lifecycle
      file_search.py    # safe file search helpers
      brief.py          # local daily brief generation
    memory/
      json_file.py      # atomic JSON storage helper
  data/
    assistant.db
    notes/
    todos/
    briefs/
  logs/
    unified/
      _session.log
      <flow>.log
    ps1/
    py/
  docs/
    ARCHITECTURE.md
    SETUP.md
    WORKING-GUIDE.md
    DOCUMENTATION-STANDARD.md
    LOGGING.md
  scripts/
    pa.ps1
    openclaw-control.ps1
    summarize-example-app.ps1
    latest-openclaw-errors.ps1
    start.ps1
    install.ps1
  tests/
  .env.example
  README.md
  PLAN.md
```

## MVP Features

Phase 1: OpenClaw Setup

- Install OpenClaw.
- Run onboarding.
- Choose one model provider.
- Connect one chat surface, probably CLI first, then Telegram/WhatsApp later.
- Keep permissions conservative at first.

Phase 2: Local Personal Tools

- Save and retrieve personal notes.
- Add/list/complete todos.
- Read-only file search inside approved folders.
- Morning brief from local notes/tasks.
- Use shared logging for every local tool, with unified logs and `_session.log`.

Phase 3: Safe Actions

- Launch common apps or websites.
- Run approved project scripts.
- Create notes from prompts.
- Summarize selected files.
- Add a confirmation step for actions that change files.
- Expose approved Windows actions through config-driven `devctl.py task` commands so Telegram/OpenClaw can call known safe runners instead of ad hoc shell.

Phase 4: Daily Workflow

- Morning brief: tasks, reminders, calendar, active projects.
- End-of-day summary: what changed, what remains.
- Project context loader for folders such as `jar`, `projects`, and selected workspaces.

Phase 5: Voice/Desktop Layer

- Hotkey to open assistant.
- Push-to-talk voice input.
- Spoken short answers.
- Optional tray app or small local web UI.

Phase 6: Simple OpenClaw Agent UI

Build this only after setup, logging, mobile capture, and runner flows are stable and the user says things are looking good.

- Create a simple local UI page for the OpenClaw agent.
- Keep it operational, not marketing-style:
  - chat input/output
  - quick buttons for common recipes such as `summarize-example-app`, `latest-openclaw-errors`, `handoff`, and `daily-brief`
  - OpenClaw status panel
  - recent personal-assistant logs panel
  - pending mobile commands panel
  - clear indicators for whether Gateway/node service is running
- Route backend actions through `devctl.py` or a thin API wrapper around it so logging and safety gates stay centralized.
- Do not expose secrets, auth state, tokens, or raw private mobile messages in the UI.
- Start local-only on loopback. Do not expose it to LAN, Tailnet, or public internet until explicitly approved.

## First Implementation Checklist

- [x] Confirm model preference: default `github-copilot/gpt-5.4`, agentic `github-copilot/gpt-5.3-codex`, max-complex `github-copilot/claude-opus-4.7`.
- [x] Install OpenClaw.
- [x] Complete OpenClaw local onboarding.
- [x] Save OpenClaw config notes under `openclaw/config-notes.md`.
- [x] Check installed versions of Python, Git, and PowerShell.
- [x] Add shared logging contract and helpers.
- [x] Add principal-engineer engineering standard.
- [x] Add cross-chat checkpoint.
- [x] Add Python `devctl.py` control plane.
- [x] Add runner scripts for OpenClaw control, example-app summary, and OpenClaw error logs.
- [x] Add mobile command capture/list/drain flow with local JSONL audit trail.
- [x] Add central `config/settings.toml`.
- [x] Refactor runner paths, models, aliases, timeouts, log limits, mobile settings, and recipes to config.
- [x] Add full documentation suite: architecture, setup, working guide, and documentation standard.
- [x] Onboard personal-assistant to Desktop sync with a config-driven required-files policy.
- [x] Onboard personal-assistant/OpenClaw log roots to the port `7000` log viewer.
- [x] Add local loopback mobile webhook bridge for command capture.
- [x] Add OpenClaw `doctor-report` artifact capture.
- [x] Add read-only OpenClaw doctor triage.
- [x] Add config-driven model route validation.
- [x] Create Python project structure for local tools and memory.
- [x] Add `.env.example`.
- [x] Add local JSON/JSONL and markdown memory/task stores.
- [x] Add CLI commands for notes, todos, approved-folder search, and daily brief.
- [x] Add first safe tools: notes, todos, file search, daily brief.
- [ ] Add provider adapter for chosen LLM only if OpenClaw cannot route the needed provider directly.
- [x] Add basic tests for Phase 1 local tools.
- [x] Add `scripts/start.ps1`.
- [x] Add configured OpenClaw role agents for default, agentic, and max-complex workflows.
- [x] Add external mobile/channel readiness and login command scaffolding.
- [x] Add guarded OpenClaw command-owner status/set flow for future external mobile commands.
- [x] Add optional local/S3 archive command scaffolding.
- [x] Complete Telegram native channel setup through token file, S3 fallback, pairing approval, and command-owner configuration.
- [x] Verify one real post-pairing Telegram command from the phone returns a bot response.
- [x] Add config-driven laptop tasks for ExampleApp log checks, primary-screen screenshots, Telegram screenshot delivery, and confirmation-gated ExampleApp hotkey startup.
- [x] Add config-driven OpenClaw-native Telegram timeout recovery: retry markers, one gateway-only restart, native retry, then Bot API fallback.
- [x] Add config-driven OpenClaw watchdog with stale-lock handling, native start, direct hidden fallback, and silent Task Scheduler automation.
- [x] Build independent Telegram bridge (long-poll `getUpdates`) as the primary inbound path. Disable OpenClaw native Telegram channel. Bridge supports `ping`, `help`, shortcut `kind=task` rules (`screenshot`, `start example-app`, `example-app errors`), generic `task <name>`, `ask <text>`, and free-form fallback to OpenClaw agent `main`. Audit JSONL persists inbound text, reply text, and dispatch/send/total timing for historic queries.
- [ ] Final stable-state step: build simple local OpenClaw agent UI page after the user confirms the core flow looks good.

## Safety Model

Tool levels:

- Level 0: answer only, no tools.
- Level 1: read-only tools such as search, list, read approved files.
- Level 2: local writes such as notes/todos inside this project.
- Level 3: external or system actions such as launching apps or editing outside the project.
- Level 4: sensitive actions such as deleting files, sending email, credentials, purchases, or system settings.

Default behavior:

- Level 0-1 can run automatically.
- Level 2 should be logged.
- Level 3 should ask for confirmation.
- Level 4 should require explicit typed approval each time.

Logging defaults:

- Shared helpers are mandatory for custom Python and PowerShell tools.
- Primary logs go to `logs/unified/<flow>.log`.
- Cross-flow logs mirror to `logs/unified/_session.log`.
- Per-language logs go to `logs/ps1/` and `logs/py/`.
- Log format: `[YYYY-MM-DD HH:MM:SS] [LANG] [flow] [LEVEL] message`.
- Do not log secrets, tokens, credentials, or private message bodies.

## Good First Assistant Commands

```powershell
pa ask "what should I work on today?"
pa remember "I prefer concise technical explanations"
pa recall "communication preferences"
pa todo add "Review reverse-engineering-memory-context.md"
pa todo list
pa brief morning
```

## Open Decisions

- Should OpenClaw be the main assistant runtime? Recommended: yes.
- Should the assistant use local-only models first, cloud models first, or support both? Current direction: cloud-first through GitHub Copilot Enterprise with `gpt-5.4` default, `gpt-5.3-codex` for agentic/coding workflows, `claude-opus-4.7` for max-complexity work, and local tools/memory controlled by this repo.
- Should it be terminal-first, hotkey-first, or web-ui-first? Current direction: terminal-first now, simple local web UI after the core flow is accepted as stable.
- Which folders should it be allowed to read?
- Which actions should it be allowed to perform without asking?
- Should personal memory stay JSON/JSONL plus markdown for now, or move to SQLite later?
- Should S3 archive be enabled later for memory/context backup? Done: S3 upload is enabled for the dedicated private bucket and gated by `--upload-s3 --confirm`; lifecycle/object-lock policy is config-driven and verified with 60-day archive expiration.
- What frontend stack should the final simple OpenClaw UI use? Recommended later: small local web UI with a thin Python or Node backend that calls `devctl.py`.

## Plan Mode Operating Contract

When this repo is being worked on in plan mode, the plan must capture:

- Current verified setup state.
- Goal and acceptance criteria.
- Files, folders, or modules likely to change.
- Safety gates and approval requirements.
- Logging impact.
- Tests or smoke checks.
- Open decisions.
- Next commands to run.

Plan mode should keep durable handoff files current:

- `CHECKPOINT.md` for what is done, what remains, and how a new chat should resume.
- `PLAN.md` for architecture and roadmap.
- `LOCAL-SETUP.md` for verified tool/runtime state.
- `openclaw/config-notes.md` for OpenClaw-specific decisions.
- `docs/CONFIG.md`, `docs/LOGGING.md`, and `docs/ENGINEERING-STANDARD.md` for repo-wide contracts.
- `docs/DOCUMENTATION-STANDARD.md` defines the rule that every meaningful change must update docs.

Execution mode must still carry plan mode:

- Every non-trivial command should have a visible intent.
- Every script action should log start/result.
- Runner commands should save artifacts when they call OpenClaw.
- Checkpoint updates should follow setup, architecture, or workflow changes.
