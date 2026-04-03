---
name: ezviz-cb60-control
description: Control an EZVIZ or 萤石 CB60 camera when the user wants PTZ left/right movement, zoom, snapshots, or live stream URLs. Use this skill for CB60 device operations backed by EZVIZ Open Platform APIs, and keep all credentials in environment variables instead of files or command logs.
---

# Ezviz Cb60 Control

## Overview

Use this skill when the user wants to operate a 萤石 CB60 camera from OpenClaw or Codex. It packages the reusable workflow around a small Python controller script and keeps secrets out of code, files, and shell history.

The current implementation covers:

- PTZ left and right
- Snapshot capture
- Live stream URL retrieval
- Managed stream creation / update / listing / address fetch
- Video encode query / switch attempt
- Device info / status / battery query
- Manual live URL handoff
- Preview URL diagnosis
- Logical channel probing for dual-lens validation
- Local shot-planning workflow for up to 4 clips
- Recurring OpenClaw task management for daily capture windows
- Strict merchant command parsing around the wake word `龙虾`
- Daily reporting and capture-session logging
- Auto-convert recorded clips to rotated MP4 when ffmpeg is available
- Minute-level status monitoring with local logs and report output
- Capability inspection

The device profile this skill assumes:

- `support_ptz=支持`
- `ptz_left_right=支持`
- `ptz_top_bottom=不支持`
- `support_close_up_focal_adjust=1`
- `support_talk=支持双工`

Real-device validation currently confirms:

- Left and right PTZ works
- Snapshot capture works
- Live URL still needs tenant-specific `source` input
- Manual preview URLs can be passed through directly
- REST zoom control is rejected by the device on this control path
- No public lens-switch API has been confirmed yet
- The public API follows the lens currently selected in the EZVIZ app

Voice talk is documented but not executed by the portable script yet because the official capability is exposed through player SDK methods rather than a small, stable REST flow that can be safely validated in this environment.

## Safety Rules

- Never put `AppKey`, `Secret`, `AccessToken`, device serials, or validate codes into code, markdown, tests, or shell command strings.
- Read credentials only from environment variables:
  - `EZVIZ_APP_KEY`
  - `EZVIZ_APP_SECRET`
  - `EZVIZ_ACCESS_TOKEN`
  - `EZVIZ_DEVICE_SERIAL`
  - `EZVIZ_VALIDATE_CODE`
  - `EZVIZ_CHANNEL_NO`
  - `EZVIZ_BASE_URL`
  - `EZVIZ_LIVE_URL_PATH`
  - `EZVIZ_LIVE_SOURCE`
  - `EZVIZ_MANUAL_LIVE_URL`
- If env vars are missing, ask the user to export them. Do not create `.env` files unless the user explicitly asks.

## Quick Start

The controller lives at `scripts/ezviz_cb60_control.py`.
The local workflow helper lives at `scripts/cb60_capture_workflow.py`.
The recurring task helper lives at `scripts/cb60_task_manager.py`.

Before running the skill on a new machine, create and load a local environment file such as:

```bash
python3 scripts/ezviz_cb60_control.py setup-env
source ~/.ezviz_cb60_env
```

The setup wizard actively asks for:

- `EZVIZ_APP_KEY`
- `EZVIZ_APP_SECRET`
- `EZVIZ_ACCESS_TOKEN`
- `EZVIZ_DEVICE_SERIAL`
- `EZVIZ_VALIDATE_CODE`
- `EZVIZ_CHANNEL_NO`

If an operator wants to prepare a second camera profile on the same machine:

```bash
python3 scripts/ezviz_cb60_control.py setup-env --output ~/.ezviz_cb60_env_cam2
source ~/.ezviz_cb60_env_cam2
```

Manual file creation is still allowed, but the wizard is now the preferred onboarding path.

Do not rely on temporary files under `/tmp` for long-term setup, because they may disappear after cleanup or reboot.

If you need to switch between cameras on the same machine, keep one env file per device and pass it per command:

```bash
python3 scripts/ezviz_cb60_control.py --env-file ~/.ezviz_cb60_env_cam2 doctor
python3 scripts/cb60_capture_workflow.py --env-file ~/.ezviz_cb60_env_cam2 capture-shot --session ./artifacts/workflows/<session>/session.json
```

This keeps the plugin single-device per invocation while still letting the operator switch between multiple cameras without editing code.

Common commands:

