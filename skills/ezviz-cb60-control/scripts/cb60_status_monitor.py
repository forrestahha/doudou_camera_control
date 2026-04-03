#!/usr/bin/env python3
"""Lightweight CB60 device status monitor."""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from ezviz_cb60_control import EnvConfig, EzvizClient, EzvizError, extract_env_file_arg

JsonDict = Dict[str, Any]


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


def normalize_status_dump(dump: JsonDict) -> JsonDict:
    info = dump.get("device_info", {})
    status = dump.get("device_status", {})
    battery = dump.get("battery", {})
    return {
        "timestamp": time.time(),
        "device_online": info.get("status"),
        "defence": info.get("defence"),
        "battery_percent": battery.get("battery_percent"),
        "signal": status.get("signal", info.get("signal")),
        "cloudStatus": status.get("cloudStatus"),
        "privacyStatus": status.get("privacyStatus"),
        "netType": info.get("netType"),
        "raw": dump,
    }


def render_report(
    path: Path,
    *,
    config: EnvConfig,
    interval_seconds: int,
    started_at: float,
    samples: Sequence[JsonDict],
    last_error: Optional[str],
    consecutive_errors: int = 0,
) -> None:
    latest = samples[-1] if samples else {}
    lines = [
        "# CB60 Status Monitor Report",
        "",
        f"- Started at: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(started_at))}",
        f"- Device serial: {config.device_serial}",
        f"- Poll interval: {interval_seconds}s",
        f"- Samples collected: {len(samples)}",
        f"- Last error: {last_error}" if last_error else "- Last error: none",
        f"- Consecutive errors: {consecutive_errors}",
        "",
        "## Latest Status",
        "",
        f"- Device online: {latest.get('device_online', 'unknown')}",
        f"- Defence: {latest.get('defence', 'unknown')}",
        f"- Battery: {latest.get('battery_percent', 'unknown')}",
        f"- Signal: {latest.get('signal', 'unknown')}",
        f"- Cloud status: {latest.get('cloudStatus', 'unknown')}",
        f"- Privacy status: {latest.get('privacyStatus', 'unknown')}",
        f"- Network type: {latest.get('netType', 'unknown')}",
        "",
        "## Recent Samples",
        "",
        "| Time | Online | Battery | Signal | Cloud | Privacy |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for sample in samples[-10:]:
        lines.append(
            "| {time} | {online} | {battery} | {signal} | {cloud} | {privacy} |".format(
                time=time.strftime("%m-%d %H:%M:%S", time.localtime(sample["timestamp"])),
                online=sample.get("device_online", ""),
                battery=sample.get("battery_percent", ""),
                signal=sample.get("signal", ""),
                cloud=sample.get("cloudStatus", ""),
                privacy=sample.get("privacyStatus", ""),
            )
        )
    path.write_text("\n".join(lines), encoding="utf-8")


@dataclass
class MonitorArtifacts:
    root: Path
    report_path: Path
    samples_jsonl: Path
    samples_csv: Path
    events_jsonl: Path

    @classmethod
    def create(cls, root: Path) -> "MonitorArtifacts":
        root.mkdir(parents=True, exist_ok=True)
        return cls(
            root=root,
            report_path=root / "report.md",
            samples_jsonl=root / "samples.jsonl",
            samples_csv=root / "samples.csv",
            events_jsonl=root / "events.jsonl",
        )


def run_monitor(
    *,
    config: EnvConfig,
    artifacts: MonitorArtifacts,
    interval_seconds: int,
    max_rounds: Optional[int] = None,
    max_hours: Optional[float] = None,
    max_consecutive_errors: int = 5,
) -> JsonDict:
    client = EzvizClient(config)
    started_at = time.time()
    samples: List[JsonDict] = []
    last_error: Optional[str] = None
    fieldnames = [
        "timestamp",
        "device_online",
        "defence",
        "battery_percent",
        "signal",
        "cloudStatus",
        "privacyStatus",
        "netType",
    ]

    round_index = 0
    consecutive_errors = 0
    while True:
        now = time.time()
        if max_hours is not None and now - started_at >= max_hours * 3600:
            break
        if max_rounds is not None and round_index >= max_rounds:
            break

        try:
            sample = normalize_status_dump(client.dump_device())
            samples.append(sample)
            append_jsonl(artifacts.samples_jsonl, sample)
            append_csv(
                artifacts.samples_csv,
                {key: sample.get(key) for key in fieldnames},
                fieldnames,
            )
            append_jsonl(
                artifacts.events_jsonl,
                {
                    "timestamp": sample["timestamp"],
                    "event": "status_sample",
                    "round": round_index + 1,
                    "battery_percent": sample.get("battery_percent"),
                    "device_online": sample.get("device_online"),
                    "signal": sample.get("signal"),
                },
            )
            last_error = None
            consecutive_errors = 0
        except Exception as exc:  # pragma: no cover - runtime path
            last_error = str(exc)
            consecutive_errors += 1
            append_jsonl(
                artifacts.events_jsonl,
                {
                    "timestamp": now,
                    "event": "status_error",
                    "round": round_index + 1,
                    "error": last_error,
                    "consecutive_errors": consecutive_errors,
                },
            )
            render_report(
                artifacts.report_path,
                config=config,
                interval_seconds=interval_seconds,
                started_at=started_at,
                samples=samples,
                last_error=last_error,
                consecutive_errors=consecutive_errors,
            )
            if consecutive_errors >= max_consecutive_errors:
                break
            round_index += 1
            time.sleep(interval_seconds)
            continue

        render_report(
            artifacts.report_path,
            config=config,
            interval_seconds=interval_seconds,
            started_at=started_at,
            samples=samples,
            last_error=last_error,
            consecutive_errors=consecutive_errors,
        )
        round_index += 1
        time.sleep(interval_seconds)

    render_report(
        artifacts.report_path,
        config=config,
        interval_seconds=interval_seconds,
        started_at=started_at,
        samples=samples,
        last_error=last_error,
        consecutive_errors=consecutive_errors,
    )
    return {
        "report_path": str(artifacts.report_path),
        "samples_path": str(artifacts.samples_jsonl),
        "csv_path": str(artifacts.samples_csv),
        "events_path": str(artifacts.events_jsonl),
        "sample_count": len(samples),
        "last_error": last_error,
        "consecutive_errors": consecutive_errors,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Poll EZVIZ CB60 device status on a fixed interval.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="Run the status monitor loop.")
    run_parser.add_argument("--output-root", type=Path, default=Path("./artifacts/status-monitor"), help="Directory for monitor logs and reports.")
    run_parser.add_argument("--interval-seconds", type=int, default=60, help="How often to poll device status.")
    run_parser.add_argument("--max-rounds", type=int, help="Optional number of samples before stopping.")
    run_parser.add_argument("--max-hours", type=float, help="Optional stop limit in hours.")
    run_parser.add_argument("--max-consecutive-errors", type=int, default=5, help="How many consecutive poll errors to tolerate before stopping.")
    return parser


def emit_json(payload: JsonDict) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


def main(argv: Optional[Sequence[str]] = None) -> int:
    try:
        normalized_argv, env_file = extract_env_file_arg(argv)
    except EzvizError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    parser = build_parser()
    args = parser.parse_args(normalized_argv)

    if args.command == "run":
        try:
            payload = run_monitor(
                config=EnvConfig.from_env(env_file=env_file),
                artifacts=MonitorArtifacts.create(args.output_root),
                interval_seconds=args.interval_seconds,
                max_rounds=args.max_rounds,
                max_hours=args.max_hours,
                max_consecutive_errors=args.max_consecutive_errors,
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
