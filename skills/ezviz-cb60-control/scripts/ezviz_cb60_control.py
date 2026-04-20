#!/usr/bin/env python3
"""Portable controller for EZVIZ CB60 camera operations.

This script is REST-first and intentionally reads secrets from environment
variables only. It is designed to be packaged inside an OpenClaw/Codex skill.
"""

from __future__ import annotations

import argparse
import getpass
import importlib.util
import json
import os
import shlex
import shutil
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Optional

JsonDict = Dict[str, Any]


class EzvizError(RuntimeError):
    """Raised when the EZVIZ API or local execution fails."""


def first_nonempty(*values: Any) -> Optional[Any]:
    for value in values:
        if value not in (None, "", [], {}):
            return value
    return None


def join_url(base_url: str, path: str) -> str:
    return base_url.rstrip("/") + "/" + path.lstrip("/")


def find_first_url(payload: Any) -> Optional[str]:
    if isinstance(payload, str):
        if payload.startswith(("http://", "https://", "rtmp://", "rtsp://", "ws://", "wss://")):
            return payload
        return None
    if isinstance(payload, dict):
        preferred_keys = (
            "url",
            "liveAddress",
            "hls",
            "flv",
            "rtmp",
            "rtsp",
            "picUrl",
            "pictureUrl",
            "snapshotUrl",
        )
        for key in preferred_keys:
            value = payload.get(key)
            found = find_first_url(value)
            if found:
                return found
        for value in payload.values():
            found = find_first_url(value)
            if found:
                return found
        return None
    if isinstance(payload, list):
        for item in payload:
            found = find_first_url(item)
            if found:
                return found
    return None


def flatten_battery_signals(payload: Any, prefix: str = "") -> Dict[str, Any]:
    results: Dict[str, Any] = {}
    if isinstance(payload, dict):
        for key, value in payload.items():
            next_prefix = f"{prefix}.{key}" if prefix else key
            lowered = key.lower()
            if any(token in lowered for token in ("battery", "battry", "electric", "power", "charge", "capacity")):
                results[next_prefix] = value
            results.update(flatten_battery_signals(value, next_prefix))
    elif isinstance(payload, list):
        for index, value in enumerate(payload):
            next_prefix = f"{prefix}[{index}]"
            results.update(flatten_battery_signals(value, next_prefix))
    return results


def tos_sdk_installed() -> bool:
    return importlib.util.find_spec("tos") is not None


def binary_available(name: str) -> bool:
    return shutil.which(name) is not None


def default_tos_endpoint(region: str) -> str:
    resolved_region = region.strip() if isinstance(region, str) and region.strip() else "cn-beijing"
    return f"tos-{resolved_region}.volces.com"


def parse_tos_url(url: str) -> tuple[str, str]:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "tos" or not parsed.netloc or not parsed.path:
        raise EzvizError(f"Invalid TOS URL: {url}")
    return parsed.netloc, parsed.path.lstrip("/")


def safe_tos_bucket(url: str) -> Optional[str]:
    if not url:
        return None
    try:
        bucket, _ = parse_tos_url(url)
        return bucket
    except EzvizError:
        return None


def validate_tos_stage_prefix(env_name: str, value: str) -> str:
    resolved = value.strip()
    if not resolved:
        return ""
    bucket, key = parse_tos_url(resolved)
    if not bucket or not key:
        raise EzvizError(f"{env_name} 必须是有效的 tos:// 前缀。")
    normalized = resolved if resolved.endswith("/") else resolved + "/"
    parsed = urllib.parse.urlparse(normalized)
    last_segment = parsed.path.rstrip("/").split("/")[-1]
    expected_suffix = "_original" if env_name == "TOS_ORIGINAL" else "_final"
    disallowed_segments = {"original", "final"}
    if last_segment in disallowed_segments or not last_segment.endswith(expected_suffix):
        raise EzvizError(
            f"{env_name} 必须显式指向商家目录，并以 {expected_suffix} 结尾，"
            f"例如 tos://doudou-video/openclaw/store1_jsspa{expected_suffix}/。"
        )
    return normalized


def load_env_file(path: str) -> Dict[str, str]:
    env_path = Path(path).expanduser()
    if not env_path.exists():
        raise EzvizError(f"Env file not found: {env_path}")
    values: Dict[str, str] = {}
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            raise EzvizError(f"Invalid env file line: {raw_line}")
        key, raw_value = line.split("=", 1)
        key = key.strip()
        raw_value = raw_value.strip()
        try:
            parsed = shlex.split(raw_value)
        except ValueError as exc:
            raise EzvizError(f"Invalid env file value for {key}: {exc}") from exc
        values[key] = parsed[0] if parsed else ""
    return values


def update_env_file_value(path: str, key: str, value: str) -> None:
    env_path = Path(path).expanduser()
    if not env_path.exists():
        raise EzvizError(f"Env file not found: {env_path}")
    prefix = f"{key}="
    export_prefix = f"export {key}="
    replacement = f"export {key}={value!r}"
    lines = env_path.read_text(encoding="utf-8").splitlines()
    replaced = False
    next_lines = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith(prefix) or stripped.startswith(export_prefix):
            next_lines.append(replacement)
            replaced = True
        else:
            next_lines.append(line)
    if not replaced:
        next_lines.append(replacement)
    env_path.write_text("\n".join(next_lines) + "\n", encoding="utf-8")
    os.chmod(env_path, 0o600)


def parse_fixed_bboxes(raw_value: str) -> tuple[tuple[int, int, int, int], ...]:
    raw_value = raw_value.strip()
    if not raw_value:
        return ()
    try:
        payload = json.loads(raw_value)
    except json.JSONDecodeError as exc:
        raise EzvizError("LAS_INPAINT_FIXED_BBOXES 必须是 JSON 数组，例如 [[0,920,340,1000]]。") from exc
    if not isinstance(payload, list):
        raise EzvizError("LAS_INPAINT_FIXED_BBOXES 必须是 JSON 数组。")

    boxes = []
    for item in payload:
        if not isinstance(item, list) or len(item) != 4:
            raise EzvizError("LAS_INPAINT_FIXED_BBOXES 的每一项都必须是 4 个整数的数组。")
        try:
            x1, y1, x2, y2 = (int(value) for value in item)
        except (TypeError, ValueError) as exc:
            raise EzvizError("LAS_INPAINT_FIXED_BBOXES 的坐标必须是整数。") from exc
        if not (0 <= x1 < x2 <= 1000 and 0 <= y1 < y2 <= 1000):
            raise EzvizError("LAS_INPAINT_FIXED_BBOXES 坐标必须满足 0 <= x1 < x2 <= 1000 且 0 <= y1 < y2 <= 1000。")
        boxes.append((x1, y1, x2, y2))
    return tuple(boxes)


