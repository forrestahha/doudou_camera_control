#!/usr/bin/env python3
"""Shot-planning and local capture workflow for EZVIZ CB60 sessions."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from ezviz_cb60_control import EnvConfig, EzvizClient, EzvizError, extract_env_file_arg

JsonDict = Dict[str, Any]
RotationMode = str
DEFAULT_LIVE_PROTOCOL_ID = 4
DEFAULT_LIVE_QUALITY = 1
DEFAULT_LIVE_SUPPORT_H265 = 1
DEFAULT_LIVE_MUTE = 0
DEFAULT_LIVE_ADDRESS_TYPE = 1
DEFAULT_LOG_NAME = "capture-log.jsonl"
DEFAULT_REPORT_NAME = "capture-report.md"


ZONE_ORDER = {
    "entrance": 10,
    "window": 20,
    "counter": 30,
    "product": 40,
    "seating": 50,
    "custom": 90,
}


@dataclass(frozen=True)
class ShotTemplate:
    shot_id: str
    label: str
    zone: str
    keywords: Tuple[str, ...]
    framing: str
    placement: str
    operator_message: str
    duration_seconds: int


SHOT_TEMPLATES: Tuple[ShotTemplate, ...] = (
    ShotTemplate(
        shot_id="storefront",
        label="门头外景",
        zone="entrance",
        keywords=("门头", "店门", "招牌", "外景", "入口"),
        framing="让门头和进店路径同时进入画面，保留一点两侧环境。",
        placement="把设备放在店外正前方 2 到 4 米，高度接近胸口，略微朝下。",
        operator_message="请把我放到店外正前方，保证门头完整可见，然后开始录制。",
        duration_seconds=15,
    ),
    ShotTemplate(
        shot_id="interior-wide",
        label="店内全景",
        zone="entrance",
        keywords=("全景", "店内", "环境", "大厅", "空间"),
        framing="优先覆盖主通道、核心陈列和顾客活动区。",
        placement="把设备放在进门后 1 到 2 米的位置，朝向店内主体空间。",
        operator_message="请把我放在进门后朝向店内的稳定位置，让主要经营区域都入镜。",
        duration_seconds=15,
    ),
    ShotTemplate(
        shot_id="counter",
        label="收银台或操作台",
        zone="counter",
        keywords=("收银", "前台", "吧台", "操作台", "柜台"),
        framing="突出操作区和交互动作，避免过多无关背景。",
        placement="把设备放在收银台斜前方 1 到 2 米，避免正对强光。",
        operator_message="请把我移到收银台或操作台斜前方，让操作动作清楚可见。",
        duration_seconds=15,
    ),
    ShotTemplate(
        shot_id="process",
        label="制作过程",
        zone="counter",
        keywords=("制作", "出餐", "制作区", "操作", "加工", "烹饪", "冲泡"),
        framing="主体动作保持在画面中间，不要被设备或手臂大面积遮挡。",
        placement="把设备放在制作区侧前方，离主体 1 米左右，稍微俯拍。",
        operator_message="请把我放在制作区侧前方，保证关键制作动作稳定入镜。",
        duration_seconds=20,
    ),
    ShotTemplate(
        shot_id="product-closeup",
        label="商品近景",
        zone="product",
        keywords=("商品", "特写", "菜品", "爆品", "陈列", "细节", "近景"),
        framing="单个重点商品占据画面主体，背景尽量干净。",
        placement="把设备放在商品前方 0.5 到 1 米，保持画面稳定，避免逆光。",
        operator_message="请把我放在目标商品正前方近距离位置，突出商品主体。",
        duration_seconds=12,
    ),
    ShotTemplate(
        shot_id="seating",
        label="就餐区或客区",
        zone="seating",
        keywords=("就餐区", "座位", "客区", "餐桌", "休息区"),
        framing="展示座位布局和环境氛围，避免只拍到空白墙面。",
        placement="把设备放在客区边缘，朝向主要座位排布方向。",
        operator_message="请把我放在客区边缘，朝向主要座位区域，然后开始录制。",
        duration_seconds=15,
    ),
)


def split_brief(brief: str) -> List[str]:
    parts = [item.strip() for item in re.split(r"[,\n，；;。]+", brief) if item.strip()]
    return parts


def score_template(part: str, template: ShotTemplate) -> int:
    return sum(1 for keyword in template.keywords if keyword in part)


def build_custom_shot(part: str, custom_index: int) -> JsonDict:
    return {
        "shot_id": f"custom-{custom_index}",
        "label": part[:24],
        "zone": "custom",
        "request_text": part,
        "framing": "请让你想强调的主体占据画面中心，保证稳定和清晰。",
        "placement": "请把设备放在能清楚看到目标主体的位置，避免遮挡和强逆光。",
        "operator_message": f"请把我放到适合拍摄“{part}”的位置，然后开始录制。",
        "duration_seconds": 15,
    }


def plan_shots(brief: str, max_shots: int = 4) -> List[JsonDict]:
    planned: List[Tuple[int, JsonDict]] = []
    seen_template_ids = set()
    custom_index = 1

    for order, part in enumerate(split_brief(brief)):
        best_template = None
        best_score = 0
        for template in SHOT_TEMPLATES:
            score = score_template(part, template)
            if score > best_score:
                best_template = template
                best_score = score

        if best_template and best_template.shot_id not in seen_template_ids:
            seen_template_ids.add(best_template.shot_id)
            planned.append(
                (
                    order,
                    {
                        "shot_id": best_template.shot_id,
                        "label": best_template.label,
                        "zone": best_template.zone,
                        "request_text": part,
                        "framing": best_template.framing,
                        "placement": best_template.placement,
                        "operator_message": best_template.operator_message,
                        "duration_seconds": best_template.duration_seconds,
                    },
                )
            )
        elif not best_template:
            planned.append((order, build_custom_shot(part, custom_index)))
            custom_index += 1

    if not planned:
        planned = [
            (
                0,
                {
                    "shot_id": "interior-wide",
                    "label": "默认全景",
                    "zone": "entrance",
                    "request_text": "默认全景",
                    "framing": "让主要经营区域尽量完整入镜。",
                    "placement": "请把设备放在能看到主要经营区域的位置。",
                    "operator_message": "请把我放在能看到主要经营区域的位置，然后开始录制。",
                    "duration_seconds": 15,
                },
            )
        ]

    planned.sort(key=lambda item: (ZONE_ORDER.get(item[1]["zone"], 999), item[0]))
    shots = [item[1] for item in planned[:max_shots]]

    for index, shot in enumerate(shots, start=1):
        shot["index"] = index
        shot["status"] = "pending"
    return shots


def session_summary(session: JsonDict) -> JsonDict:
    completed = sum(1 for shot in session["shots"] if shot["status"] == "captured")
    pending = [shot for shot in session["shots"] if shot["status"] == "pending"]
    next_shot = pending[0] if pending else None
    return {
        "session_id": session["session_id"],
        "brief": session["brief"],
        "completed_count": completed,
        "total_count": len(session["shots"]),
        "storage_root": session["storage_root"],
        "next_shot": next_shot,
    }


def save_session(session_path: Path, session: JsonDict) -> None:
    session_path.write_text(json.dumps(session, ensure_ascii=False, indent=2))


def load_session(session_path: Path) -> JsonDict:
    return json.loads(session_path.read_text())


def init_session(brief: str, session_root: Path, max_shots: int = 4) -> JsonDict:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    session_dir = session_root / f"session-{stamp}"
    shots_dir = session_dir / "shots"
    shots_dir.mkdir(parents=True, exist_ok=True)

    shots = plan_shots(brief, max_shots=max_shots)
    for shot in shots:
        shot["output_path"] = str(shots_dir / f"{shot['index']:02d}-{shot['shot_id']}.ts")

    session = {
        "session_id": stamp,
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "brief": brief,
        "storage_root": str(session_dir),
        "workflow_artifacts": {
            "log_path": str(session_dir / DEFAULT_LOG_NAME),
            "report_path": str(session_dir / DEFAULT_REPORT_NAME),
        },
        "shots": shots,
        "move_policy": {
            "max_shots": max_shots,
            "reduce_repositioning": True,
            "guidance": "Shots are grouped by zone to reduce operator repositioning.",
        },
    }
    save_session(session_dir / "session.json", session)
    return session


def get_next_pending_shot(session: JsonDict) -> Optional[JsonDict]:
    for shot in session["shots"]:
        if shot["status"] == "pending":
            return shot
    return None


def mark_shot_captured(session: JsonDict, shot_index: int, output_path: Optional[str] = None) -> JsonDict:
    for shot in session["shots"]:
        if shot["index"] == shot_index:
            shot["status"] = "captured"
            shot["captured_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            if output_path:
                shot["output_path"] = output_path
            return shot
    raise EzvizError(f"Shot index {shot_index} not found in session.")


def fetch_bytes(url: str, timeout: float) -> bytes:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return response.read()


def stream_url_path(url: str) -> str:
    return urllib.parse.urlparse(url).path.lower()


def record_hls_clip(
    playlist_url: str,
    output_path: Path,
    target_duration: float,
    timeout_seconds: float = 20.0,
    poll_interval_seconds: float = 2.0,
    max_wait_seconds: float = 45.0,
    fetcher: Callable[[str, float], bytes] = fetch_bytes,
) -> JsonDict:
    seen = set()
    segments: List[Tuple[str, float]] = []
    duration = 0.0
    start = time.time()

    while duration < target_duration and time.time() - start < max_wait_seconds:
        playlist = fetcher(playlist_url, timeout_seconds).decode("utf-8", errors="replace")
        lines = [line.strip() for line in playlist.splitlines() if line.strip()]
        pending_duration = None

        for line in lines:
            if line.startswith("#EXTINF:"):
                pending_duration = float(line.split(":", 1)[1].split(",", 1)[0])
            elif not line.startswith("#"):
                segment_url = urllib.parse.urljoin(playlist_url, line)
                segment_duration = pending_duration or 0.0
                pending_duration = None
                if segment_url in seen:
                    continue
                seen.add(segment_url)
                segments.append((segment_url, segment_duration))
                duration += segment_duration
                if duration >= target_duration:
                    break

        if duration >= target_duration:
            break
        time.sleep(poll_interval_seconds)

    if not segments:
        raise EzvizError("No HLS segments were found for recording.")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("wb") as output_file:
        for segment_url, _ in segments:
            output_file.write(fetcher(segment_url, timeout_seconds))

    return {
        "output_path": str(output_path),
        "segment_count": len(segments),
        "captured_duration_seconds": round(sum(item[1] for item in segments), 3),
    }


def record_flv_clip(
    stream_url: str,
    output_path: Path,
    target_duration: float,
) -> JsonDict:
    ffmpeg_path = shutil.which("ffmpeg")
    if not ffmpeg_path:
        raise EzvizError("Recording FLV streams requires ffmpeg to be installed.")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    command = [
        ffmpeg_path,
        "-y",
        "-nostdin",
        "-t",
        str(target_duration),
        "-i",
        stream_url,
        "-c",
        "copy",
        str(output_path),
    ]
    timeout_seconds = max(float(target_duration) + 20.0, 30.0)
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    timed_out = False
    try:
        _, stderr = process.communicate(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        timed_out = True
        process.kill()
        _, stderr = process.communicate()

    if (process.returncode != 0 and not timed_out) or not output_path.exists():
        raise EzvizError(stderr[-500:] if stderr else "ffmpeg failed to record FLV stream.")

    if timed_out and output_path.stat().st_size <= 0:
        raise EzvizError("ffmpeg timed out before producing a usable FLV recording.")

    return {
        "output_path": str(output_path),
        "captured_duration_seconds": round(target_duration, 3),
        "segment_count": None,
        "source_protocol": "flv",
        "terminated_on_timeout": timed_out,
    }


def transcode_recording_to_mp4(input_path: Path, rotation_mode: RotationMode = "cw90") -> JsonDict:
    ffmpeg_path = shutil.which("ffmpeg")
    if not ffmpeg_path:
        return {
            "ok": False,
            "reason": "ffmpeg_not_found",
        }

    output_path = input_path.with_suffix(".mp4")
    command = build_rotated_mp4_command(ffmpeg_path, input_path, output_path, rotation_mode=rotation_mode)
    completed = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0 or not output_path.exists():
        return {
            "ok": False,
            "reason": "ffmpeg_failed",
            "stderr": completed.stderr[-500:] if completed.stderr else "",
        }

    return {
        "ok": True,
        "output_path": str(output_path),
        "layout": rotation_mode,
    }


def build_rotated_mp4_command(
    ffmpeg_path: str,
    input_path: Path,
    output_path: Path,
    rotation_mode: RotationMode = "cw90",
) -> List[str]:
    if rotation_mode == "cw90":
        video_filter = "transpose=1,format=yuv420p"
    elif rotation_mode == "ccw90":
        video_filter = "transpose=2,format=yuv420p"
    elif rotation_mode == "flip180":
        video_filter = "hflip,vflip,format=yuv420p"
    elif rotation_mode == "none":
        video_filter = "format=yuv420p"
    else:
        raise EzvizError(f"Unsupported rotation mode: {rotation_mode}")

    return [
        ffmpeg_path,
        "-y",
        "-i",
        str(input_path),
        "-vf",
        video_filter,
        "-map",
        "0:v:0",
        "-map",
        "0:a?",
        "-c:v",
        "libx264",
        "-c:a",
        "aac",
        "-movflags",
        "+faststart",
        str(output_path),
    ]


def resolve_stream_url(
    config: EnvConfig,
    source: Optional[str] = None,
    protocol_id: Optional[int] = None,
) -> str:
    client = EzvizClient(config)
    if config.managed_stream_id:
        managed = client.get_stream_address(
            stream_id=config.managed_stream_id,
            protocol=config.managed_stream_protocol,
            quality=config.managed_stream_quality,
            support_h265=config.managed_stream_support_h265,
            mute=config.managed_stream_mute,
        )
        stream_url = managed["address"]
        if not isinstance(stream_url, str) or not stream_url_path(stream_url).endswith((".m3u8", ".flv")):
            raise EzvizError("Managed stream address did not return a supported HLS or FLV URL.")
        return stream_url

    effective_protocol_id = protocol_id if protocol_id is not None else DEFAULT_LIVE_PROTOCOL_ID
    live = client.get_live_url(
        source=source,
        protocol_id=effective_protocol_id,
        quality=DEFAULT_LIVE_QUALITY,
        support_h265=DEFAULT_LIVE_SUPPORT_H265,
        mute=DEFAULT_LIVE_MUTE,
        address_type=DEFAULT_LIVE_ADDRESS_TYPE,
    )
    stream_url = live["stream_url"]
    if not isinstance(stream_url, str) or not stream_url_path(stream_url).endswith((".m3u8", ".flv")):
        raise EzvizError("The workflow recorder currently supports HLS (.m3u8) and FLV (.flv) stream URLs only.")
    return stream_url


def build_raw_recording_path(output_path: Path, stream_url: str) -> Path:
    if stream_url_path(stream_url).endswith(".flv"):
        return output_path.with_suffix(".flv")
    return output_path.with_suffix(".ts")


def workflow_log_path(session: JsonDict, session_path: Path) -> Path:
    artifacts = session.get("workflow_artifacts", {})
    configured = artifacts.get("log_path")
    if isinstance(configured, str) and configured:
        return Path(configured)
    return session_path.parent / DEFAULT_LOG_NAME


def workflow_report_path(session: JsonDict, session_path: Path) -> Path:
    artifacts = session.get("workflow_artifacts", {})
    configured = artifacts.get("report_path")
    if isinstance(configured, str) and configured:
        return Path(configured)
    return session_path.parent / DEFAULT_REPORT_NAME


def append_workflow_log(session: JsonDict, session_path: Path, event: str, payload: JsonDict) -> Path:
    log_path = workflow_log_path(session, session_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "event": event,
        "session_id": session.get("session_id"),
        "payload": payload,
    }
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    return log_path


def ffprobe_json(media_path: Path) -> JsonDict:
    ffprobe_path = shutil.which("ffprobe")
    if not ffprobe_path or not media_path.exists():
        return {}

    command = [
        ffprobe_path,
        "-v",
        "error",
        "-show_entries",
        "format=duration,size:stream=index,codec_type,codec_name,width,height",
        "-of",
        "json",
        str(media_path),
    ]
    completed = subprocess.run(command, check=False, capture_output=True, text=True)
    if completed.returncode != 0 or not completed.stdout.strip():
        return {}
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError:
        return {}


def probe_media_metrics(media_path: Path) -> JsonDict:
    payload = ffprobe_json(media_path)
    streams = payload.get("streams", [])
    video_stream = next((stream for stream in streams if stream.get("codec_type") == "video"), {})
    audio_stream = next((stream for stream in streams if stream.get("codec_type") == "audio"), {})
    format_info = payload.get("format", {})

    def as_float(value: Any) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0

    def as_int(value: Any) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0

    return {
        "duration_seconds": as_float(format_info.get("duration")),
        "size_bytes": as_int(format_info.get("size")),
        "width": as_int(video_stream.get("width")),
        "height": as_int(video_stream.get("height")),
        "video_codec": video_stream.get("codec_name", ""),
        "audio_codec": audio_stream.get("codec_name", ""),
    }


def classify_capture_output(metrics: JsonDict, target_duration: int) -> str:
    if not metrics:
        return "failed"

    duration = float(metrics.get("duration_seconds", 0.0) or 0.0)
    width = int(metrics.get("width", 0) or 0)
    height = int(metrics.get("height", 0) or 0)
    min_duration = max(float(target_duration) - 2.0, float(target_duration) * 0.8)

    if duration >= min_duration and width >= 1000 and height >= 1000:
        return "accepted"
    if duration > 0.0:
        return "abnormal"
    return "failed"


def extract_failure_frame(input_path: Path, output_path: Path, second: float = 1.0) -> Optional[Path]:
    ffmpeg_path = shutil.which("ffmpeg")
    if not ffmpeg_path or not input_path.exists():
        return None

    command = [
        ffmpeg_path,
        "-y",
        "-ss",
        str(second),
        "-i",
        str(input_path),
        "-frames:v",
        "1",
        str(output_path),
    ]
    completed = subprocess.run(command, check=False, capture_output=True, text=True)
    if completed.returncode != 0 or not output_path.exists():
        return None
    return output_path


def analyze_failure_frame(frame_path: Optional[Path], metrics: JsonDict) -> str:
    duration = round(float(metrics.get("duration_seconds", 0.0) or 0.0), 3)
    width = int(metrics.get("width", 0) or 0)
    height = int(metrics.get("height", 0) or 0)
    video_codec = metrics.get("video_codec") or "unknown"
    audio_codec = metrics.get("audio_codec") or "unknown"
    analysis_parts = [
        "录制结果异常。",
        f"时长={duration}s。",
        f"分辨率={width}x{height}。",
        f"视频编码={video_codec}。",
        f"音频编码={audio_codec}。",
    ]

    if frame_path and shutil.which("tesseract"):
        command = [
            "tesseract",
            str(frame_path),
            "stdout",
            "--psm",
            "6",
        ]
        completed = subprocess.run(command, check=False, capture_output=True, text=True)
        text = " ".join(completed.stdout.split())
        if text:
            analysis_parts.append(f"OCR文本={text[:240]}")
        else:
            analysis_parts.append("OCR未识别到可读文字。")
    elif frame_path:
        analysis_parts.append("已保存异常帧，但当前机器未安装 tesseract，未执行 OCR。")
    else:
        analysis_parts.append("未能抽取异常帧。")
    return " ".join(analysis_parts)


def render_workflow_report(session: JsonDict, session_path: Path) -> Path:
    report_path = workflow_report_path(session, session_path)
    shots = session.get("shots", [])
    accepted_count = sum(1 for shot in shots if shot.get("validation", {}).get("status") == "accepted")
    abnormal_count = sum(1 for shot in shots if shot.get("validation", {}).get("status") == "abnormal")
    failed_count = sum(1 for shot in shots if shot.get("validation", {}).get("status") == "failed")
    lines = [
        "# CB60 Capture Workflow Report",
        "",
        f"- Session ID: {session.get('session_id')}",
        f"- Brief: {session.get('brief')}",
        f"- Created at: {session.get('created_at')}",
        f"- Storage root: {session.get('storage_root')}",
        "- Default live chain: protocol=4, quality=1, supportH265=1, type=1",
        f"- Accepted shots: {accepted_count}",
        f"- Abnormal shots: {abnormal_count}",
        f"- Failed shots: {failed_count}",
        "",
        "## Shots",
        "",
        "| # | Label | Capture | Validation | Output | Notes |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for shot in shots:
        validation = shot.get("validation", {})
        note = validation.get("analysis") or ""
        note = note.replace("|", "/")[:120]
        lines.append(
            "| {index} | {label} | {capture} | {validation_status} | {output_path} | {note} |".format(
                index=shot.get("index", "-"),
                label=shot.get("label", ""),
                capture=shot.get("status", ""),
                validation_status=validation.get("status", "pending"),
                output_path=Path(shot.get("output_path", "")).name,
                note=note,
            )
        )
    report_path.write_text("\n".join(lines), encoding="utf-8")
    return report_path


def record_stream_clip(
    stream_url: str,
    output_path: Path,
    target_duration: float,
    timeout_seconds: float,
) -> JsonDict:
    if stream_url_path(stream_url).endswith(".flv"):
        return record_flv_clip(
            stream_url=stream_url,
            output_path=output_path,
            target_duration=target_duration,
        )
    return record_hls_clip(
        playlist_url=stream_url,
        output_path=output_path,
        target_duration=target_duration,
        timeout_seconds=timeout_seconds,
    )


def capture_next_shot(
    session_path: Path,
    config: EnvConfig,
    source: Optional[str] = None,
    protocol_id: Optional[int] = None,
    stream_url: Optional[str] = None,
    rotation_mode: RotationMode = "cw90",
    transcode_func: Callable[[Path, RotationMode], JsonDict] = transcode_recording_to_mp4,
    probe_func: Callable[[Path], JsonDict] = probe_media_metrics,
    classify_func: Callable[[JsonDict, int], str] = classify_capture_output,
    extract_frame_func: Callable[[Path, Path, float], Optional[Path]] = extract_failure_frame,
    analyze_failure_func: Callable[[Optional[Path], JsonDict], str] = analyze_failure_frame,
    log_func: Callable[[JsonDict, Path, str, JsonDict], Path] = append_workflow_log,
    report_func: Callable[[JsonDict, Path], Path] = render_workflow_report,
) -> JsonDict:
    session = load_session(session_path)
    shot = get_next_pending_shot(session)
    if not shot:
        return {
            "message": "All planned shots have already been captured.",
            "summary": session_summary(session),
        }

    effective_stream_url = stream_url or resolve_stream_url(config, source=source, protocol_id=protocol_id)
    log_path = log_func(
        session,
        session_path,
        "capture_started",
        {
            "shot_index": shot["index"],
            "shot_id": shot["shot_id"],
            "rotation_mode": rotation_mode,
            "stream_url": effective_stream_url,
            "default_live_chain": {
                "protocol": DEFAULT_LIVE_PROTOCOL_ID,
                "quality": DEFAULT_LIVE_QUALITY,
                "support_h265": DEFAULT_LIVE_SUPPORT_H265,
                "type": DEFAULT_LIVE_ADDRESS_TYPE,
            },
        },
    )
    raw_output_path = build_raw_recording_path(Path(shot["output_path"]), effective_stream_url)
    result = record_stream_clip(
        stream_url=effective_stream_url,
        output_path=raw_output_path,
        target_duration=float(shot["duration_seconds"]),
        timeout_seconds=config.timeout_seconds,
    )
    conversion = transcode_func(Path(result["output_path"]), rotation_mode)
    final_output_path = result["output_path"]
    if conversion.get("ok") and isinstance(conversion.get("output_path"), str):
        final_output_path = conversion["output_path"]

    inspection_path = Path(final_output_path)
    metrics = probe_func(inspection_path) if inspection_path.exists() else {}
    validation_status = classify_func(metrics, int(shot["duration_seconds"]))
    frame_path: Optional[Path] = None
    failure_analysis = ""
    if validation_status != "accepted" and inspection_path.exists():
        frame_path = extract_frame_func(
            inspection_path,
            session_path.parent / "shots" / f"{shot['index']:02d}-{shot['shot_id']}-failure-frame.jpg",
            1.0,
        )
        failure_analysis = analyze_failure_func(frame_path, metrics)

    captured = mark_shot_captured(session, shot["index"], output_path=final_output_path)
    captured["raw_output_path"] = result["output_path"]
    captured["validation"] = {
        "status": validation_status,
        "metrics": metrics,
        "frame_path": str(frame_path) if frame_path else None,
        "analysis": failure_analysis,
    }
    save_session(session_path, session)
    report_path = report_func(session, session_path)
    log_func(
        session,
        session_path,
        "capture_completed",
        {
            "shot_index": shot["index"],
            "shot_id": shot["shot_id"],
            "recording": result,
            "conversion": conversion,
            "validation": captured["validation"],
            "final_output_path": final_output_path,
        },
    )

    next_shot = get_next_pending_shot(session)
    payload = {
        "captured_shot": captured,
        "recording": result,
        "conversion": conversion,
        "workflow_log_path": str(log_path),
        "workflow_report_path": str(report_path),
        "summary": session_summary(session),
    }
    if next_shot:
        payload["next_instruction"] = next_shot["operator_message"]
    else:
        payload["next_instruction"] = "素材采集完成，请到 storage_root 查看本地文件。"
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Plan and capture short CB60 footage sessions.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init-session", help="Create a local capture session from a free-text brief.")
    init_parser.add_argument("--brief", required=True, help="What素材 to capture. Use natural language or comma-separated items.")
    init_parser.add_argument(
        "--session-root",
        type=Path,
        default=Path("./artifacts/workflows"),
        help="Where to create the local session folder.",
    )
    init_parser.add_argument("--max-shots", type=int, default=4, help="Maximum number of planned shots.")

    next_parser = subparsers.add_parser("next-shot", help="Show the next shot instruction for a session.")
    next_parser.add_argument("--session", type=Path, required=True, help="Path to session.json")

    status_parser = subparsers.add_parser("status", help="Show a compact status summary for a session.")
    status_parser.add_argument("--session", type=Path, required=True, help="Path to session.json")

    capture_parser = subparsers.add_parser("capture-shot", help="Record the next planned shot to a local file.")
    capture_parser.add_argument("--session", type=Path, required=True, help="Path to session.json")
    capture_parser.add_argument("--source", help="Optional live source parameter for the stream API.")
    capture_parser.add_argument("--protocol-id", type=int, help="Optional numeric protocol value expected by the tenant.")
    capture_parser.add_argument("--stream-url", help="Optional direct HLS or FLV URL override.")
    capture_parser.add_argument(
        "--rotation",
        default="cw90",
        choices=["cw90", "ccw90", "flip180", "none"],
        help="Output rotation mode. cw90 is the default portrait output.",
    )

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

    if args.command == "init-session":
        session = init_session(args.brief, args.session_root, max_shots=min(max(args.max_shots, 1), 4))
        emit_json(
            {
                "session_path": str(Path(session["storage_root"]) / "session.json"),
                "summary": session_summary(session),
                "first_instruction": get_next_pending_shot(session)["operator_message"],
            }
        )
        return 0

    if args.command == "next-shot":
        session = load_session(args.session)
        next_shot = get_next_pending_shot(session)
        emit_json(
            {
                "next_shot": next_shot,
                "summary": session_summary(session),
            }
        )
        return 0

    if args.command == "status":
        session = load_session(args.session)
        emit_json(session_summary(session))
        return 0

    if args.command == "capture-shot":
        try:
            payload = capture_next_shot(
                session_path=args.session,
                config=EnvConfig.from_env(env_file=env_file),
                source=args.source,
                protocol_id=args.protocol_id,
                stream_url=args.stream_url,
                rotation_mode=args.rotation,
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
