from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
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

from cb60_capture_workflow import (  # noqa: E402
    analyze_failure_frame,
    build_capture_timestamp,
    build_tos_video_filename,
    build_las_stage_prefix,
    build_rotated_mp4_command,
    build_las_task_description,
    resolve_las_inpaint_fixed_bboxes,
    build_timestamped_shot_path,
    derive_store_slug_from_tos_prefix,
    las_skill_call_context,
    build_raw_recording_path,
    build_las_postprocess_state,
    classify_capture_output,
    capture_next_shot,
    extract_failure_frame,
    init_session,
    load_session,
    mark_shot_captured,
    plan_shots,
    probe_media_metrics,
    get_next_pending_shot,
    record_flv_clip,
    record_hls_clip,
    resolve_stream_url,
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

    def test_resolve_stream_url_prefers_managed_stream_when_configured(self):
        import cb60_capture_workflow as workflow

        class FakeClient:
            def __init__(self, config):
                self.config = config

            def get_stream_address(self, **kwargs):
                return {"address": "https://demo/live.flv?sid=managed"}

        config = EnvConfig(
            access_token="t",
            device_serial="d",
            managed_stream_id="stream-long-lived",
        )
        old_client = workflow.EzvizClient
        try:
            workflow.EzvizClient = FakeClient
            resolved = resolve_stream_url(config)
        finally:
            workflow.EzvizClient = old_client

        self.assertEqual(resolved, "https://demo/live.flv?sid=managed")

    def test_resolve_stream_url_defaults_to_flv_protocol_for_live_lookup(self):
        import cb60_capture_workflow as workflow

        class FakeClient:
            def __init__(self, config):
                self.config = config

            def get_live_url(self, **kwargs):
                self.kwargs = kwargs
                return {"stream_url": "https://demo/live.flv?sid=direct"}

        config = EnvConfig(
            access_token="t",
            device_serial="d",
        )
        fake_client = FakeClient(config)
        old_client = workflow.EzvizClient
        try:
            workflow.EzvizClient = lambda config: fake_client
            resolved = resolve_stream_url(config)
        finally:
            workflow.EzvizClient = old_client

        self.assertEqual(resolved, "https://demo/live.flv?sid=direct")
        self.assertEqual(fake_client.kwargs["protocol_id"], 4)
        self.assertEqual(fake_client.kwargs["quality"], 1)
        self.assertEqual(fake_client.kwargs["support_h265"], 1)
        self.assertEqual(fake_client.kwargs["mute"], 0)
        self.assertEqual(fake_client.kwargs["address_type"], 1)

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
            self.assertTrue(session["las_pipeline"]["enabled"])
            self.assertEqual(session["las_pipeline"]["required_bridge"]["status"], "pending_config")

    def test_mark_shot_captured_updates_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = init_session("门头, 商品近景", Path(tmp))
            mark_shot_captured(session, 1)
            summary = session_summary(session)
            self.assertEqual(summary["completed_count"], 1)
            self.assertEqual(get_next_pending_shot(session)["index"], 2)

    def test_build_capture_timestamp_uses_expected_format(self):
        self.assertEqual(build_capture_timestamp(1776163200), "20260414-184000")

    def test_build_timestamped_shot_path_includes_time(self):
        path = build_timestamped_shot_path(Path("/tmp/shots"), 1, "process", "20260414-160101", ".ts")
        self.assertEqual(path, Path("/tmp/shots/01-process-20260414-160101.ts"))

    def test_derive_store_slug_from_tos_prefix_removes_stage_suffix(self):
        slug = derive_store_slug_from_tos_prefix(
            "tos://doudou-video/openclaw/store1_jsspa_original/",
            "tos://doudou-video/openclaw/store1_jsspa_final/",
        )
        self.assertEqual(slug, "store1_jsspa")

    def test_build_tos_video_filename_uses_store_date_time_stage_and_seq(self):
        filename = build_tos_video_filename("store1_jsspa", "20260415-101530", "original", 2)
        self.assertEqual(filename, "store1_jsspa_20260415_101530_original_02.mp4")

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

    def test_record_flv_clip_accepts_nonzero_exit_when_file_exists(self):
        import cb60_capture_workflow as workflow

        class FakePopen:
            def __init__(self, *args, **kwargs):
                self.returncode = 1

            def communicate(self, timeout=None):
                return ("", "End of file")

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

            self.assertEqual(result["ffmpeg_returncode"], 1)
            self.assertEqual(result["source_protocol"], "flv")
            self.assertEqual(result["output_path"], str(output))

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
                    probe_func=lambda _path: {
                        "duration_seconds": 15.0,
                        "size_bytes": 1234,
                        "width": 1080,
                        "height": 1920,
                        "video_codec": "h264",
                        "audio_codec": "aac",
                    },
                    classify_func=lambda _metrics, _target: "accepted",
                    rotation_mode="cw90",
                )
            finally:
                workflow.fetch_bytes = old_fetcher
                workflow.record_hls_clip = old_record

    def test_capture_next_shot_retries_hls_when_auto_flv_recording_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = init_session("门头", Path(tmp))
            session_path = Path(session["storage_root"]) / "session.json"
            import cb60_capture_workflow as workflow

            old_resolve = workflow.resolve_stream_url
            old_record = workflow.record_stream_clip
            try:
                resolved_urls = iter(["https://demo/live.flv", "https://demo/live.m3u8"])
                calls = []

                def fake_resolve(*args, **kwargs):
                    return next(resolved_urls)

                def fake_record(stream_url: str, output_path: Path, *args, **kwargs):
                    calls.append(stream_url)
                    if stream_url.endswith(".flv"):
                        raise RuntimeError("flv failed")
                    output_path.parent.mkdir(parents=True, exist_ok=True)
                    output_path.write_bytes(b"TS")
                    return {
                        "output_path": str(output_path),
                        "captured_duration_seconds": 15.0,
                        "segment_count": 1,
                        "source_protocol": "hls",
                    }

                workflow.resolve_stream_url = fake_resolve
                workflow.record_stream_clip = fake_record

                def fake_transcode(input_path: Path, rotation_mode: str):
                    output_path = input_path.with_suffix(".mp4")
                    output_path.write_bytes(b"MP4")
                    return {"ok": True, "output_path": str(output_path), "layout": rotation_mode}

                payload = capture_next_shot(
                    session_path=session_path,
                    config=EnvConfig(access_token="t", device_serial="d"),
                    transcode_func=fake_transcode,
                    probe_func=lambda _: {"duration_seconds": 15.0, "width": 1080, "height": 1920},
                )
            finally:
                workflow.resolve_stream_url = old_resolve
                workflow.record_stream_clip = old_record

            self.assertEqual(calls, ["https://demo/live.flv", "https://demo/live.m3u8"])
            self.assertEqual(payload["captured_shot"]["status"], "captured")
            log_text = (Path(session["storage_root"]) / "capture-log.jsonl").read_text(encoding="utf-8")
            self.assertIn("flv_failed_retry_hls", log_text)

            reloaded = load_session(session_path)
            self.assertEqual(reloaded["shots"][0]["status"], "captured")
            self.assertIn("next_instruction", payload)
            self.assertTrue(Path(reloaded["shots"][0]["output_path"]).exists())
            self.assertTrue(reloaded["shots"][0]["output_path"].endswith(".mp4"))
            self.assertTrue(reloaded["shots"][0]["raw_output_path"].endswith(".ts"))
            self.assertRegex(Path(reloaded["shots"][0]["output_path"]).name, r"01-storefront-\d{8}-\d{6}\.mp4")
            self.assertRegex(Path(reloaded["shots"][0]["raw_output_path"]).name, r"01-storefront-\d{8}-\d{6}\.ts")
            self.assertTrue(payload["conversion"]["ok"])
            self.assertEqual(payload["conversion"]["layout"], "cw90")
            self.assertEqual(reloaded["shots"][0]["validation"]["status"], "accepted")
            self.assertRegex(reloaded["shots"][0]["capture_timestamp"], r"\d{8}-\d{6}")
            self.assertEqual(reloaded["shots"][0]["postprocess"]["status"], "pending_config")
            self.assertEqual(reloaded["shots"][0]["postprocess"]["steps"][0]["step"], "upload_to_tos")
            self.assertEqual(reloaded["shots"][0]["postprocess"]["steps"][1]["step"], "las_highlight_edit")
            self.assertTrue(Path(payload["workflow_log_path"]).exists())
            self.assertTrue(Path(payload["workflow_report_path"]).exists())

    def test_capture_next_shot_stops_when_wall_timeout_is_exceeded(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = init_session("门头", Path(tmp))
            session_path = Path(session["storage_root"]) / "session.json"
            import cb60_capture_workflow as workflow

            old_record = workflow.record_stream_clip
            try:
                def fake_record(stream_url: str, output_path: Path, *args, **kwargs):
                    output_path.parent.mkdir(parents=True, exist_ok=True)
                    output_path.write_bytes(b"FLV")
                    time.sleep(0.02)
                    return {
                        "output_path": str(output_path),
                        "captured_duration_seconds": 15.0,
                        "segment_count": None,
                        "source_protocol": "flv",
                    }

                workflow.record_stream_clip = fake_record

                with self.assertRaisesRegex(Exception, "Capture workflow timed out"):
                    capture_next_shot(
                        session_path=session_path,
                        config=EnvConfig(
                            access_token="t",
                            device_serial="d",
                            capture_wall_timeout_seconds=0.001,
                        ),
                        stream_url="https://demo/live.flv",
                    )
            finally:
                workflow.record_stream_clip = old_record

            log_text = (Path(session["storage_root"]) / "capture-log.jsonl").read_text(encoding="utf-8")
            self.assertIn("capture_timed_out", log_text)
            reloaded = load_session(session_path)
            self.assertEqual(reloaded["shots"][0]["status"], "pending")

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
                    probe_func=lambda _path: {
                        "duration_seconds": 15.0,
                        "size_bytes": 1234,
                        "width": 1080,
                        "height": 1920,
                        "video_codec": "mpegts",
                        "audio_codec": "",
                    },
                    classify_func=lambda _metrics, _target: "accepted",
                )
            finally:
                workflow.fetch_bytes = old_fetcher
                workflow.record_hls_clip = old_record

            reloaded = load_session(session_path)
            self.assertTrue(reloaded["shots"][0]["output_path"].endswith(".ts"))
            self.assertEqual(reloaded["shots"][0]["raw_output_path"], reloaded["shots"][0]["output_path"])
            self.assertFalse(payload["conversion"]["ok"])
            self.assertEqual(reloaded["shots"][0]["validation"]["status"], "accepted")

    def test_capture_next_shot_extracts_failure_frame_and_analysis_for_abnormal_clip(self):
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

                def fake_transcode(input_path: Path, rotation_mode: str):
                    output_path = input_path.with_suffix(".mp4")
                    output_path.write_bytes(b"bad-mp4")
                    return {"ok": True, "output_path": str(output_path), "layout": rotation_mode}

                def fake_extract(input_path: Path, output_path: Path, second: float):
                    output_path.write_bytes(b"jpeg")
                    return output_path

                payload = capture_next_shot(
                    session_path=session_path,
                    config=EnvConfig(access_token="t", device_serial="d", manual_live_url="https://demo/live.m3u8"),
                    transcode_func=fake_transcode,
                    probe_func=lambda _path: {
                        "duration_seconds": 6.0,
                        "size_bytes": 456,
                        "width": 288,
                        "height": 512,
                        "video_codec": "h264",
                        "audio_codec": "aac",
                    },
                    classify_func=lambda _metrics, _target: "abnormal",
                    extract_frame_func=fake_extract,
                    analyze_failure_func=lambda frame, metrics: f"分析完成:{frame.name}:{metrics['width']}x{metrics['height']}",
                )
            finally:
                workflow.fetch_bytes = old_fetcher
                workflow.record_hls_clip = old_record

            reloaded = load_session(session_path)
            validation = reloaded["shots"][0]["validation"]
            self.assertEqual(validation["status"], "abnormal")
            self.assertIn("分析完成", validation["analysis"])
            self.assertTrue(Path(validation["frame_path"]).exists())
            self.assertEqual(reloaded["shots"][0]["postprocess"]["status"], "skipped_capture_not_accepted")
            self.assertEqual(payload["captured_shot"]["validation"]["status"], "abnormal")

    def test_build_las_postprocess_state_marks_pending_config_for_accepted_clip(self):
        session = init_session("门头", Path(tempfile.mkdtemp()))
        shot = session["shots"][0]
        state = build_las_postprocess_state(
            session=session,
            shot=shot,
            final_output_path="/tmp/demo.mp4",
            validation_status="accepted",
        )
        self.assertEqual(state["status"], "pending_config")
        self.assertEqual(state["steps"][0]["step"], "upload_to_tos")
        self.assertEqual(state["steps"][0]["status"], "pending_config")
        self.assertEqual(state["steps"][1]["status"], "blocked")

    def test_build_las_postprocess_state_skips_unaccepted_clip(self):
        session = init_session("门头", Path(tempfile.mkdtemp()))
        shot = session["shots"][0]
        state = build_las_postprocess_state(
            session=session,
            shot=shot,
            final_output_path="/tmp/demo.mp4",
            validation_status="failed",
        )
        self.assertEqual(state["status"], "skipped_capture_not_accepted")
        self.assertTrue(all(step["status"] == "skipped" for step in state["steps"]))

    def test_build_las_task_description_uses_business_highlight_prompt(self):
        description = build_las_task_description("后厨", "上菜全过程和前台接待")
        self.assertIn("高光时刻标准", description)
        self.assertIn("非高光剔除标准", description)
        self.assertIn("输出要求", description)
        self.assertIn("上菜全过程和前台接待", description)

    def test_resolve_las_inpaint_fixed_bboxes_defaults_to_bottom_left_timestamp_area(self):
        config = EnvConfig()
        self.assertEqual(resolve_las_inpaint_fixed_bboxes(config), [[0, 650, 150, 970]])

    def test_resolve_las_inpaint_fixed_bboxes_prefers_env_override(self):
        config = EnvConfig(las_inpaint_fixed_bboxes=((10, 900, 280, 990),))
        self.assertEqual(resolve_las_inpaint_fixed_bboxes(config), [[10, 900, 280, 990]])

    def test_build_las_stage_prefix_isolated_per_round(self):
        edit_prefix = build_las_stage_prefix(
            "tos://demo-bucket/openclaw/original/",
            "session-1/01-round-01",
            "las-edit",
        )
        inpaint_prefix = build_las_stage_prefix(
            "tos://demo-bucket/openclaw/original/",
            "session-1/02-round-02",
            "las-inpaint",
        )
        self.assertEqual(edit_prefix, "tos://demo-bucket/openclaw/original/session-1/01-round-01/las-edit/")
        self.assertEqual(inpaint_prefix, "tos://demo-bucket/openclaw/original/session-1/02-round-02/las-inpaint/")

    def test_las_skill_call_context_serializes_env_access_between_threads(self):
        config_a = EnvConfig(las_api_key="key-A", las_region="region-A")
        config_b = EnvConfig(las_api_key="key-B", las_region="region-B")
        entered_a = threading.Event()
        release_a = threading.Event()
        thread_b_done = threading.Event()
        seen: list[tuple[str, str, str]] = []

        previous_key = os.environ.get("LAS_API_KEY")
        previous_region = os.environ.get("LAS_REGION")

        def worker_a():
            with las_skill_call_context(config_a):
                seen.append(("a", os.environ.get("LAS_API_KEY", ""), os.environ.get("LAS_REGION", "")))
                entered_a.set()
                release_a.wait(timeout=2)

        def worker_b():
            entered_a.wait(timeout=2)
            with las_skill_call_context(config_b):
                seen.append(("b", os.environ.get("LAS_API_KEY", ""), os.environ.get("LAS_REGION", "")))
            thread_b_done.set()

        ta = threading.Thread(target=worker_a)
        tb = threading.Thread(target=worker_b)
        ta.start()
        tb.start()
        entered_a.wait(timeout=2)
        self.assertFalse(thread_b_done.is_set())
        release_a.set()
        ta.join(timeout=2)
        tb.join(timeout=2)

        self.assertEqual(seen[0], ("a", "key-A", "region-A"))
        self.assertEqual(seen[1], ("b", "key-B", "region-B"))
        self.assertEqual(os.environ.get("LAS_API_KEY"), previous_key)
        self.assertEqual(os.environ.get("LAS_REGION"), previous_region)

    def test_classify_capture_output_distinguishes_accepted_abnormal_failed(self):
        self.assertEqual(
            classify_capture_output(
                {"duration_seconds": 20.0, "width": 1080, "height": 1920, "video_codec": "h264"},
                20,
            ),
            "accepted",
        )
        self.assertEqual(
            classify_capture_output(
                {"duration_seconds": 6.0, "width": 288, "height": 512, "video_codec": "h264"},
                20,
            ),
            "abnormal",
        )
        self.assertEqual(classify_capture_output({}, 20), "failed")


if __name__ == "__main__":
    unittest.main()
