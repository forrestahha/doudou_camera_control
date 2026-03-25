from __future__ import annotations

import json
import subprocess
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

from cb60_capture_workflow import (  # noqa: E402
    build_rotated_mp4_command,
    build_raw_recording_path,
    capture_next_shot,
    get_next_pending_shot,
    init_session,
    load_session,
    mark_shot_captured,
    plan_shots,
    record_flv_clip,
    record_hls_clip,
    session_summary,
    stream_url_path,
)
from ezviz_cb60_control import EnvConfig  # noqa: E402


class WorkflowTests(unittest.TestCase):
    def test_build_rotated_mp4_command_targets_portrait_output(self):
        command = build_rotated_mp4_command(
            "/opt/homebrew/bin/ffmpeg",
            Path("/tmp/input.flv"),
            Path("/tmp/output.mp4"),
            rotation_mode="cw90",
        )
        joined = " ".join(command)
        self.assertIn("transpose=1", joined)
        self.assertIn("0:a?", joined)
        self.assertEqual(command[-1], "/tmp/output.mp4")

    def test_build_rotated_mp4_command_supports_flip_180(self):
        command = build_rotated_mp4_command(
            "/opt/homebrew/bin/ffmpeg",
            Path("/tmp/input.flv"),
            Path("/tmp/output.mp4"),
            rotation_mode="flip180",
        )
        joined = " ".join(command)
        self.assertIn("hflip,vflip", joined)

    def test_build_raw_recording_path_uses_flv_for_flv_streams(self):
        self.assertTrue(
            str(build_raw_recording_path(Path("/tmp/demo.ts"), "https://demo/live.flv")).endswith(".flv")
        )
        self.assertTrue(
            str(build_raw_recording_path(Path("/tmp/demo.ts"), "https://demo/live.flv?sid=1")).endswith(".flv")
        )
        self.assertTrue(
            str(build_raw_recording_path(Path("/tmp/demo.ts"), "https://demo/live.m3u8")).endswith(".ts")
        )

    def test_stream_url_path_ignores_query_string(self):
        self.assertEqual(stream_url_path("https://demo/live.flv?sid=1&t=abc"), "/live.flv")

    def test_plan_shots_caps_at_four_and_groups_by_zone(self):
        shots = plan_shots("商品近景, 门头, 制作过程, 收银台, 店内全景", max_shots=4)
        self.assertEqual(len(shots), 4)
        self.assertEqual(shots[0]["shot_id"], "storefront")
        self.assertEqual(shots[1]["shot_id"], "interior-wide")
        self.assertEqual(shots[2]["zone"], "counter")
        self.assertEqual(shots[3]["zone"], "counter")

    def test_init_session_creates_local_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = init_session("门头, 商品近景", Path(tmp))
            session_path = Path(session["storage_root"]) / "session.json"
            self.assertTrue(session_path.exists())
            self.assertTrue(session["shots"][0]["output_path"].endswith(".ts"))

    def test_mark_shot_captured_updates_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = init_session("门头, 商品近景", Path(tmp))
            mark_shot_captured(session, 1)
            summary = session_summary(session)
            self.assertEqual(summary["completed_count"], 1)
            self.assertEqual(get_next_pending_shot(session)["index"], 2)

    def test_record_hls_clip_collects_segments(self):
        playlist = "\n".join(
            [
                "#EXTM3U",
                "#EXTINF:2.0,",
                "seg-1.ts",
                "#EXTINF:2.5,",
                "seg-2.ts",
            ]
        ).encode("utf-8")
        blobs = {
            "https://demo/live.m3u8": playlist,
            "https://demo/seg-1.ts": b"AAA",
            "https://demo/seg-2.ts": b"BBB",
        }

        def fake_fetcher(url: str, timeout: float) -> bytes:
            return blobs[url]

        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "clip.ts"
            result = record_hls_clip(
                "https://demo/live.m3u8",
                output,
                target_duration=4.0,
                max_wait_seconds=0.1,
                poll_interval_seconds=0.0,
                fetcher=fake_fetcher,
            )
            self.assertTrue(output.exists())
            self.assertEqual(result["segment_count"], 2)
            self.assertEqual(output.read_bytes(), b"AAABBB")

    def test_record_flv_clip_marks_timeout_as_usable_when_file_exists(self):
        import cb60_capture_workflow as workflow

        class FakePopen:
            def __init__(self, *args, **kwargs):
                self.returncode = None
                self._timed_out = False

            def communicate(self, timeout=None):
                if not self._timed_out:
                    self._timed_out = True
                    raise subprocess.TimeoutExpired(cmd="ffmpeg", timeout=timeout)
                return ("", "")

            def kill(self):
                pass

        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "clip.flv"
            output.write_bytes(b"FLV")
            old_which = workflow.shutil.which
            old_popen = workflow.subprocess.Popen
            try:
                workflow.shutil.which = lambda _: "/opt/homebrew/bin/ffmpeg"
                workflow.subprocess.Popen = FakePopen
                result = record_flv_clip(
                    "https://demo/live.flv",
                    output,
                    target_duration=20.0,
                )
            finally:
                workflow.shutil.which = old_which
                workflow.subprocess.Popen = old_popen

            self.assertTrue(result["terminated_on_timeout"])
            self.assertEqual(result["source_protocol"], "flv")
            self.assertEqual(result["output_path"], str(output))

    def test_record_flv_clip_raises_when_timeout_produces_empty_file(self):
        import cb60_capture_workflow as workflow

        class FakePopen:
            def __init__(self, *args, **kwargs):
                self.returncode = None
                self._timed_out = False

            def communicate(self, timeout=None):
                if not self._timed_out:
                    self._timed_out = True
                    raise subprocess.TimeoutExpired(cmd="ffmpeg", timeout=timeout)
                return ("", "")

            def kill(self):
                pass

        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "clip.flv"
            output.write_bytes(b"")
            old_which = workflow.shutil.which
            old_popen = workflow.subprocess.Popen
            try:
                workflow.shutil.which = lambda _: "/opt/homebrew/bin/ffmpeg"
                workflow.subprocess.Popen = FakePopen
                with self.assertRaisesRegex(Exception, "timed out before producing a usable FLV recording"):
                    record_flv_clip(
                        "https://demo/live.flv",
                        output,
                        target_duration=20.0,
                    )
            finally:
                workflow.shutil.which = old_which
                workflow.subprocess.Popen = old_popen

    def test_capture_next_shot_updates_session_file(self):
        playlist = "\n".join(
            [
                "#EXTM3U",
                "#EXTINF:15.0,",
                "seg-1.ts",
            ]
        ).encode("utf-8")
        blobs = {
            "https://demo/live.m3u8": playlist,
            "https://demo/seg-1.ts": b"AAA",
        }

        def fake_fetcher(url: str, timeout: float) -> bytes:
            return blobs[url]

        with tempfile.TemporaryDirectory() as tmp:
            session = init_session("门头, 商品近景", Path(tmp))
            session_path = Path(session["storage_root"]) / "session.json"
            import cb60_capture_workflow as workflow

            old_fetcher = workflow.fetch_bytes
            old_record = workflow.record_hls_clip
            try:
                workflow.fetch_bytes = fake_fetcher

                def patched_record(*args, **kwargs):
                    kwargs["fetcher"] = fake_fetcher
                    return old_record(*args, **kwargs)

                workflow.record_hls_clip = patched_record

                def fake_transcode(input_path: Path, rotation_mode: str):
                    output_path = input_path.with_suffix(".mp4")
                    output_path.write_bytes(b"MP4")
                    return {"ok": True, "output_path": str(output_path), "layout": rotation_mode}

                payload = capture_next_shot(
                    session_path=session_path,
                    config=EnvConfig(access_token="t", device_serial="d", manual_live_url="https://demo/live.m3u8"),
                    transcode_func=fake_transcode,
                    rotation_mode="cw90",
                )
            finally:
                workflow.fetch_bytes = old_fetcher
                workflow.record_hls_clip = old_record

            reloaded = load_session(session_path)
            self.assertEqual(reloaded["shots"][0]["status"], "captured")
            self.assertIn("next_instruction", payload)
            self.assertTrue(Path(reloaded["shots"][0]["output_path"]).exists())
            self.assertTrue(reloaded["shots"][0]["output_path"].endswith(".mp4"))
            self.assertTrue(reloaded["shots"][0]["raw_output_path"].endswith(".ts"))
            self.assertTrue(payload["conversion"]["ok"])
            self.assertEqual(payload["conversion"]["layout"], "cw90")

    def test_capture_next_shot_falls_back_when_mp4_conversion_is_unavailable(self):
        playlist = "\n".join(
            [
                "#EXTM3U",
                "#EXTINF:15.0,",
                "seg-1.ts",
            ]
        ).encode("utf-8")
        blobs = {
            "https://demo/live.m3u8": playlist,
            "https://demo/seg-1.ts": b"AAA",
        }

        def fake_fetcher(url: str, timeout: float) -> bytes:
            return blobs[url]

        with tempfile.TemporaryDirectory() as tmp:
            session = init_session("门头", Path(tmp))
            session_path = Path(session["storage_root"]) / "session.json"
            import cb60_capture_workflow as workflow

            old_fetcher = workflow.fetch_bytes
            old_record = workflow.record_hls_clip
            try:
                workflow.fetch_bytes = fake_fetcher

                def patched_record(*args, **kwargs):
                    kwargs["fetcher"] = fake_fetcher
                    return old_record(*args, **kwargs)

                workflow.record_hls_clip = patched_record
                payload = capture_next_shot(
                    session_path=session_path,
                    config=EnvConfig(access_token="t", device_serial="d", manual_live_url="https://demo/live.m3u8"),
                    transcode_func=lambda *_: {"ok": False, "reason": "ffmpeg_not_found"},
                )
            finally:
                workflow.fetch_bytes = old_fetcher
                workflow.record_hls_clip = old_record

            reloaded = load_session(session_path)
            self.assertTrue(reloaded["shots"][0]["output_path"].endswith(".ts"))
            self.assertEqual(reloaded["shots"][0]["raw_output_path"], reloaded["shots"][0]["output_path"])
            self.assertFalse(payload["conversion"]["ok"])


if __name__ == "__main__":
    unittest.main()
