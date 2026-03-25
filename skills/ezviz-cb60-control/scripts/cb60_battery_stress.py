#!/usr/bin/env python3
"""Battery stress test for keeping a CB60 live stream active."""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from ezviz_cb60_control import EnvConfig, EzvizClient, EzvizError

JsonDict = Dict[str, Any]


def fetch_bytes(url: str, timeout: float) -> bytes:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return response.read()


def append_jsonl(path: Path, payload: JsonDict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


def append_csv(path: Path, row: Dict[str, Any], fieldnames: Sequence[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    exists = path.exists()
    with path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        if not exists:
            writer.writeheader()
        writer.writerow(row)


def compute_hourly_drain(samples: Sequence[JsonDict]) -> Optional[float]:
    valid = [item for item in samples if isinstance(item.get("battery_percent"), (int, float))]
    if len(valid) < 2:
        return None
    first = valid[0]
    last = valid[-1]
    elapsed_hours = (last["timestamp"] - first["timestamp"]) / 3600.0
    if elapsed_hours <= 0:
        return None
    drain = float(first["battery_percent"]) - float(last["battery_percent"])
    return drain / elapsed_hours


def estimate_remaining_hours(samples: Sequence[JsonDict]) -> Optional[float]:
    valid = [item for item in samples if isinstance(item.get("battery_percent"), (int, float))]
    if not valid:
        return None
    hourly = compute_hourly_drain(valid)
    if hourly is None or hourly <= 0:
        return None
    current = float(valid[-1]["battery_percent"])
    return current / hourly


def render_report(
    path: Path,
    *,
    config: EnvConfig,
    samples: Sequence[JsonDict],
    started_at: float,
    stream_url: str,
    last_stream_stats: Optional[JsonDict],
    last_error: Optional[str] = None,
    recovery_state: Optional[JsonDict] = None,
) -> None:
    hourly = compute_hourly_drain(samples)
    remaining = estimate_remaining_hours(samples)
    current_battery = samples[-1]["battery_percent"] if samples else None
    lines = [
        "# CB60 Battery Stress Report",
        "",
        f"- Started at: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(started_at))}",
        f"- Device serial: {config.device_serial}",
        f"- Channel: {config.channel_no}",
        f"- Stream mode: always-on HLS keepalive",
        f"- Stream URL source: {'manual' if config.manual_live_url else 'api/live-url or override'}",
        f"- Current battery: {current_battery if current_battery is not None else 'unknown'}",
        f"- Samples collected: {len(samples)}",
        f"- Estimated hourly drain: {round(hourly, 2)}%" if hourly is not None else "- Estimated hourly drain: not enough data yet",
        f"- Estimated remaining hours: {round(remaining, 2)}h" if remaining is not None else "- Estimated remaining hours: not enough data yet",
        f"- Last error: {last_error}" if last_error else "- Last error: none",
        f"- Last recovery: {json.dumps(recovery_state, ensure_ascii=False)}" if recovery_state else "- Last recovery: none",
        "",
        "## Latest Stream Stats",
        "",
    ]

    if last_stream_stats:
        for key, value in last_stream_stats.items():
            lines.append(f"- {key}: {value}")
    else:
        lines.append("- No stream stats yet")

    lines.extend(
        [
            "",
            "## Latest Samples",
            "",
            "| Time | Battery | Signal | Cloud | Privacy |",
            "| --- | ---: | ---: | ---: | ---: |",
        ]
    )

    for sample in samples[-10:]:
        lines.append(
            "| {time} | {battery} | {signal} | {cloud} | {privacy} |".format(
                time=time.strftime("%m-%d %H:%M:%S", time.localtime(sample["timestamp"])),
                battery=sample.get("battery_percent", ""),
                signal=sample.get("signal", ""),
                cloud=sample.get("cloudStatus", ""),
                privacy=sample.get("privacyStatus", ""),
            )
        )

    lines.extend(
        [
            "",
            "## Stream URL",
            "",
            stream_url,
            "",
            "This report is updated as the stress test runs.",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def diagnose_stream_failure(client: EzvizClient, channel_no: Optional[int] = None) -> JsonDict:
    try:
        dump = client.dump_device(channel_no=channel_no)
        info = dump.get("device_info", {})
        status = dump.get("device_status", {})
        battery = dump.get("battery", {})
        return {
            "ok": True,
            "device_online": info.get("status"),
            "defence": info.get("defence"),
            "battery_percent": battery.get("battery_percent"),
            "signal": status.get("signal", info.get("signal")),
            "cloudStatus": status.get("cloudStatus"),
            "privacyStatus": status.get("privacyStatus"),
            "raw": dump,
        }
    except Exception as exc:  # pragma: no cover - runtime path
        return {
            "ok": False,
            "error": str(exc),
        }


def refresh_stream_url(
    client: EzvizClient,
    *,
    source: Optional[str] = None,
    protocol_id: Optional[int] = None,
) -> JsonDict:
    live = client.get_live_url(source=source, protocol_id=protocol_id)
    return {
        "ok": True,
        "stream_url": live["stream_url"],
        "path": live.get("path"),
    }


def keep_stream_alive_once(
    playlist_url: str,
    timeout_seconds: float,
    segment_limit: int = 3,
    fetcher: Callable[[str, float], bytes] = fetch_bytes,
) -> JsonDict:
    playlist = fetcher(playlist_url, timeout_seconds).decode("utf-8", errors="replace")
    lines = [line.strip() for line in playlist.splitlines() if line.strip()]
    segments: List[str] = []
    for line in lines:
        if line.startswith("#"):
            continue
        segments.append(urllib.parse.urljoin(playlist_url, line))
        if len(segments) >= segment_limit:
            break

    downloaded = 0
    total_bytes = len(playlist.encode("utf-8"))
    for segment_url in segments:
        blob = fetcher(segment_url, timeout_seconds)
        total_bytes += len(blob)
        downloaded += 1

    return {
        "playlist_bytes": len(playlist.encode("utf-8")),
        "segments_downloaded": downloaded,
        "bytes_downloaded": total_bytes,
    }


def sample_status(client: EzvizClient) -> JsonDict:
    dump = client.dump_device()
    info = dump["device_info"]
    status = dump["device_status"]
    battery = dump["battery"]
    return {
        "timestamp": time.time(),
        "battery_percent": battery.get("battery_percent"),
        "signal": status.get("signal", info.get("signal")),
        "cloudStatus": status.get("cloudStatus"),
        "privacyStatus": status.get("privacyStatus"),
        "raw": dump,
    }


@dataclass
class StressArtifacts:
    root: Path
    report_path: Path
    samples_jsonl: Path
    samples_csv: Path
    events_jsonl: Path

    @classmethod
    def create(cls, root: Path) -> "StressArtifacts":
        root.mkdir(parents=True, exist_ok=True)
        return cls(
            root=root,
            report_path=root / "report.md",
            samples_jsonl=root / "samples.jsonl",
            samples_csv=root / "samples.csv",
            events_jsonl=root / "events.jsonl",
        )


def run_stress_test(
    *,
    config: EnvConfig,
    artifacts: StressArtifacts,
    stream_url: str,
    sample_interval_seconds: int,
    keepalive_interval_seconds: int,
    channel_no: Optional[int] = None,
    max_hours: Optional[float] = None,
    fetcher: Callable[[str, float], bytes] = fetch_bytes,
    source: Optional[str] = None,
    protocol_id: Optional[int] = None,
) -> JsonDict:
    client = EzvizClient(config)
    current_stream_url = stream_url
    started_at = time.time()
    next_sample_at = started_at
    next_keepalive_at = started_at
    samples: List[JsonDict] = []
    last_stream_stats: Optional[JsonDict] = None
    last_error: Optional[str] = None
    last_recovery: Optional[JsonDict] = None
    csv_fields = ["timestamp", "battery_percent", "signal", "cloudStatus", "privacyStatus"]

    while True:
        now = time.time()
        if max_hours is not None and now - started_at >= max_hours * 3600:
            break

        if now >= next_keepalive_at:
            try:
                last_stream_stats = keep_stream_alive_once(
                    playlist_url=current_stream_url,
                    timeout_seconds=config.timeout_seconds,
                    fetcher=fetcher,
                )
                append_jsonl(
                    artifacts.events_jsonl,
                    {
                        "timestamp": now,
                        "event": "keepalive",
                        "stream": last_stream_stats,
                    },
                )
                last_error = None
            except Exception as exc:  # pragma: no cover - network/runtime path
                last_error = f"keepalive failed: {exc}"
                diagnosis = diagnose_stream_failure(client, channel_no=channel_no)
                recovery: JsonDict = {
                    "reason": "keepalive_timeout",
                    "diagnosis": diagnosis,
                }
                if diagnosis.get("ok"):
                    try:
                        refreshed = refresh_stream_url(client, source=source, protocol_id=protocol_id)
                        current_stream_url = refreshed["stream_url"]
                        recovery["refresh"] = refreshed
                        recovery["action"] = "stream_refreshed"
                        last_recovery = recovery
                        append_jsonl(
                            artifacts.events_jsonl,
                            {
                                "timestamp": now,
                                "event": "keepalive_recovered",
                                "recovery": recovery,
                            },
                        )
                        next_keepalive_at = now + keepalive_interval_seconds
                        continue
                    except Exception as refresh_exc:  # pragma: no cover - runtime path
                        recovery["refresh"] = {"ok": False, "error": str(refresh_exc)}
                recovery["action"] = "stopped"
                last_recovery = recovery
                append_jsonl(
                    artifacts.events_jsonl,
                    {
                        "timestamp": now,
                        "event": "keepalive_error",
                        "error": last_error,
                        "recovery": recovery,
                    },
                )
                break
            next_keepalive_at = now + keepalive_interval_seconds

        if now >= next_sample_at:
            try:
                sample = sample_status(client)
                samples.append(sample)
                append_jsonl(artifacts.samples_jsonl, sample)
                append_csv(
                    artifacts.samples_csv,
                    {
                        "timestamp": sample["timestamp"],
                        "battery_percent": sample.get("battery_percent"),
                        "signal": sample.get("signal"),
                        "cloudStatus": sample.get("cloudStatus"),
                        "privacyStatus": sample.get("privacyStatus"),
                    },
                    csv_fields,
                )
                last_error = None
            except Exception as exc:  # pragma: no cover - network/runtime path
                last_error = f"sample failed: {exc}"
                append_jsonl(
                    artifacts.events_jsonl,
                    {
                        "timestamp": now,
                        "event": "sample_error",
                        "error": last_error,
                    },
                )
                break
            render_report(
                artifacts.report_path,
                config=config,
                samples=samples,
                started_at=started_at,
                stream_url=current_stream_url,
                last_stream_stats=last_stream_stats,
                last_error=last_error,
                recovery_state=last_recovery,
            )
            battery = sample.get("battery_percent")
            if isinstance(battery, (int, float)) and battery <= 0:
                break
            next_sample_at = now + sample_interval_seconds

        time.sleep(1.0)

    render_report(
        artifacts.report_path,
        config=config,
        samples=samples,
        started_at=started_at,
        stream_url=current_stream_url,
        last_stream_stats=last_stream_stats,
        last_error=last_error,
        recovery_state=last_recovery,
    )
    return {
        "report_path": str(artifacts.report_path),
        "samples_path": str(artifacts.samples_jsonl),
        "csv_path": str(artifacts.samples_csv),
        "events_path": str(artifacts.events_jsonl),
        "sample_count": len(samples),
        "estimated_hourly_drain": compute_hourly_drain(samples),
        "estimated_remaining_hours": estimate_remaining_hours(samples),
        "last_error": last_error,
        "last_recovery": last_recovery,
        "final_stream_url": current_stream_url,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run an overnight CB60 battery stress test with always-on stream keepalive.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="Run the stress test loop.")
    run_parser.add_argument("--stream-url", required=True, help="HLS stream URL to keep alive.")
    run_parser.add_argument("--output-root", type=Path, default=Path("./artifacts/battery-stress"), help="Directory for logs and report.")
    run_parser.add_argument("--sample-interval-seconds", type=int, default=1800, help="How often to sample battery and device status.")
    run_parser.add_argument("--keepalive-interval-seconds", type=int, default=20, help="How often to refresh playlist and download a few segments.")
    run_parser.add_argument("--source", help="Optional live source parameter for automatic stream refresh.")
    run_parser.add_argument("--protocol-id", type=int, help="Optional numeric protocol value for automatic stream refresh.")
    run_parser.add_argument("--max-hours", type=float, help="Optional stop limit for testing.")

    return parser


def emit_json(payload: JsonDict) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "run":
        try:
            artifacts = StressArtifacts.create(args.output_root)
            payload = run_stress_test(
                config=EnvConfig.from_env(),
                artifacts=artifacts,
                stream_url=args.stream_url,
                sample_interval_seconds=args.sample_interval_seconds,
                keepalive_interval_seconds=args.keepalive_interval_seconds,
                max_hours=args.max_hours,
                source=args.source,
                protocol_id=args.protocol_id,
            )
        except EzvizError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        emit_json(payload)
        return 0

    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
