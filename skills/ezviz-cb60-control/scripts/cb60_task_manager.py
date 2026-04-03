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


def emit_json(payload: JsonDict) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


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
        "status": {
            "active": True,
            "stopped_at": None,
            "stop_reason": None,
        },
        "reporting": {
            "merchant_daily_summary": merchant_daily_summary,
        },
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


def stop_task(task_path: Path, *, source: str, reason: str) -> JsonDict:
    task = load_task(task_path)
    task["status"]["active"] = False
    task["status"]["stopped_at"] = now_text()
    task["status"]["stop_reason"] = reason
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
    if task_is_due(task, now_ts):
        return "当前在拍摄时间窗口内。"
    if not task["status"]["active"]:
        return "当前任务已停止，不会自动执行拍摄。"
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

    status_parser = subparsers.add_parser("task-status", help="Show current task status and schedule.")
    status_parser.add_argument("--task", type=Path, required=True, help="Path to task.json.")

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

        if args.command == "task-status":
            task = load_task(args.task)
            emit_json(
                {
                    "task_id": task["task_id"],
                    "task_name": task["task_name"],
                    "status": task["status"],
                    "schedule": task["schedule"],
                    "should_run_now": task_is_due(task),
                    "schedule_hint": build_schedule_hint(task, None),
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
            emit_json(
                {
                    "task_id": task["task_id"],
                    "should_run_now": task_is_due(task),
                    "schedule_hint": build_schedule_hint(task, None),
                    "active": task["status"]["active"],
                }
            )
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
    except EzvizError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
