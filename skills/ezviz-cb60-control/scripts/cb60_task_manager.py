#!/usr/bin/env python3
"""Task orchestration helpers for recurring OpenClaw capture jobs."""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from ezviz_cb60_control import EnvConfig, EzvizClient, EzvizError, extract_env_file_arg

JsonDict = Dict[str, Any]
TIME_RANGE_SEPARATORS = r"(?:-|到|至|~|—|－)"
DEFAULT_BATTERY_REMINDER_THRESHOLD = 85
DEFAULT_PRECHECK_LEAD_MINUTES = 60
DEFAULT_CUSTOM_CAPTURE_INTERVAL_MINUTES = 10
DEFAULT_CUSTOM_CLIP_DURATION_SECONDS = 20
DEFAULT_SCHEDULER_CHECK_INTERVAL_MINUTES = 10
DEFAULT_LAS_SKILL_INSTALL_COMMANDS = [
    "npx skills add https://skills.volces.com/skills/volcengine/las -s byted-las-video-edit --agent openclaw",
    "npx skills add https://skills.volces.com/skills/volcengine/las -s byted-las-video-resize --agent openclaw",
    "npx skills add https://skills.volces.com/skills/volcengine/las -s byted-las-video-inpaint --agent openclaw",
]


def build_scheduler_state() -> JsonDict:
    return {
        "required": True,
        "check_every_minutes": DEFAULT_SCHEDULER_CHECK_INTERVAL_MINUTES,
        "automation_created": False,
        "automation_name": None,
        "delivery_channel": None,
        "created_at": None,
        "updated_at": None,
        "source": None,
    }


def build_scheduler_spec(task: JsonDict, task_path: Optional[Path] = None) -> JsonDict:
    resolved_task_path = Path(task["artifacts"]["config_path"]) if task_path is None else task_path
    scheduler_state = task.get("scheduler") or build_scheduler_state()
    automation_name = scheduler_state.get("automation_name") or f"doudou_camera_shot_check_{task['task_id']}"
    return {
        "required": True,
        "automation_name": automation_name,
        "mode": "periodic_poll",
        "check_every_minutes": int(scheduler_state.get("check_every_minutes", DEFAULT_SCHEDULER_CHECK_INTERVAL_MINUTES) or DEFAULT_SCHEDULER_CHECK_INTERVAL_MINUTES),
        "requires_delivery_channel": True,
        "delivery_channel_note": "Set delivery.channel explicitly, or bind this automation to a main session with an existing channel.",
        "task_path": str(resolved_task_path),
        "execution_contract": {
            "step_1": "run battery-precheck first",
            "step_2": "run should-run-now",
            "step_3": "only start capture workflow when should_run_now=true",
            "step_4": "after session completes, call record-session",
        },
        "commands": {
            "battery_precheck": f"python3 scripts/cb60_task_manager.py battery-precheck --task {resolved_task_path}",
            "should_run_now": f"python3 scripts/cb60_task_manager.py should-run-now --task {resolved_task_path}",
        },
        "state": scheduler_state,
    }


def emit_json(payload: JsonDict) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


def workflow_spec() -> JsonDict:
    return {
        "plugin_contract": {
            "mode": "recurring_daily_capture",
            "default_repeat_policy": "首次配置一次后，每日按相同时间窗口重复执行，直到商家明确说“停止拍摄”或后台停用任务。",
            "camera_scope": "单次任务绑定当前环境里的单台摄像头；多摄像头通过不同 env-file 切换。",
        },
        "installation_onboarding": {
            "send_message_after_install": True,
            "next_step_after_requirements": "收齐安装信息后，再向商家追问“你希望这个摄像头在什么时候拍？”。",
            "default_skill_install_commands": DEFAULT_LAS_SKILL_INSTALL_COMMANDS,
            "runtime_prerequisites": [
                "运行环境需要预装 ffmpeg。",
                "运行环境需要预装 tesseract-ocr，供异常帧 OCR 分析使用。",
                "完整云上链路还需要预装 Python 包 tos。",
                "首次安装插件后，默认先安装 3 个 LAS skill：高光剪辑、去水印、变高清。",
            ],
        },
        "merchant_onboarding": {
            "first_question": "你希望这个摄像头在什么时候拍？",
            "accepted_answer_format": [
                "11:00-12:00",
                "11点到12点",
                "上午11点到12点",
            ],
            "first_boot_action": "收到时间段后，立即创建每日重复定时任务，并立即给 OpenClaw 创建周期检查任务。",
        },
        "merchant_command_rules": {
            "wake_word": "龙虾",
            "allowed_commands": [
                {
                    "intent": "set_capture_time",
                    "example": "龙虾，帮我改一下拍摄时间 11:00-12:00",
                    "effect": "更新每日重复拍摄时间窗口。",
                },
                {
                    "intent": "start_custom_capture",
                    "example": "龙虾，从现在开始拍视频，每10分钟拍一次，拍到22:00",
                    "effect": "立即开启临时拍摄模式，但不会修改原有的每日定时任务。",
                },
                {
                    "intent": "diagnose_capture_problem",
                    "example": "龙虾，怎么没有拍摄，帮我找找问题",
                    "effect": "排查任务状态、设备状态、电量和最近一次拍摄结果。",
                },
                {
                    "intent": "stop_capture",
                    "example": "龙虾，停止拍摄",
                    "effect": "停止后续每日自动拍摄。",
                },
            ],
            "forbidden_scope": "除修改拍摄时间、排查拍摄问题、停止拍摄外，商家无其他交互权限。",
        },
        "custom_capture_rules": {
            "mode": "custom_capture_window",
            "trigger_examples": [
                "龙虾，从现在开始拍视频，每10分钟拍一次，拍到22:00",
                "龙虾，帮我拍视频，每15分钟拍一次，拍到21点30",
            ],
            "required_parameters": [
                "拍摄间隔",
                "结束时间",
            ],
            "default_clip_duration_seconds": DEFAULT_CUSTOM_CLIP_DURATION_SECONDS,
            "coexist_with_recurring_daily_schedule": True,
        },
        "scheduler_rules": {
            "required_openclaw_automation": {
                "must_create_after_first_boot_setup": True,
                "check_every_minutes": DEFAULT_SCHEDULER_CHECK_INTERVAL_MINUTES,
                "delivery_channel_required": True,
                "automation_purpose": "周期执行 battery-precheck 和 should-run-now，命中后再触发拍摄工作流。",
            },
            "before_window": [
                "在下一次拍摄开始前 60 分钟执行一次 battery-precheck。",
                f"如果电量低于 {DEFAULT_BATTERY_REMINDER_THRESHOLD}% ，提醒商家充电。",
            ],
            "inside_window": [
                "进入拍摄时间窗口后，OpenClaw 通过 should-run-now 判断是否执行。",
                "如果任务 active=true 且当前时间落在每日定时窗口或临时拍摄窗口内，则开始拍摄工作流。",
            ],
            "after_capture": [
                "每次会话结束后调用 record-session 写任务日志。",
                "日报通过 daily-report 汇总拍摄片段数、上传成功数和设备状态。",
            ],
        },
        "capture_command_rules": {
            "default_live_chain": {
                "protocol": 4,
                "quality": 1,
                "supportH265": 0,
                "source_strategy": "adaptive_retry",
                "validation_retry_strategy": "retry_h265_hls_once_when_low_quality_or_abnormal",
                "type": 1,
            },
            "workflow_defaults": [
                "录后自动转竖屏 MP4。",
                "录后自动验片。",
                "验片主规则以时长和分辨率为准；达到 10 秒且分辨率达标即可 accepted。",
                "tesseract 只用于异常帧 OCR 分析，不是 accepted 的前置条件。",
                "失败或异常时自动截帧并补图像分析。",
                "验片通过后自动进入 LAS 顺序：高光剪辑 -> 去水印 -> 变高清。",
            ],
        },
        "recommended_openclaw_sequence": [
            "首次安装后运行 setup-env。",
            "首次和商家只对话一次，获取每日拍摄时间段。",
            "如果商家临时说“帮我拍视频”，则改走自定义模式：补齐拍摄间隔和结束时间。",
            "调用 first-boot-setup 创建 task.json。",
            "每天定时前 1 小时运行 battery-precheck。",
            "到达时间窗口时运行 should-run-now。",
            "如果 should_run_now=true，则执行拍摄工作流。",
            "拍完后调用 record-session。",
            "按天调用 daily-report 生成设备运行日报。",
        ],
    }


