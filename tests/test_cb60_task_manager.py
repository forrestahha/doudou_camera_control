from __future__ import annotations

import json
import sys
import tempfile
import time
import unittest
from pathlib import Path

SCRIPT_DIR = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "ezviz-cb60-control"
    / "scripts"
)
sys.path.insert(0, str(SCRIPT_DIR))

from cb60_task_manager import (  # noqa: E402
    daily_report,
    diagnose_task,
    init_task,
    load_task,
    merchant_command,
    parse_merchant_command,
    record_session_result,
    set_schedule,
    task_is_due,
    today_text,
)


class TaskManagerTests(unittest.TestCase):
    def create_session_file(self, root: Path) -> Path:
        session_dir = root / "session-1"
        session_dir.mkdir(parents=True, exist_ok=True)
        session = {
            "session_id": "session-1",
            "storage_root": str(session_dir),
            "brief": "门头, 店内全景",
            "workflow_artifacts": {
                "report_path": str(session_dir / "capture-report.md"),
            },
            "shots": [
                {
                    "index": 1,
                    "status": "captured",
                    "validation": {"status": "accepted"},
                },
                {
                    "index": 2,
                    "status": "captured",
                    "validation": {"status": "failed"},
                },
            ],
        }
        session_path = session_dir / "session.json"
        session_path.write_text(json.dumps(session, ensure_ascii=False, indent=2), encoding="utf-8")
        return session_path

    def test_init_task_creates_task_and_event_log(self):
        with tempfile.TemporaryDirectory() as tmp:
            task = init_task(
                task_root=Path(tmp),
                start_time="11:00",
                end_time="12:00",
                brief="门头, 店内全景",
            )
            self.assertEqual(task["schedule"]["start_time"], "11:00")
            self.assertTrue(Path(task["artifacts"]["config_path"]).exists())
            self.assertTrue(Path(task["artifacts"]["events_path"]).exists())

    def test_parse_merchant_command_supports_schedule_change_and_stop(self):
        parsed = parse_merchant_command("龙虾，帮我改一下拍摄时间 11:00-12:00")
        self.assertTrue(parsed["recognized"])
        self.assertEqual(parsed["intent"], "set_capture_time")
        self.assertEqual(parsed["start_time"], "11:00")
        self.assertEqual(parsed["end_time"], "12:00")

        parsed_stop = parse_merchant_command("龙虾，停止拍摄")
        self.assertEqual(parsed_stop["intent"], "stop_capture")

    def test_merchant_command_rejects_unsupported_scope(self):
        with tempfile.TemporaryDirectory() as tmp:
            task = init_task(
                task_root=Path(tmp),
                start_time="11:00",
                end_time="12:00",
                brief="门头",
            )
            result = merchant_command(Path(task["artifacts"]["config_path"]), "龙虾，给我播放音乐")
            self.assertFalse(result["recognized"])
            self.assertEqual(result["reason"], "unsupported_command")

    def test_set_schedule_updates_task_and_should_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            task = init_task(
                task_root=Path(tmp),
                start_time="11:00",
                end_time="12:00",
                brief="门头",
            )
            task_path = Path(task["artifacts"]["config_path"])
            updated = set_schedule(task_path, start_time="13:00", end_time="14:30", source="backend")
            self.assertEqual(updated["schedule"]["start_time"], "13:00")
            self.assertEqual(updated["schedule"]["end_time"], "14:30")

            reloaded = load_task(task_path)
            due_time = time.mktime(time.strptime("2026-04-01 13:10:00", "%Y-%m-%d %H:%M:%S"))
            not_due_time = time.mktime(time.strptime("2026-04-01 12:00:00", "%Y-%m-%d %H:%M:%S"))
            self.assertTrue(task_is_due(reloaded, now_ts=due_time))
            self.assertFalse(task_is_due(reloaded, now_ts=not_due_time))

    def test_record_session_and_daily_report_aggregate_counts(self):
        with tempfile.TemporaryDirectory() as tmp:
            task = init_task(
                task_root=Path(tmp) / "task",
                start_time="11:00",
                end_time="12:00",
                brief="门头",
            )
            session_path = self.create_session_file(Path(tmp))
            payload = record_session_result(
                Path(task["artifacts"]["config_path"]),
                session_path=session_path,
                uploaded_success_count=1,
                uploaded_failed_count=1,
            )
            self.assertEqual(payload["captured_shot_count"], 2)
            self.assertEqual(payload["accepted_shot_count"], 1)
            self.assertEqual(payload["failed_shot_count"], 1)

            status_root = Path(tmp) / "status"
            status_root.mkdir(parents=True, exist_ok=True)
            target_date = today_text()
            sample_1_ts = time.time()
            sample_2_ts = sample_1_ts + 3600
            (status_root / "samples.jsonl").write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "timestamp": sample_1_ts,
                                "device_online": 1,
                                "battery_percent": 90,
                            },
                            ensure_ascii=False,
                        ),
                        json.dumps(
                            {
                                "timestamp": sample_2_ts,
                                "device_online": 0,
                                "battery_percent": 82,
                            },
                            ensure_ascii=False,
                        ),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            report = daily_report(
                Path(task["artifacts"]["config_path"]),
                report_date=target_date,
                status_root=status_root,
            )
            self.assertTrue(Path(report["report_path"]).exists())
            report_text = Path(report["report_path"]).read_text(encoding="utf-8")
            self.assertIn("上传成功片段数：1", report_text)
            self.assertIn("离线采样次数：1", report_text)

    def test_diagnose_task_reports_low_battery_and_latest_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            task = init_task(
                task_root=Path(tmp) / "task",
                start_time="11:00",
                end_time="12:00",
                brief="门头",
            )
            session_path = self.create_session_file(Path(tmp))
            record_session_result(Path(task["artifacts"]["config_path"]), session_path=session_path)

            class FakeClient:
                def __init__(self, config):
                    self.config = config

                def dump_device(self):
                    return {
                        "device_info": {"status": 1, "isEncrypt": 0},
                        "battery": {"battery_percent": 10},
                    }

            diagnosis = diagnose_task(
                Path(task["artifacts"]["config_path"]),
                client_factory=FakeClient,
            )
            joined = " ".join(diagnosis["issues"])
            self.assertIn("10%", joined)
            self.assertIn("最近一次拍摄任务失败", joined)


if __name__ == "__main__":
    unittest.main()
