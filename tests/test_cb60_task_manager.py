from __future__ import annotations

import json
import sys
import tempfile
import time
import unittest
from unittest import mock
from pathlib import Path

SCRIPT_DIR = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "ezviz-cb60-control"
    / "scripts"
)
sys.path.insert(0, str(SCRIPT_DIR))

from cb60_task_manager import (  # noqa: E402
    battery_precheck,
    build_install_onboarding_message,
    build_scheduler_spec,
    current_capture_mode,
    custom_capture_is_due,
    daily_report,
    diagnose_task,
    first_boot_setup,
    init_task,
    load_task,
    merchant_command,
    next_capture_start_ts,
    parse_merchant_command,
    record_session_result,
    mark_scheduler_installed,
    set_schedule,
    should_run_battery_precheck,
    should_run_now,
    task_is_due,
    today_text,
    workflow_spec,
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
            self.assertEqual(task["reminders"]["battery_threshold_percent"], 85)
            self.assertEqual(task["reminders"]["precheck_lead_minutes"], 60)

    def test_first_boot_setup_only_needs_time_window_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            task = first_boot_setup(
                task_root=Path(tmp),
                time_window_text="我希望 11:00-12:00 拍",
            )
            self.assertEqual(task["schedule"]["start_time"], "11:00")
            self.assertEqual(task["schedule"]["end_time"], "12:00")
            self.assertTrue(task["scheduler"]["required"])
            self.assertFalse(task["scheduler"]["automation_created"])

    def test_build_scheduler_spec_freezes_openclaw_polling_contract(self):
        with tempfile.TemporaryDirectory() as tmp:
            task = init_task(
                task_root=Path(tmp),
                start_time="11:00",
                end_time="12:00",
                brief="门头",
            )
            spec = build_scheduler_spec(task)
            self.assertTrue(spec["required"])
            self.assertEqual(spec["check_every_minutes"], 10)
            self.assertTrue(spec["requires_delivery_channel"])
            self.assertIn("should-run-now", spec["commands"]["should_run_now"])

    def test_mark_scheduler_installed_persists_delivery_channel(self):
        with tempfile.TemporaryDirectory() as tmp:
            task = init_task(
                task_root=Path(tmp),
                start_time="11:00",
                end_time="12:00",
                brief="门头",
            )
            task_path = Path(task["artifacts"]["config_path"])
            updated = mark_scheduler_installed(
                task_path,
                automation_name="doudou_camera_shot_check",
                delivery_channel="main-session-channel",
            )
            self.assertTrue(updated["scheduler"]["automation_created"])
            self.assertEqual(updated["scheduler"]["delivery_channel"], "main-session-channel")

    def test_parse_merchant_command_supports_schedule_change_and_stop(self):
        parsed = parse_merchant_command("龙虾，帮我改一下拍摄时间 11:00-12:00")
        self.assertTrue(parsed["recognized"])
        self.assertEqual(parsed["intent"], "set_capture_time")
        self.assertEqual(parsed["start_time"], "11:00")
        self.assertEqual(parsed["end_time"], "12:00")

        parsed_stop = parse_merchant_command("龙虾，停止拍摄")
        self.assertEqual(parsed_stop["intent"], "stop_capture")

    def test_parse_merchant_command_supports_custom_capture_and_missing_follow_up(self):
        parsed = parse_merchant_command("龙虾，从现在开始拍视频，每10分钟拍一次，拍到22:00")
        self.assertTrue(parsed["recognized"])
        self.assertEqual(parsed["intent"], "start_custom_capture")
        self.assertEqual(parsed["interval_minutes"], 10)
        self.assertEqual(parsed["end_time"], "22:00")
        self.assertEqual(parsed["clip_duration_seconds"], 20)

        missing = parse_merchant_command("龙虾，帮我拍视频")
        self.assertTrue(missing["recognized"])
        self.assertEqual(missing["intent"], "start_custom_capture")
        self.assertIn("interval_minutes", missing["missing"])
        self.assertIn("end_time", missing["missing"])
        self.assertIn("拍到几点", missing["response"])

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

    def test_merchant_command_can_start_custom_capture_without_changing_daily_schedule(self):
        with tempfile.TemporaryDirectory() as tmp:
            task = init_task(
                task_root=Path(tmp),
                start_time="11:00",
                end_time="12:00",
                brief="门头",
            )
            task_path = Path(task["artifacts"]["config_path"])
            original_window = dict(task["schedule"])
            with mock.patch("cb60_task_manager.time.time", return_value=time.mktime(time.strptime("2026-04-15 20:30:00", "%Y-%m-%d %H:%M:%S"))):
                result = merchant_command(task_path, "龙虾，从现在开始拍视频，每10分钟拍一次，拍到22:00")
            self.assertTrue(result["recognized"])
            self.assertEqual(result["intent"], "start_custom_capture")
            reloaded = load_task(task_path)
            self.assertEqual(reloaded["schedule"], original_window)
            self.assertTrue(reloaded["custom_capture"]["active"])
            self.assertEqual(reloaded["custom_capture"]["interval_minutes"], 10)
            self.assertEqual(reloaded["custom_capture"]["clip_duration_seconds"], 20)

    def test_should_run_now_prefers_custom_capture_when_custom_window_is_active(self):
        with tempfile.TemporaryDirectory() as tmp:
            task = init_task(
                task_root=Path(tmp),
                start_time="11:00",
                end_time="12:00",
                brief="门头",
            )
            task_path = Path(task["artifacts"]["config_path"])
            start_ts = time.mktime(time.strptime("2026-04-15 20:30:00", "%Y-%m-%d %H:%M:%S"))
            with mock.patch("cb60_task_manager.time.time", return_value=start_ts):
                merchant_command(task_path, "龙虾，从现在开始拍视频，每10分钟拍一次，拍到22:00")
            reloaded = load_task(task_path)
            active_ts = time.mktime(time.strptime("2026-04-15 21:00:00", "%Y-%m-%d %H:%M:%S"))
            self.assertTrue(custom_capture_is_due(reloaded, now_ts=active_ts))
            self.assertEqual(current_capture_mode(reloaded, now_ts=active_ts), "custom_capture")
            payload = should_run_now(reloaded, now_ts=active_ts)
            self.assertTrue(payload["should_run_now"])
            self.assertEqual(payload["mode"], "custom_capture")
            self.assertIn("custom_capture", payload)

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
            self.assertIn("周期检查定时器", joined)
            self.assertIn("10%", joined)
            self.assertIn("最近一次拍摄任务失败", joined)

    def test_diagnose_task_reports_missing_delivery_channel_after_scheduler_install(self):
        with tempfile.TemporaryDirectory() as tmp:
            task = init_task(
                task_root=Path(tmp) / "task",
                start_time="11:00",
                end_time="12:00",
                brief="门头",
            )
            task_path = Path(task["artifacts"]["config_path"])
            mark_scheduler_installed(task_path, automation_name="doudou_camera_shot_check")

            class FakeClient:
                def __init__(self, config):
                    self.config = config

                def dump_device(self):
                    return {
                        "device_info": {"status": 1, "isEncrypt": 0},
                        "battery": {"battery_percent": 88},
                    }

            diagnosis = diagnose_task(task_path, client_factory=FakeClient)
            joined = " ".join(diagnosis["issues"])
            self.assertIn("delivery channel", joined)

    def test_should_run_battery_precheck_checks_one_hour_before_window(self):
        with tempfile.TemporaryDirectory() as tmp:
            task = init_task(
                task_root=Path(tmp) / "task",
                start_time="11:00",
                end_time="12:00",
                brief="门头",
            )
            task_path = Path(task["artifacts"]["config_path"])
            loaded = load_task(task_path)
            inside_window = time.mktime(time.strptime("2026-04-01 10:20:00", "%Y-%m-%d %H:%M:%S"))
            outside_window = time.mktime(time.strptime("2026-04-01 09:30:00", "%Y-%m-%d %H:%M:%S"))
            self.assertTrue(should_run_battery_precheck(loaded, now_ts=inside_window))
            self.assertFalse(should_run_battery_precheck(loaded, now_ts=outside_window))

            next_start = next_capture_start_ts(loaded, now_ts=inside_window)
            self.assertEqual(time.strftime("%H:%M", time.localtime(next_start)), "11:00")

    def test_battery_precheck_requests_charge_below_threshold(self):
        with tempfile.TemporaryDirectory() as tmp:
            task = init_task(
                task_root=Path(tmp) / "task",
                start_time="11:00",
                end_time="12:00",
                brief="门头",
            )

            class FakeClient:
                def __init__(self, config):
                    self.config = config

                def dump_device(self):
                    return {
                        "device_info": {"status": 1},
                        "battery": {"battery_percent": 72},
                    }

            inside_window = time.mktime(time.strptime("2026-04-01 10:20:00", "%Y-%m-%d %H:%M:%S"))
            payload = battery_precheck(
                Path(task["artifacts"]["config_path"]),
                now_ts=inside_window,
                client_factory=FakeClient,
            )
            self.assertTrue(payload["should_check_now"])
            self.assertTrue(payload["needs_charge_reminder"])
            self.assertIn("请商家在下次拍摄前充电", payload["reminder_message"])

    def test_workflow_spec_freezes_daily_recurring_rules(self):
        spec = workflow_spec()
        self.assertEqual(spec["plugin_contract"]["mode"], "recurring_daily_capture")
        self.assertIn("每日", spec["plugin_contract"]["default_repeat_policy"])
        self.assertTrue(spec["installation_onboarding"]["send_message_after_install"])
        self.assertEqual(spec["merchant_onboarding"]["first_question"], "你希望这个摄像头在什么时候拍？")
        self.assertEqual(spec["capture_command_rules"]["default_live_chain"]["quality"], 1)
        self.assertIn("高光剪辑 -> 去水印 -> 变高清", " ".join(spec["capture_command_rules"]["workflow_defaults"]))
        self.assertEqual(spec["custom_capture_rules"]["default_clip_duration_seconds"], 20)
        self.assertTrue(spec["custom_capture_rules"]["coexist_with_recurring_daily_schedule"])

    def test_install_onboarding_message_lists_required_fields(self):
        payload = build_install_onboarding_message()
        self.assertTrue(payload["send_after_install"])
        self.assertIn("EZVIZ_APP_KEY", payload["required_fields"])
        self.assertIn("TOS_ORIGINAL", payload["required_fields"])
        self.assertIn("TOS_FINAL", payload["required_fields"])
        self.assertEqual(payload["next_question"], "你希望这个摄像头在什么时候拍？")
        self.assertIn("store1_jsspa_original", payload["message_text"])


if __name__ == "__main__":
    unittest.main()
