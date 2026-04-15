from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPT_DIR = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "ezviz-cb60-control"
    / "scripts"
)
sys.path.insert(0, str(SCRIPT_DIR))

from cb60_interval_capture_test import (  # noqa: E402
    IntervalArtifacts,
    classify_clip,
    extract_frame,
    run_interval_capture_test,
    probe_metrics,
    render_report,
    render_summary,
)
from ezviz_cb60_control import EnvConfig  # noqa: E402


class IntervalCaptureTests(unittest.TestCase):
    def test_probe_metrics_extracts_video_and_audio(self):
        metrics = probe_metrics(
            {
                "streams": [
                    {"codec_type": "video", "codec_name": "h264", "width": 1080, "height": 1920},
                    {"codec_type": "audio", "codec_name": "aac"},
                ],
                "format": {"duration": "20.04", "size": "12345"},
            }
        )
        self.assertEqual(metrics["video_codec"], "h264")
        self.assertEqual(metrics["audio_codec"], "aac")
        self.assertEqual(metrics["height"], 1920)

    def test_classify_clip_accepts_expected_portrait_clip(self):
        status = classify_clip(
            {
                "duration": 20.04,
                "width": 1080,
                "height": 1920,
            },
            target_duration=20,
        )
        self.assertEqual(status, "accepted")

    def test_classify_clip_rejects_short_low_res_clip(self):
        status = classify_clip(
            {
                "duration": 7.0,
                "width": 288,
                "height": 512,
            },
            target_duration=20,
        )
        self.assertEqual(status, "abnormal")

    def test_render_summary_counts_statuses(self):
        with tempfile.TemporaryDirectory() as tmp:
            artifacts = IntervalArtifacts.create(Path(tmp) / "run")
            summary = render_summary(
                artifacts,
                [
                    {"round": 1, "status": "accepted"},
                    {"round": 2, "status": "abnormal"},
                    {"round": 3, "status": "record_failed"},
                ],
            )
            self.assertEqual(summary["accepted_count"], 1)
            self.assertEqual(summary["abnormal_count"], 1)
            self.assertEqual(summary["failed_count"], 1)
            self.assertTrue(artifacts.summary_json.exists())

    def test_render_report_writes_markdown(self):
        with tempfile.TemporaryDirectory() as tmp:
            artifacts = IntervalArtifacts.create(Path(tmp) / "run")
            render_report(
                artifacts,
                rows=[
                    {
                        "round": 1,
                        "started_at": "2026-03-26 15:25:06",
                        "status": "accepted",
                        "duration": 20.032,
                        "width": 1080,
                        "height": 1920,
                        "mp4_output_path": str(artifacts.clips_dir / "round-01.mp4"),
                        "note": "",
                    }
                ],
                clip_duration_seconds=20,
                interval_seconds=60,
            )
            self.assertTrue(artifacts.report_md.exists())
            content = artifacts.report_md.read_text()
            self.assertIn("CB60 Interval Capture Report", content)
            self.assertIn("round-01.mp4", content)

    def test_run_interval_capture_test_writes_failure_analysis_for_abnormal_clip(self):
        with tempfile.TemporaryDirectory() as tmp:
            artifacts = IntervalArtifacts.create(Path(tmp) / "run")
            raw_path = artifacts.clips_dir / "round-01.flv"
            mp4_path = artifacts.clips_dir / "round-01.mp4"
            frame_path = artifacts.clips_dir / "round-01-frame.jpg"
            raw_path.write_bytes(b"raw")
            mp4_path.write_bytes(b"mp4")
            frame_path.write_bytes(b"jpg")
            config = EnvConfig(
                app_key="a",
                app_secret="b",
                access_token="c",
                device_serial="serial",
                validate_code="code",
            )

            with mock.patch("cb60_interval_capture_test.resolve_stream_url", return_value="https://example.com/live.flv"), \
                mock.patch("cb60_interval_capture_test.record_stream_clip", return_value={"output_path": str(raw_path)}), \
                mock.patch("cb60_interval_capture_test.transcode_recording_to_mp4", return_value={"ok": True, "output_path": str(mp4_path)}), \
                mock.patch(
                    "cb60_interval_capture_test.ffprobe_json",
                    return_value={
                        "streams": [
                            {"codec_type": "video", "codec_name": "h264", "width": 288, "height": 512},
                            {"codec_type": "audio", "codec_name": "aac"},
                        ],
                        "format": {"duration": "7.0", "size": "123"},
                    },
                ), \
                mock.patch("cb60_interval_capture_test.extract_frame", return_value=frame_path), \
                mock.patch("cb60_interval_capture_test.analyze_failure_frame", return_value="OCR文本=视频编码类型非H264"), \
                mock.patch(
                    "cb60_interval_capture_test.run_las_postprocess_pipeline",
                    return_value={"status": "skipped_capture_not_accepted"},
                ):
                summary = run_interval_capture_test(
                    config=config,
                    artifacts=artifacts,
                    rounds=1,
                    clip_duration_seconds=20,
                    interval_seconds=1,
                )

            self.assertEqual(summary["abnormal_count"], 1)
            row = summary["rounds"][0]
            self.assertEqual(row["status"], "abnormal")
            self.assertEqual(row["frame_path"], str(frame_path))
            self.assertIn("视频编码类型非H264", row["text_analysis"])
            report = artifacts.report_md.read_text()
            self.assertIn("Review Needed", report)

    def test_run_interval_capture_test_records_las_paths_when_postprocess_succeeds(self):
        with tempfile.TemporaryDirectory() as tmp:
            artifacts = IntervalArtifacts.create(Path(tmp) / "run")
            raw_path = artifacts.clips_dir / "round-01.flv"
            mp4_path = artifacts.clips_dir / "round-01.mp4"
            raw_path.write_bytes(b"raw")
            mp4_path.write_bytes(b"mp4")
            config = EnvConfig(
                app_key="a",
                app_secret="b",
                access_token="c",
                device_serial="serial",
                validate_code="code",
            )

            with mock.patch("cb60_interval_capture_test.resolve_stream_url", return_value="https://example.com/live.flv"), \
                mock.patch("cb60_interval_capture_test.record_stream_clip", return_value={"output_path": str(raw_path)}), \
                mock.patch("cb60_interval_capture_test.transcode_recording_to_mp4", return_value={"ok": True, "output_path": str(mp4_path)}), \
                mock.patch(
                    "cb60_interval_capture_test.ffprobe_json",
                    return_value={
                        "streams": [
                            {"codec_type": "video", "codec_name": "h264", "width": 1440, "height": 2560},
                            {"codec_type": "audio", "codec_name": "aac"},
                        ],
                        "format": {"duration": "20.0", "size": "123456"},
                    },
                ), \
                mock.patch(
                    "cb60_interval_capture_test.run_las_postprocess_pipeline",
                    return_value={
                        "status": "completed",
                        "uploaded_tos_path": "tos://bucket/original/store1_jsspa_20260415_101530_original_01.mp4",
                        "final_tos_path": "tos://bucket/final/store1_jsspa_20260415_101530_final_01.mp4",
                    },
                ):
                summary = run_interval_capture_test(
                    config=config,
                    artifacts=artifacts,
                    rounds=1,
                    clip_duration_seconds=20,
                    interval_seconds=1,
                )

            row = summary["rounds"][0]
            self.assertEqual(row["status"], "accepted")
            self.assertEqual(row["postprocess_status"], "completed")
            self.assertEqual(row["uploaded_tos_path"], "tos://bucket/original/store1_jsspa_20260415_101530_original_01.mp4")
            self.assertEqual(row["final_tos_path"], "tos://bucket/final/store1_jsspa_20260415_101530_final_01.mp4")
            report = artifacts.report_md.read_text()
            self.assertIn("LAS completed", report)

    def test_run_interval_capture_test_uses_timestamped_file_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            artifacts = IntervalArtifacts.create(Path(tmp) / "run")
            config = EnvConfig(
                app_key="a",
                app_secret="b",
                access_token="c",
                device_serial="serial",
                validate_code="code",
            )

            def fake_record_stream_clip(**kwargs):
                output_path = Path(kwargs["output_path"])
                output_path.write_bytes(b"raw")
                return {"output_path": str(output_path)}

            def fake_transcode_recording_to_mp4(input_path: Path, rotation_mode: str = "cw90"):
                output_path = input_path.with_suffix(".mp4")
                output_path.write_bytes(b"mp4")
                return {"ok": True, "output_path": str(output_path)}

            with mock.patch("cb60_interval_capture_test.build_capture_timestamp", return_value="20260414-170000"), \
                mock.patch("cb60_interval_capture_test.resolve_stream_url", return_value="https://example.com/live.flv"), \
                mock.patch("cb60_interval_capture_test.record_stream_clip", side_effect=fake_record_stream_clip), \
                mock.patch("cb60_interval_capture_test.transcode_recording_to_mp4", side_effect=fake_transcode_recording_to_mp4), \
                mock.patch(
                    "cb60_interval_capture_test.ffprobe_json",
                    return_value={
                        "streams": [
                            {"codec_type": "video", "codec_name": "h264", "width": 1440, "height": 2560},
                            {"codec_type": "audio", "codec_name": "aac"},
                        ],
                        "format": {"duration": "20.0", "size": "123456"},
                    },
                ), \
                mock.patch(
                    "cb60_interval_capture_test.run_las_postprocess_pipeline",
                    return_value={"status": "skipped_capture_not_accepted"},
                ):
                summary = run_interval_capture_test(
                    config=config,
                    artifacts=artifacts,
                    rounds=1,
                    clip_duration_seconds=20,
                    interval_seconds=1,
                )

            row = summary["rounds"][0]
            self.assertTrue(row["raw_output_path"].endswith("round-01-20260414-170000.flv"))
            self.assertTrue(row["mp4_output_path"].endswith("round-01-20260414-170000.mp4"))


if __name__ == "__main__":
    unittest.main()
