<div align="center">

# 🦞 Personal Assistant

### _Your laptop. Your rules. Your agent._

**An always-on, OpenClaw-powered AI co-pilot that listens on Telegram, runs on your Windows machine, and answers in seconds — not minutes.**

[![Made with Python](https://img.shields.io/badge/Python-3.13+-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Powered by OpenClaw](https://img.shields.io/badge/OpenClaw-2026.5-FF6B6B)](https://docs.openclaw.ai)
[![GitHub Copilot](https://img.shields.io/badge/GitHub_Copilot-Enterprise-24292e?logo=github)](https://github.com/features/copilot)
[![Telegram Bot](https://img.shields.io/badge/Telegram-Bot_API-26A5E4?logo=telegram&logoColor=white)](https://core.telegram.org/bots/api)
[![Windows](https://img.shields.io/badge/Windows-10/11-0078D6?logo=windows)](https://www.microsoft.com/windows)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

<sub>SOLID • Liskov-safe adapters • Mandatory logging • Auditable everything</sub>

</div>

---

## ✨ Why this exists

Most "personal AI assistants" are either:

- 🌥️ **Cloud silos** that own your data, your prompts, and your latency floor.
- 🧱 **Hobby scripts** that break the moment Windows reboots, the token rotates, or the model deprecates.

This project is neither.

It is a **principal-engineer-grade, config-driven, multi-agent assistant** that lives on _your_ laptop, routes through _your_ OpenClaw + GitHub Copilot enterprise auth, and exposes itself wherever you actually are: **Telegram, terminal, or scheduled task**.

You speak. It acts. Logs persist. Secrets stay local. Nothing leaves your bucket.

---

## 🚀 What you can do from your phone

Send a message to your bot. Get a useful response in **5–15 seconds** for shortcuts, **30–80 seconds** for free-form chat with the LLM.

```text
You:  ss
Bot:  📷 [screenshot of your primary monitor]

You:  uc errors
Bot:  task: app-latest-errors / status: ok
      <recent warn/error log lines>

You:  start uc
Bot:  task: start-app-hotkeys / status: ok
      DONE All desktop hotkey scripts launched | delay=425ms total=2804ms

You:  what should I work on today?
Bot:  Here's what's on your plate:
      - Review reverse-engineering memory context
      - Resume bridge latency tuning
      ...
```

**Every command is config-driven.** No hard-coded shortcuts. Add a new one by editing `config/settings.toml` and restarting the bridge — no Python edits.

---

## 🧠 Architecture, at a glance

```text
┌─────────────────┐   long-poll    ┌───────────────────────┐
│ Telegram Bot API│ ◄────────────► │  Independent Bridge   │
└─────────────────┘   getUpdates   │  (Python, devctl.py)  │
                                   └──────────┬────────────┘
                                              │
                       ┌──────────────────────┼─────────────────────┐
                       ▼                      ▼                     ▼
              ┌────────────────┐   ┌──────────────────┐   ┌──────────────────┐
              │ Shortcut Rules │   │ Laptop Task      │   │ OpenClaw Agent   │
              │ (kind=canned/  │   │ Dispatcher       │   │ (gpt-5.4 / mini, │
              │  task)         │   │ (PS1/Python/desktop hotkey scripts) │   │  thinking=off)   │
              └────────┬───────┘   └────────┬─────────┘   └────────┬─────────┘
                       │                    │                      │
                       └────────────────────┴──────────────────────┘
                                            │
                                            ▼
                                ┌──────────────────────┐
                                │  Audit JSONL + logs  │
                                │  (every flow, every  │
                                │   call, hashed ids)  │
                                └──────────────────────┘
```

Three classes of inbound message, three latency tiers:

| Tier        | Examples                                | Path                                     | Typical latency |
| ----------- | --------------------------------------- | ---------------------------------------- | --------------- |
| ⚡ Canned   | `ping`, `help`                          | Bridge replies directly                  | <1s             |
| 🔧 Shortcut | `ss`, `uc`, `uc errors`, `start uc`     | Bridge → laptop task (PS1)           | 2–10s           |
| 🧠 LLM ask  | `ask <q>`, anything not matching above  | Bridge → OpenClaw `main` agent → Copilot | 30–80s          |

Every tier writes to `data/telegram-bridge/{inbound,outbound}.jsonl` with hashed sender ids, rule id, dispatch/send/total milliseconds, and (configurable) the full inbound text + reply preview. **Debugging a missed message is one `Get-Content -Tail` away.**

---

## 🧰 Capabilities

<table>
<tr>
<td>

### 💬 Multi-surface chat
- Telegram bridge (primary inbound)
- Local CLI via `devctl.py ask`
- Future: simple local web UI

</td>
<td>

### 🧪 Three configured agents
- `main` (default, `gpt-5.4-mini`, thinking=off)
- `pa-codex` (coding, `gpt-5.3-codex`)
- `pa-max` (max-complexity, `claude-opus-4.7`)

</td>
</tr>
<tr>
<td>

### 🛠️ Approved laptop tasks
- Screenshot + Telegram delivery
- ExampleApp start / log tail
- Daily brief, todos, notes
- All gated by `requires_confirm`

</td>
<td>

### 🧹 Auto-healing infra
- OpenClaw gateway watchdog
- Stale-lock recovery
- Hidden Windows Scheduler tasks
- Battery-aware execution

</td>
</tr>
<tr>
<td>

### 📦 Encrypted offsite archive
- 7-Zip PPMd archives
- Optional S3 with Object Lock
- 60-day governance retention
- Lifecycle policy verified

</td>
<td>

### 📊 Mandatory logging
- Unified per-flow logs
- Session mirror
- Cron-rotated, secrets-redacted
- VS Code colored log scopes

</td>
</tr>
</table>

---

## 🏗️ Engineering principles

This is not a weekend hack. The repo enforces a **principal-engineer standard**:

- 🧱 **SOLID** across every adapter, provider, and tool boundary
- 🔁 **Liskov-safe substitution** — swap a provider, never weaken permissions or skip logs
- 🔐 **Explicit permissions** — Level 0–4 safety model with typed approval for destructive actions
- 📝 **Mandatory logging** — every custom tool emits start + terminal `OK`/`ERROR` lines
- 📚 **Durable docs** — `docs/ARCHITECTURE.md`, `SETUP.md`, `WORKING-GUIDE.md`, `ENGINEERING-STANDARD.md`, `DOCUMENTATION-STANDARD.md`
- 🧪 **Real tests** — unit tests for bridge, dispatchers, owner filter, extractor
- 🧬 **Config-driven** — paths, models, timeouts, recipes, rules all live in `config/settings.toml`. No magic strings in `.py` / `.ps1`.

---

## 🧭 Tech stack

| Layer            | Choice                                                     |
| ---------------- | ---------------------------------------------------------- |
| Runtime          | Python 3.13, PowerShell 7, Node.js 24 (for OpenClaw)       |
| Agent runtime    | [OpenClaw 2026.5](https://docs.openclaw.ai) (Node.js)      |
| LLM provider     | GitHub Copilot Enterprise (`gpt-5.4`, `gpt-5.3-codex`, `claude-opus-4.7`, `gpt-5.4-mini`) |
| Messaging        | Telegram Bot API (long-poll, no webhook)                   |
| Storage          | Local JSON/JSONL + markdown notes, optional S3 archive    |
| Scheduler        | Windows Task Scheduler (silent VBS launchers)              |
| Automation       | PowerShell, desktop automation tools, Python subprocess               |
| Logging          | Custom shared helpers (`pa_logging.py`, `_log_helper.ps1`) |

---

## 🚦 Status

| Component                            | State                |
| ------------------------------------ | -------------------- |
| Telegram bridge (independent)        | ✅ Production         |
| OpenClaw native Telegram channel     | 🚫 Disabled (race)    |
| Shortcut rules (ping/help/ss/uc/...) | ✅ 8 rules configured |
| OpenClaw agent role smokes           | ✅ Passing            |
| OpenClaw watchdog                    | ✅ Installed (5 min)  |
| Daily S3 archive                     | ✅ Installed (22:30)  |
| Local OpenClaw onboarding            | ✅ Complete           |
| Phase 6 local web UI                 | 🟡 Planned ([#21](https://github.com/varadharajaan/personal-assistant/issues/21)) |

---

## 📖 Where to go next

| If you want to…                                    | Start here                                                            |
| -------------------------------------------------- | --------------------------------------------------------------------- |
| **Read the operator manual** (what to actually run) | [`README-TECHNICAL.md`](README-TECHNICAL.md)                          |
| **Understand the architecture**                    | [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)                        |
| **Set this up on a fresh machine**                 | [`docs/SETUP.md`](docs/SETUP.md)                                      |
| **Add a new Telegram shortcut**                    | [`docs/WORKING-GUIDE.md`](docs/WORKING-GUIDE.md)                      |
| **See the bridge internals**                       | [`docs/MOBILE-BRIDGE.md`](docs/MOBILE-BRIDGE.md)                      |
| **Read the engineering standard**                  | [`docs/ENGINEERING-STANDARD.md`](docs/ENGINEERING-STANDARD.md)        |
| **See the full plan + roadmap**                    | [`PLAN.md`](PLAN.md)                                                  |

---

## 🔒 Security & privacy

- Telegram tokens are read from a local credentials file — never logged, never committed.
- Bot replies are gated by an **owner allowlist** loaded from OpenClaw config.
- Sender ids are **hashed** before being written to audit JSONL.
- Run logs **redact** anything matching configured secret patterns.
- All inbound/outbound previews are size-capped (default 4000 chars) and tokens are hard-redacted by `[logs].redaction_patterns`.
- S3 archives use **AES-256 SSE**, **Object Lock GOVERNANCE**, and a **60-day lifecycle**.

If you fork this and run it: rotate your Telegram token, set your own owner id, and don't push `data/`, `logs/`, or `.env`.

---

## 🤝 Contributing

This is currently a personal-assistant codebase, not a general-purpose framework. PRs that improve safety, logging, or testability are welcome. PRs that add features without honoring the engineering standard will be politely declined.

Read [`docs/ENGINEERING-STANDARD.md`](docs/ENGINEERING-STANDARD.md) before opening one.

---

## 📜 License

[MIT](LICENSE) — do what you want, no warranty, attribution appreciated.

---

<div align="center">

**Built with ❤️ and `thinking=off` to keep the latency reasonable.**

</div>
