---
name: ezviz-cb60-control
description: Control an EZVIZ or 萤石 CB60 camera when the user wants PTZ left/right movement, zoom, snapshots, or live stream URLs. Use this skill for CB60 device operations backed by EZVIZ Open Platform APIs, and keep all credentials in environment variables instead of files or command logs.
---

# Ezviz Cb60 Control

## Runtime Hard Boundary

When this skill runs inside a merchant/OpenClaw workspace, it is an operator, not a maintainer.

Runtime agents must never edit, patch, rewrite, or create plugin source files in the installed workspace, including:

- `scripts/`
- `skills/`
- `tests/`
- `README.md`
- `SOUL.md`
- `openclaw.plugin.json`
- any Git-tracked repository file

Do not use edit/write actions to hot-fix code under paths such as `/root/.openclaw/workspace/doudou_camera_control/...`.

Allowed runtime changes are limited to non-source operational state:

- env files such as `~/.ezviz_cb60_env`
- task JSON files
- capture/session artifacts
- status logs
- OpenClaw runtime configuration directly required to load this plugin

If a capture fails because of a plugin source bug, stop, record the error, and tell the operator to update the plugin from GitHub. Source fixes must be made in the development repository, tested, committed, and pushed by the maintainer.

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
- First-boot onboarding that only asks the merchant for one capture time window
- Strict merchant command parsing around the wake word `龙虾`
- Battery precheck one hour before the next scheduled capture window
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

If OpenClaw is responsible for onboarding the merchant, call this first:

```bash
python3 scripts/cb60_task_manager.py install-onboarding-message
```

OpenClaw should send the returned `message_text` to the user immediately after plugin install, collect the required credentials and TOS prefixes, and only then continue to ask:

```text
你希望这个摄像头在什么时候拍？
```

The setup wizard actively asks for:

- `EZVIZ_APP_KEY`
- `EZVIZ_APP_SECRET`
- `EZVIZ_ACCESS_TOKEN`
- `EZVIZ_DEVICE_SERIAL`
- `EZVIZ_VALIDATE_CODE`
- `EZVIZ_CHANNEL_NO`

`EZVIZ_ACCESS_TOKEN` expires after 7 days. If an EZVIZ request returns `10002`, the controller refreshes the token once with `EZVIZ_APP_KEY` + `EZVIZ_APP_SECRET`, retries the original request, and writes the fresh token back to the same `--env-file` when one is provided. Do not make OpenClaw generate ad-hoc token refresh scripts during normal capture.

Runtime OpenClaw agents must not hot-patch plugin source files. If capture fails because of a bug in `scripts/`, `skills/`, `tests/`, `README.md`, `SOUL.md`, or `openclaw.plugin.json`, stop, record the error, and tell the operator to update the plugin from GitHub. Only the maintainer should modify source in the development repository, run tests, and push a new version.

After plugin install, OpenClaw should first install these 3 LAS skills by default before starting the full workflow:

```bash
npx skills add https://skills.volces.com/skills/volcengine/las -s byted-las-video-edit --agent openclaw
npx skills add https://skills.volces.com/skills/volcengine/las -s byted-las-video-resize --agent openclaw
npx skills add https://skills.volces.com/skills/volcengine/las -s byted-las-video-inpaint --agent openclaw
```

LAS hard rule:

- OpenClaw must call the installed local LAS skills directly for highlight edit, inpaint, and resize
- OpenClaw must not rewrite LAS authentication, switch domains, invent AK/SK signing, decode unrelated secrets, or replace the built-in Bearer `LAS_API_KEY` flow
- If LAS fails, inspect the installed skill output and plugin logs first; do not patch LAS transport/auth logic during merchant-facing runs

The setup wizard now always collects the full Volcano LAS/TOS configuration required by the complete plugin workflow:

- `LAS_API_KEY`
- `LAS_REGION`
- `TOS_ACCESS_KEY`
- `TOS_SECRET_KEY`
- `TOS_ORIGINAL`
- `TOS_FINAL`
- `TOS_ORIGINAL` 和 `TOS_FINAL` 必须是显式商家目录，不能只给 `TOS_PREFIX`
- 禁止使用通用目录，如 `tos://.../openclaw/original/` 或 `tos://.../openclaw/final/`

Before any full LAS/TOS run, the runtime should verify TOS readiness with:

```bash
python3 scripts/ezviz_cb60_control.py doctor
python3 scripts/ezviz_cb60_control.py tos-preflight
```

Do not guess package names or install dependencies ad hoc during merchant-facing runs. The required Python package name is exactly `tos`, and it should be preinstalled in the runtime image/environment.

The runtime should also preinstall:

