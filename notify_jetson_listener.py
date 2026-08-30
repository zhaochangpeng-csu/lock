#!/usr/bin/env python3
"""Jetson Nano 硬件认证失败监听服务。

该服务只做一件事：轮询硬件端写入的 auth_context.json（默认
logs/auth_context.json）。一旦发现一条新的 fusion_passed=false 记录，
就向本机通知中心（notify_hub_service.py）POST 一条认证失败事件。

无需第三方依赖，Python 3.8+ 可用。

常用环境变量（也可用命令行参数覆盖）：
    LOCK_AUTH_CONTEXT_PATH   认证结果文件路径，默认 logs/auth_context.json
    AUTH_FAIL_HUB_URL        本机通知中心地址，例如 http://192.168.1.100:8788
    AUTH_FAIL_HUB_TOKEN      通知中心 Bearer token
    AUTH_FAIL_POLL_INTERVAL  轮询间隔秒，默认 1.0
    AUTH_FAIL_RETRY_SECONDS  发送失败后的重试间隔秒，默认 10.0
    AUTH_FAIL_STATE_PATH     去重状态文件，默认 logs/auth_fail_notifier_state.json

用法：
    python3 notify_jetson_listener.py
    python3 notify_jetson_listener.py --hub-url http://192.168.1.100:8788 --token <token>
    python3 notify_jetson_listener.py --test   # 发送一条测试失败事件后退出
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import socket
import sys
import time
from pathlib import Path
from typing import Any, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

LOG = logging.getLogger("notify_jetson")


def _load_dotenv(path: Path) -> None:
    """Minimal .env loader: only sets keys that are not already exported."""
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _read_json(path: str) -> Optional[dict[str, Any]]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _write_json(path: str, value: dict[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(target)


def _env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    try:
        return float(value) if value else default
    except ValueError:
        return default


def _load_state(state_path: str) -> dict[str, Any]:
    state = _read_json(state_path)
    if state is None:
        return {
            "version": 1,
            "initialized_with_credential_id": None,
            "last_notified_credential_id": None,
            "next_attempt_at": 0.0,
        }
    state.setdefault("version", 1)
    state.setdefault("initialized_with_credential_id", None)
    state.setdefault("last_notified_credential_id", None)
    state.setdefault("next_attempt_at", 0.0)
    return state


def _build_failure_payload(
    record: dict[str, Any], device: Optional[str] = None
) -> dict[str, Any]:
    time_text = str(record.get("time") or "")
    score = record.get("fusion_score")
    score_text = f"{float(score):.3f}" if isinstance(score, (int, float)) else "未知"
    host = device or socket.gethostname()
    message = (
        f"智能门锁认证失败（{host}）\n"
        f"融合分数：{score_text}\n"
        f"时间：{time_text or '未知'}\n"
        "请及时处理。"
    )
    return {
        "event": "auth_failed",
        "credential_id": str(record.get("credential_id") or ""),
        "time": time_text,
        "fusion_score": score,
        "fusion_passed": bool(record.get("fusion_passed")),
        "device": host,
        "message": message,
    }


def send_event(hub_url: str, token: str, payload: dict[str, Any], timeout: float = 10.0) -> tuple[bool, dict[str, Any]]:
    """POST 到通知中心。返回 (成功?, 响应或错误信息)。"""
    url = hub_url.rstrip("/") + "/notify/auth_failed"
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            body = response.read(4096).decode("utf-8", "replace")
            try:
                result = json.loads(body)
                if isinstance(result, dict):
                    return bool(result.get("ok")), result
                return response.status < 300, {"raw": body}
            except json.JSONDecodeError:
                return response.status < 300, {"raw": body}
    except HTTPError as exc:
        body = exc.read(4096).decode("utf-8", "replace")
        return False, {"http_error": exc.code, "raw": body}
    except (URLError, TimeoutError, OSError) as exc:
        return False, {"error": str(exc)}


def run_forever(args: argparse.Namespace) -> None:
    context_path = args.auth_context or os.getenv("LOCK_AUTH_CONTEXT_PATH", "logs/auth_context.json")
    state_path = args.state or os.getenv("AUTH_FAIL_STATE_PATH", "logs/auth_fail_notifier_state.json")
    hub_url = args.hub_url or os.getenv("AUTH_FAIL_HUB_URL", "")
    token = args.token or os.getenv("AUTH_FAIL_HUB_TOKEN", "")
    poll_interval = args.poll_interval or _env_float("AUTH_FAIL_POLL_INTERVAL", 1.0)
    retry_seconds = args.retry_seconds or _env_float("AUTH_FAIL_RETRY_SECONDS", 10.0)
    device = args.device or os.getenv("AUTH_FAIL_DEVICE", socket.gethostname())

    if not hub_url:
        LOG.error("AUTH_FAIL_HUB_URL 未设置，无法发送失败事件。请用 --hub-url 或环境变量指定本机服务地址。")
        raise SystemExit(2)

    state = _load_state(state_path)

    # 首次启动只初始化去重状态，不补发历史失败。
    first_record = _read_json(context_path)
    if first_record is not None and state.get("initialized_with_credential_id") is None:
        state["initialized_with_credential_id"] = first_record.get("credential_id")
        state["last_notified_credential_id"] = first_record.get("credential_id")
        _write_json(state_path, state)
        LOG.info(
            "listener initialized; current credential_id=%s (不会补发历史失败)",
            first_record.get("credential_id"),
        )

    LOG.info("watching %s -> %s every %.1fs", context_path, hub_url, poll_interval)
    while True:
        record = _read_json(context_path)
        if record is not None:
            credential_id = str(record.get("credential_id") or "")
            failed = not bool(record.get("fusion_passed"))
            last_notified = state.get("last_notified_credential_id")
            now = time.time()

            if (
                failed
                and credential_id
                and credential_id != last_notified
                and now >= float(state.get("next_attempt_at", 0.0))
            ):
                payload = _build_failure_payload(record, device)
                LOG.info("new auth failure detected; credential_id=%s; sending...", credential_id)
                ok, result = send_event(hub_url, token, payload, timeout=10.0)
                if ok:
                    state["last_notified_credential_id"] = credential_id
                    state["next_attempt_at"] = 0.0
                    _write_json(state_path, state)
                    LOG.info("auth failure notification sent; credential_id=%s", credential_id)
                else:
                    state["next_attempt_at"] = now + retry_seconds
                    _write_json(state_path, state)
                    LOG.warning(
                        "send failed (%s); will retry in %.1fs",
                        result,
                        retry_seconds,
                    )

        time.sleep(poll_interval)


def run_test(args: argparse.Namespace) -> None:
    hub_url = args.hub_url or os.getenv("AUTH_FAIL_HUB_URL", "")
    token = args.token or os.getenv("AUTH_FAIL_HUB_TOKEN", "")
    device = args.device or os.getenv("AUTH_FAIL_DEVICE", socket.gethostname())
    if not hub_url:
        LOG.error("AUTH_FAIL_HUB_URL 未设置，无法发送测试事件。")
        raise SystemExit(2)
    record = {
        "credential_id": "test-credential-id",
        "time": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "fusion_passed": False,
        "fusion_score": 0.123,
    }
    payload = _build_failure_payload(record, device)
    payload["event"] = "auth_failed_test"
    payload["message"] = f"[测试] {payload['message']}"
    ok, result = send_event(hub_url, token, payload, timeout=10.0)
    print(json.dumps({"ok": ok, "result": result}, ensure_ascii=False, indent=2))
    raise SystemExit(0 if ok else 1)


def main() -> None:
    parser = argparse.ArgumentParser(description="Jetson auth failure listener")
    parser.add_argument("--auth-context", help="auth_context.json path")
    parser.add_argument("--hub-url", help="local hub URL, e.g. http://192.168.1.100:8788")
    parser.add_argument("--token", help="hub bearer token")
    parser.add_argument("--poll-interval", type=float, help="poll interval seconds")
    parser.add_argument("--retry-seconds", type=float, help="retry interval after a failed send")
    parser.add_argument("--state", help="dedupe state file path")
    parser.add_argument("--device", help="device name shown in the message")
    parser.add_argument("--test", action="store_true", help="send one test failure event and exit")
    parser.add_argument("--log-level", default=os.getenv("LOG_LEVEL", "INFO"))
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    _load_dotenv(Path(__file__).parent / ".env")

    if args.test:
        run_test(args)
    else:
        run_forever(args)


if __name__ == "__main__":
    main()
