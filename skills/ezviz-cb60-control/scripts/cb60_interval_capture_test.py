#!/usr/bin/env python3
"""Periodic local recording stability test for EZVIZ CB60."""

from __future__ import annotations

import argparse
import csv
import json
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from cb60_capture_workflow import (
    RotationMode,
    analyze_failure_frame,
    build_raw_recording_path,
    record_stream_clip,
    resolve_stream_url,
    transcode_recording_to_mp4,
)
from ezviz_cb60_control import EnvConfig, EzvizError, extract_env_file_arg

JsonDict = Dict[str, Any]


@dataclass
class IntervalArtifacts:
    root: Path
    clips_dir: Path
    results_csv: Path
    summary_json: Path
    report_md: Path

    @classmethod
    def create(cls, root: Path) -> "IntervalArtifacts":
        clips_dir = root / "clips"
        clips_dir.mkdir(parents=True, exist_ok=True)
        return cls(
            root=root,
            clips_dir=clips_dir,
            results_csv=root / "results.csv",
            summary_json=root / "summary.json",
            report_md=root / "report.md",
        )


def ffprobe_json(path: Path) -> JsonDict:
    completed = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "stream=codec_type,width,height,codec_name:format=duration,size",
            "-of",
            "json",
            str(path),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        return {"error": completed.stderr.strip() or completed.stdout.strip()}
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError:
        return {"error": "ffprobe returned invalid JSON", "raw": completed.stdout}


def probe_metrics(probe: JsonDict) -> JsonDict:
    fmt = probe.get("format", {}) if isinstance(probe, dict) else {}
    streams = probe.get("streams", []) if isinstance(probe, dict) else []
    video = next((stream for stream in streams if stream.get("codec_type") == "video"), {})
    audio = next((stream for stream in streams if stream.get("codec_type") == "audio"), {})
    return {
        "duration": float(fmt.get("duration") or 0.0),
        "size": int(float(fmt.get("size") or 0.0)),
        "width": int(video.get("width") or 0),
        "height": int(video.get("height") or 0),
        "video_codec": video.get("codec_name") or "",
        "audio_codec": audio.get("codec_name") or "",
    }


def classify_clip(metrics: JsonDict, target_duration: int) -> str:
    duration_ok = float(metrics.get("duration") or 0.0) >= max(target_duration - 2, target_duration * 0.8)
    portrait_ok = int(metrics.get("height") or 0) > int(metrics.get("width") or 0)
    resolution_ok = int(metrics.get("height") or 0) >= 1000
    return "accepted" if duration_ok and portrait_ok and resolution_ok else "abnormal"


def append_csv(path: Path, row: JsonDict, fieldnames: Sequence[str]) -> None:
    exists = path.exists()
    with path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        if not exists:
            writer.writeheader()
        writer.writerow(row)


def render_summary(artifacts: IntervalArtifacts, rows: Sequence[JsonDict]) -> JsonDict:
    summary = {
        "root": str(artifacts.root),
        "round_count": len(rows),
        "accepted_count": sum(1 for row in rows if row["status"] == "accepted"),
        "abnormal_count": sum(1 for row in rows if row["status"] == "abnormal"),
        "failed_count": sum(1 for row in rows if row["status"] not in ("accepted", "abnormal")),
        "rounds": rows,
    }
    artifacts.summary_json.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def render_report(
    artifacts: IntervalArtifacts,
    *,
    rows: Sequence[JsonDict],
    clip_duration_seconds: int,
    interval_seconds: int,
) -> None:
    accepted = [row for row in rows if row["status"] == "accepted"]
    abnormal = [row for row in rows if row["status"] == "abnormal"]
    failed = [row for row in rows if row["status"] not in ("accepted", "abnormal")]
    avg_size = round(sum(int(row.get("size") or 0) for row in accepted) / len(accepted)) if accepted else 0
    lines = [
        "# CB60 Interval Capture Report",
        "",
        f"- Output root: {artifacts.root}",
        f"- Target interval: {interval_seconds}s",
        f"- Target clip duration: {clip_duration_seconds}s",
        f"- Rounds completed: {len(rows)}",
        f"- Accepted: {len(accepted)}",
        f"- Abnormal: {len(abnormal)}",
        f"- Failed: {len(failed)}",
        f"- Average accepted MP4 size: {avg_size} bytes" if accepted else "- Average accepted MP4 size: n/a",
        "",
        "## Latest Rounds",
        "",
        "| Round | Time | Status | Duration | Resolution | MP4 | Note |",
        "| --- | --- | --- | ---: | --- | --- | --- |",
    ]
    for row in rows[-15:]:
        lines.append(
            "| {round} | {started_at} | {status} | {duration:.3f} | {width}x{height} | {mp4} | {note} |".format(
                round=row.get("round", ""),
                started_at=row.get("started_at", ""),
                status=row.get("status", ""),
                duration=float(row.get("duration") or 0.0),
                width=row.get("width", 0),
                height=row.get("height", 0),
                mp4=Path(str(row.get("mp4_output_path") or "")).name,
                note=row.get("note", ""),
            )
        )

    if abnormal or failed:
        lines.extend(["", "## Review Needed", ""])
        for row in abnormal + failed:
            lines.append(
                "- Round {round}: {status}; frame={frame}; note={note}".format(
                    round=row.get("round", ""),
                    status=row.get("status", ""),
                    frame=row.get("frame_path", ""),
                    note=row.get("note", ""),
                )
            )

    artifacts.report_md.write_text("\n".join(lines), encoding="utf-8")


