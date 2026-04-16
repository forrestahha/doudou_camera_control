#!/usr/bin/env python3
"""Shot-planning and local capture workflow for EZVIZ CB60 sessions."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
from contextlib import contextmanager
from dataclasses import dataclass
from functools import lru_cache
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
LAS_PIPELINE_SKILL_ORDER: Tuple[Tuple[str, str], ...] = (
    ("upload_to_tos", "上传到火山 TOS"),
    ("las_highlight_edit", "LAS 高光剪辑"),
    ("las_video_inpaint", "LAS 去水印"),
    ("las_video_resize", "LAS 变高清"),
)
LAS_EDIT_SKILL_PATH = Path.home() / ".codex" / "skills" / "byted-las-video-edit" / "scripts" / "skill.py"
LAS_INPAINT_SKILL_PATH = Path.home() / ".codex" / "skills" / "byted-las-video-inpaint" / "scripts" / "skill.py"
LAS_RESIZE_SKILL_PATH = Path.home() / ".codex" / "skills" / "byted-las-video-resize" / "scripts" / "skill.py"
LAS_SKILL_CALL_LOCK = threading.RLock()
DEFAULT_LAS_HIGHLIGHT_PROMPT_TEMPLATE = (
    "任务：对商家高光用餐时段，在后厨或前台固定摆放摄像头拍摄的视频，进行高光时刻识别与剪辑提取。"
    " 高光时刻标准：画面具备动态变化，无静止卡顿；画面内容丰富，包含有效场景动作，典型示例包括菜品上菜全过程、后厨食材处理、前台接待或操作、"
    "人员走动互动、餐具摆放等营业相关动态画面。 非高光剔除标准：完全静态、无人物、无动作、无画面变化的空镜画面；无有效内容的单调静止镜头。"
    " 输出要求：精准截取视频中的高光片段，剔除所有静态无效画面，完成高光时刻精简剪辑，保留完整连贯的有效动态内容。"
    " 当前镜头重点：{focus}。"
)
DEFAULT_LAS_INPAINT_TARGETS: Tuple[str, ...] = ("watermark",)
DEFAULT_LAS_INPAINT_BACKEND = "pixel_replace"
# 1000x1000 归一化坐标，默认加强左下角时间水印区域。
DEFAULT_LAS_INPAINT_FIXED_BBOXES: Tuple[Tuple[int, int, int, int], ...] = ((0, 650, 150, 970),)
DEFAULT_LAS_RESIZE_MIN_WIDTH = 1440
DEFAULT_LAS_RESIZE_MAX_WIDTH = 2560
DEFAULT_LAS_RESIZE_MIN_HEIGHT = 2560
DEFAULT_LAS_RESIZE_MAX_HEIGHT = 2560


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
        "las_pipeline": {
            "enabled": True,
            "execution_mode": "deferred_until_tos_ready",
            "source_policy": "use_final_mp4_if_available_else_raw_capture",
            "required_bridge": {
                "type": "tos_upload",
                "status": "pending_config",
                "message": "需要先提供火山 TOS 上传访问配置，LAS 才能从 tos:// 路径取视频。",
            },
            "steps": [
                {
                    "step": step_id,
                    "label": label,
                }
                for step_id, label in LAS_PIPELINE_SKILL_ORDER
            ],
        },
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


def has_usable_recording(output_path: Path) -> bool:
    return output_path.exists() and output_path.stat().st_size > 0


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
        errors="replace",
    )
    timed_out = False
    try:
        _, stderr = process.communicate(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        timed_out = True
        process.kill()
        _, stderr = process.communicate()

    if (process.returncode != 0 and not timed_out) and not has_usable_recording(output_path):
        raise EzvizError(stderr[-500:] if stderr else "ffmpeg failed to record FLV stream.")

    if timed_out and not has_usable_recording(output_path):
        raise EzvizError("ffmpeg timed out before producing a usable FLV recording.")

    return {
        "output_path": str(output_path),
        "captured_duration_seconds": round(target_duration, 3),
        "segment_count": None,
        "source_protocol": "flv",
        "terminated_on_timeout": timed_out,
        "ffmpeg_returncode": process.returncode,
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
        errors="replace",
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
    completed = subprocess.run(command, check=False, capture_output=True, text=True, errors="replace")
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


def has_las_postprocess_config(config: EnvConfig) -> bool:
    return all(
        (
            config.las_api_key,
            config.las_region,
            config.tos_access_key,
            config.tos_secret_key,
            config.tos_original,
            config.tos_final,
        )
    )


def normalize_tos_prefix(prefix: str) -> str:
    value = prefix.strip()
    if not value:
        raise EzvizError("TOS prefix is empty.")
    if not value.startswith("tos://"):
        raise EzvizError(f"Invalid TOS prefix: {value}")
    if not value.endswith("/"):
        value += "/"
    return value


def parse_tos_url(url: str) -> Tuple[str, str]:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "tos" or not parsed.netloc or not parsed.path:
        raise EzvizError(f"Invalid TOS URL: {url}")
    return parsed.netloc, parsed.path.lstrip("/")


def join_tos_prefix(prefix: str, *parts: str) -> str:
    normalized = normalize_tos_prefix(prefix)
    parsed = urllib.parse.urlparse(normalized)
    key_prefix = parsed.path.lstrip("/")
    suffix = "/".join(part.strip("/") for part in parts if part)
    key = key_prefix + suffix
    return f"tos://{parsed.netloc}/{key}"


def build_las_stage_prefix(prefix: str, unique_prefix: str, stage_name: str) -> str:
    return normalize_tos_prefix(join_tos_prefix(prefix, unique_prefix, stage_name))


def write_json_artifact(path: Path, payload: JsonDict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def build_tos_client(config: EnvConfig) -> Any:
    try:
        import tos  # type: ignore
    except ImportError as exc:
        raise EzvizError("Missing tos SDK; unable to upload videos to TOS.") from exc

    region = config.las_region or "cn-beijing"
    endpoint = f"tos-{region}.volces.com"
    return tos.TosClientV2(
        ak=config.tos_access_key,
        sk=config.tos_secret_key,
        endpoint=endpoint,
        region=region,
    )


def upload_local_file_to_tos(config: EnvConfig, local_path: Path, target_tos_url: str) -> JsonDict:
    if not local_path.exists():
        raise EzvizError(f"Local file not found for TOS upload: {local_path}")

    bucket, key = parse_tos_url(target_tos_url)
    client = build_tos_client(config)
    with local_path.open("rb") as handle:
        client.put_object(
            bucket=bucket,
            key=key,
            content=handle,
            content_type="video/mp4",
        )
    client.head_object(bucket=bucket, key=key)
    return {
        "bucket": bucket,
        "key": key,
        "tos_url": target_tos_url,
        "size_bytes": local_path.stat().st_size,
    }


@lru_cache(maxsize=8)
def load_skill_module(module_name: str, script_path: str) -> Any:
    path = Path(script_path)
    if not path.exists():
        raise EzvizError(f"Skill script not found: {path}")
    spec = importlib.util.spec_from_file_location(module_name, str(path))
    if spec is None or spec.loader is None:
        raise EzvizError(f"Unable to load skill script: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@contextmanager
def las_env_context(config: EnvConfig) -> Iterable[None]:
    keys = {
        "LAS_API_KEY": config.las_api_key,
        "LAS_REGION": config.las_region,
    }
    previous = {name: os.environ.get(name) for name in keys}
    try:
        for name, value in keys.items():
            if value:
                os.environ[name] = value
        yield
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


@contextmanager
def las_skill_call_context(config: EnvConfig) -> Iterable[None]:
    # LAS skill 在运行时直接从进程环境读取 API key，线程间需要串行化这段临界区。
    with LAS_SKILL_CALL_LOCK:
        with las_env_context(config):
            yield


def call_las_skill(config: EnvConfig, func: Callable[..., JsonDict], *args: Any, **kwargs: Any) -> JsonDict:
    with las_skill_call_context(config):
        return func(*args, **kwargs)


def wait_for_poll_completion(
    poller: Callable[[], JsonDict],
    *,
    timeout_seconds: int = 1800,
    interval_seconds: int = 5,
) -> JsonDict:
    started = time.time()
    while True:
        result = poller()
        metadata = result.get("metadata", {})
        status = metadata.get("task_status")
        business_code = metadata.get("business_code")
        error_msg = metadata.get("error_msg")
        if status == "COMPLETED" and str(business_code) in {"0", "200"}:
            return result
        if status in {"FAILED", "TIMEOUT"} or (status == "COMPLETED" and str(business_code) not in {"0", "200"}):
            raise EzvizError(f"LAS task failed: status={status} business_code={business_code} error_msg={error_msg}")
        if time.time() - started > timeout_seconds:
            raise EzvizError(f"LAS task timed out after {timeout_seconds}s; last status={status}")
        time.sleep(interval_seconds)


def build_las_task_description(task_label: str, task_text: str) -> str:
    focus = task_text.strip() or task_label.strip() or "店内经营画面"
    return DEFAULT_LAS_HIGHLIGHT_PROMPT_TEMPLATE.format(focus=focus)


def build_capture_timestamp(ts: Optional[float] = None) -> str:
    moment = time.localtime(time.time() if ts is None else ts)
    return time.strftime("%Y%m%d-%H%M%S", moment)


def build_timestamped_shot_path(shots_dir: Path, shot_index: int, shot_id: str, timestamp: str, suffix: str) -> Path:
    return shots_dir / f"{shot_index:02d}-{shot_id}-{timestamp}{suffix}"


def derive_store_slug_from_tos_prefix(*prefixes: str) -> str:
    for prefix in prefixes:
        if not prefix:
            continue
        parsed = urllib.parse.urlparse(normalize_tos_prefix(prefix))
        parts = [part for part in parsed.path.strip("/").split("/") if part]
        if not parts:
            continue
        candidate = parts[-1]
        for suffix in ("_original", "_final"):
            if candidate.endswith(suffix):
                candidate = candidate[: -len(suffix)]
                break
        candidate = re.sub(r"[^a-zA-Z0-9_-]+", "_", candidate).strip("_")
        if candidate:
            return candidate
    return "openclaw_store"


def build_tos_video_filename(store_slug: str, capture_timestamp: str, stage: str, seq: int, suffix: str = ".mp4") -> str:
    date_part, time_part = capture_timestamp.split("-", 1)
    normalized_stage = re.sub(r"[^a-zA-Z0-9_-]+", "_", stage).strip("_") or "original"
    return f"{store_slug}_{date_part}_{time_part}_{normalized_stage}_{seq:02d}{suffix}"


def resolve_las_inpaint_fixed_bboxes(config: EnvConfig) -> List[List[int]]:
    boxes = config.las_inpaint_fixed_bboxes or DEFAULT_LAS_INPAINT_FIXED_BBOXES
    return [list(item) for item in boxes]


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
    completed = subprocess.run(command, check=False, capture_output=True, text=True, errors="replace")
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
        completed = subprocess.run(command, check=False, capture_output=True, text=True, errors="replace")
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
        f"- LAS pipeline mode: {session.get('las_pipeline', {}).get('execution_mode', 'disabled')}",
        "",
        "## Shots",
        "",
        "| # | Label | Capture | Validation | LAS | Output | Notes |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for shot in shots:
        validation = shot.get("validation", {})
        postprocess = shot.get("postprocess", {})
        note = validation.get("analysis") or ""
        note = note.replace("|", "/")[:120]
        lines.append(
            "| {index} | {label} | {capture} | {validation_status} | {postprocess_status} | {output_path} | {note} |".format(
                index=shot.get("index", "-"),
                label=shot.get("label", ""),
                capture=shot.get("status", ""),
                validation_status=validation.get("status", "pending"),
                postprocess_status=postprocess.get("status", "pending"),
                output_path=Path(shot.get("output_path", "")).name,
                note=note,
            )
        )
    report_path.write_text("\n".join(lines), encoding="utf-8")
    return report_path


def build_las_postprocess_state(
    session: JsonDict,
    shot: JsonDict,
    final_output_path: str,
    validation_status: str,
) -> JsonDict:
    pipeline_config = session.get("las_pipeline", {})
    source_path = final_output_path or shot.get("output_path", "")
    if validation_status != "accepted":
        return {
            "status": "skipped_capture_not_accepted",
            "source_path": source_path,
            "reason": "只有验片通过的片段才会进入 LAS 后处理流水线。",
            "steps": [
                {
                    "step": step_id,
                    "label": label,
                    "status": "skipped",
                    "reason": "capture_not_accepted",
                }
                for step_id, label in LAS_PIPELINE_SKILL_ORDER
            ],
        }

    tos_bridge = pipeline_config.get("required_bridge", {})
    bridge_status = tos_bridge.get("status", "pending_config")
    bridge_message = tos_bridge.get("message", "缺少 TOS 上传访问配置。")
    steps: List[JsonDict] = []
    for index, (step_id, label) in enumerate(LAS_PIPELINE_SKILL_ORDER):
        if index == 0:
            steps.append(
                {
                    "step": step_id,
                    "label": label,
                    "status": bridge_status,
                    "reason": bridge_message,
                }
            )
            continue
        steps.append(
            {
                "step": step_id,
                "label": label,
                "status": "blocked",
                "depends_on": LAS_PIPELINE_SKILL_ORDER[index - 1][0],
                "reason": "等待上一环节完成后再执行。",
            }
        )
    return {
        "status": bridge_status,
        "execution_mode": pipeline_config.get("execution_mode", "deferred_until_tos_ready"),
        "source_path": source_path,
        "input_policy": pipeline_config.get("source_policy", "use_final_mp4_if_available_else_raw_capture"),
        "steps": steps,
    }


def run_las_postprocess_pipeline(
    *,
    config: EnvConfig,
    session: JsonDict,
    shot: JsonDict,
    final_output_path: str,
    validation_status: str,
    artifacts_root: Path,
) -> JsonDict:
    if validation_status != "accepted":
        return build_las_postprocess_state(session, shot, final_output_path, validation_status)
    if not has_las_postprocess_config(config):
        return build_las_postprocess_state(session, shot, final_output_path, validation_status)

    source_path = Path(final_output_path)
    if not source_path.exists():
        raise EzvizError(f"Missing accepted capture for LAS pipeline: {source_path}")

    session_id = str(session.get("session_id", "session"))
    shot_index = int(shot.get("index", 0) or 0)
    shot_id = str(shot.get("shot_id", "shot"))
    shot_label = str(shot.get("label", ""))
    shot_text = str(shot.get("request_text", ""))
    capture_timestamp = str(shot.get("capture_timestamp") or session.get("capture_timestamp") or build_capture_timestamp())
    store_slug = derive_store_slug_from_tos_prefix(config.tos_original, config.tos_final)
    unique_prefix = f"{session_id}/{shot_index:02d}-{shot_id}"
    original_output_name = build_tos_video_filename(store_slug, capture_timestamp, "original", shot_index)
    original_tos_url = join_tos_prefix(config.tos_original, unique_prefix, original_output_name)
    edit_output_prefix = build_las_stage_prefix(config.tos_original, unique_prefix, "las-edit")
    inpaint_output_prefix = build_las_stage_prefix(config.tos_original, unique_prefix, "las-inpaint")
    final_output_name = build_tos_video_filename(store_slug, capture_timestamp, "final", shot_index)

    steps: List[JsonDict] = [
        {"step": step_id, "label": label, "status": "pending"}
        for step_id, label in LAS_PIPELINE_SKILL_ORDER
    ]
    results_dir = artifacts_root / "las-results"
    results_dir.mkdir(parents=True, exist_ok=True)

    def block_remaining(start_index: int, reason: str) -> None:
        for index in range(start_index, len(steps)):
            if steps[index]["status"] == "pending":
                steps[index]["status"] = "blocked"
                steps[index]["reason"] = reason

    try:
        upload_info = upload_local_file_to_tos(config, source_path, original_tos_url)
        steps[0].update(
            {
                "status": "completed",
                "tos_url": upload_info["tos_url"],
                "size_bytes": upload_info["size_bytes"],
            }
        )
        write_json_artifact(results_dir / f"{shot_index:02d}-{shot_id}-upload.json", upload_info)

        edit_module = load_skill_module("las_video_edit_skill", str(LAS_EDIT_SKILL_PATH))
        edit_submit = call_las_skill(
            config,
            edit_module.submit_task,
            region=config.las_region,
            video_url=original_tos_url,
            output_tos_path=edit_output_prefix,
            task_description=build_las_task_description(shot_label, shot_text),
            mode="simple",
            output_format="mp4",
        )
        edit_task_id = ((edit_submit.get("metadata") or {}).get("task_id")) or ""
        edit_result = wait_for_poll_completion(
            lambda: call_las_skill(
                config,
                edit_module.poll_task,
                edit_task_id,
                region=config.las_region,
            ),
            timeout_seconds=1800,
            interval_seconds=5,
        )

        write_json_artifact(results_dir / f"{shot_index:02d}-{shot_id}-las-edit.json", edit_result)
        clips = ((edit_result.get("data") or {}).get("clips") or [])
        first_clip = clips[0] if clips else {}
        clip_url = first_clip.get("clip_url") if isinstance(first_clip, dict) else ""
        if not clip_url:
            raise EzvizError("LAS highlight step completed but returned no clip_url.")
        steps[1].update(
            {
                "status": "completed",
                "task_id": edit_task_id,
                "clip_url": clip_url,
                "clip_count": len(clips),
            }
        )

        inpaint_module = load_skill_module("las_video_inpaint_skill", str(LAS_INPAINT_SKILL_PATH))
        inpaint_submit = call_las_skill(
            config,
            inpaint_module.submit_task,
            region=config.las_region,
            video_url=clip_url,
            output_tos_path=inpaint_output_prefix,
            targets=list(DEFAULT_LAS_INPAINT_TARGETS),
            detection_precise_mode=True,
            inpainting_backend=DEFAULT_LAS_INPAINT_BACKEND,
            fixed_bboxes=resolve_las_inpaint_fixed_bboxes(config),
        )
        inpaint_task_id = ((inpaint_submit.get("metadata") or {}).get("task_id")) or ""
        inpaint_result = wait_for_poll_completion(
            lambda: call_las_skill(
                config,
                inpaint_module.poll_task,
                region=config.las_region,
                task_id=inpaint_task_id,
            ),
            timeout_seconds=1800,
            interval_seconds=5,
        )

        write_json_artifact(results_dir / f"{shot_index:02d}-{shot_id}-las-inpaint.json", inpaint_result)
        inpainted_video_path = ((inpaint_result.get("data") or {}).get("inpainted_video_path")) or ""
        if not inpainted_video_path:
            raise EzvizError("LAS inpaint step completed but returned no inpainted_video_path.")
        steps[2].update(
            {
                "status": "completed",
                "task_id": inpaint_task_id,
                "inpainted_video_path": inpainted_video_path,
            }
        )

        resize_module = load_skill_module("las_video_resize_skill", str(LAS_RESIZE_SKILL_PATH))
        resize_submit = call_las_skill(
            config,
            resize_module.submit_task,
            api_base=None,
            region=config.las_region,
            video_path=inpainted_video_path,
            output_tos_dir=normalize_tos_prefix(config.tos_final),
            output_file_name=final_output_name,
            min_width=DEFAULT_LAS_RESIZE_MIN_WIDTH,
            max_width=DEFAULT_LAS_RESIZE_MAX_WIDTH,
            min_height=DEFAULT_LAS_RESIZE_MIN_HEIGHT,
            max_height=DEFAULT_LAS_RESIZE_MAX_HEIGHT,
            force_original_aspect_ratio_type="increase",
            force_divisible_by=2,
        )
        resize_task_id = ((resize_submit.get("metadata") or {}).get("task_id")) or ""
        resize_result = wait_for_poll_completion(
            lambda: call_las_skill(
                config,
                resize_module.poll_task,
                api_base=None,
                region=config.las_region,
                task_id=resize_task_id,
            ),
            timeout_seconds=1800,
            interval_seconds=5,
        )

        write_json_artifact(results_dir / f"{shot_index:02d}-{shot_id}-las-resize.json", resize_result)
        final_tos_path = ((resize_result.get("data") or {}).get("output_path")) or ""
        if not final_tos_path:
            raise EzvizError("LAS resize step completed but returned no output_path.")
        steps[3].update(
            {
                "status": "completed",
                "task_id": resize_task_id,
                "output_path": final_tos_path,
            }
        )

        return {
            "status": "completed",
            "execution_mode": "inline_after_capture",
            "source_path": str(source_path),
            "uploaded_tos_path": original_tos_url,
            "final_tos_path": final_tos_path,
            "input_policy": session.get("las_pipeline", {}).get("source_policy", "use_final_mp4_if_available_else_raw_capture"),
            "steps": steps,
        }
    except Exception as exc:
        for index, step in enumerate(steps):
            if step["status"] == "pending":
                step["status"] = "failed"
                step["reason"] = str(exc)
                block_remaining(index + 1, "上一 LAS 步骤失败，后续已阻塞。")
                break
        return {
            "status": "failed",
            "execution_mode": "inline_after_capture",
            "source_path": str(source_path),
            "uploaded_tos_path": original_tos_url,
            "reason": str(exc),
            "input_policy": session.get("las_pipeline", {}).get("source_policy", "use_final_mp4_if_available_else_raw_capture"),
            "steps": steps,
        }


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
    capture_timestamp = build_capture_timestamp()
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
    base_output_path = build_timestamped_shot_path(
        session_path.parent / "shots",
        int(shot["index"]),
        str(shot["shot_id"]),
        capture_timestamp,
        ".ts",
    )
    raw_output_path = build_raw_recording_path(base_output_path, effective_stream_url)
    try:
        result = record_stream_clip(
            stream_url=effective_stream_url,
            output_path=raw_output_path,
            target_duration=float(shot["duration_seconds"]),
            timeout_seconds=config.timeout_seconds,
        )
    except Exception as exc:
        if stream_url or protocol_id is not None or not stream_url_path(effective_stream_url).endswith(".flv"):
            raise
        fallback_stream_url = resolve_stream_url(config, source=source, protocol_id=1)
        log_func(
            session,
            session_path,
            "flv_failed_retry_hls",
            {
                "shot_index": shot["index"],
                "shot_id": shot["shot_id"],
                "reason": str(exc),
                "fallback_stream_url": fallback_stream_url,
            },
        )
        effective_stream_url = fallback_stream_url
        raw_output_path = build_raw_recording_path(base_output_path, effective_stream_url)
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
            build_timestamped_shot_path(
                session_path.parent / "shots",
                int(shot["index"]),
                str(shot["shot_id"]),
                capture_timestamp,
                "-failure-frame.jpg",
            ),
            1.0,
        )
        failure_analysis = analyze_failure_func(frame_path, metrics)

    if has_las_postprocess_config(config):
        session.setdefault("las_pipeline", {})["execution_mode"] = "inline_after_capture"
        session["las_pipeline"].setdefault("required_bridge", {})
        session["las_pipeline"]["required_bridge"]["status"] = "configured"
        session["las_pipeline"]["required_bridge"]["message"] = "已配置 TOS 和 LAS，录制完成后会自动执行后处理。"

    captured = mark_shot_captured(session, shot["index"], output_path=final_output_path)
    captured["capture_timestamp"] = capture_timestamp
    captured["raw_output_path"] = result["output_path"]
    captured["validation"] = {
        "status": validation_status,
        "metrics": metrics,
        "frame_path": str(frame_path) if frame_path else None,
        "analysis": failure_analysis,
    }
    captured["postprocess"] = run_las_postprocess_pipeline(
        config=config,
        session=session,
        shot=captured,
        final_output_path=final_output_path,
        validation_status=validation_status,
        artifacts_root=session_path.parent,
    )
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
            "postprocess": captured["postprocess"],
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
