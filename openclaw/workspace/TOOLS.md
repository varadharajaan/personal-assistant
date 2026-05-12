# TOOLS.md - Local Notes

Skills define _how_ tools work. This file is for _your_ specifics — the stuff that's unique to your setup.

## What Goes Here

Things like:

- Camera names and locations
- SSH hosts and aliases
- Preferred voices for TTS
- Speaker/room names
- Device nicknames
- Anything environment-specific

## Examples

```markdown
### Cameras

- living-room → Main area, 180° wide angle
- front-door → Entrance, motion-triggered

### SSH

- home-server → 192.168.1.100, user: admin

### TTS

- Preferred voice: "Nova" (warm, slightly British)
- Default speaker: Kitchen HomePod
```

## Why Separate?

Skills are shared. Your setup is yours. Keeping them apart means you can update skills without losing your notes, and share skills without leaking your infrastructure.

---

Add whatever helps you do your job. This is your cheat sheet.

## Personal Assistant Control Plane

Primary repo:

```text
<repo-root>
```

Use `devctl.py` through the repo root for local assistant tools:

```powershell
python .\devctl.py startup run
python .\devctl.py ask "message"
python .\devctl.py ask "message" --role agentic
python .\devctl.py ask "message" --role max
python .\devctl.py notes list
python .\devctl.py todos list
python .\devctl.py files search --scope personal --query "text"
python .\devctl.py brief daily --show
python .\devctl.py mobile list --status pending
python .\devctl.py mobile owner status --json
python .\devctl.py archive plan
python .\devctl.py openclaw doctor-triage
python .\devctl.py task list
python .\devctl.py task run ultracode-latest-errors --send-telegram --confirm
python .\devctl.py task run screen-primary-screenshot --send-telegram --confirm
python .\devctl.py task run ultracode-start-hotkeys --send-telegram --confirm
```

Role routing:

- `default`: normal personal assistant work, OpenClaw agent `main`, model `github-copilot/gpt-5.4`.
- `agentic`: coding and multi-step implementation, OpenClaw agent `pa-codex`, model `github-copilot/gpt-5.3-codex`.
- `max`: maximum-complexity reasoning, OpenClaw agent `pa-max`, model `github-copilot/claude-opus-4.7`.

Safety:

- Desktop sync runs only when the user explicitly asks.
- ClipSync is protected; do not stop, restart, re-pair, reconfigure, change ports, or edit its runtime state unless the user explicitly asks for ClipSync work.
- Mobile external exposure is disabled by default. Do not expose the webhook to LAN or a tunnel until token protection and channel choice are explicit.
- Do not set OpenClaw command owners until the approved external channel-native id is known.
- Sending messages, email, purchases, credential changes, deletion, and broad system automation require explicit confirmation.
- Laptop actions must use configured `devctl.py task` names. Do not invent ad hoc commands for hotkeys, screenshots, or UltraCode log checks.
- Screenshot delivery tries OpenClaw native Telegram media send first, then the configured Telegram Bot API fallback if the gateway message-send path times out.
- Telegram-originated laptop tasks should use `--send-telegram --confirm` for their task command, because the devctl delivery path has the configured OpenClaw-native retry and Bot API fallback.

Logs:

- Custom Python and PowerShell tools log under `logs/unified`, `logs/py`, and `logs/ps1`.
- Do not copy tokens, credentials, or raw private mobile messages into ordinary logs.

## Related

- [Agent workspace](/concepts/agent-workspace)
