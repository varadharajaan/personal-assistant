# Documentation Standard

Documentation is part of the product in this repo. A change is incomplete if the documentation no longer matches reality.

## Non-Negotiable Rule

Every meaningful change must update documentation in the same work session.

This applies to:

- code behavior
- config keys or defaults
- folder structure
- model policy
- setup/onboarding steps
- runner commands
- mobile command flow
- logging behavior
- safety gates
- architecture boundaries
- known issues or recovery steps
- completed work and remaining work

## Required Docs By Change Type

Architecture or folder ownership:

- `docs/ARCHITECTURE.md`
- `PLAN.md`
- `CHECKPOINT.md`

Setup, install, onboarding, versions, or machine state:

- `docs/SETUP.md`
- `LOCAL-SETUP.md`
- `CHECKPOINT.md`

Runner commands, `devctl.py`, PowerShell wrappers, aliases, recipes, or mobile flow:

- `docs/RUNNERS.md`
- `docs/WORKING-GUIDE.md`
- `config/settings.toml` if defaults changed
- `CHECKPOINT.md` if current behavior changed

Mobile bridge, webhook capture, or external mobile channel setup:

- `docs/MOBILE-BRIDGE.md`
- `docs/RUNNERS.md`
- `docs/WORKING-GUIDE.md`
- `docs/CONFIG.md` if endpoint/auth defaults changed
- `CHECKPOINT.md`

Local tools, notes, todos, file search, brief generation, or memory storage:

- `docs/LOCAL-TOOLS.md`
- `docs/RUNNERS.md`
- `docs/ARCHITECTURE.md` if folder ownership changed
- `docs/CONFIG.md` if defaults or config keys changed
- `PLAN.md` if roadmap state changed
- `CHECKPOINT.md`

Config keys, defaults, includes, excludes, models, providers, limits, or timeouts:

- `docs/CONFIG.md`
- `config/settings.toml`
- `CHECKPOINT.md` if the operational policy changed

Logging behavior:

- `docs/LOGGING.md`
- `docs/RUNNERS.md` if runner logs changed
- `CHECKPOINT.md` if verification state changed

OpenClaw provider, onboarding, gateway, node host, model routing, or channel setup:

- `openclaw/config-notes.md`
- `docs/SETUP.md`
- `docs/WORKING-GUIDE.md`
- `CHECKPOINT.md`

Safety or permission behavior:

- `docs/ENGINEERING-STANDARD.md`
- `docs/ARCHITECTURE.md`
- `docs/RUNNERS.md` if runner behavior changed
- `CHECKPOINT.md`

Roadmap or phase changes:

- `PLAN.md`
- `CHECKPOINT.md`

## Definition Of Done

A change is done only when:

- implementation is complete
- configurable values are in `config/settings.toml`
- logs are written for custom scripts/tools
- smoke checks or tests were run
- docs affected by the change are updated
- `CHECKPOINT.md` is updated for state/roadmap changes
- known issues are documented rather than hidden in chat

## Documentation Quality Bar

Docs should be:

- accurate enough for a new chat to resume
- explicit about what is done and not done
- command-oriented where setup or operations are involved
- honest about failures, timeouts, and known issues
- concise, but not vague
- written in plain engineering language

## Cross-Chat Memory

Treat these files as durable memory:

- `CHECKPOINT.md`
- `PLAN.md`
- `LOCAL-SETUP.md`
- `openclaw/config-notes.md`
- `docs/ARCHITECTURE.md`
- `docs/SETUP.md`
- `docs/WORKING-GUIDE.md`
- `docs/CONFIG.md`
- `docs/LOGGING.md`
- `docs/LOCAL-TOOLS.md`
- `docs/RUNNERS.md`
- `docs/MOBILE-BRIDGE.md`
- `docs/ENGINEERING-STANDARD.md`

When opening a fresh chat, read `CHECKPOINT.md` first.
