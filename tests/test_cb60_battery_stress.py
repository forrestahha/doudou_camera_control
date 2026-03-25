from __future__ import annotations

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

from cb60_battery_stress import (  # noqa: E402
    StressArtifacts,
    compute_hourly_drain,
    diagnose_stream_failure,
    estimate_remaining_hours,
    keep_stream_alive_once,
    render_report,
    run_stress_test,
)
from ezviz_cb60_control import EnvConfig, EzvizClient, EzvizError  # noqa: E402


class BatteryStressTests(unittest.TestCase):
    def test_compute_hourly_drain(self):
        samples = [
            {"timestamp": 0.0, "battery_percent": 100},
            {"timestamp": 3600.0, "battery_percent": 92},
        ]
        self.assertEqual(compute_hourly_drain(samples), 8.0)

    def test_estimate_remaining_hours(self):
        samples = [
            {"timestamp": 0.0, "battery_percent": 100},
            {"timestamp": 3600.0, "battery_percent": 90},
        ]
        self.assertEqual(estimate_remaining_hours(samples), 9.0)

    def test_keep_stream_alive_once_downloads_playlist_and_segments(self):
        blobs = {
            "https://demo/live.m3u8": b"#EXTM3U\n#EXTINF:2.0,\nseg-1.ts\n#EXTINF:2.0,\nseg-2.ts\n",
            "https://demo/seg-1.ts": b"A" * 10,
            "https://demo/seg-2.ts": b"B" * 20,
        }

        def fake_fetcher(url: str, timeout: float) -> bytes:
            return blobs[url]

        stats = keep_stream_alive_once("https://demo/live.m3u8", 5.0, fetcher=fake_fetcher)
        self.assertEqual(stats["segments_downloaded"], 2)
        self.assertGreater(stats["bytes_downloaded"], 30)

    def test_render_report_writes_markdown(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = Path(tmp) / "report.md"
            render_report(
                report,
                config=EnvConfig(device_serial="device-1", channel_no=1, manual_live_url="https://demo/live.m3u8"),
                samples=[
                    {"timestamp": 0.0, "battery_percent": 100, "signal": 100, "cloudStatus": 2, "privacyStatus": 0},
                    {"timestamp": 3600.0, "battery_percent": 90, "signal": 99, "cloudStatus": 2, "privacyStatus": 0},
                ],
                started_at=0.0,
                stream_url="https://demo/live.m3u8",
                last_stream_stats={"segments_downloaded": 2},
            )
            content = report.read_text()
            self.assertIn("Estimated hourly drain", content)
            self.assertIn("https://demo/live.m3u8", content)

    def test_stress_artifacts_create_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            artifacts = StressArtifacts.create(Path(tmp) / "artifacts")
            self.assertTrue(artifacts.root.exists())
            self.assertEqual(artifacts.report_path.name, "report.md")

    def test_diagnose_stream_failure_reads_device_dump(self):
        class StubClient:
            def dump_device(self, channel_no=None):
                return {
                    "device_info": {"status": 1, "defence": 1, "signal": "100%"},
                    "device_status": {"cloudStatus": 2, "privacyStatus": 0, "signal": 100},
                    "battery": {"battery_percent": 88},
                }

        diagnosis = diagnose_stream_failure(StubClient())
        self.assertTrue(diagnosis["ok"])
        self.assertEqual(diagnosis["battery_percent"], 88)
        self.assertEqual(diagnosis["device_online"], 1)

    def test_run_stress_test_recovers_by_refreshing_stream(self):
        fetch_calls = []
        state = {"failed_once": False}
        playlist = b"#EXTM3U\n#EXTINF:2.0,\nseg-1.ts\n"
        blobs = {
            "https://new/live.m3u8": playlist,
            "https://new/seg-1.ts": b"A" * 10,
        }

        def fake_fetcher(url: str, timeout: float) -> bytes:
            fetch_calls.append(url)
            if url == "https://old/live.m3u8" and not state["failed_once"]:
                state["failed_once"] = True
                raise TimeoutError("timed out")
            return blobs[url]

        old_get_live_url = EzvizClient.get_live_url
        old_dump_device = EzvizClient.dump_device
        old_sleep = __import__("time").sleep
        try:
            EzvizClient.get_live_url = lambda self, source=None, protocol_id=None: {  # type: ignore[method-assign]
                "stream_url": "https://new/live.m3u8",
                "path": "/api/lapp/v2/live/address/get",
            }
            EzvizClient.dump_device = lambda self, channel_no=None: {  # type: ignore[method-assign]
                "device_info": {"status": 1, "defence": 1, "signal": "100%"},
                "device_status": {"cloudStatus": 2, "privacyStatus": 0, "signal": 100},
                "battery": {"battery_percent": 95},
            }
            __import__("time").sleep = lambda _: None

            with tempfile.TemporaryDirectory() as tmp:
                artifacts = StressArtifacts.create(Path(tmp) / "artifacts")
                payload = run_stress_test(
                    config=EnvConfig(access_token="t", device_serial="d", timeout_seconds=1),
                    artifacts=artifacts,
                    stream_url="https://old/live.m3u8",
                    sample_interval_seconds=999,
                    keepalive_interval_seconds=999,
                    max_hours=0.00001,
                    fetcher=fake_fetcher,
                    source="device",
                )
                self.assertIsNotNone(payload["last_recovery"])
                self.assertEqual(payload["last_recovery"]["action"], "stream_refreshed")
                self.assertEqual(payload["final_stream_url"], "https://new/live.m3u8")
        finally:
            EzvizClient.get_live_url = old_get_live_url  # type: ignore[method-assign]
            EzvizClient.dump_device = old_dump_device  # type: ignore[method-assign]
            __import__("time").sleep = old_sleep


if __name__ == "__main__":
    unittest.main()
