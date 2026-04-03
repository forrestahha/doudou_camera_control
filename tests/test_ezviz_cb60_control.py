from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
import urllib.parse
from pathlib import Path

SCRIPT_DIR = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "ezviz-cb60-control"
    / "scripts"
)
sys.path.insert(0, str(SCRIPT_DIR))

from ezviz_cb60_control import (  # noqa: E402
    EnvConfig,
    EzvizClient,
    EzvizError,
    extract_env_file_arg,
    find_first_url,
    flatten_battery_signals,
    join_url,
    run_setup_wizard,
    write_env_file,
)


class FakeRequester:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def __call__(self, request, timeout):
        body = request.data.decode("utf-8") if request.data else ""
        params = urllib.parse.parse_qs(body)
        flat_params = {key: values[-1] for key, values in params.items()}
        self.calls.append((request.full_url, flat_params, timeout))
        key = (request.full_url, flat_params.get("channelNo"))
        response = self.responses.get(key)
        if response is None:
            response = self.responses.get(request.full_url)
        if isinstance(response, Exception):
            raise response
        if response is None:
            raise AssertionError(f"Unexpected URL: {request.full_url}")
        return response


class EzvizControlTests(unittest.TestCase):
    def make_config(self):
        return EnvConfig(
            access_token="test-token",
            device_serial="device-1",
            channel_no=1,
            base_url="https://open.ys7.com",
            timeout_seconds=3,
        )

    def test_join_url(self):
        self.assertEqual(join_url("https://open.ys7.com/", "/api/test"), "https://open.ys7.com/api/test")

    def test_find_first_url_walks_nested_payloads(self):
        payload = {"data": {"nested": [{"foo": "bar"}, {"hls": "https://stream.example/hls.m3u8"}]}}
        self.assertEqual(find_first_url(payload), "https://stream.example/hls.m3u8")

    def test_flatten_battery_signals_finds_nested_fields(self):
        payload = {"deviceStatus": {"battery": 78, "extra": {"powerState": "charging"}}, "battryStatus": 96}
        signals = flatten_battery_signals(payload)
        self.assertEqual(signals["deviceStatus.battery"], 78)
        self.assertEqual(signals["deviceStatus.extra.powerState"], "charging")
        self.assertEqual(signals["battryStatus"], 96)

    def test_env_config_can_load_from_env_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / "cam2.env"
            env_path.write_text(
                "\n".join(
                    [
                        "export EZVIZ_APP_KEY='key-2'",
                        "export EZVIZ_ACCESS_TOKEN='token-2'",
                        "export EZVIZ_DEVICE_SERIAL='device-2'",
                        "export EZVIZ_VALIDATE_CODE='code-2'",
                        "export EZVIZ_CHANNEL_NO='3'",
                    ]
                ),
                encoding="utf-8",
            )
            old_serial = os.environ.get("EZVIZ_DEVICE_SERIAL")
            os.environ["EZVIZ_DEVICE_SERIAL"] = "device-1"
            try:
                config = EnvConfig.from_env(env_file=str(env_path))
            finally:
                if old_serial is None:
                    os.environ.pop("EZVIZ_DEVICE_SERIAL", None)
                else:
                    os.environ["EZVIZ_DEVICE_SERIAL"] = old_serial
            self.assertEqual(config.app_key, "key-2")
            self.assertEqual(config.access_token, "token-2")
            self.assertEqual(config.device_serial, "device-2")
            self.assertEqual(config.validate_code, "code-2")
            self.assertEqual(config.channel_no, 3)

    def test_extract_env_file_arg_accepts_inline_and_positional_forms(self):
        cleaned, env_file = extract_env_file_arg(["capture-shot", "--env-file", "~/.ezviz_cb60_env_cam2", "--foo"])
        self.assertEqual(cleaned, ["capture-shot", "--foo"])
        self.assertEqual(env_file, "~/.ezviz_cb60_env_cam2")

        cleaned2, env_file2 = extract_env_file_arg(["--env-file=~/.ezviz_cb60_env_cam1", "doctor"])
        self.assertEqual(cleaned2, ["doctor"])
        self.assertEqual(env_file2, "~/.ezviz_cb60_env_cam1")

    def test_write_env_file_writes_expected_exports(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cam.env"
            write_env_file(
                path,
                {
                    "EZVIZ_APP_KEY": "key",
                    "EZVIZ_APP_SECRET": "secret",
                    "EZVIZ_ACCESS_TOKEN": "token",
                    "EZVIZ_DEVICE_SERIAL": "serial",
                    "EZVIZ_VALIDATE_CODE": "verify",
                    "EZVIZ_CHANNEL_NO": "1",
                },
            )
            text = path.read_text(encoding="utf-8")
            self.assertIn("export EZVIZ_APP_KEY='key'", text)
            self.assertIn("export EZVIZ_DEVICE_SERIAL='serial'", text)

    def test_run_setup_wizard_creates_env_file(self):
        answers = iter(
            [
                "device-9",
                "1",
            ]
        )
        secret_answers = iter(
            [
                "key-9",
                "secret-9",
                "token-9",
                "verify-9",
            ]
        )

        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / "wizard.env"
            payload = run_setup_wizard(
                output_path=env_path,
                force=True,
                prompt_func=lambda _prompt: next(answers),
                secret_prompt_func=lambda _prompt: next(secret_answers),
            )
            self.assertTrue(env_path.exists())
            self.assertEqual(payload["env_file"], str(env_path))
            loaded = EnvConfig.from_env(env_file=str(env_path))
            self.assertEqual(loaded.app_key, "key-9")
            self.assertEqual(loaded.device_serial, "device-9")

    def test_ptz_pulse_issues_start_and_stop(self):
        requester = FakeRequester(
            {
                "https://open.ys7.com/api/lapp/device/ptz/start": {"code": "200", "data": {"ok": True}},
                "https://open.ys7.com/api/lapp/device/ptz/stop": {"code": "200", "data": {"ok": True}},
            }
        )
        client = EzvizClient(self.make_config(), requester=requester, sleeper=lambda _: None)
        payload = client.ptz_pulse("left", duration=0.25, speed=2)
        self.assertEqual(payload["direction"], "left")
        self.assertEqual(len(requester.calls), 2)
        start_url, start_params, _ = requester.calls[0]
        stop_url, stop_params, _ = requester.calls[1]
        self.assertEqual(start_url, "https://open.ys7.com/api/lapp/device/ptz/start")
        self.assertEqual(stop_url, "https://open.ys7.com/api/lapp/device/ptz/stop")
        self.assertEqual(start_params["direction"], "2")
        self.assertEqual(start_params["speed"], "2")
        self.assertEqual(stop_params["direction"], "2")

    def test_snapshot_returns_pic_url(self):
        requester = FakeRequester(
            {
                "https://open.ys7.com/api/lapp/device/capture": {
                    "code": "200",
                    "data": {"picUrl": "https://img.example/snap.jpg"},
                }
            }
        )
        client = EzvizClient(self.make_config(), requester=requester)
        payload = client.capture_snapshot()
        self.assertEqual(payload["snapshot_url"], "https://img.example/snap.jpg")

    def test_live_url_falls_back_to_legacy_path(self):
        requester = FakeRequester(
            {
                "https://open.ys7.com/api/lapp/v2/live/address/get": EzvizError("404"),
                "https://open.ys7.com/api/lapp/live/address/get": {
                    "code": "200",
                    "data": {"liveAddress": "https://stream.example/live.m3u8"},
                },
            }
        )
        client = EzvizClient(self.make_config(), requester=requester)
        payload = client.get_live_url()
        self.assertEqual(payload["path"], "/api/lapp/live/address/get")
        self.assertEqual(payload["stream_url"], "https://stream.example/live.m3u8")
        self.assertEqual(len(requester.calls), 2)

    def test_live_url_sends_source_when_provided(self):
        requester = FakeRequester(
            {
                "https://open.ys7.com/api/lapp/v2/live/address/get": {
                    "code": "200",
                    "data": {"liveAddress": "https://stream.example/live.m3u8"},
                }
            }
        )
        client = EzvizClient(self.make_config(), requester=requester)
        payload = client.get_live_url(source="device")
        self.assertEqual(payload["stream_url"], "https://stream.example/live.m3u8")
        _, params, _ = requester.calls[0]
        self.assertEqual(params["source"], "device")

    def test_manual_live_url_short_circuits_api_lookup(self):
        config = self.make_config()
        config.manual_live_url = "https://open.ys7.com/v3/openlive/demo.m3u8"
        requester = FakeRequester({})
        client = EzvizClient(config, requester=requester)
        payload = client.get_live_url()
        self.assertEqual(payload["path"], "manual")
        self.assertEqual(payload["stream_url"], "https://open.ys7.com/v3/openlive/demo.m3u8")
        self.assertEqual(requester.calls, [])

    def test_diagnose_preview_reports_h265_and_expiry(self):
        client = EzvizClient(self.make_config(), requester=FakeRequester({}))
        diagnosis = client.diagnose_preview(
            "https://open.ys7.com/v3/openlive/demo.m3u8?expire=1&supportH265=1"
        )
        hints = " ".join(diagnosis["hints"])
        self.assertIn("H.265", hints)
        self.assertIn("signed temporary URL", hints)

    def test_get_battery_status_extracts_likely_fields(self):
        requester = FakeRequester(
            {
                "https://open.ys7.com/api/lapp/device/status/get": {
                    "code": "200",
                    "data": {"battryStatus": 66, "powerMode": "normal"},
                }
            }
        )
        client = EzvizClient(self.make_config(), requester=requester)
        payload = client.get_battery_status()
        self.assertEqual(payload["battery_signals"]["battryStatus"], 66)
        self.assertEqual(payload["battery_percent"], 66)
        self.assertEqual(payload["battery_signals"]["powerMode"], "normal")

    def test_get_device_info_returns_raw_payload(self):
        requester = FakeRequester(
            {
                "https://open.ys7.com/api/lapp/device/info": {
                    "code": "200",
                    "data": {"deviceName": "CB60", "status": 1},
                }
            }
        )
        client = EzvizClient(self.make_config(), requester=requester)
        payload = client.get_device_info()
        self.assertEqual(payload["device_serial"], "device-1")
        self.assertEqual(payload["raw"]["deviceName"], "CB60")

    def test_get_video_encode_uses_headers_and_query(self):
        requester = FakeRequester(
            {
                "https://open.ys7.com/api/v3/das/device/video/encode?streamType=1": {
                    "meta": {"code": 200, "message": "ok"},
                    "data": {"videoCode": 5},
                }
            }
        )
        client = EzvizClient(self.make_config(), requester=requester)
        payload = client.get_video_encode(stream_type=1)
        self.assertEqual(payload["video_code"], 5)
        request_url, _, _ = requester.calls[0]
        self.assertEqual(request_url, "https://open.ys7.com/api/v3/das/device/video/encode?streamType=1")

    def test_set_video_encode_posts_form(self):
        requester = FakeRequester(
            {
                "https://open.ys7.com/api/v3/das/device/video/encode": {
                    "meta": {"code": 200, "message": "ok"},
                    "data": None,
                }
            }
        )
        client = EzvizClient(self.make_config(), requester=requester)
        payload = client.set_video_encode("H264")
        self.assertEqual(payload["encode_type"], "H264")
        _, params, _ = requester.calls[0]
        self.assertEqual(params["encodeType"], "H264")

    def test_create_stream_returns_stream_id(self):
        requester = FakeRequester(
            {
                "https://open.ys7.com/api/service/media/streammanage/stream?accessType=1&startTime=2026-03-25+18%3A00%3A00&endTime=2026-03-25+20%3A00%3A00": {
                    "meta": {"code": 200, "message": "ok"},
                    "data": {"streamId": "stream-123"},
                }
            }
        )
        client = EzvizClient(self.make_config(), requester=requester)
        payload = client.create_stream(
            start_time="2026-03-25 18:00:00",
            end_time="2026-03-25 20:00:00",
        )
        self.assertEqual(payload["stream_id"], "stream-123")

    def test_get_stream_address_returns_address(self):
        requester = FakeRequester(
            {
                "https://open.ys7.com/api/service/media/streammanage/stream/address?streamId=stream-123&protocol=4&quality=1&supportH265=0&mute=0&type=1": {
                    "meta": {"code": 200, "message": "ok"},
                    "data": {"address": "https://stream.example/live.flv"},
                }
            }
        )
        client = EzvizClient(self.make_config(), requester=requester)
        payload = client.get_stream_address(stream_id="stream-123", protocol=4)
        self.assertEqual(payload["address"], "https://stream.example/live.flv")

    def test_dump_device_combines_info_status_and_battery(self):
        requester = FakeRequester(
            {
                "https://open.ys7.com/api/lapp/device/info": {
                    "code": "200",
                    "data": {"deviceName": "CB60"},
                },
                "https://open.ys7.com/api/lapp/device/status/get": {
                    "code": "200",
                    "data": {"battryStatus": 91, "signal": 100},
                },
            }
        )
        client = EzvizClient(self.make_config(), requester=requester)
        payload = client.dump_device()
        self.assertEqual(payload["device_info"]["deviceName"], "CB60")
        self.assertEqual(payload["device_status"]["signal"], 100)
        self.assertEqual(payload["battery"]["battery_percent"], 91)

    def test_probe_channels_detects_only_channel_one(self):
        requester = FakeRequester(
            {
                ("https://open.ys7.com/api/lapp/device/capture", "1"): {
                    "code": "200",
                    "data": {"picUrl": "https://img.example/ch1.jpg"},
                },
                ("https://open.ys7.com/api/lapp/device/capture", "2"): EzvizError("60020: channel not found"),
                ("https://open.ys7.com/api/lapp/v2/live/address/get", "1"): {
                    "code": "200",
                    "data": {"liveAddress": "https://stream.example/ch1.m3u8"},
                },
                ("https://open.ys7.com/api/lapp/v2/live/address/get", "2"): EzvizError("60020: channel not found"),
                ("https://open.ys7.com/api/lapp/live/address/get", "2"): EzvizError("60020: channel not found"),
            }
        )
        client = EzvizClient(self.make_config(), requester=requester)
        result = client.probe_channels(channels=[1, 2], source="device")
        self.assertEqual(result["inference"]["status"], "likely_single_public_channel")
        self.assertTrue(result["channels"][0]["snapshot"]["ok"])
        self.assertFalse(result["channels"][1]["snapshot"]["ok"])

    def test_probe_channels_flags_possible_second_channel(self):
        requester = FakeRequester(
            {
                ("https://open.ys7.com/api/lapp/device/capture", "1"): {
                    "code": "200",
                    "data": {"picUrl": "https://img.example/ch1.jpg"},
                },
                ("https://open.ys7.com/api/lapp/device/capture", "2"): {
                    "code": "200",
                    "data": {"picUrl": "https://img.example/ch2.jpg"},
                },
                ("https://open.ys7.com/api/lapp/v2/live/address/get", "1"): {
                    "code": "200",
                    "data": {"liveAddress": "https://stream.example/ch1.m3u8"},
                },
                ("https://open.ys7.com/api/lapp/v2/live/address/get", "2"): {
                    "code": "200",
                    "data": {"liveAddress": "https://stream.example/ch2.m3u8"},
                },
            }
        )
        client = EzvizClient(self.make_config(), requester=requester)
        result = client.probe_channels(channels=[1, 2], source="device")
        self.assertEqual(result["inference"]["status"], "possible_second_logical_channel")
        self.assertTrue(result["channels"][1]["live_url"]["ok"])

    def test_doctor_redacts_presence_only(self):
        config = EnvConfig(
            app_key="APPKEY-SECRET-VALUE",
            app_secret="APPSECRET-SECRET-VALUE",
            access_token="ACCESSTOKEN-SECRET-VALUE",
            device_serial="DEVICE-SERIAL-VALUE",
            validate_code="VALIDATE-CODE-VALUE",
        )
        doctor = config.doctor()
        dumped = json.dumps(doctor)
        self.assertNotIn("APPKEY-SECRET-VALUE", dumped)
        self.assertNotIn("APPSECRET-SECRET-VALUE", dumped)
        self.assertNotIn("ACCESSTOKEN-SECRET-VALUE", dumped)
        self.assertNotIn("DEVICE-SERIAL-VALUE", dumped)
        self.assertNotIn("VALIDATE-CODE-VALUE", dumped)
        self.assertTrue(doctor["ok"])

    def test_capabilities_match_verified_runtime(self):
        client = EzvizClient(self.make_config(), requester=FakeRequester({}))
        capabilities = client.capabilities()
        self.assertFalse(capabilities["implemented"]["zoom_in"])
        self.assertFalse(capabilities["implemented"]["zoom_out"])
        self.assertFalse(capabilities["verified_runtime"]["zoom_rest_control"])


if __name__ == "__main__":
    unittest.main()
