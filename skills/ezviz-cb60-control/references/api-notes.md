# API Notes

## Officially Confirmed From Vendor Docs

- EZVIZ Android `EZOpenSDK` exposes `controlPTZ` and `captureCamera`.
- EZVIZ Android `EZPlayer` exposes `capturePicture`, `startLocalRecord`, and `startVoiceTalk`.
- EZVIZ PC/OpenSDK documents PTZ and defence control.
- OpenClaw plugins can ship skills through `openclaw.plugin.json`.

These sources are enough to justify that the camera family supports:

- Left/right PTZ
- Snapshot capture
- Live preview workflows
- Voice talk through SDK-backed players

The specific CB60 validation run in this workspace confirmed:

- `/api/lapp/device/capture` works and returns `picUrl`
- `/api/lapp/device/ptz/start` + `/api/lapp/device/ptz/stop` works for left/right
- PTZ zoom commands were rejected by the device with `60000: 设备不支持云台控制`
- Live URL lookup complained `source为空`, so the tenant needs an explicit `source` parameter
- The user can manually generate preview URLs in EZOPEN, RTMP, HTTP-FLV, and HLS formats from the platform help flow
- Public device capability metadata still reports only one supported channel
- Probing `channelNo=2` returned `通道不存在`, while snapshots changed after the user switched lenses in the EZVIZ app

## Important Boundary

This skill chooses a REST-first implementation because it is portable and easy to package. Voice talk is intentionally left at the documented boundary because the portable CLI environment here does not ship the official native player SDK or audio pipeline that `startVoiceTalk` depends on.

For production-like use, prefer splitting responsibilities:

- OpenClaw handles task planning, operator prompts, and session orchestration.
- A small capture server handles stream fetch, reconnect, local recording, and asset persistence.

See `references/minimal-server-architecture.md` for the minimal service contract.

The workflow helper is intentionally conservative:

- It plans at most 4 shots per session.
- It stores everything locally under a session folder.
- It assumes the operator will physically move the device when asked.
- It avoids repeated confirmation loops and instead emits the next placement instruction after each captured clip.

## REST Endpoints In This Skill

The controller script uses these endpoint paths:

- `/api/lapp/token/get`
- `/api/lapp/device/ptz/start`
- `/api/lapp/device/ptz/stop`
- `/api/lapp/device/capture`
- `/api/lapp/v2/live/address/get`
- `/api/lapp/live/address/get`

The first four are standard EZVIZ Open Platform patterns. The two live-address paths are handled as a fallback pair because the public doc surface is fragmented and account/product variants can differ. If stream lookup fails in production, validate the exact path against the tenant's current Open Platform documentation before changing the script.

The live URL command accepts `source` explicitly because the real device/account returned `10001: source为空!` without it.

The skill also accepts a manual preview URL through `EZVIZ_MANUAL_LIVE_URL`. This is useful when the platform console can generate a working address faster than the API can be debugged.

## Playback Caveats

- EZOPEN playback may require embedding the device verify code when video encryption is enabled.
- URLs with `supportH265=1` require an H.265-capable player or SDK.
- Signed RTMP/HLS/HTTP-FLV URLs expire and must be refreshed after their expiry time.

## Dual-Lens Validation Strategy

The product marketing page indicates CB60 is a dual-lens device, but the public API evidence so far still looks like a single logical channel.

Use this validation order:

1. Probe `channelNo=1` and `channelNo=2` with `probe-channels`.
2. If `channelNo=2` returns a valid snapshot or live URL, compare it with channel 1 output.
3. If `channelNo=2` consistently fails while `channelNo=1` works, assume lens switching is handled inside the app/player UI and is not exposed as a separate public channel.
4. If the user changes lenses in the app and the `channelNo=1` snapshots change accordingly, treat the public API as a view of the current app-selected lens state.

## CB60 Device-Specific Notes

- Safe to expose in the skill:
  - PTZ left/right supported
  - PTZ up/down not supported
  - Duplex talk supported at device level
- Runtime behavior confirmed:
  - The public stream/snapshot follows the lens currently selected in the EZVIZ app
  - The plugin cannot directly switch between wide and 3x lenses
- Supported by device metadata but not by the current REST control path:
  - Zoom/focal adjustment
- Do not hardcode any device serial, validate code, app key, app secret, or access token into the skill.