- `ffmpeg`
- `tesseract-ocr`

`tesseract` is only used for OCR on saved failure frames. It is not a prerequisite for an `accepted` capture.

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

For LAS watermark removal, the workflow now supports an optional env var:

```bash
export LAS_INPAINT_FIXED_BBOXES='[[0,650,150,970]]'
```

This uses 1000x1000 normalized coordinates and is tuned for the common "bottom-left timestamp watermark" case. Keep it as-is by default, or adjust it if a different camera places the watermark elsewhere.

For merchant-specific LAS/TOS outputs, configure per-store prefixes such as:

```bash
export TOS_ORIGINAL='tos://doudou-video/openclaw/store1_jsspa_original/'
export TOS_FINAL='tos://doudou-video/openclaw/store1_jsspa_final/'
```

The plugin will derive the merchant slug (`store1_jsspa`) from these prefixes and name cloud videos like:

```text
store1_jsspa_20260415_101530_original_01.mp4
store1_jsspa_20260415_101530_final_01.mp4
```

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
python3 scripts/ezviz_cb60_control.py stream-address --stream-id <stream_id> --protocol 1 --quality 1 --support-h265 1
python3 scripts/ezviz_cb60_control.py live-url --source <tenant-source>
python3 scripts/ezviz_cb60_control.py diagnose-preview --url '<preview-url>'
python3 scripts/ezviz_cb60_control.py probe-channels --source <tenant-source> --output-dir /tmp/cb60-probe
python3 scripts/cb60_capture_workflow.py init-session --brief '门头, 店内全景, 商品近景'
python3 scripts/cb60_capture_workflow.py next-shot --session ./artifacts/workflows/<session>/session.json
python3 scripts/cb60_capture_workflow.py capture-shot --session ./artifacts/workflows/<session>/session.json --rotation cw90
python3 scripts/cb60_task_manager.py init-task --task-root ./artifacts/task-manager/store-a --start-time 11:00 --end-time 12:00 --brief '门头, 店内全景'
python3 scripts/cb60_task_manager.py first-boot-setup --task-root ./artifacts/task-manager/store-a --time-window-text '11:00-12:00'
python3 scripts/cb60_task_manager.py merchant-command --task ./artifacts/task-manager/store-a/task.json --text '龙虾，怎么没有拍摄，帮我找找问题'
python3 scripts/cb60_task_manager.py battery-precheck --task ./artifacts/task-manager/store-a/task.json
python3 scripts/cb60_task_manager.py daily-report --task ./artifacts/task-manager/store-a/task.json --status-root ./artifacts/status-monitor/live
python3 scripts/cb60_task_manager.py workflow-spec
python3 scripts/cb60_status_monitor.py run --interval-seconds 60 --max-rounds 5
```

For `stream-address`, the managed-stream API protocol mapping is:

- `1 = hls`
- `2 = rtmp`
- `3 = flv`

The CLI also accepts legacy `4` and silently treats it as `flv` for backward compatibility.

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
15. For OpenClaw-style runtime environments, the workflow should use a single fixed capture path: managed-stream address -> HLS -> `supportH265=1`. Do not keep a FLV-first or H264-compatible-first path in the capture workflow.
16. If `EZVIZ_MANAGED_STREAM_ID` is set, the workflow should reuse that long-lived managed stream and fetch a fresh HLS playback address from it. If no long-lived managed stream is configured, create a temporary stream and still resolve the HLS address through `stream-address`.
17. The workflow now tries to auto-convert recorded clips into rotated `.mp4` output when `ffmpeg` is available. The default is `cw90`, which turns a landscape source into portrait output.
18. If conversion fails or `ffmpeg` is missing, it safely falls back to the original recorded container.
19. For capture workflow runs, do not call direct `live-url` as the primary acquisition path, and do not keep a `supportH265=0` first pass. The capture workflow should directly request `protocol=1`, `quality=1`, `supportH265=1`, and `type=1` through `stream-address`.
20. OpenClaw should not manually flip protocol / source / supportH265 knobs at runtime. If capture fails, read the workflow logs; do not fall back to ad-hoc direct live probing.
21. For the current CB60 managed-stream path, do not attempt device-side H264 encode switching in the capture workflow. The fixed steady-state path is `H265 + HLS -> local H264 MP4`.
22. If a manually fetched or automatically fetched FLV URL fails with `502`, `Bad Gateway`, or a port-9188 connectivity error, do not immediately conclude that the server IP is banned. Let `capture-shot` own the retry/fallback path first. If manual intervention is still required, switch to `HLS` rather than stopping at the FLV diagnosis.
23. For capture jobs, `capture-shot` is the only supported recording entrypoint. Do not use `live-url`, handwritten `python -c`, `curl`, or ad-hoc API probes to replace the capture workflow.
24. If `capture-shot` fails, read `capture-log.jsonl`, `capture-report.md`, and `session.json` for diagnosis. Do not manually derive stream URLs or jump to network-ban / CDN-failure conclusions from partial probes.
25. Every workflow capture should append structured logs to `capture-log.jsonl` and refresh `capture-report.md` inside the session folder.
26. After each capture, validate the resulting file. A clip should be accepted once duration reaches at least 10 seconds and resolution remains production-usable. If validation is abnormal or failed, save a failure frame and attach a short failure analysis. If `tesseract` is available locally, include OCR text from the saved frame.
27. After an accepted capture, attach a deferred LAS post-process pipeline to the shot metadata. The fixed order is: `upload_to_tos -> las_highlight_edit -> las_video_inpaint -> las_video_resize`.
28. Until the user provides the required TOS bridge access, keep that LAS pipeline in `pending_config` state. Do not invent TOS credentials or silently upload anything.
29. Enforce wall-clock runtime limits for every `capture-shot`: default `CB60_CAPTURE_WALL_TIMEOUT_SECONDS=180` for capture-only runs and `CB60_CAPTURE_WITH_LAS_WALL_TIMEOUT_SECONDS=5400` when LAS/TOS is configured. If the deadline is hit, stop, write `capture_timed_out`, and report the failure instead of debugging or retrying indefinitely.
30. If a clip is abnormal or failed, mark the LAS pipeline as `skipped_capture_not_accepted` so later orchestration knows not to send bad media into LAS.
31. Keep all captured clips local for now under the session folder. Do not add cloud upload unless the user asks.
32. If the user requests recurring device health polling, use `cb60_status_monitor.py`; default to 60-second intervals unless the user asks for something else.
33. The status monitor should write `samples.jsonl`, `samples.csv`, `events.jsonl`, and `report.md` under its output directory.
34. If the user wants OpenClaw to run a daily capture window, use `cb60_task_manager.py init-task` to create a local task state file instead of inventing a new scheduler format.
32. For first-boot onboarding, prefer `first-boot-setup` so the merchant only needs to answer one thing: the capture time window.
31. After `first-boot-setup`, OpenClaw must immediately read `scheduler-spec` and create the recurring checker automation. Writing `task.json` alone is not enough to make captures happen.
32. The recurring checker must run every 10 minutes, call `battery-precheck` first, then `should-run-now`, and only trigger capture when `should_run_now=true`.
33. If OpenClaw creates that recurring checker successfully, it should call `scheduler-installed` and persist the automation name plus delivery channel into the task file.
34. Merchant-side interaction must stay inside the fixed boundary: modify capture time, start a temporary custom capture window, diagnose capture problems, or stop capture. Reject anything else through `merchant-command`.
35. Before the next scheduled capture window starts, run `battery-precheck`. If battery is below 85%, return a reminder for the merchant to charge the camera.
36. After each finished capture session, call `record-session` so the plugin can build a real daily report with clip counts and upload counts.
37. Use `daily-report` for end-of-day summaries and `diagnose-task` when the merchant says “怎么没有拍摄”.
38. Treat the plugin as recurring-by-default: once the merchant gives one daily time window, the task repeats every day until the merchant explicitly says `龙虾，停止拍摄` or the backend disables the task.
39. Keep the first merchant question fixed to `你希望这个摄像头在什么时候拍？` and do not expand onboarding into a broader survey unless the user explicitly asks.
40. If the merchant says “帮我拍视频” or “从现在开始拍视频”, treat it as a temporary capture request. Ask for two parameters when missing: capture interval and end time. Keep the original recurring daily schedule unchanged.
41. If the orchestrator needs a machine-readable contract for this behavior, use `cb60_task_manager.py workflow-spec` instead of inventing a parallel command grammar.
42. The default LAS edit prompt should prefer dynamic business highlights: serving dishes, kitchen prep, front-desk reception or operation, staff movement/interaction, tableware placement, and should remove static empty shots.
43. The default LAS inpaint step should target visible watermarks and use precise detection by default.
44. The default LAS resize step should output a 2K portrait result when the clip enters the full post-process chain.
45. If the user requests zoom, explain that the current REST control path was rejected by the real CB60 device.
46. If the user requests voice talk, explain that this skill currently stops at the SDK boundary and refer to `references/api-notes.md`.

## API Notes

Read `references/api-notes.md` when you need the official/inferred API boundary, especially before changing endpoint behavior or expanding into voice talk.

For the recommended split between OpenClaw workflow orchestration and a small local capture server, read:

- `references/minimal-server-architecture.md`
