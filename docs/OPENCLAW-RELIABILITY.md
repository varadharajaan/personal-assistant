# OpenClaw Reliability

## Problem Observed

OpenClaw Telegram worked, but the gateway could become unreliable after Telegram API fetch timeouts. The observed failure shape was:

- Telegram `getMe` / `getUpdates` fetch timeout.
- OpenClaw diagnostic liveness warning for event-loop delay/utilization.
- Gateway/node host tick timeout.
- Gateway port `127.0.0.1:18789` stopped listening.
- An orphan OpenClaw node process remained:
  `openclaw/dist/index.js node run --host 127.0.0.1 --port 18789`.

This is not ClipSync and not ExampleApp Launcher. The recovery layer must not stop or reconfigure ClipSync.

## Watchdog

The repo now has a config-driven OpenClaw watchdog:

```powershell
python .\devctl.py watchdog status
python .\devctl.py watchdog run
python .\devctl.py watchdog schedule-status
```

Configuration lives in:

```toml
[openclaw_watchdog]
[openclaw_watchdog_schedule]
```

The watchdog checks:

- loopback port `127.0.0.1:18789`
- the configured lightweight HTTP probe, currently `GET /__openclaw__/canvas/`
- a real OpenClaw CLI channel-status round trip, currently `openclaw channels status --timeout 30000 --json`
- OpenClaw gateway/node command-line markers from config
- stale gateway lock files under the configured OpenClaw temp log path

The layered probe is intentional. The HTTP probe quickly catches a closed
listener. The CLI channel-status probe catches the deeper stuck state where the
canvas endpoint answers but the gateway WebSocket command path times out.

If unhealthy, it:

1. Kills only configured OpenClaw gateway/node processes.
2. Refuses protected processes whose command line matches configured ClipSync markers.
3. Renames stale gateway lock files when their recorded PID no longer exists.
4. Tries OpenClaw-native `gateway start` / `node start`.
5. If native start returns success but the gateway still does not bind, uses the configured direct hidden gateway fallback.

## Scheduled Task

The installed task is:

```text
PersonalAssistantOpenClawWatchdog
```

It runs every 10 minutes through:

```text
wscript.exe //B //Nologo scripts/openclaw-watchdog.vbs
```

The VBS wrapper starts `scripts/openclaw-watchdog.ps1` hidden, waits for the result, and writes to:

```text
logs/unified/openclaw-watchdog.log
logs/unified/_session.log
logs/py/openclaw-watchdog.log
logs/vbs/openclaw-watchdog.log
```

## Recovery Commands

Manual health check:

```powershell
python .\devctl.py watchdog status
python .\devctl.py mobile channel status --json --timeout-ms 30000
```

Manual recovery:

```powershell
python .\devctl.py watchdog run
```

Task management:

```powershell
python .\devctl.py watchdog schedule-plan
python .\devctl.py watchdog schedule-install --confirm
python .\devctl.py watchdog schedule-status
python .\devctl.py watchdog schedule-run-now --confirm
python .\devctl.py watchdog schedule-delete --confirm
```

## Verification

Latest verified recovery:

- Watchdog detected `gateway-process-missing`, `gateway-port-closed`, and `orphan-node-process`.
- Killed only configured OpenClaw gateway/node processes.
- Native OpenClaw start returned success but did not leave the gateway healthy.
- Direct hidden fallback started the gateway.
- `watchdog status` reported healthy through the layered HTTP plus channel-status probe.
- `mobile channel status --json --timeout-ms 30000` reported Telegram configured, running, connected, and token available after warm-up.
- Scheduled task run at 2026-05-11 20:35 returned `Last Result: 0`.
- A real OpenClaw model call returned exactly `PA_GATEWAY_OK` through `github-copilot/gpt-5.4`.
- A real Telegram delivery test through `app-latest-errors --send-telegram --confirm` completed with Telegram return code `0`.

Residual risk: `mobile channel status` can still report
`eventLoop.degraded=true` while Telegram is connected. The watchdog keeps the
assistant recoverable, but the cleaner long-term channel design is to move from
Telegram polling to an OpenClaw-supported webhook/tunnel when that channel is
ready.

2026-05-11 22:45 update: a Telegram test message was not processed while the
gateway command path was timing out. `lastInboundAt` remained `null` after
recovery. The watchdog now uses `probe_strategy = "http_and_cli"` so the same
gateway timeout state is detected automatically instead of being hidden by a
healthy canvas HTTP response.

2026-05-11 22:58 update: OpenClaw Telegram polling was tuned through native
OpenClaw config:

```text
channels.telegram.timeoutSeconds = 8
channels.telegram.pollingStallThresholdMs = 45000
```

After gateway restart and warm-up, `mobile channel status` reported Telegram
`connected=true`, `watchdog status` reported healthy, and the scheduled
watchdog task run at 22:55 returned `LastTaskResult = 0`.