def write_env_file(path: Path, values: Dict[str, str]) -> Path:
    path = path.expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    ordered_keys = (
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
        "LAS_INPAINT_FIXED_BBOXES",
        "TOS_ORIGINAL",
        "TOS_FINAL",
        "TOS_BUCKET",
        "TOS_PREFIX",
    )
    for key in ordered_keys:
        if key not in values:
            continue
        value = values.get(key, "")
        lines.append(f"export {key}={value!r}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.chmod(path, 0o600)
    return path


def run_setup_wizard(
    *,
    output_path: Path,
    force: bool = False,
    prompt_func: Callable[[str], str] = input,
    secret_prompt_func: Callable[[str], str] = getpass.getpass,
) -> JsonDict:
    target_path = output_path.expanduser()
    if target_path.exists() and not force:
        overwrite = prompt_func(f"{target_path} 已存在，是否覆盖？输入 yes 继续: ").strip().lower()
        if overwrite not in {"y", "yes"}:
            raise EzvizError("安装向导已取消，现有环境文件未改动。")

    app_key = secret_prompt_func("EZVIZ_APP_KEY: ").strip()
    app_secret = secret_prompt_func("EZVIZ_APP_SECRET: ").strip()
    access_token = secret_prompt_func("EZVIZ_ACCESS_TOKEN: ").strip()
    device_serial = prompt_func("EZVIZ_DEVICE_SERIAL: ").strip()
    validate_code = secret_prompt_func("EZVIZ_VALIDATE_CODE: ").strip()
    channel_raw = prompt_func("EZVIZ_CHANNEL_NO (默认 1): ").strip() or "1"

    try:
        channel_no = int(channel_raw)
    except ValueError as exc:
        raise EzvizError("EZVIZ_CHANNEL_NO 必须是整数。") from exc

    required_values = {
        "EZVIZ_APP_KEY": app_key,
        "EZVIZ_APP_SECRET": app_secret,
        "EZVIZ_ACCESS_TOKEN": access_token,
        "EZVIZ_DEVICE_SERIAL": device_serial,
        "EZVIZ_VALIDATE_CODE": validate_code,
        "EZVIZ_CHANNEL_NO": str(channel_no),
    }
    missing = [key for key, value in required_values.items() if not value]
    if missing:
        raise EzvizError("安装向导失败，以下字段不能为空: " + ", ".join(missing))

    values_to_write = dict(required_values)
    las_api_key = secret_prompt_func("LAS_API_KEY: ").strip()
    las_region = prompt_func("LAS_REGION (例如 cn-beijing): ").strip()
    tos_access_key = secret_prompt_func("TOS_ACCESS_KEY: ").strip()
    tos_secret_key = secret_prompt_func("TOS_SECRET_KEY: ").strip()
    tos_original = validate_tos_stage_prefix(
        "TOS_ORIGINAL",
        prompt_func("TOS_ORIGINAL (例如 tos://doudou-video/openclaw/store1_jsspa_original/): ").strip(),
    )
    tos_final = validate_tos_stage_prefix(
        "TOS_FINAL",
        prompt_func("TOS_FINAL (例如 tos://doudou-video/openclaw/store1_jsspa_final/): ").strip(),
    )

    las_required = {
        "LAS_API_KEY": las_api_key,
        "LAS_REGION": las_region,
        "TOS_ACCESS_KEY": tos_access_key,
        "TOS_SECRET_KEY": tos_secret_key,
        "TOS_ORIGINAL": tos_original,
        "TOS_FINAL": tos_final,
    }
    las_missing = [key for key, value in las_required.items() if not value]
    if las_missing:
        raise EzvizError("安装向导失败，完整插件链路必须配置火山云 LAS/TOS，以下字段不能为空: " + ", ".join(las_missing))
    values_to_write.update(las_required)

    written_path = write_env_file(target_path, values_to_write)
    return {
        "ok": True,
        "env_file": str(written_path),
        "next_step": f"source {written_path}",
        "keys_written": sorted(values_to_write.keys()),
        "las_tos_configured": True,
        "workflow_mode": "full_capture_with_las",
    }


def normalize_stream_address_protocol(protocol: int) -> int:
    if protocol == 4:
        return 3
    if protocol not in {1, 2, 3}:
        raise EzvizError("stream-address protocol must be 1(hls), 2(rtmp), 3(flv); legacy 4 is also accepted as flv.")
    return protocol


def extract_env_file_arg(argv: Optional[Iterable[str]]) -> tuple[list[str], Optional[str]]:
    items = list(argv if argv is not None else sys.argv[1:])
    cleaned: list[str] = []
    env_file: Optional[str] = None
    index = 0
    while index < len(items):
        item = items[index]
        if item == "--env-file":
            if index + 1 >= len(items):
                raise EzvizError("--env-file requires a path argument.")
            env_file = items[index + 1]
            index += 2
            continue
        if item.startswith("--env-file="):
            env_file = item.split("=", 1)[1]
            index += 1
            continue
        cleaned.append(item)
        index += 1
    return cleaned, env_file


@dataclass
class EnvConfig:
    app_key: str = ""
    app_secret: str = ""
    access_token: str = ""
    device_serial: str = ""
    validate_code: str = ""
    channel_no: int = 1
    base_url: str = "https://open.ys7.com"
    live_url_path: str = ""
    live_source: str = ""
    manual_live_url: str = ""
    managed_stream_id: str = ""
    managed_stream_protocol: int = 1
    managed_stream_quality: int = 1
    managed_stream_support_h265: int = 1
    managed_stream_mute: int = 0
    timeout_seconds: float = 20.0
    las_api_key: str = ""
    las_region: str = ""
    tos_access_key: str = ""
    tos_secret_key: str = ""
    las_inpaint_fixed_bboxes: tuple[tuple[int, int, int, int], ...] = ()
    tos_bucket: str = ""
    tos_prefix: str = ""
    tos_original: str = ""
    tos_final: str = ""
    env_file_path: str = ""
    capture_wall_timeout_seconds: float = 180.0
    capture_with_las_wall_timeout_seconds: float = 5400.0

    @classmethod
    def from_env(cls, env_file: Optional[str] = None) -> "EnvConfig":
        env = dict(os.environ)
        resolved_env_file = ""
        if env_file:
            resolved_env_file = str(Path(env_file).expanduser())
            env.update(load_env_file(env_file))

        channel_raw = env.get("EZVIZ_CHANNEL_NO", "1").strip() or "1"
        try:
            channel_no = int(channel_raw)
        except ValueError as exc:
            raise EzvizError("EZVIZ_CHANNEL_NO must be an integer.") from exc

        def parse_int_env(name: str, default: int) -> int:
            raw = env.get(name, str(default)).strip() or str(default)
            try:
                return int(raw)
            except ValueError as exc:
                raise EzvizError(f"{name} must be an integer.") from exc

        def parse_float_env(name: str, default: float) -> float:
            raw = env.get(name, str(default)).strip() or str(default)
            try:
                value = float(raw)
            except ValueError as exc:
                raise EzvizError(f"{name} must be a number.") from exc
            if value <= 0:
                raise EzvizError(f"{name} must be greater than 0.")
            return value

        return cls(
            app_key=env.get("EZVIZ_APP_KEY", "").strip(),
            app_secret=env.get("EZVIZ_APP_SECRET", "").strip(),
            access_token=env.get("EZVIZ_ACCESS_TOKEN", "").strip(),
            device_serial=env.get("EZVIZ_DEVICE_SERIAL", "").strip(),
            validate_code=env.get("EZVIZ_VALIDATE_CODE", "").strip(),
            channel_no=channel_no,
            base_url=env.get("EZVIZ_BASE_URL", "https://open.ys7.com").strip() or "https://open.ys7.com",
            live_url_path=env.get("EZVIZ_LIVE_URL_PATH", "").strip(),
            live_source=env.get("EZVIZ_LIVE_SOURCE", "").strip(),
            manual_live_url=env.get("EZVIZ_MANUAL_LIVE_URL", "").strip(),
            managed_stream_id=env.get("EZVIZ_MANAGED_STREAM_ID", "").strip(),
            managed_stream_protocol=parse_int_env("EZVIZ_MANAGED_STREAM_PROTOCOL", 1),
            managed_stream_quality=parse_int_env("EZVIZ_MANAGED_STREAM_QUALITY", 1),
            managed_stream_support_h265=parse_int_env("EZVIZ_MANAGED_STREAM_SUPPORT_H265", 1),
            managed_stream_mute=parse_int_env("EZVIZ_MANAGED_STREAM_MUTE", 0),
            timeout_seconds=float(env.get("EZVIZ_TIMEOUT_SECONDS", "20").strip() or "20"),
            las_api_key=env.get("LAS_API_KEY", "").strip(),
            las_region=env.get("LAS_REGION", "").strip(),
            tos_access_key=env.get("TOS_ACCESS_KEY", "").strip(),
            tos_secret_key=env.get("TOS_SECRET_KEY", "").strip(),
            las_inpaint_fixed_bboxes=parse_fixed_bboxes(env.get("LAS_INPAINT_FIXED_BBOXES", "")),
            tos_bucket=env.get("TOS_BUCKET", "").strip(),
            tos_prefix=env.get("TOS_PREFIX", "").strip(),
            tos_original=validate_tos_stage_prefix("TOS_ORIGINAL", env.get("TOS_ORIGINAL", "").strip()),
            tos_final=validate_tos_stage_prefix("TOS_FINAL", env.get("TOS_FINAL", "").strip()),
            env_file_path=resolved_env_file,
            capture_wall_timeout_seconds=parse_float_env("CB60_CAPTURE_WALL_TIMEOUT_SECONDS", 180.0),
            capture_with_las_wall_timeout_seconds=parse_float_env("CB60_CAPTURE_WITH_LAS_WALL_TIMEOUT_SECONDS", 5400.0),
        )

    def doctor(self) -> JsonDict:
        required_capture = {
            "EZVIZ_DEVICE_SERIAL": bool(self.device_serial),
            "EZVIZ_ACCESS_TOKEN": bool(self.access_token),
        }
        optional_refresh = {
            "EZVIZ_APP_KEY": bool(self.app_key),
            "EZVIZ_APP_SECRET": bool(self.app_secret),
            "EZVIZ_VALIDATE_CODE": bool(self.validate_code),
        }
        required_full_workflow = {
            "LAS_API_KEY": bool(self.las_api_key),
            "LAS_REGION": bool(self.las_region),
            "TOS_ACCESS_KEY": bool(self.tos_access_key),
            "TOS_SECRET_KEY": bool(self.tos_secret_key),
            "TOS_ORIGINAL": bool(self.tos_original),
            "TOS_FINAL": bool(self.tos_final),
        }
        optional_postprocess = {
            "LAS_INPAINT_FIXED_BBOXES": bool(self.las_inpaint_fixed_bboxes),
        }
        capture_ready = all(required_capture.values())
        full_workflow_ready = capture_ready and all(required_full_workflow.values())
        return {
            "ok": full_workflow_ready,
            "capture_ready": capture_ready,
            "full_workflow_ready": full_workflow_ready,
            "required_capture": required_capture,
            "required_full_workflow": required_full_workflow,
            "optional": optional_refresh,
            "optional_postprocess": optional_postprocess,
            "missing_full_workflow": [key for key, value in required_full_workflow.items() if not value],
            "channel_no": self.channel_no,
            "base_url": self.base_url,
            "live_url_path_override": bool(self.live_url_path),
            "live_source_override": bool(self.live_source),
            "manual_live_url_override": bool(self.manual_live_url),
            "managed_stream_override": bool(self.managed_stream_id),
            "workflow_timeouts": {
                "capture_wall_timeout_seconds": self.capture_wall_timeout_seconds,
                "capture_with_las_wall_timeout_seconds": self.capture_with_las_wall_timeout_seconds,
            },
            "runtime_dependencies": {
                "tos_sdk_installed": tos_sdk_installed(),
                "tos_sdk_package_name": "tos",
                "ffmpeg_installed": binary_available("ffmpeg"),
                "ffmpeg_package_hint": "ffmpeg",
                "tesseract_installed": binary_available("tesseract"),
                "tesseract_package_hint": "tesseract-ocr",
            },
            "tos_runtime": {
                "endpoint": default_tos_endpoint(self.las_region),
                "original_bucket": safe_tos_bucket(self.tos_original),
                "final_bucket": safe_tos_bucket(self.tos_final),
            },
        }


class EzvizClient:
    PTZ_COMMANDS = {
        "left": 2,
        "right": 3,
        "zoom-in": 8,
        "zoom-out": 9,
    }

    LIVE_PATHS = (
        "/api/lapp/v2/live/address/get",
        "/api/lapp/live/address/get",
    )
    STREAM_MANAGE_PATH = "/api/service/media/streammanage/stream"
    STREAM_LIST_PATH = "/api/service/media/streammanage/stream/list"
    STREAM_ADDRESS_PATH = "/api/service/media/streammanage/stream/address"
    VIDEO_ENCODE_PATH = "/api/v3/das/device/video/encode"

    def __init__(
        self,
        config: EnvConfig,
        requester: Optional[Callable[[urllib.request.Request, float], JsonDict]] = None,
        sleeper: Optional[Callable[[float], None]] = None,
    ) -> None:
        self.config = config
        self._requester = requester or self._default_request_json
        self._sleeper = sleeper or time.sleep

    def _default_request_json(self, request: urllib.request.Request, timeout: float) -> JsonDict:
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                body = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            error_body = exc.read().decode("utf-8", errors="replace")
            raise EzvizError(f"HTTP {exc.code} calling {request.full_url}: {error_body}") from exc
        except urllib.error.URLError as exc:
            raise EzvizError(f"Network error calling {request.full_url}: {exc}") from exc

        try:
            payload = json.loads(body)
        except json.JSONDecodeError as exc:
            raise EzvizError(f"Non-JSON response from {request.full_url}: {body[:200]}") from exc

        if not isinstance(payload, dict):
            raise EzvizError(f"Unexpected response shape from {request.full_url}: {type(payload).__name__}")
        return payload

    def _post_form(self, path: str, params: JsonDict) -> JsonDict:
        return self._request_form("POST", path, form_params=params)

    def _request_form(
        self,
        method: str,
        path: str,
        *,
        form_params: Optional[JsonDict] = None,
        query_params: Optional[JsonDict] = None,
        header_params: Optional[JsonDict] = None,
    ) -> JsonDict:
        form_values = dict(form_params or {})
        query_values = dict(query_params or {})
        header_values = dict(header_params or {})

        for attempt in range(2):
            encoded = None
            if form_values:
                encoded = urllib.parse.urlencode(form_values).encode("utf-8")

            url = join_url(self.config.base_url, path)
            if query_values:
                filtered = {key: value for key, value in query_values.items() if value not in (None, "")}
                if filtered:
                    url += "?" + urllib.parse.urlencode(filtered)

            headers = {}
            if encoded is not None:
                headers["Content-Type"] = "application/x-www-form-urlencoded"
            if header_values:
                headers.update({key: str(value) for key, value in header_values.items() if value not in (None, "")})

            request = urllib.request.Request(
                url,
                data=encoded,
                headers=headers,
                method=method,
            )
            payload = self._requester(request, self.config.timeout_seconds)
            code, message = self._extract_error(payload)
            if code == "10002" and attempt == 0 and path != "/api/lapp/token/get":
                fresh_token = self.get_access_token(force_refresh=True)
                self._replace_access_token(form_values, query_values, header_values, token=fresh_token)
                continue
            if code is not None:
                raise EzvizError(f"{code}: {message}")
            return payload

        raise EzvizError("Request failed after accessToken refresh.")

    def _extract_error(self, payload: JsonDict) -> tuple[Optional[str], str]:
        top_code = payload.get("code")
        if top_code is not None and str(top_code) not in {"0", "200"}:
            message = first_nonempty(payload.get("msg"), payload.get("message"), payload.get("detail")) or "API error"
            return str(top_code), str(message)

        meta = payload.get("meta")
        if isinstance(meta, dict):
            meta_code = meta.get("code")
            if meta_code is not None and str(meta_code) not in {"0", "200"}:
                message = first_nonempty(meta.get("msg"), meta.get("message"), meta.get("detail")) or "API error"
                return str(meta_code), str(message)
        return None, ""

    def _ensure_success(self, payload: JsonDict) -> None:
        code, message = self._extract_error(payload)
        if code is not None:
            raise EzvizError(f"{code}: {message}")

    def _replace_access_token(self, *param_sets: JsonDict, token: str) -> None:
        for params in param_sets:
            if "accessToken" in params:
                params["accessToken"] = token

    def _extract_data(self, payload: JsonDict) -> Any:
        if "data" in payload:
            return payload["data"]
        if "result" in payload:
            return payload["result"]
        return payload

    def get_access_token(self, force_refresh: bool = False) -> str:
        if self.config.access_token and not force_refresh:
            return self.config.access_token
        if not self.config.app_key or not self.config.app_secret:
            raise EzvizError("Missing EZVIZ_ACCESS_TOKEN and no app credentials are available for refresh.")

        payload = self._post_form(
            "/api/lapp/token/get",
            {
                "appKey": self.config.app_key,
                "appSecret": self.config.app_secret,
            },
        )
        data = self._extract_data(payload)
        token = None
        if isinstance(data, dict):
            token = first_nonempty(data.get("accessToken"), data.get("token"))
        token = token or first_nonempty(payload.get("accessToken"), payload.get("token"))
        if not isinstance(token, str) or not token:
            raise EzvizError("Token refresh succeeded but no access token was returned.")
        self.config.access_token = token
        if self.config.env_file_path:
            update_env_file_value(self.config.env_file_path, "EZVIZ_ACCESS_TOKEN", token)
        return token

    def _device_params(self, channel_no: Optional[int] = None) -> JsonDict:
        if not self.config.device_serial:
            raise EzvizError("Missing EZVIZ_DEVICE_SERIAL.")
        effective_channel_no = self.config.channel_no if channel_no is None else channel_no
        return {
            "accessToken": self.get_access_token(),
            "deviceSerial": self.config.device_serial,
            "channelNo": effective_channel_no,
        }

    def ptz_start(self, direction: str, speed: int = 1) -> JsonDict:
        if direction not in self.PTZ_COMMANDS:
            raise EzvizError(f"Unsupported PTZ direction: {direction}")
        params = self._device_params()
        params.update({"direction": self.PTZ_COMMANDS[direction], "speed": speed})
        return self._post_form("/api/lapp/device/ptz/start", params)

    def ptz_stop(self, direction: str) -> JsonDict:
        if direction not in self.PTZ_COMMANDS:
            raise EzvizError(f"Unsupported PTZ direction: {direction}")
        params = self._device_params()
        params.update({"direction": self.PTZ_COMMANDS[direction]})
        return self._post_form("/api/lapp/device/ptz/stop", params)

    def ptz_pulse(self, direction: str, duration: float = 1.0, speed: int = 1) -> JsonDict:
        self.ptz_start(direction, speed=speed)
        try:
            self._sleeper(duration)
        finally:
            stop_payload = self.ptz_stop(direction)
        return {
            "direction": direction,
            "duration_seconds": duration,
            "speed": speed,
            "stop_response": self._extract_data(stop_payload),
        }

    def capture_snapshot(self, output_path: Optional[Path] = None, channel_no: Optional[int] = None) -> JsonDict:
        payload = self._post_form("/api/lapp/device/capture", self._device_params(channel_no=channel_no))
        data = self._extract_data(payload)
        snapshot_url = find_first_url(data)
        result: JsonDict = {
            "snapshot_url": snapshot_url,
            "raw": data,
        }
        if output_path:
            if not snapshot_url:
                raise EzvizError("Snapshot call succeeded but no downloadable snapshot URL was returned.")
            self._download(snapshot_url, output_path)
            result["downloaded_to"] = str(output_path)
        return result

    def _download(self, url: str, output_path: Path) -> None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(url, timeout=self.config.timeout_seconds) as response:
            output_path.write_bytes(response.read())

    def get_live_url(
        self,
        expire_seconds: int = 300,
        protocol_id: Optional[int] = None,
        source: Optional[str] = None,
        channel_no: Optional[int] = None,
        quality: Optional[int] = None,
        support_h265: Optional[int] = None,
        mute: Optional[int] = None,
        address_type: Optional[int] = None,
    ) -> JsonDict:
        if self.config.manual_live_url and channel_no in (None, self.config.channel_no):
            return {
                "path": "manual",
                "stream_url": self.config.manual_live_url,
                "raw": {"manual": True},
            }
        params = self._device_params(channel_no=channel_no)
        params["expireTime"] = expire_seconds
        if protocol_id is not None:
            params["protocol"] = protocol_id
        if quality is not None:
            params["quality"] = quality
        if support_h265 is not None:
            params["supportH265"] = support_h265
        if mute is not None:
            params["mute"] = mute
        if address_type is not None:
            params["type"] = address_type
        candidate_paths = [self.config.live_url_path] if self.config.live_url_path else list(self.LIVE_PATHS)
        errors = []
        for path in candidate_paths:
            source_value = first_nonempty(source, self.config.live_source)
            attempt_params = dict(params)
            if source_value:
                attempt_params["source"] = source_value
            attempted_with_source = "source" in attempt_params
            try_without_source_after_illegal = attempted_with_source
            try_with_default_source_after_missing = not attempted_with_source

            for retry_mode in ("primary", "without_source", "default_source"):
                if retry_mode == "without_source":
                    if not try_without_source_after_illegal:
                        continue
                    attempt_params = dict(params)
                elif retry_mode == "default_source":
                    if not try_with_default_source_after_missing:
                        continue
                    attempt_params = dict(params)
                    attempt_params["source"] = "1"

                try:
                    payload = self._post_form(path, attempt_params)
                    data = self._extract_data(payload)
                    stream_url = find_first_url(data)
                    if not stream_url:
                        raise EzvizError("No stream URL field was found in the response payload.")
                    return {
                        "path": path,
                        "stream_url": stream_url,
                        "raw": data,
                    }
                except EzvizError as exc:
                    error_text = str(exc)
                    if retry_mode == "primary" and attempted_with_source and "source格式非法" in error_text:
                        continue
                    if retry_mode == "primary" and not attempted_with_source and "source为空" in error_text:
                        continue
                    errors.append(f"{path}: {exc}")
                    break
        raise EzvizError("Failed to get live stream URL. " + " | ".join(errors))

    def diagnose_preview(self, url: Optional[str] = None) -> JsonDict:
        target = first_nonempty(url, self.config.manual_live_url)
        if not isinstance(target, str) or not target:
            raise EzvizError("No preview URL provided. Pass --url or set EZVIZ_MANUAL_LIVE_URL.")

        parsed = urllib.parse.urlparse(target)
        query = urllib.parse.parse_qs(parsed.query)
        diagnosis = {
            "scheme": parsed.scheme,
            "host": parsed.netloc,
            "path": parsed.path,
            "hints": [],
        }

        support_h265 = query.get("supportH265", [])
        if support_h265 and support_h265[-1] == "1":
            diagnosis["hints"].append("The URL requests H.265 playback. Use a player or SDK that supports H.265.")

        if "expire" in query:
            diagnosis["hints"].append("This is a signed temporary URL. If playback fails later, regenerate a fresh URL.")

        if parsed.scheme == "ezopen":
            diagnosis["hints"].append("If playback fails and device video encryption is enabled, include the device verify code in the ezopen URL.")
            if "@" not in target.split("open.ys7.com", 1)[0]:
                diagnosis["hints"].append("Current ezopen URL does not appear to embed a verify code before open.ys7.com.")
        elif parsed.scheme in {"https", "http"} and parsed.path.endswith(".m3u8"):
            diagnosis["hints"].append("HLS playback works best in players with H.265 support. Safari/VLC are common checks.")
        elif parsed.scheme in {"https", "http"} and parsed.path.endswith(".flv"):
            diagnosis["hints"].append("HTTP-FLV usually needs flv.js or a player that explicitly supports FLV over HTTP.")
        elif parsed.scheme == "rtmp":
            diagnosis["hints"].append("RTMP playback usually needs ffplay, VLC, OBS, or another RTMP-capable player.")

        if not diagnosis["hints"]:
            diagnosis["hints"].append("No obvious URL issue detected. Verify codec support, encryption, and URL freshness.")
        return diagnosis

    def get_device_status(self, channel_no: Optional[int] = None) -> JsonDict:
        payload = self._post_form("/api/lapp/device/status/get", self._device_params(channel_no=channel_no))
        data = self._extract_data(payload)
        return {
            "channel_no": self.config.channel_no if channel_no is None else channel_no,
            "raw": data,
        }

    def get_device_info(self) -> JsonDict:
        payload = self._post_form(
            "/api/lapp/device/info",
            {
                "accessToken": self.get_access_token(),
                "deviceSerial": self.config.device_serial,
            },
        )
        data = self._extract_data(payload)
        return {
            "device_serial": self.config.device_serial,
            "raw": data,
        }

    def tos_preflight(
        self,
        resolver: Callable[..., Any] = socket.getaddrinfo,
    ) -> JsonDict:
        missing = []
        if not self.config.tos_access_key:
            missing.append("TOS_ACCESS_KEY")
        if not self.config.tos_secret_key:
            missing.append("TOS_SECRET_KEY")
        if not self.config.tos_original:
            missing.append("TOS_ORIGINAL")
        if not self.config.tos_final:
            missing.append("TOS_FINAL")

        endpoint = default_tos_endpoint(self.config.las_region)
        payload: JsonDict = {
            "ok": False,
            "endpoint": endpoint,
            "sdk_package_name": "tos",
            "sdk_installed": tos_sdk_installed(),
            "missing_fields": missing,
            "buckets": {
                "original": safe_tos_bucket(self.config.tos_original),
                "final": safe_tos_bucket(self.config.tos_final),
            },
        }

        if missing:
            payload["stage"] = "config_missing"
            payload["reason"] = "Missing required TOS configuration."
            return payload

        if not payload["sdk_installed"]:
            payload["stage"] = "sdk_missing"
            payload["reason"] = "Missing Python package `tos` required for Volcano TOS uploads."
            payload["install_hint"] = "Preinstall package `tos` in the runtime image/environment; do not guess package names at runtime."
            return payload

        try:
            answers = resolver(endpoint, 443, type=socket.SOCK_STREAM)
            addresses = sorted({item[4][0] for item in answers if item and len(item) >= 5 and item[4]})
            payload["dns"] = {
                "resolved": bool(addresses),
                "addresses": addresses,
            }
        except Exception as exc:
            payload["stage"] = "dns_failed"
            payload["reason"] = f"Failed to resolve TOS endpoint: {exc}"
            payload["dns"] = {
                "resolved": False,
                "addresses": [],
            }
            return payload

        try:
            import tos  # type: ignore

            client = tos.TosClientV2(
                ak=self.config.tos_access_key,
                sk=self.config.tos_secret_key,
                endpoint=endpoint,
                region=self.config.las_region or "cn-beijing",
            )
            checked_buckets = []
            for bucket in {payload["buckets"]["original"], payload["buckets"]["final"]}:
                if not bucket:
                    continue
                client.head_bucket(bucket=bucket)
                checked_buckets.append(bucket)
            payload["ok"] = True
            payload["stage"] = "ok"
            payload["checked_buckets"] = checked_buckets
            return payload
        except Exception as exc:
            payload["stage"] = "bucket_check_failed"
            payload["reason"] = str(exc)
            payload["network_hint"] = "If SDK is installed and credentials are correct, this usually points to TOS network reachability, DNS, or auth failure."
            return payload

    def get_video_encode(self, stream_type: int = 1, channel_no: Optional[int] = None) -> JsonDict:
        effective_channel = self.config.channel_no if channel_no is None else channel_no
        payload = self._request_form(
            "GET",
            self.VIDEO_ENCODE_PATH,
            query_params={"streamType": stream_type},
            header_params={
                "Content-Type": "application/x-www-form-urlencoded",
                "accessToken": self.get_access_token(),
                "deviceSerial": self.config.device_serial,
                "channelNo": effective_channel,
            },
        )
        data = self._extract_data(payload)
        return {
            "device_serial": self.config.device_serial,
            "channel_no": effective_channel,
            "stream_type": stream_type,
            "video_code": data.get("videoCode") if isinstance(data, dict) else None,
            "raw": data,
        }

    def set_video_encode(self, encode_type: str, channel_no: Optional[int] = None) -> JsonDict:
        normalized = encode_type.upper()
        if normalized not in {"H264", "H265"}:
            raise EzvizError("encode_type must be H264 or H265.")
        effective_channel = self.config.channel_no if channel_no is None else channel_no
        payload = self._request_form(
            "POST",
            self.VIDEO_ENCODE_PATH,
            form_params={"encodeType": normalized},
            header_params={
                "Content-Type": "application/x-www-form-urlencoded",
                "accessToken": self.get_access_token(),
                "deviceSerial": self.config.device_serial,
                "channelNo": effective_channel,
            },
        )
        return {
            "device_serial": self.config.device_serial,
            "channel_no": effective_channel,
            "encode_type": normalized,
            "raw": self._extract_data(payload),
        }

    def create_stream(
        self,
        *,
        start_time: str,
        end_time: str,
        local_index: Optional[int] = None,
        access_type: int = 1,
    ) -> JsonDict:
        effective_local_index = self.config.channel_no if local_index is None else local_index
        payload = self._request_form(
            "POST",
            self.STREAM_MANAGE_PATH,
            query_params={
                "accessType": access_type,
                "startTime": start_time,
                "endTime": end_time,
            },
            header_params={
                "accessToken": self.get_access_token(),
                "deviceSerial": self.config.device_serial,
                "localIndex": effective_local_index,
            },
        )
        data = self._extract_data(payload)
        return {
            "device_serial": self.config.device_serial,
            "local_index": effective_local_index,
            "stream_id": data.get("streamId") if isinstance(data, dict) else None,
            "raw": data,
        }

    def update_stream(self, *, stream_id: str, start_time: str, end_time: str) -> JsonDict:
        payload = self._request_form(
            "PUT",
            self.STREAM_MANAGE_PATH,
            query_params={
                "streamId": stream_id,
                "startTime": start_time,
                "endTime": end_time,
            },
            header_params={
                "accessToken": self.get_access_token(),
            },
        )
        return {
            "stream_id": stream_id,
            "raw": self._extract_data(payload),
        }

    def list_streams(
        self,
        *,
        stream_id: Optional[str] = None,
        device_serial: Optional[str] = None,
        page_start: int = 0,
        page_size: int = 50,
        access_type: int = 1,
        status: Optional[int] = None,
    ) -> JsonDict:
        payload = self._request_form(
            "GET",
            self.STREAM_LIST_PATH,
            query_params={
                "streamId": stream_id,
                "pageStart": page_start,
                "pageSize": page_size,
                "accessType": access_type,
                "status": status,
            },
            header_params={
                "accessToken": self.get_access_token(),
                "deviceSerial": first_nonempty(device_serial, self.config.device_serial),
            },
        )
        data = self._extract_data(payload)
        return {
            "raw": data,
            "stream_list": data.get("streamList", []) if isinstance(data, dict) else [],
        }

    def get_stream_address(
        self,
        *,
        stream_id: str,
        protocol: int,
        quality: int = 1,
        support_h265: int = 0,
        mute: int = 0,
        address_type: int = 1,
        expire_time: Optional[int] = None,
    ) -> JsonDict:
        payload = self._request_form(
            "GET",
            self.STREAM_ADDRESS_PATH,
            query_params={
                "streamId": stream_id,
                "protocol": protocol,
                "quality": quality,
                "supportH265": support_h265,
                "mute": mute,
                "type": address_type,
                "expireTime": expire_time,
            },
            header_params={
                "accessToken": self.get_access_token(),
            },
        )
        data = self._extract_data(payload)
        address = data.get("address") if isinstance(data, dict) else None
        return {
            "stream_id": stream_id,
            "protocol": protocol,
            "address": address,
            "raw": data,
        }

    def get_battery_status(self, channel_no: Optional[int] = None) -> JsonDict:
        status = self.get_device_status(channel_no=channel_no)
        raw = status["raw"]
        signals = flatten_battery_signals(raw)
        return {
            "channel_no": status["channel_no"],
            "battery_signals": signals,
            "battery_percent": first_nonempty(
                signals.get("battery"),
                signals.get("battryStatus"),
                signals.get("deviceStatus.battery"),
                signals.get("deviceStatus.battryStatus"),
            ),
            "raw": raw,
        }

    def dump_device(self, channel_no: Optional[int] = None) -> JsonDict:
        info = self.get_device_info()
        status = self.get_device_status(channel_no=channel_no)
        battery = self.get_battery_status(channel_no=channel_no)
        return {
            "device_serial": self.config.device_serial,
            "channel_no": status["channel_no"],
            "device_info": info["raw"],
            "device_status": status["raw"],
            "battery": {
                "battery_percent": battery["battery_percent"],
                "battery_signals": battery["battery_signals"],
            },
        }

    def probe_channels(
        self,
        channels: Iterable[int],
        output_dir: Optional[Path] = None,
        expire_seconds: int = 300,
        protocol_id: Optional[int] = None,
        source: Optional[str] = None,
    ) -> JsonDict:
        results = []
        for channel in channels:
            channel_result: JsonDict = {"channel": channel}

            snapshot_output = output_dir / f"channel-{channel}-snapshot.jpg" if output_dir else None
            try:
                snapshot = self.capture_snapshot(output_path=snapshot_output, channel_no=channel)
                channel_result["snapshot"] = {
                    "ok": True,
                    "snapshot_url": snapshot.get("snapshot_url"),
                    "downloaded_to": snapshot.get("downloaded_to"),
                }
            except EzvizError as exc:
                channel_result["snapshot"] = {"ok": False, "error": str(exc)}

            try:
                live = self.get_live_url(
                    expire_seconds=expire_seconds,
                    protocol_id=protocol_id,
                    source=source,
                    channel_no=channel,
                )
                channel_result["live_url"] = {
                    "ok": True,
                    "path": live.get("path"),
                    "stream_url": live.get("stream_url"),
                }
            except EzvizError as exc:
                channel_result["live_url"] = {"ok": False, "error": str(exc)}

            results.append(channel_result)

        return {
            "channels": results,
            "inference": self._infer_channel_probe(results),
        }

    def _infer_channel_probe(self, results: Iterable[JsonDict]) -> JsonDict:
        by_channel = {item["channel"]: item for item in results}
        channel1 = by_channel.get(1)
        channel2 = by_channel.get(2)

        def channel_has_success(item: Optional[JsonDict]) -> bool:
            if not item:
                return False
            return bool(item.get("snapshot", {}).get("ok") or item.get("live_url", {}).get("ok"))

        if channel_has_success(channel1) and channel_has_success(channel2):
            return {
                "status": "possible_second_logical_channel",
                "message": "Channel 2 returned data. Compare saved snapshots or stream content to confirm it is a distinct lens view.",
            }
        if channel_has_success(channel1) and channel2 and not channel_has_success(channel2):
            return {
                "status": "likely_single_public_channel",
                "message": "Channel 1 works but channel 2 does not. This suggests the dual-lens view is not exposed as a second public API channel.",
            }
        return {
            "status": "inconclusive",
            "message": "Probe did not produce a clean channel-1/channel-2 distinction. Verify credentials, live source, and current device status.",
        }

    def capabilities(self) -> JsonDict:
        return {
            "device_profile": "EZVIZ/萤石 CB60",
            "implemented": {
                "pan_left": True,
                "pan_right": True,
                "tilt_up": False,
                "tilt_down": False,
                "zoom_in": False,
                "zoom_out": False,
                "snapshot": True,
                "live_stream_url": True,
                "manual_live_url": True,
                "channel_probe": True,
                "device_info": True,
                "device_status": True,
                "battery_status": True,
                "stream_manage": True,
                "video_encode": True,
            },
            "verified_runtime": {
                "pan_left": True,
                "pan_right": True,
                "snapshot": True,
                "zoom_rest_control": False,
            },
            "sdk_boundary": {
                "voice_talk": {
                    "implemented": False,
                    "reason": "Official docs expose talk through player SDK methods such as startVoiceTalk, not this portable REST CLI.",
                }
            },
            "notes": {
                "zoom": "Device metadata advertises focal adjustment, but the real CB60 rejected REST PTZ zoom commands during validation.",
                "live_url": "Some EZVIZ tenants require the source parameter for live URL retrieval. Set EZVIZ_LIVE_SOURCE or pass --source.",
                "lens_switch": "No public lens-switch API has been confirmed yet. Use probe-channels to test whether a second logical channel is exposed.",
                "battery": "Battery data depends on what the device reports through /api/lapp/device/status/get.",
            },
        }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Control an EZVIZ CB60 camera via EZVIZ Open Platform APIs.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("capabilities", help="Show the skill's current control surface.")
    subparsers.add_parser("doctor", help="Show which environment variables are present.")
    setup_parser = subparsers.add_parser("setup-env", help="Interactive install wizard for writing a local EZVIZ env file.")
    setup_parser.add_argument("--output", type=Path, default=Path("~/.ezviz_cb60_env"), help="Where to write the local env file.")
    setup_parser.add_argument("--force", action="store_true", help="Overwrite the target file without asking.")

    ptz_parser = subparsers.add_parser("ptz", help="Move the camera left/right or zoom.")
    ptz_parser.add_argument("direction", choices=sorted(EzvizClient.PTZ_COMMANDS.keys()))
    ptz_parser.add_argument("--duration", type=float, default=1.0, help="How long to hold the PTZ action before stop.")
    ptz_parser.add_argument("--speed", type=int, default=1, help="PTZ speed to request.")

    snapshot_parser = subparsers.add_parser("snapshot", help="Capture a snapshot and optionally download it.")
    snapshot_parser.add_argument("--output", type=Path, help="Optional local file path to save the snapshot.")

    live_parser = subparsers.add_parser("live-url", help="Fetch a live stream URL for the camera.")
    live_parser.add_argument("--expire-seconds", type=int, default=300, help="Requested URL lifetime in seconds.")
    live_parser.add_argument("--protocol-id", type=int, help="Optional numeric protocol value expected by the tenant.")
    live_parser.add_argument("--source", help="Optional live source parameter required by some EZVIZ tenants.")

    encode_get_parser = subparsers.add_parser("video-encode-get", help="Fetch current device video encoding info.")
    encode_get_parser.add_argument("--stream-type", type=int, default=1, help="1=main stream, 2=sub stream.")
    encode_get_parser.add_argument("--channel", type=int, help="Optional channel number override.")

    encode_set_parser = subparsers.add_parser("video-encode-set", help="Set device video encode type to H264 or H265.")
    encode_set_parser.add_argument("encode_type", choices=["H264", "H265", "h264", "h265"])
    encode_set_parser.add_argument("--channel", type=int, help="Optional channel number override.")

    stream_create_parser = subparsers.add_parser("stream-create", help="Create a managed live stream for the current device.")
    stream_create_parser.add_argument("--start-time", required=True, help="Start time, format: YYYY-MM-DD HH:MM:SS")
    stream_create_parser.add_argument("--end-time", required=True, help="End time, format: YYYY-MM-DD HH:MM:SS")
    stream_create_parser.add_argument("--local-index", type=int, help="Optional device local index/channel override.")
    stream_create_parser.add_argument("--access-type", type=int, default=1, help="1=device access, 2=rtmp access")

    stream_update_parser = subparsers.add_parser("stream-update", help="Update a managed live stream time window.")
    stream_update_parser.add_argument("--stream-id", required=True)
    stream_update_parser.add_argument("--start-time", required=True, help="Start time, format: YYYY-MM-DD HH:MM:SS")
    stream_update_parser.add_argument("--end-time", required=True, help="End time, format: YYYY-MM-DD HH:MM:SS")

    stream_list_parser = subparsers.add_parser("stream-list", help="List managed streams for the current device.")
    stream_list_parser.add_argument("--stream-id")
    stream_list_parser.add_argument("--page-start", type=int, default=0)
    stream_list_parser.add_argument("--page-size", type=int, default=50)
    stream_list_parser.add_argument("--access-type", type=int, default=1)
    stream_list_parser.add_argument("--status", type=int, choices=[0, 1])

    stream_address_parser = subparsers.add_parser("stream-address", help="Fetch a playback address by streamId.")
    stream_address_parser.add_argument("--stream-id", required=True)
    stream_address_parser.add_argument("--protocol", type=int, required=True, choices=[1, 2, 3, 4], help="1=hls 2=rtmp 3=flv (legacy 4 is also treated as flv)")
    stream_address_parser.add_argument("--quality", type=int, default=1, choices=[1, 2])
    stream_address_parser.add_argument("--support-h265", type=int, default=0, choices=[0, 1])
    stream_address_parser.add_argument("--mute", type=int, default=0, choices=[0, 1])
    stream_address_parser.add_argument("--address-type", type=int, default=1, choices=[1, 2])
    stream_address_parser.add_argument("--expire-time", type=int)

    diagnose_parser = subparsers.add_parser("diagnose-preview", help="Diagnose a preview URL or manual stream URL.")
    diagnose_parser.add_argument("--url", help="Preview URL to inspect. Falls back to EZVIZ_MANUAL_LIVE_URL.")

    subparsers.add_parser("tos-preflight", help="Check whether the runtime can use Volcano TOS for uploads.")

    probe_parser = subparsers.add_parser("probe-channels", help="Probe whether the device exposes a second logical channel.")
    probe_parser.add_argument("--channels", nargs="+", type=int, default=[1, 2], help="Channel numbers to probe. Defaults to 1 2.")
    probe_parser.add_argument("--output-dir", type=Path, help="Optional directory for saving probe snapshots.")
    probe_parser.add_argument("--expire-seconds", type=int, default=300, help="Requested URL lifetime in seconds.")
    probe_parser.add_argument("--protocol-id", type=int, help="Optional numeric protocol value expected by the tenant.")
    probe_parser.add_argument("--source", help="Optional live source parameter required by some EZVIZ tenants.")

    subparsers.add_parser("device-info", help="Fetch raw device info from the EZVIZ device info API.")

    dump_parser = subparsers.add_parser("dump-device", help="Fetch device info, status, and battery in one payload.")
    dump_parser.add_argument("--channel", type=int, help="Optional channel number override for status and battery.")

    status_parser = subparsers.add_parser("device-status", help="Fetch raw device status from the EZVIZ status API.")
    status_parser.add_argument("--channel", type=int, help="Optional channel number override.")

    battery_parser = subparsers.add_parser("battery", help="Fetch device status and extract likely battery-related fields.")
    battery_parser.add_argument("--channel", type=int, help="Optional channel number override.")

    subparsers.add_parser("talk", help="Explain the current voice-talk boundary for this portable skill.")
    return parser


def emit_json(payload: JsonDict) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


def main(argv: Optional[Iterable[str]] = None) -> int:
    try:
        normalized_argv, env_file = extract_env_file_arg(argv)
    except EzvizError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    parser = build_parser()
    args = parser.parse_args(normalized_argv)

    config = EnvConfig.from_env(env_file=env_file)
    client = EzvizClient(config)

    if args.command == "capabilities":
        emit_json(client.capabilities())
        return 0

    if args.command == "doctor":
        emit_json(config.doctor())
        return 0

    if args.command == "setup-env":
        emit_json(run_setup_wizard(output_path=args.output, force=args.force))
        return 0

    if args.command == "talk":
        emit_json(
            {
                "implemented": False,
                "reason": "Voice talk is documented by EZVIZ SDK player methods but is not implemented in this portable REST-first script.",
                "next_step": "Use the official native SDK if full duplex talk is required.",
            }
        )
        return 2

    if args.command == "diagnose-preview":
        emit_json(client.diagnose_preview(url=args.url))
        return 0
    if args.command == "tos-preflight":
        emit_json(client.tos_preflight())
        return 0
    if args.command == "device-info":
        emit_json(client.get_device_info())
        return 0
    if args.command == "video-encode-get":
        emit_json(client.get_video_encode(stream_type=args.stream_type, channel_no=args.channel))
        return 0
    if args.command == "video-encode-set":
        emit_json(client.set_video_encode(args.encode_type, channel_no=args.channel))
        return 0
    if args.command == "stream-create":
        emit_json(
            client.create_stream(
                start_time=args.start_time,
                end_time=args.end_time,
                local_index=args.local_index,
                access_type=args.access_type,
            )
        )
        return 0
    if args.command == "stream-update":
        emit_json(
            client.update_stream(
                stream_id=args.stream_id,
                start_time=args.start_time,
                end_time=args.end_time,
            )
        )
        return 0
    if args.command == "stream-list":
        emit_json(
            client.list_streams(
                stream_id=args.stream_id,
                page_start=args.page_start,
                page_size=args.page_size,
                access_type=args.access_type,
                status=args.status,
            )
        )
        return 0
    if args.command == "stream-address":
        normalized_protocol = normalize_stream_address_protocol(args.protocol)
        emit_json(
            client.get_stream_address(
                stream_id=args.stream_id,
                protocol=normalized_protocol,
                quality=args.quality,
                support_h265=args.support_h265,
                mute=args.mute,
                address_type=args.address_type,
                expire_time=args.expire_time,
            )
        )
        return 0
    if args.command == "dump-device":
        emit_json(client.dump_device(channel_no=args.channel))
        return 0
    if args.command == "device-status":
        emit_json(client.get_device_status(channel_no=args.channel))
        return 0
    if args.command == "battery":
        emit_json(client.get_battery_status(channel_no=args.channel))
        return 0

    try:
        if args.command == "ptz":
            emit_json(client.ptz_pulse(args.direction, duration=args.duration, speed=args.speed))
            return 0
        if args.command == "snapshot":
            emit_json(client.capture_snapshot(output_path=args.output))
            return 0
        if args.command == "live-url":
            emit_json(
                client.get_live_url(
                    expire_seconds=args.expire_seconds,
                    protocol_id=args.protocol_id,
                    source=args.source,
                )
            )
            return 0
        if args.command == "probe-channels":
            emit_json(
                client.probe_channels(
                    channels=args.channels,
                    output_dir=args.output_dir,
                    expire_seconds=args.expire_seconds,
                    protocol_id=args.protocol_id,
                    source=args.source,
                )
            )
            return 0
    except EzvizError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