def extract_frame(input_path: Path, output_path: Path, second: float = 1.0) -> Optional[Path]:
    completed = subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-ss",
            str(second),
            "-i",
            str(input_path),
            "-frames:v",
            "1",
            str(output_path),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0 or not output_path.exists():
        return None
    return output_path


def run_interval_capture_test(
    *,
    config: EnvConfig,
    artifacts: IntervalArtifacts,
    rounds: int,
    clip_duration_seconds: int,
    interval_seconds: int,
    rotation_mode: RotationMode = "cw90",
) -> JsonDict:
    rows: List[JsonDict] = []
    fieldnames = [
        "round",
        "started_at",
        "stream_url",
        "raw_output_path",
        "mp4_output_path",
        "duration",
        "size",
        "width",
        "height",
        "video_codec",
        "audio_codec",
        "frame_path",
        "text_analysis",
        "status",
        "note",
    ]

    for round_index in range(1, rounds + 1):
        round_started = time.time()
        started_at = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(round_started))
        row: JsonDict = {
            "round": round_index,
            "started_at": started_at,
            "stream_url": "",
            "raw_output_path": "",
            "mp4_output_path": "",
            "duration": 0.0,
            "size": 0,
            "width": 0,
            "height": 0,
            "video_codec": "",
            "audio_codec": "",
            "frame_path": "",
            "text_analysis": "",
            "status": "failed",
            "note": "",
        }

        try:
            stream_url = resolve_stream_url(config)
            row["stream_url"] = stream_url
            raw_output_path = build_raw_recording_path(
                artifacts.clips_dir / f"round-{round_index:02d}.ts",
                stream_url,
            )
            result = record_stream_clip(
                stream_url=stream_url,
                output_path=raw_output_path,
                target_duration=float(clip_duration_seconds),
                timeout_seconds=config.timeout_seconds,
            )
            row["raw_output_path"] = result["output_path"]

            conversion = transcode_recording_to_mp4(Path(result["output_path"]), rotation_mode=rotation_mode)
            if not conversion.get("ok"):
                row["status"] = "convert_failed"
                row["note"] = str(conversion.get("reason") or "mp4 conversion failed")
            else:
                mp4_path = Path(str(conversion["output_path"]))
                row["mp4_output_path"] = str(mp4_path)
                metrics = probe_metrics(ffprobe_json(mp4_path))
                row.update(metrics)
                row["status"] = classify_clip(metrics, clip_duration_seconds)
                row["note"] = "" if row["status"] == "accepted" else "abnormal clip"
                if row["status"] != "accepted":
                    frame_path = extract_frame(mp4_path, artifacts.clips_dir / f"round-{round_index:02d}-frame.jpg")
                    if frame_path:
                        row["frame_path"] = str(frame_path)
                        row["text_analysis"] = analyze_failure_frame(frame_path, metrics)
        except EzvizError as exc:
            row["status"] = "record_failed"
            row["note"] = str(exc)

        rows.append(row)
        append_csv(artifacts.results_csv, row, fieldnames)
        render_report(
            artifacts,
            rows=rows,
            clip_duration_seconds=clip_duration_seconds,
            interval_seconds=interval_seconds,
        )

        if round_index < rounds:
            elapsed = time.time() - round_started
            sleep_seconds = max(0.0, float(interval_seconds) - elapsed)
            time.sleep(sleep_seconds)

    summary = render_summary(artifacts, rows)
    render_report(
        artifacts,
        rows=rows,
        clip_duration_seconds=clip_duration_seconds,
        interval_seconds=interval_seconds,
    )
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a periodic local MP4 stability test for CB60.")
    parser.add_argument("--output-root", type=Path, required=True, help="Root folder for results and clips.")
    parser.add_argument("--rounds", type=int, default=10, help="How many rounds to capture.")
    parser.add_argument("--clip-duration-seconds", type=int, default=20, help="Per-round recording duration.")
    parser.add_argument("--interval-seconds", type=int, default=60, help="Seconds between round starts.")
    parser.add_argument(
        "--rotation",
        default="cw90",
        choices=["cw90", "ccw90", "flip180", "none"],
        help="Rotation mode for MP4 output.",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    try:
        normalized_argv, env_file = extract_env_file_arg(argv)
    except EzvizError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    parser = build_parser()
    args = parser.parse_args(normalized_argv)
    artifacts = IntervalArtifacts.create(args.output_root)
    summary = run_interval_capture_test(
        config=EnvConfig.from_env(env_file=env_file),
        artifacts=artifacts,
        rounds=max(args.rounds, 1),
        clip_duration_seconds=max(args.clip_duration_seconds, 1),
        interval_seconds=max(args.interval_seconds, 1),
        rotation_mode=args.rotation,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