def build_install_onboarding_message() -> JsonDict:
    text = (
        "为了完成首次安装，请先让 OpenClaw 默认安装下面 3 个 LAS skill：\n"
        "1. npx skills add https://skills.volces.com/skills/volcengine/las -s byted-las-video-edit --agent openclaw\n"
        "2. npx skills add https://skills.volces.com/skills/volcengine/las -s byted-las-video-resize --agent openclaw\n"
        "3. npx skills add https://skills.volces.com/skills/volcengine/las -s byted-las-video-inpaint --agent openclaw\n"
        "4. 然后再把下面这些信息准备好发给我：\n"
        "   - 萤石摄像头信息：AppKey、AppSecret、AccessToken、设备序列号、设备验证码、通道号（默认 1）。\n"
        "   - 火山云 LAS/TOS 信息：LAS_API_KEY、LAS_REGION、TOS_ACCESS_KEY、TOS_SECRET_KEY。\n"
        "5. 商家自己的两个 TOS 目录：\n"
        "   - TOS_ORIGINAL，例如 tos://doudou-video/openclaw/store1_jsspa_original/\n"
        "   - TOS_FINAL，例如 tos://doudou-video/openclaw/store1_jsspa_final/\n"
        "6. 以上信息配置完成后，我会继续问你：你希望这个摄像头在什么时候拍？\n"
        "后续插件会按这个时间段每日自动重复拍摄，直到你明确说“停止拍摄”。"
    )
    return {
        "send_after_install": True,
        "message_text": text,
        "default_skill_install_commands": DEFAULT_LAS_SKILL_INSTALL_COMMANDS,
        "required_fields": [
            "EZVIZ_APP_KEY",
            "EZVIZ_APP_SECRET",
            "EZVIZ_ACCESS_TOKEN",
            "EZVIZ_DEVICE_SERIAL",
            "EZVIZ_VALIDATE_CODE",
            "EZVIZ_CHANNEL_NO",
            "LAS_API_KEY",
            "LAS_REGION",
            "TOS_ACCESS_KEY",
            "TOS_SECRET_KEY",
            "TOS_ORIGINAL",
            "TOS_FINAL",
        ],
        "merchant_examples": {
            "tos_original": "tos://doudou-video/openclaw/store1_jsspa_original/",
            "tos_final": "tos://doudou-video/openclaw/store1_jsspa_final/",
        },
        "next_question": "你希望这个摄像头在什么时候拍？",
    }


def now_text(now_ts: Optional[float] = None) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now_ts or time.time()))


def today_text(now_ts: Optional[float] = None) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(now_ts or time.time()))