```bash
python3 scripts/ezviz_cb60_control.py capabilities
python3 scripts/ezviz_cb60_control.py doctor
python3 scripts/ezviz_cb60_control.py setup-env
python3 scripts/ezviz_cb60_control.py device-info
python3 scripts/ezviz_cb60_control.py device-status
python3 scripts/ezviz_cb60_control.py battery
python3 scripts/ezviz_cb60_control.py ptz left --duration 1.2
python3 scripts/ezviz_cb60_control.py ptz right --duration 1.2
python3 scripts/ezviz_cb60_control.py snapshot --output /tmp/cb60.jpg
python3 scripts/ezviz_cb60_control.py video-encode-get --stream-type 1
python3 scripts/ezviz_cb60_control.py stream-create --start-time '2026-03-25 19:15:55' --end-time '2026-03-25 21:15:55'
python3 scripts/ezviz_cb60_control.py stream-address --stream-id <stream_id> --protocol 3 --quality 1 --support-h265 1
python3 scripts/ezviz_cb60_control.py live-url --source <tenant-source>
python3 scripts/ezviz_cb60_control.py diagnose-preview --url '<preview-url>'
python3 scripts/ezviz_cb60_control.py probe-channels --source <tenant-source> --output-dir /tmp/cb60-probe
python3 scripts/cb60_capture_workflow.py init-session --brief '门头, 店内全景, 商品近景'
python3 scripts/cb60_capture_workflow.py next-shot --session ./artifacts/workflows/<session>/session.json
python3 scripts/cb60_capture_workflow.py capture-shot --session ./artifacts/workflows/<session>/session.json --stream-url '<flv-or-hls-url>' --rotation cw90
python3 scripts/cb60_task_manager.py init-task --task-root ./artifacts/task-manager/store-a --start-time 11:00 --end-time 12:00 --brief '门头, 店内全景'
python3 scripts/cb60_task_manager.py merchant-command --task ./artifacts/task-manager/store-a/task.json --text '龙虾，怎么没有拍摄，帮我找找问题'
python3 scripts/cb60_task_manager.py daily-report --task ./artifacts/task-manager/store-a/task.json --status-root ./artifacts/status-monitor/live
python3 scripts/cb60_status_monitor.py run --interval-seconds 60 --max-rounds 5
```

Run commands from:

`/Users/bytedance/Documents/Playground/working/openclaw-plugins/ezviz-cb60-control/skills/ezviz-cb60-control`

## Workflow

1. Run `doctor` to confirm required env vars are present.
2. If the user requests PTZ left/right, use `ptz <direction> --duration <seconds>`.
3. If the user requests a screenshot, use `snapshot` and optionally download to a file.
4. If the user requests a playable stream, use `live-url --source ...` and return the URL or hand it to another tool.
5. If the API path is unstable but the user already has a valid preview URL, set `EZVIZ_MANUAL_LIVE_URL` and use `live-url` or `diagnose-preview`.
6. If playback fails, run `diagnose-preview` and check codec support, encryption, and URL freshness.
7. If the user wants to verify dual-lens exposure, run `probe-channels --channels 1 2 --output-dir ...`.
8. If channel 2 succeeds, compare the saved snapshots to verify whether it is the tele lens or just another stream variant.
9. If channel 2 fails while channel 1 succeeds, treat the device as a single public API channel.
10. If snapshots change after the user manually switches lenses in the EZVIZ app, treat the public API as following the app-selected lens state.
11. Do not promise direct lens switching from the plugin unless a public API or SDK method is later confirmed.
12. If the user wants a lightweight shoot flow, create a local session with `init-session` and keep the shot count at 3 or 4 maximum.
13. Use the generated shot order to reduce repositioning. The workflow intentionally groups shots by zone before capture.
14. After each capture, read the next instruction from `capture-shot` or `next-shot` instead of asking for repeated confirmations.
15. The workflow now prefers FLV recording when a FLV address is provided, because it has proven more stable than the earlier HLS-only path in real-device validation.
16. If `EZVIZ_MANAGED_STREAM_ID` is set, the workflow should prefer that long-lived managed stream and fetch a fresh playback address from it instead of creating or assuming a temporary live address.
17. The workflow now tries to auto-convert recorded clips into rotated `.mp4` output when `ffmpeg` is available. The default is `cw90`, which turns a landscape source into portrait output.
18. If conversion fails or `ffmpeg` is missing, it safely falls back to the original recorded container.
19. When no direct `--stream-url` and no managed stream are provided, the workflow should default to `protocol=4`, `quality=1`, `supportH265=1`, and `type=1` so it requests the same fresh FLV address shape that passed real-device validation.
20. Every workflow capture should append structured logs to `capture-log.jsonl` and refresh `capture-report.md` inside the session folder.
21. After each capture, validate the resulting file. If validation is abnormal or failed, save a failure frame and attach a short failure analysis. If `tesseract` is available locally, include OCR text from the saved frame.
22. Keep all captured clips local for now under the session folder. Do not add cloud upload unless the user asks.
23. If the user requests recurring device health polling, use `cb60_status_monitor.py`; default to 60-second intervals unless the user asks for something else.
24. The status monitor should write `samples.jsonl`, `samples.csv`, `events.jsonl`, and `report.md` under its output directory.
25. If the user wants OpenClaw to run a daily capture window, use `cb60_task_manager.py init-task` to create a local task state file instead of inventing a new scheduler format.
26. Merchant-side interaction must stay inside the fixed boundary: modify capture time, diagnose capture problems, or stop capture. Reject anything else through `merchant-command`.
27. After each finished capture session, call `record-session` so the plugin can build a real daily report with clip counts and upload counts.
28. Use `daily-report` for end-of-day summaries and `diagnose-task` when the merchant says “怎么没有拍摄”.
29. If the user requests zoom, explain that the current REST control path was rejected by the real CB60 device.
30. If the user requests voice talk, explain that this skill currently stops at the SDK boundary and refer to `references/api-notes.md`.

## API Notes

Read `references/api-notes.md` when you need the official/inferred API boundary, especially before changing endpoint behavior or expanding into voice talk.

For the recommended split between OpenClaw workflow orchestration and a small local capture server, read:

- `references/minimal-server-architecture.md`
