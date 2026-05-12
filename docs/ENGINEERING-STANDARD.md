# Principal Engineer Standard

This project should be maintained like a long-lived principal-engineer repo, even while it is still small.

## Non-Negotiables

- Keep the architecture extensible before it becomes clever.
- Keep all tunable values in `config/settings.toml`.
- Prefer explicit contracts, schemas, registries, and small modules over hidden coupling.
- Every custom tool or skill must log through the shared helpers in `shared/`.
- Never log secrets, tokens, full auth headers, private message bodies, or credentials.
- Keep permissions narrow by default, then expand deliberately.
- Treat OpenClaw as the assistant runtime and custom code as well-scoped local tools.
- Use native OpenClaw features first. Add custom code only when OpenClaw does not cover the need, or when the repo must add local logging, config, safety gates, or small glue around a native capability.
- Do not add shell, browser, email, calendar, S3, command-owner, mobile exposure, or system automation without a safety gate.
- Keep docs current when decisions change. Future chats should not need to rediscover basics.
- Follow `docs/DOCUMENTATION-STANDARD.md`; documentation updates are mandatory for every meaningful change.

## Configuration Standard

- No configurable model id, path, timeout, limit, include/exclude rule, recipe prompt, alias, provider, local tool status, local tool scope, or local tool file policy should be hardcoded in Python or PowerShell.
- Use `assistant/devctl/config.py` from Python.
- Use `devctl.py alias` from PowerShell wrappers instead of embedding recipe or command details.
- If a value might need to change later, put it in `config/settings.toml` now.

## Protected Local Services

Some local tools are already working and must not be disturbed by assistant setup work.

Protected service:

- ClipSync: local Wi-Fi clipboard/text sync between machine 1 and peer machines.

Rules:

- Do not stop, restart, kill, unregister, re-pair, reconfigure, change ports, delete state, or edit runtime config for protected services unless the user explicitly asks for that service.
- OpenClaw device registration and pairing work is scoped to OpenClaw only; it must not modify ClipSync pairing, peers, ports, startup entries, or runtime state.
- Read-only docs/log/status checks are acceptable when needed for context.
- The protected-service registry lives in `config/settings.toml` under `[protected_services.*]`.

## SOLID Guidance

- Single responsibility: each tool should do one job, with storage, logging, validation, and command routing kept separate.
- Open/closed: add new tools through registries or adapters instead of editing a central switch for every change.
- Liskov substitution: any provider, storage backend, or tool adapter must honor the same contract as the interface it replaces. A fallback provider must not weaken safety checks, skip logging, change output shape, or create side effects the caller did not request.
- Interface segregation: keep small interfaces for notes, todos, file search, memory, archive, and actions. Do not force a tool to depend on methods it does not use.
- Dependency inversion: core assistant flows should depend on local interfaces, not directly on OpenClaw, S3, SQLite, or a specific model vendor.

## Folder Standards

- `assistant/` owns custom local assistant code.
- `assistant/tools/` owns user-facing tool implementations.
- `assistant/memory/` owns local durable memory and archive retrieval code.
- `assistant/llm/` owns provider adapters only if a custom provider layer becomes necessary.
- `shared/` owns reusable helpers, especially logging.
- `data/` owns local runtime data such as markdown notes, JSON/JSONL todos, generated briefs, and future SQLite stores.
- `logs/` owns runtime logs only.
- `openclaw/` owns OpenClaw config, prompts, skills, and workspace notes.
- `docs/` owns design contracts, operational notes, and decisions.
- `scripts/` owns repeatable setup/start/smoke-check commands.
- `tests/` owns automated tests and regression checks.

## Logging Standard

Follow `docs/LOGGING.md` for every custom tool.

Required behavior:

- Initialize logging at the start of each script/tool flow.
- Write at least one `INFO` start event and one `OK`, `WARN`, or `ERROR` finish event.
- For `devctl.py`, the top-level command dispatcher owns the start/finish audit log; inner modules should log only side effects or accept a logger from the caller.
- Mirror to `logs/unified/<flow>.log` and `logs/unified/_session.log`.
- Include useful context such as tool name, record count, file count, or mode.
- Redact secrets and sensitive personal content.

## Plan Mode Contract

When working in plan mode for this repo, the plan must include:

- Current verified state.
- Goal and acceptance criteria.
- Files or modules likely to change.
- Risks and safety gates.
- Logging impact.
- Tests or smoke checks.
- Decisions still open.
- Next commands that should be run.

Plan mode should update durable docs when a decision is made, especially `PLAN.md`, `LOCAL-SETUP.md`, `openclaw/config-notes.md`, and `CHECKPOINT.md`.

Plan mode is part of execution mode here. Even when implementing directly, preserve the plan discipline:

- state intent before significant edits or commands
- keep logs for script/tool execution
- save artifacts for OpenClaw runs
- update checkpoint/docs when the repo state changes

## Definition Of Done

A change is done only when:

- The implementation is scoped to the requested behavior.
- Logging is present for custom scripts/tools.
- Safety gates are documented for risky actions.
- Tests or smoke checks have been run, or the reason they could not run is recorded.
- Relevant docs are updated in the same work session.
- The checkpoint is updated if the change affects setup state, architecture, or the next step.