def append_jsonl(path: Path, payload: JsonDict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


def read_jsonl(path: Path) -> List[JsonDict]:
    if not path.exists():
        return []
    rows: List[JsonDict] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        rows.append(json.loads(line))
    return rows


def parse_hhmm(value: str) -> Tuple[int, int]:
    match = re.fullmatch(r"(\d{1,2}):(\d{2})", value.strip())
    if not match:
        raise EzvizError(f"Invalid time format: {value}. Expected HH:MM.")
    hour = int(match.group(1))
    minute = int(match.group(2))
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise EzvizError(f"Invalid time value: {value}.")
    return hour, minute


def normalize_hhmm(hour: int, minute: int) -> str:
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise EzvizError(f"Invalid time value: {hour}:{minute}.")
    return f"{hour:02d}:{minute:02d}"


def validate_time_window(start_time: str, end_time: str) -> Tuple[str, str]:
    start_hour, start_minute = parse_hhmm(start_time)
    end_hour, end_minute = parse_hhmm(end_time)
    if (start_hour, start_minute) >= (end_hour, end_minute):
        raise EzvizError("Schedule end time must be later than start time.")
    return normalize_hhmm(start_hour, start_minute), normalize_hhmm(end_hour, end_minute)


def extract_time_window(text: str) -> Optional[Tuple[str, str]]:
    colon_match = re.search(
        rf"(\d{{1,2}}:\d{{2}})\s*{TIME_RANGE_SEPARATORS}\s*(\d{{1,2}}:\d{{2}})",
        text,
    )
    if colon_match:
        return validate_time_window(colon_match.group(1), colon_match.group(2))

    chinese_match = re.search(
        rf"(\d{{1,2}})点(?:(\d{{1,2}})分?)?\s*{TIME_RANGE_SEPARATORS}\s*(\d{{1,2}})点(?:(\d{{1,2}})分?)?",
        text,
    )
    if chinese_match:
        start_hour = int(chinese_match.group(1))
        start_minute = int(chinese_match.group(2) or 0)
        end_hour = int(chinese_match.group(3))
        end_minute = int(chinese_match.group(4) or 0)
        return validate_time_window(
            normalize_hhmm(start_hour, start_minute),
            normalize_hhmm(end_hour, end_minute),
        )
    return None


def extract_single_time(text: str) -> Optional[str]:
    colon_matches = re.findall(r"(\d{1,2}:\d{2})", text)
    if colon_matches:
        hour, minute = parse_hhmm(colon_matches[-1])
        return normalize_hhmm(hour, minute)

    chinese_matches = re.findall(r"(\d{1,2})点(?:(\d{1,2})分?)?", text)
    if chinese_matches:
        hour_text, minute_text = chinese_matches[-1]
        hour = int(hour_text)
        minute = int(minute_text or 0)
        return normalize_hhmm(hour, minute)
    return None


def extract_interval_minutes(text: str) -> Optional[int]:
    patterns = [
        r"每隔\s*(\d{1,3})\s*分钟",
        r"每\s*(\d{1,3})\s*分钟(?:拍|录)",
        r"间隔\s*(\d{1,3})\s*分钟",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            minutes = int(match.group(1))
            if minutes <= 0:
                raise EzvizError("临时拍摄间隔必须大于 0 分钟。")
            return minutes
    return None


def extract_clip_duration_seconds(text: str) -> Optional[int]:
    second_match = re.search(r"拍\s*(\d{1,4})\s*秒", text)
    if second_match:
        seconds = int(second_match.group(1))
        if seconds <= 0:
            raise EzvizError("单条视频时长必须大于 0 秒。")
        return seconds

    minute_match = re.search(r"拍\s*(\d{1,3})\s*分钟", text)
    if minute_match:
        minutes = int(minute_match.group(1))
        if minutes <= 0:
            raise EzvizError("单条视频时长必须大于 0 秒。")
        return minutes * 60
    return None


def create_task_id() -> str:
    return time.strftime("task-%Y%m%d-%H%M%S")


def task_paths(task_root: Path) -> JsonDict:
    return {
        "root": str(task_root),
        "config_path": str(task_root / "task.json"),
        "events_path": str(task_root / "task-events.jsonl"),
        "report_path": str(task_root / "daily-report.md"),
    }


def save_task(task_path: Path, task: JsonDict) -> None:
    task["updated_at"] = now_text()
    task_path.write_text(json.dumps(task, ensure_ascii=False, indent=2), encoding="utf-8")


def load_task(task_path: Path) -> JsonDict:
    return json.loads(task_path.read_text(encoding="utf-8"))


def append_task_event(task: JsonDict, event: str, payload: JsonDict, now_ts: Optional[float] = None) -> Path:
    events_path = Path(task["artifacts"]["events_path"])
    record = {
        "timestamp": now_text(now_ts),
        "date": today_text(now_ts),
        "event": event,
        "payload": payload,
    }
    append_jsonl(events_path, record)
    return events_path


def init_task(
    *,
    task_root: Path,
    start_time: str,
    end_time: str,
    brief: str,
    task_name: str = "OpenClaw 视频拍摄任务",
    wake_word: str = "龙虾",
    max_shots: int = 4,
    merchant_daily_summary: bool = True,
    battery_reminder_threshold: int = DEFAULT_BATTERY_REMINDER_THRESHOLD,
    precheck_lead_minutes: int = DEFAULT_PRECHECK_LEAD_MINUTES,
) -> JsonDict:
    schedule_start, schedule_end = validate_time_window(start_time, end_time)
    task_root.mkdir(parents=True, exist_ok=True)
    task_id = create_task_id()
    paths = task_paths(task_root)
    task = {
        "task_id": task_id,
        "task_name": task_name,
        "created_at": now_text(),
        "updated_at": now_text(),
        "wake_word": wake_word,
        "merchant_rules": {
            "allowed_actions": [
                "set_capture_time",
                "start_custom_capture",
                "diagnose_capture_problem",
                "stop_capture",
            ],
            "forbidden_note": "商家只能通过唤醒词执行修改拍摄时间、排查拍摄问题、停止拍摄三类操作。",
        },
        "schedule": {
            "start_time": schedule_start,
            "end_time": schedule_end,
            "timezone": "Asia/Shanghai",
            "repeat": "daily",
        },
        "capture": {
            "brief": brief,
            "max_shots": max(1, min(max_shots, 4)),
        },
        "custom_capture": {
            "active": False,
            "start_at": None,
            "end_at": None,
            "interval_minutes": DEFAULT_CUSTOM_CAPTURE_INTERVAL_MINUTES,
            "clip_duration_seconds": DEFAULT_CUSTOM_CLIP_DURATION_SECONDS,
            "source_command": None,
        },
        "status": {
            "active": True,
            "stopped_at": None,
            "stop_reason": None,
        },
        "reporting": {
            "merchant_daily_summary": merchant_daily_summary,
        },
        "reminders": {
            "battery_threshold_percent": battery_reminder_threshold,
            "precheck_lead_minutes": precheck_lead_minutes,
        },
        "scheduler": build_scheduler_state(),
        "artifacts": paths,
    }
    task_path = Path(paths["config_path"])
    save_task(task_path, task)
    append_task_event(
        task,
        "task_created",
        {
            "task_name": task_name,
            "schedule": task["schedule"],
            "brief": brief,
            "max_shots": task["capture"]["max_shots"],
        },
    )
    return task


def first_boot_setup(
    *,
    task_root: Path,
    time_window_text: str,
    brief: str = "自动拍摄任务",
    task_name: str = "OpenClaw 视频拍摄任务",
    wake_word: str = "龙虾",
    max_shots: int = 4,
) -> JsonDict:
    window = extract_time_window(time_window_text)
    if not window:
        raise EzvizError("首次开机配置失败，未识别到拍摄时间段，请提供类似 11:00-12:00 的时间窗口。")
    return init_task(
        task_root=task_root,
        start_time=window[0],
        end_time=window[1],
        brief=brief,
        task_name=task_name,
        wake_word=wake_word,
        max_shots=max_shots,
    )


def custom_capture_is_due(task: JsonDict, now_ts: Optional[float] = None) -> bool:
    if not task["status"]["active"]:
        return False
    custom = task.get("custom_capture") or {}
    if not custom.get("active"):
        return False
    start_at = custom.get("start_at")
    end_at = custom.get("end_at")
    if not start_at or not end_at:
        return False
    current_ts = now_ts or time.time()
    start_ts = time.mktime(time.strptime(start_at, "%Y-%m-%d %H:%M:%S"))
    end_ts = time.mktime(time.strptime(end_at, "%Y-%m-%d %H:%M:%S"))
    return start_ts <= current_ts < end_ts


def current_capture_mode(task: JsonDict, now_ts: Optional[float] = None) -> str:
    if custom_capture_is_due(task, now_ts):
        return "custom_capture"
    if task_is_due(task, now_ts):
        return "recurring_daily"
    return "idle"


def task_is_due(task: JsonDict, now_ts: Optional[float] = None) -> bool:
    if not task["status"]["active"]:
        return False
    now_struct = time.localtime(now_ts or time.time())
    current = now_struct.tm_hour * 60 + now_struct.tm_min
    start_hour, start_minute = parse_hhmm(task["schedule"]["start_time"])
    end_hour, end_minute = parse_hhmm(task["schedule"]["end_time"])
    start_value = start_hour * 60 + start_minute
    end_value = end_hour * 60 + end_minute
    return start_value <= current < end_value


def should_run_now(task: JsonDict, now_ts: Optional[float] = None) -> JsonDict:
    mode = current_capture_mode(task, now_ts)
    due = mode != "idle"
    payload: JsonDict = {
        "task_id": task["task_id"],
        "should_run_now": due,
        "active": task["status"]["active"],
        "mode": mode,
    }
    if mode == "custom_capture":
        payload["custom_capture"] = task.get("custom_capture", {})
    else:
        payload["schedule"] = task["schedule"]
    payload["schedule_hint"] = build_schedule_hint(task, now_ts)
    return payload


def next_capture_start_ts(task: JsonDict, now_ts: Optional[float] = None) -> float:
    base_ts = now_ts or time.time()
    local = time.localtime(base_ts)
    start_hour, start_minute = parse_hhmm(task["schedule"]["start_time"])
    target = time.struct_time(
        (
            local.tm_year,
            local.tm_mon,
            local.tm_mday,
            start_hour,
            start_minute,
            0,
            local.tm_wday,
            local.tm_yday,
            local.tm_isdst,
        )
    )
    target_ts = time.mktime(target)
    if target_ts <= base_ts:
        target_ts += 24 * 3600
    return target_ts


def should_run_battery_precheck(task: JsonDict, now_ts: Optional[float] = None) -> bool:
    if not task["status"]["active"]:
        return False
    lead_minutes = int(task.get("reminders", {}).get("precheck_lead_minutes", DEFAULT_PRECHECK_LEAD_MINUTES) or DEFAULT_PRECHECK_LEAD_MINUTES)
    next_start = next_capture_start_ts(task, now_ts)
    remaining = next_start - (now_ts or time.time())
    return 0 <= remaining <= lead_minutes * 60


def battery_precheck(
    task_path: Path,
    *,
    env_file: Optional[str] = None,
    now_ts: Optional[float] = None,
    client_factory: Callable[[EnvConfig], EzvizClient] = EzvizClient,
) -> JsonDict:
    task = load_task(task_path)
    next_start_ts = next_capture_start_ts(task, now_ts)
    should_check = should_run_battery_precheck(task, now_ts)
    threshold = int(task.get("reminders", {}).get("battery_threshold_percent", DEFAULT_BATTERY_REMINDER_THRESHOLD) or DEFAULT_BATTERY_REMINDER_THRESHOLD)

    config = EnvConfig.from_env(env_file=env_file)
    client = client_factory(config)
    dump = client.dump_device()
    battery_percent = dump.get("battery", {}).get("battery_percent")

    needs_charge = isinstance(battery_percent, (int, float)) and battery_percent < threshold
    reminder_message = None
    if needs_charge:
        reminder_message = f"摄像头当前电量 {battery_percent}%，低于 {threshold}%，请商家在下次拍摄前充电。"
    elif should_check:
        reminder_message = f"摄像头当前电量 {battery_percent}%，高于 {threshold}%，下次拍摄前无需提醒充电。"

    payload = {
        "task_id": task["task_id"],
        "should_check_now": should_check,
        "next_capture_start": now_text(next_start_ts),
        "battery_threshold_percent": threshold,
        "battery_percent": battery_percent,
        "needs_charge_reminder": needs_charge,
        "reminder_message": reminder_message,
        "device_snapshot": dump,
    }
    append_task_event(task, "battery_precheck", payload, now_ts=now_ts)
    return payload


def next_window_text(task: JsonDict) -> str:
    return f"{task['schedule']['start_time']}-{task['schedule']['end_time']}"


def set_schedule(
    task_path: Path,
    *,
    start_time: str,
    end_time: str,
    source: str,
    raw_command: Optional[str] = None,
) -> JsonDict:
    task = load_task(task_path)
    schedule_start, schedule_end = validate_time_window(start_time, end_time)
    task["schedule"]["start_time"] = schedule_start
    task["schedule"]["end_time"] = schedule_end
    save_task(task_path, task)
    append_task_event(
        task,
        "schedule_updated",
        {
            "source": source,
            "raw_command": raw_command,
            "start_time": schedule_start,
            "end_time": schedule_end,
        },
    )
    return task


def mark_scheduler_installed(
    task_path: Path,
    *,
    automation_name: str,
    delivery_channel: Optional[str] = None,
    source: str = "openclaw",
) -> JsonDict:
    task = load_task(task_path)
    task.setdefault("scheduler", build_scheduler_state())
    task["scheduler"].update(
        {
            "required": True,
            "check_every_minutes": int(task["scheduler"].get("check_every_minutes", DEFAULT_SCHEDULER_CHECK_INTERVAL_MINUTES) or DEFAULT_SCHEDULER_CHECK_INTERVAL_MINUTES),
            "automation_created": True,
            "automation_name": automation_name,
            "delivery_channel": delivery_channel,
            "created_at": task["scheduler"].get("created_at") or now_text(),
            "updated_at": now_text(),
            "source": source,
        }
    )
    save_task(task_path, task)
    append_task_event(
        task,
        "scheduler_installed",
        {
            "automation_name": automation_name,
            "delivery_channel": delivery_channel,
            "source": source,
        },
    )
    return task


def stop_task(task_path: Path, *, source: str, reason: str) -> JsonDict:
    task = load_task(task_path)
    task["status"]["active"] = False
    task["status"]["stopped_at"] = now_text()
    task["status"]["stop_reason"] = reason
    task.setdefault("custom_capture", {})
    task["custom_capture"]["active"] = False
    save_task(task_path, task)
    append_task_event(
        task,
        "task_stopped",
        {
            "source": source,
            "reason": reason,
        },
    )
    return task


def resume_task(task_path: Path, *, source: str, reason: str) -> JsonDict:
    task = load_task(task_path)
    task["status"]["active"] = True
    task["status"]["stopped_at"] = None
    task["status"]["stop_reason"] = None
    save_task(task_path, task)
    append_task_event(
        task,
        "task_resumed",
        {
            "source": source,
            "reason": reason,
        },
    )
    return task


def set_custom_capture(
    task_path: Path,
    *,
    start_at_ts: float,
    end_time: str,
    interval_minutes: int,
    clip_duration_seconds: int,
    source: str,
    raw_command: Optional[str] = None,
) -> JsonDict:
    if interval_minutes <= 0:
        raise EzvizError("临时拍摄间隔必须大于 0 分钟。")
    if clip_duration_seconds <= 0:
        raise EzvizError("单条视频时长必须大于 0 秒。")

    task = load_task(task_path)
    local = time.localtime(start_at_ts)
    end_hour, end_minute = parse_hhmm(end_time)
    end_struct = time.struct_time(
        (
            local.tm_year,
            local.tm_mon,
            local.tm_mday,
            end_hour,
            end_minute,
            0,
            local.tm_wday,
            local.tm_yday,
            local.tm_isdst,
        )
    )
    end_ts = time.mktime(end_struct)
    if end_ts <= start_at_ts:
        raise EzvizError("临时拍摄结束时间必须晚于当前时间，请直接说今天拍到几点，例如 22:00。")

    task.setdefault("custom_capture", {})
    task["custom_capture"].update(
        {
            "active": True,
            "start_at": now_text(start_at_ts),
            "end_at": now_text(end_ts),
            "interval_minutes": interval_minutes,
            "clip_duration_seconds": clip_duration_seconds,
            "source_command": raw_command,
        }
    )
    save_task(task_path, task)
    append_task_event(
        task,
        "custom_capture_started",
        {
            "source": source,
            "raw_command": raw_command,
            "start_at": task["custom_capture"]["start_at"],
            "end_at": task["custom_capture"]["end_at"],
            "interval_minutes": interval_minutes,
            "clip_duration_seconds": clip_duration_seconds,
        },
        now_ts=start_at_ts,
    )
    return task


def parse_merchant_command(text: str, wake_word: str = "龙虾") -> JsonDict:
    normalized = text.strip()
    if wake_word not in normalized:
        return {
            "recognized": False,
            "reason": "missing_wake_word",
            "response": f"未检测到唤醒词“{wake_word}”，不执行任何操作。",
        }

    if "停止拍摄" in normalized:
        return {
            "recognized": True,
            "intent": "stop_capture",
        }

    if any(keyword in normalized for keyword in ("改一下拍摄时间", "修改拍摄时间", "改拍摄时间")):
        window = extract_time_window(normalized)
        if not window:
            return {
                "recognized": True,
                "intent": "set_capture_time",
                "missing": "time_window",
                "response": "已识别到修改拍摄时间请求，但没有找到新的时间段，请补充类似 11:00-12:00 的时间窗口。",
            }
        return {
            "recognized": True,
            "intent": "set_capture_time",
            "start_time": window[0],
            "end_time": window[1],
        }

    if any(keyword in normalized for keyword in ("帮我拍视频", "现在拍视频", "从现在开始拍视频", "拍多久的视频", "开始拍视频")):
        interval_minutes = extract_interval_minutes(normalized)
        clip_duration_seconds = extract_clip_duration_seconds(normalized) or DEFAULT_CUSTOM_CLIP_DURATION_SECONDS
        end_time = extract_single_time(normalized)
        missing: List[str] = []
        if interval_minutes is None:
            missing.append("interval_minutes")
        if end_time is None:
            missing.append("end_time")
        if missing:
            response_parts = ["已识别到临时拍摄请求。"]
            if "interval_minutes" in missing:
                response_parts.append("请补充每隔多久拍一条。")
            if "end_time" in missing:
                response_parts.append("再告诉我要拍到几点，小豆电量有限。")
            response_parts.append("例如：龙虾，从现在开始拍视频，每10分钟拍一次，拍到22:00。")
            return {
                "recognized": True,
                "intent": "start_custom_capture",
                "missing": missing,
                "clip_duration_seconds": clip_duration_seconds,
                "response": "".join(response_parts),
            }
        return {
            "recognized": True,
            "intent": "start_custom_capture",
            "interval_minutes": interval_minutes,
            "clip_duration_seconds": clip_duration_seconds,
            "end_time": end_time,
        }

    if any(keyword in normalized for keyword in ("怎么没有拍摄", "排查拍摄问题", "找找问题", "帮我找问题", "帮我排查")):
        return {
            "recognized": True,
            "intent": "diagnose_capture_problem",
        }

    return {
        "recognized": False,
        "reason": "unsupported_command",
        "response": "当前商家侧只支持修改拍摄时间、排查拍摄问题、停止拍摄三类指令。",
    }


def summarize_session(session_path: Path) -> JsonDict:
    session = json.loads(session_path.read_text(encoding="utf-8"))
    shots = session.get("shots", [])
    accepted = sum(1 for shot in shots if shot.get("validation", {}).get("status") == "accepted")
    abnormal = sum(1 for shot in shots if shot.get("validation", {}).get("status") == "abnormal")
    failed = sum(1 for shot in shots if shot.get("validation", {}).get("status") == "failed")
    captured = sum(1 for shot in shots if shot.get("status") == "captured")
    return {
        "session_id": session.get("session_id"),
        "storage_root": session.get("storage_root"),
        "brief": session.get("brief"),
        "planned_shot_count": len(shots),
        "captured_shot_count": captured,
        "accepted_shot_count": accepted,
        "abnormal_shot_count": abnormal,
        "failed_shot_count": failed,
        "workflow_report_path": session.get("workflow_artifacts", {}).get("report_path"),
    }


def record_session_result(
    task_path: Path,
    *,
    session_path: Path,
    uploaded_success_count: int = 0,
    uploaded_failed_count: int = 0,
    source: str = "openclaw",
    note: str = "",
) -> JsonDict:
    task = load_task(task_path)
    summary = summarize_session(session_path)
    status = "ok"
    if summary["failed_shot_count"] > 0:
        status = "failed"
    elif summary["abnormal_shot_count"] > 0:
        status = "partial"

    payload = {
        **summary,
        "status": status,
        "uploaded_success_count": uploaded_success_count,
        "uploaded_failed_count": uploaded_failed_count,
        "source": source,
        "note": note,
    }
    append_task_event(task, "session_recorded", payload)
    return payload


def summarize_status_samples(status_root: Optional[Path], report_date: str) -> JsonDict:
    if not status_root:
        return {}
    samples_path = status_root / "samples.jsonl"
    if not samples_path.exists():
        return {}

    rows = [
        row for row in read_jsonl(samples_path)
        if time.strftime("%Y-%m-%d", time.localtime(float(row.get("timestamp", 0) or 0))) == report_date
    ]
    if not rows:
        return {}

    batteries = [row.get("battery_percent") for row in rows if isinstance(row.get("battery_percent"), (int, float))]
    online_values = [row.get("device_online") for row in rows]
    offline_count = sum(1 for value in online_values if value == 0)
    return {
        "sample_count": len(rows),
        "battery_start": batteries[0] if batteries else None,
        "battery_end": batteries[-1] if batteries else None,
        "battery_min": min(batteries) if batteries else None,
        "battery_max": max(batteries) if batteries else None,
        "offline_sample_count": offline_count,
        "latest_sample": rows[-1],
    }


def render_daily_report(task: JsonDict, report_date: str, session_rows: Sequence[JsonDict], status_summary: JsonDict) -> str:
    total_sessions = len(session_rows)
    planned = sum(int(row["payload"].get("planned_shot_count", 0) or 0) for row in session_rows)
    captured = sum(int(row["payload"].get("captured_shot_count", 0) or 0) for row in session_rows)
    accepted = sum(int(row["payload"].get("accepted_shot_count", 0) or 0) for row in session_rows)
    abnormal = sum(int(row["payload"].get("abnormal_shot_count", 0) or 0) for row in session_rows)
    failed = sum(int(row["payload"].get("failed_shot_count", 0) or 0) for row in session_rows)
    uploaded_success = sum(int(row["payload"].get("uploaded_success_count", 0) or 0) for row in session_rows)
    uploaded_failed = sum(int(row["payload"].get("uploaded_failed_count", 0) or 0) for row in session_rows)

    lines = [
        "# OpenClaw 拍摄日报",
        "",
        f"- 任务名称：{task['task_name']}",
        f"- 日期：{report_date}",
        f"- 当前拍摄窗口：{task['schedule']['start_time']}-{task['schedule']['end_time']}",
        f"- 任务状态：{'运行中' if task['status']['active'] else '已停止'}",
        f"- 总执行次数：{total_sessions}",
        f"- 计划片段数：{planned}",
        f"- 实际拍摄片段数：{captured}",
        f"- 验片通过片段数：{accepted}",
        f"- 异常片段数：{abnormal}",
        f"- 失败片段数：{failed}",
        f"- 上传成功片段数：{uploaded_success}",
        f"- 上传失败片段数：{uploaded_failed}",
        "",
        "## 设备运行情况",
        "",
    ]

    if status_summary:
        lines.extend(
            [
                f"- 状态采样数：{status_summary.get('sample_count', 0)}",
                f"- 电量起点：{status_summary.get('battery_start', 'unknown')}",
                f"- 电量终点：{status_summary.get('battery_end', 'unknown')}",
                f"- 电量最低：{status_summary.get('battery_min', 'unknown')}",
                f"- 离线采样次数：{status_summary.get('offline_sample_count', 'unknown')}",
            ]
        )
    else:
        lines.append("- 未提供状态轮询日志，本日报未包含设备心跳摘要。")

    lines.extend(
        [
            "",
            "## 执行明细",
            "",
            "| 时间 | 状态 | 已拍片段 | 通过 | 上传成功 | 备注 |",
            "| --- | --- | ---: | ---: | ---: | --- |",
        ]
    )
    for row in session_rows:
        payload = row["payload"]
        lines.append(
            "| {time} | {status} | {captured} | {accepted} | {uploaded} | {note} |".format(
                time=row["timestamp"],
                status=payload.get("status", ""),
                captured=payload.get("captured_shot_count", 0),
                accepted=payload.get("accepted_shot_count", 0),
                uploaded=payload.get("uploaded_success_count", 0),
                note=str(payload.get("note", "")).replace("|", "/")[:80],
            )
        )
    return "\n".join(lines)


def daily_report(task_path: Path, *, report_date: Optional[str] = None, status_root: Optional[Path] = None) -> JsonDict:
    task = load_task(task_path)
    target_date = report_date or today_text()
    events = read_jsonl(Path(task["artifacts"]["events_path"]))
    session_rows = [row for row in events if row.get("date") == target_date and row.get("event") == "session_recorded"]
    status_summary = summarize_status_samples(status_root, target_date)
    report_text = render_daily_report(task, target_date, session_rows, status_summary)
    report_path = Path(task["artifacts"]["root"]) / f"daily-report-{target_date}.md"
    report_path.write_text(report_text, encoding="utf-8")
    return {
        "report_path": str(report_path),
        "date": target_date,
        "session_count": len(session_rows),
        "status_summary": status_summary,
    }


def build_schedule_hint(task: JsonDict, now_ts: Optional[float]) -> str:
    if custom_capture_is_due(task, now_ts):
        custom = task.get("custom_capture", {})
        return "当前在临时拍摄窗口内，计划拍到 {end_at}，每 {interval} 分钟拍一条。".format(
            end_at=custom.get("end_at", "unknown"),
            interval=custom.get("interval_minutes", DEFAULT_CUSTOM_CAPTURE_INTERVAL_MINUTES),
        )
    if task_is_due(task, now_ts):
        return "当前在拍摄时间窗口内。"
    if not task["status"]["active"]:
        return "当前任务已停止，不会自动执行拍摄。"
    custom = task.get("custom_capture") or {}
    if custom.get("active") and custom.get("end_at"):
        return "当前不在临时拍摄窗口内；每日自动拍摄窗口是 {daily}，最近一次临时拍摄计划截止到 {end_at}。".format(
            daily=next_window_text(task),
            end_at=custom["end_at"],
        )
    return f"当前不在拍摄窗口内，下一次自动拍摄窗口是每日 {next_window_text(task)}。"


def diagnose_task(
    task_path: Path,
    *,
    env_file: Optional[str] = None,
    now_ts: Optional[float] = None,
    client_factory: Callable[[EnvConfig], EzvizClient] = EzvizClient,
) -> JsonDict:
    task = load_task(task_path)
    events = read_jsonl(Path(task["artifacts"]["events_path"]))
    latest_session = next((row for row in reversed(events) if row.get("event") == "session_recorded"), None)
    issues: List[str] = []
    schedule_hint = build_schedule_hint(task, now_ts)
    scheduler = task.get("scheduler") or build_scheduler_state()

    if not scheduler.get("automation_created"):
        issues.append(
            "OpenClaw 还没有为这个任务创建周期检查定时器。首次配置后必须创建每 {minutes} 分钟执行一次的 should-run-now/battery-precheck 自动任务，否则到时间也不会自动拍摄。".format(
                minutes=scheduler.get("check_every_minutes", DEFAULT_SCHEDULER_CHECK_INTERVAL_MINUTES)
            )
        )
    elif not scheduler.get("delivery_channel"):
        issues.append("OpenClaw 周期任务虽然已登记，但没有记录 delivery channel。若运行日志出现 “Channel is required”，请显式设置 delivery.channel。")

    if not task["status"]["active"]:
        issues.append(f"任务已停止，停止原因：{task['status'].get('stop_reason') or '未记录'}。")

    device_snapshot: JsonDict = {}
    try:
        config = EnvConfig.from_env(env_file=env_file)
        client = client_factory(config)
        device_snapshot = client.dump_device()
        device_info = device_snapshot.get("device_info", {})
        battery = device_snapshot.get("battery", {})
        if device_info.get("status") != 1:
            issues.append("设备当前离线。")
        battery_percent = battery.get("battery_percent")
        if isinstance(battery_percent, (int, float)) and battery_percent <= 15:
            issues.append(f"设备当前电量较低，仅剩 {battery_percent}%。")
        if device_info.get("isEncrypt") == 1:
            issues.append("设备视频加密已开启，可能导致录制链路被拦截。")
    except Exception as exc:  # pragma: no cover - runtime path
        issues.append(f"当前无法读取设备状态：{exc}")

    if latest_session:
        latest_payload = latest_session["payload"]
        if latest_payload.get("status") == "failed":
            issues.append(
                "最近一次拍摄任务失败，失败片段数为 {count}。".format(
                    count=latest_payload.get("failed_shot_count", 0)
                )
            )
        elif latest_payload.get("status") == "partial":
            issues.append(
                "最近一次拍摄任务存在异常片段，异常片段数为 {count}。".format(
                    count=latest_payload.get("abnormal_shot_count", 0)
                )
            )
    else:
        issues.append("当前还没有任何拍摄执行记录。")

    if not issues:
        issues.append("未发现明显异常，任务配置、设备状态和最近一次拍摄记录均正常。")

    return {
        "ok": len([item for item in issues if "未发现明显异常" not in item]) == 0,
        "task_active": task["status"]["active"],
        "schedule": task["schedule"],
        "schedule_hint": schedule_hint,
        "scheduler": scheduler,
        "latest_session": latest_session,
        "device_snapshot": device_snapshot,
        "issues": issues,
    }


def merchant_command(task_path: Path, text: str, *, env_file: Optional[str] = None) -> JsonDict:
    task = load_task(task_path)
    parsed = parse_merchant_command(text, wake_word=task["wake_word"])
    append_task_event(
        task,
        "merchant_command",
        {
            "text": text,
            "recognized": parsed.get("recognized", False),
            "intent": parsed.get("intent"),
        },
    )

    if not parsed.get("recognized"):
        return parsed

    intent = parsed["intent"]
    if intent == "set_capture_time":
        if parsed.get("missing") == "time_window":
            return parsed
        updated_task = set_schedule(
            task_path,
            start_time=parsed["start_time"],
            end_time=parsed["end_time"],
            source="merchant_command",
            raw_command=text,
        )
        return {
            "recognized": True,
            "intent": intent,
            "schedule": updated_task["schedule"],
            "response": f"已把每日拍摄时间更新为 {parsed['start_time']}-{parsed['end_time']}。",
        }

    if intent == "stop_capture":
        stopped_task = stop_task(task_path, source="merchant_command", reason="merchant_requested_stop")
        return {
            "recognized": True,
            "intent": intent,
            "status": stopped_task["status"],
            "response": "已停止自动拍摄。后续如需恢复，请由后台或运维重新开启任务。",
        }

    if intent == "start_custom_capture":
        if parsed.get("missing"):
            return parsed
        updated_task = set_custom_capture(
            task_path,
            start_at_ts=time.time(),
            end_time=parsed["end_time"],
            interval_minutes=int(parsed["interval_minutes"]),
            clip_duration_seconds=int(parsed["clip_duration_seconds"]),
            source="merchant_command",
            raw_command=text,
        )
        custom = updated_task["custom_capture"]
        return {
            "recognized": True,
            "intent": intent,
            "custom_capture": custom,
            "response": "已开启临时拍摄：从现在开始，到 {end_at} 结束，每 {interval} 分钟拍一条，每条 {duration} 秒。原来的每日定时任务保持不变。".format(
                end_at=custom["end_at"],
                interval=custom["interval_minutes"],
                duration=custom["clip_duration_seconds"],
            ),
        }

    if intent == "diagnose_capture_problem":
        diagnosis = diagnose_task(task_path, env_file=env_file)
        return {
            "recognized": True,
            "intent": intent,
            "diagnosis": diagnosis,
            "response": "已完成拍摄问题排查，请查看 diagnosis 详情。",
        }

    return {
        "recognized": False,
        "reason": "unsupported_intent",
        "response": "当前不支持该商家指令。",
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage recurring OpenClaw capture tasks for EZVIZ cameras.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init-task", help="Create a recurring OpenClaw capture task.")
    init_parser.add_argument("--task-root", type=Path, required=True, help="Directory used to store task.json and task logs.")
    init_parser.add_argument("--start-time", required=True, help="Daily start time in HH:MM.")
    init_parser.add_argument("--end-time", required=True, help="Daily end time in HH:MM.")
    init_parser.add_argument("--brief", required=True, help="Capture brief used by the workflow.")
    init_parser.add_argument("--task-name", default="OpenClaw 视频拍摄任务")
    init_parser.add_argument("--wake-word", default="龙虾")
    init_parser.add_argument("--max-shots", type=int, default=4)

    first_boot_parser = subparsers.add_parser("first-boot-setup", help="首次开机时，只根据商家提供的拍摄时间段创建每日定时任务。")
    first_boot_parser.add_argument("--task-root", type=Path, required=True)
    first_boot_parser.add_argument("--time-window-text", required=True, help="商家口述的时间窗口，例如 11:00-12:00。")
    first_boot_parser.add_argument("--brief", default="自动拍摄任务")
    first_boot_parser.add_argument("--task-name", default="OpenClaw 视频拍摄任务")
    first_boot_parser.add_argument("--wake-word", default="龙虾")
    first_boot_parser.add_argument("--max-shots", type=int, default=4)

    status_parser = subparsers.add_parser("task-status", help="Show current task status and schedule.")
    status_parser.add_argument("--task", type=Path, required=True, help="Path to task.json.")

    scheduler_spec_parser = subparsers.add_parser("scheduler-spec", help="输出 OpenClaw 必须创建的周期检查任务配置。")
    scheduler_spec_parser.add_argument("--task", type=Path, required=True, help="Path to task.json.")

    scheduler_installed_parser = subparsers.add_parser("scheduler-installed", help="记录 OpenClaw 已经创建好周期检查任务。")
    scheduler_installed_parser.add_argument("--task", type=Path, required=True)
    scheduler_installed_parser.add_argument("--automation-name", required=True)
    scheduler_installed_parser.add_argument("--delivery-channel")
    scheduler_installed_parser.add_argument("--source", default="openclaw")

    set_parser = subparsers.add_parser("set-schedule", help="Update the daily capture window.")
    set_parser.add_argument("--task", type=Path, required=True)
    set_parser.add_argument("--start-time", required=True)
    set_parser.add_argument("--end-time", required=True)
    set_parser.add_argument("--source", default="backend")

    stop_parser = subparsers.add_parser("stop-task", help="Stop recurring captures.")
    stop_parser.add_argument("--task", type=Path, required=True)
    stop_parser.add_argument("--reason", default="manual_stop")
    stop_parser.add_argument("--source", default="backend")

    resume_parser = subparsers.add_parser("resume-task", help="Resume recurring captures.")
    resume_parser.add_argument("--task", type=Path, required=True)
    resume_parser.add_argument("--reason", default="manual_resume")
    resume_parser.add_argument("--source", default="backend")

    should_run_parser = subparsers.add_parser("should-run-now", help="Tell OpenClaw whether the task should run right now.")
    should_run_parser.add_argument("--task", type=Path, required=True)

    merchant_parser = subparsers.add_parser("merchant-command", help="Handle one merchant utterance under the strict command boundary.")
    merchant_parser.add_argument("--task", type=Path, required=True)
    merchant_parser.add_argument("--text", required=True)

    record_parser = subparsers.add_parser("record-session", help="Append one completed capture session to the task log.")
    record_parser.add_argument("--task", type=Path, required=True)
    record_parser.add_argument("--session", type=Path, required=True, help="Path to session.json produced by cb60_capture_workflow.py.")
    record_parser.add_argument("--uploaded-success-count", type=int, default=0)
    record_parser.add_argument("--uploaded-failed-count", type=int, default=0)
    record_parser.add_argument("--source", default="openclaw")
    record_parser.add_argument("--note", default="")

    daily_parser = subparsers.add_parser("daily-report", help="Generate a daily merchant-facing report.")
    daily_parser.add_argument("--task", type=Path, required=True)
    daily_parser.add_argument("--date", help="Date in YYYY-MM-DD. Defaults to today.")
    daily_parser.add_argument("--status-root", type=Path, help="Optional status monitor directory for battery and heartbeat summaries.")

    diagnose_parser = subparsers.add_parser("diagnose-task", help="Diagnose why scheduled capture may not be happening.")
    diagnose_parser.add_argument("--task", type=Path, required=True)

    precheck_parser = subparsers.add_parser("battery-precheck", help="在下次拍摄前一个固定提前量内检查设备电量，并判断是否需要提醒商家充电。")
    precheck_parser.add_argument("--task", type=Path, required=True)

    subparsers.add_parser("install-onboarding-message", help="输出安装完成后，OpenClaw 应自动发送给用户的资料采集说明。")
    subparsers.add_parser("workflow-spec", help="输出固定的视频命令规则、商家交互边界和每日定时任务约定。")

    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    try:
        normalized_argv, env_file = extract_env_file_arg(argv)
    except EzvizError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    parser = build_parser()
    args = parser.parse_args(normalized_argv)
    try:
        if args.command == "init-task":
            task = init_task(
                task_root=args.task_root,
                start_time=args.start_time,
                end_time=args.end_time,
                brief=args.brief,
                task_name=args.task_name,
                wake_word=args.wake_word,
                max_shots=args.max_shots,
            )
            emit_json(
                {
                    "task_path": task["artifacts"]["config_path"],
                    "events_path": task["artifacts"]["events_path"],
                    "schedule": task["schedule"],
                }
            )
            return 0

        if args.command == "first-boot-setup":
            task = first_boot_setup(
                task_root=args.task_root,
                time_window_text=args.time_window_text,
                brief=args.brief,
                task_name=args.task_name,
                wake_word=args.wake_word,
                max_shots=args.max_shots,
            )
            emit_json(
                {
                    "task_path": task["artifacts"]["config_path"],
                    "events_path": task["artifacts"]["events_path"],
                    "schedule": task["schedule"],
                    "scheduler": task["scheduler"],
                    "scheduler_spec": build_scheduler_spec(task),
                    "response": (
                        f"已记录商家拍摄时间 {task['schedule']['start_time']}-{task['schedule']['end_time']}。"
                        "接下来必须立即给 OpenClaw 创建周期检查定时任务，否则到时间不会自动拍摄。"
                    ),
                }
            )
            return 0

        if args.command == "task-status":
            task = load_task(args.task)
            emit_json(
                {
                    "task_id": task["task_id"],
                    "task_name": task["task_name"],
                    "status": task["status"],
                    "scheduler": task.get("scheduler", build_scheduler_state()),
                    "schedule": task["schedule"],
                    "custom_capture": task.get("custom_capture", {}),
                    **should_run_now(task),
                }
            )
            return 0

        if args.command == "scheduler-spec":
            task = load_task(args.task)
            emit_json(build_scheduler_spec(task, args.task))
            return 0

        if args.command == "scheduler-installed":
            task = mark_scheduler_installed(
                args.task,
                automation_name=args.automation_name,
                delivery_channel=args.delivery_channel,
                source=args.source,
            )
            emit_json(
                {
                    "task_path": str(args.task),
                    "scheduler": task["scheduler"],
                }
            )
            return 0

        if args.command == "set-schedule":
            task = set_schedule(
                args.task,
                start_time=args.start_time,
                end_time=args.end_time,
                source=args.source,
            )
            emit_json({"schedule": task["schedule"], "task_path": str(args.task)})
            return 0

        if args.command == "stop-task":
            task = stop_task(args.task, source=args.source, reason=args.reason)
            emit_json({"status": task["status"], "task_path": str(args.task)})
            return 0

        if args.command == "resume-task":
            task = resume_task(args.task, source=args.source, reason=args.reason)
            emit_json({"status": task["status"], "task_path": str(args.task)})
            return 0

        if args.command == "should-run-now":
            task = load_task(args.task)
            emit_json(should_run_now(task))
            return 0

        if args.command == "merchant-command":
            emit_json(merchant_command(args.task, args.text, env_file=env_file))
            return 0

        if args.command == "record-session":
            emit_json(
                record_session_result(
                    args.task,
                    session_path=args.session,
                    uploaded_success_count=args.uploaded_success_count,
                    uploaded_failed_count=args.uploaded_failed_count,
                    source=args.source,
                    note=args.note,
                )
            )
            return 0

        if args.command == "daily-report":
            emit_json(daily_report(args.task, report_date=args.date, status_root=args.status_root))
            return 0

        if args.command == "diagnose-task":
            emit_json(diagnose_task(args.task, env_file=env_file))
            return 0

        if args.command == "battery-precheck":
            emit_json(battery_precheck(args.task, env_file=env_file))
            return 0

        if args.command == "install-onboarding-message":
            emit_json(build_install_onboarding_message())
            return 0

        if args.command == "workflow-spec":
            emit_json(workflow_spec())
            return 0
    except EzvizError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
