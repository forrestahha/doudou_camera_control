from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT_DIR = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "ezviz-cb60-control"
    / "scripts"
)
sys.path.insert(0, str(SCRIPT_DIR))

from cb60_status_monitor import (  # noqa: E402
    MonitorArtifacts,
    normalize_status_dump,
    render_report,
    run_monitor,
)
from ezviz_cb60_control import EnvConfig  # noqa: E402


class StatusMonitorTests(unittest.TestCase):
    def test_normalize_status_dump_extracts_key_fields(self):
        payload = normalize_status_dump(
            {
                "device_info": {"status": 1, "defence": 1, "netType": "4G", "signal": "100%"},
                "device_status": {"signal": 100, "cloudStatus": 2, "privacyStatus": 0},
                "battery": {"battery_percent": 88},
            }
        )
        self.assertEqual(payload["device_online"], 1)
        self.assertEqual(payload["battery_percent"], 88)
        self.assertEqual(payload["signal"], 100)
        self.assertEqual(payload["netType"], "4G")

    def test_render_report_writes_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = Path(tmp) / "report.md"
            render_report(
                report,
                config=EnvConfig(access_token="t", device_serial="d"),
                interval_seconds=60,
                started_at=0.0,
                samples=[
                    {
                        "timestamp": 0.0,
                        "device_online": 1,
                        "battery_percent": 99,
                        "signal": 100,
                        "cloudStatus": 2,
                        "privacyStatus": 0,
                        "netType": "4G",
                        "defence": 1,
                    }
                ],
                last_error=None,
                consecutive_errors=0,
            )
            content = report.read_text()
            self.assertIn("CB60 Status Monitor Report", content)
            self.assertIn("Poll interval: 60s", content)
            self.assertIn("Battery: 99", content)
            self.assertIn("Consecutive errors: 0", content)

    def test_run_monitor_collects_samples(self):
        with tempfile.TemporaryDirectory() as tmp:
            import cb60_status_monitor as monitor

            class FakeClient:
                def __init__(self, config):
                    self.calls = 0

                def dump_device(self):
                    self.calls += 1
                    return {
                        "device_info": {"status": 1, "defence": 1, "netType": "4G", "signal": "100%"},
                        "device_status": {"signal": 100, "cloudStatus": 2, "privacyStatus": 0},
                        "battery": {"battery_percent": 90},
                    }

            old_client = monitor.EzvizClient
            old_sleep = monitor.time.sleep
            try:
                monitor.EzvizClient = FakeClient
                monitor.time.sleep = lambda _seconds: None
                payload = run_monitor(
                    config=EnvConfig(access_token="t", device_serial="d"),
                    artifacts=MonitorArtifacts.create(Path(tmp)),
                    interval_seconds=60,
                    max_rounds=2,
                )
            finally:
                monitor.EzvizClient = old_client
                monitor.time.sleep = old_sleep

            self.assertEqual(payload["sample_count"], 2)
            self.assertTrue(Path(payload["report_path"]).exists())
            self.assertTrue(Path(payload["samples_path"]).exists())
            self.assertTrue(Path(payload["csv_path"]).exists())
            self.assertTrue(Path(payload["events_path"]).exists())
            lines = Path(payload["samples_path"]).read_text().strip().splitlines()
            self.assertEqual(len(lines), 2)
            first = json.loads(lines[0])
            self.assertEqual(first["battery_percent"], 90)

    def test_run_monitor_tolerates_single_transient_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            import cb60_status_monitor as monitor

            class FakeClient:
                def __init__(self, config):
                    self.calls = 0

                def dump_device(self):
                    self.calls += 1
                    if self.calls == 1:
                        raise RuntimeError("49999: 数据异常")
                    return {
                        "device_info": {"status": 1, "defence": 1, "netType": "4G", "signal": "100%"},
                        "device_status": {"signal": 100, "cloudStatus": 2, "privacyStatus": 0},
                        "battery": {"battery_percent": 91},
                    }

            old_client = monitor.EzvizClient
            old_sleep = monitor.time.sleep
            try:
                monitor.EzvizClient = FakeClient
                monitor.time.sleep = lambda _seconds: None
                payload = run_monitor(
                    config=EnvConfig(access_token="t", device_serial="d"),
                    artifacts=MonitorArtifacts.create(Path(tmp)),
                    interval_seconds=60,
                    max_rounds=2,
                    max_consecutive_errors=3,
                )
            finally:
                monitor.EzvizClient = old_client
                monitor.time.sleep = old_sleep

            self.assertEqual(payload["sample_count"], 1)
            self.assertEqual(payload["consecutive_errors"], 0)
            events = Path(payload["events_path"]).read_text()
            self.assertIn("status_error", events)
            self.assertIn("status_sample", events)


if __name__ == "__main__":
    unittest.main()
