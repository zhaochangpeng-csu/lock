#!/usr/bin/env python3
"""本机通知中心服务。

接收 Jetson 硬件端发来的认证失败事件，并调用 WorkBuddy 助理的
QQ / 微信机器人把消息发到手机。

默认监听 0.0.0.0:8788，供 Jetson 通过局域网访问。

无需第三方依赖，Python 3.8+ 可用。

环境变量：
    NOTIFY_HUB_HOST          监听地址，默认 0.0.0.0
    NOTIFY_HUB_PORT          监听端口，默认 8788
    NOTIFY_HUB_TOKEN         校验 Jetson 请求的 Bearer token；为空则不校验
    NOTIFY_HUB_LOG_PATH      请求日志，默认 logs/notify_hub.log
    NOTIFY_HUB_EVENTS_PATH   事件 JSONL，默认 logs/notify_hub_events.jsonl

    WORKBUDDY_BOT_URL        必填（真实发送时）。WorkBuddy 助理 QQ/微信机器人发消息接口。
                             例如 http://127.0.0.1:xxxx/api/message/send
    WORKBUDDY_BOT_TOKEN      机器人接口 Bearer token；没有则不携带
    WORKBUDDY_BOT_CHANNEL    发送渠道，qq / wechat / qq,wechat，默认 qq
    WORKBUDDY_BOT_TIMEOUT    调用机器人超时秒，默认 10
    WORKBUDDY_BOT_PAYLOAD    JSON 模板字符串。可用占位符 {message} 和 {channel}。
                             默认 {"channel": "{channel}", "message": "{message}"}

用法：
    python3 notify_hub_service.py
    python3 notify_hub_service.py --port 8788 --host 0.0.0.0
    python3 notify_hub_service.py --test "测试消息"   # 直接给手机发一条测试消息
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

LOG = logging.getLogger("notify_hub")


def _load_dotenv(path: Path) -> None:
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


def _append_jsonl(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


class WorkbuddyBotSender:
    """通过 WorkBuddy 助理的 QQ / 微信机器人发送消息。

    默认报文格式为 {"channel": "<qq|wechat>", "message": "<文本>"}。
    如果 WorkBuddy 机器人接口不是这个格式，设置 WORKBUDDY_BOT_PAYLOAD
    为对应 JSON 模板，用 {message} / {channel} 占位。
    """

    def __init__(self) -> None:
        self.url = os.getenv("WORKBUDDY_BOT_URL", "").strip()
        self.token = os.getenv("WORKBUDDY_BOT_TOKEN", "").strip()
        self.channels = [
            channel.strip()
            for channel in os.getenv("WORKBUDDY_BOT_CHANNEL", "qq").split(",")
            if channel.strip()
        ]
        self.timeout = self._env_float("WORKBUDDY_BOT_TIMEOUT", 10.0)
        raw_template = os.getenv(
            "WORKBUDDY_BOT_PAYLOAD",
            '{"channel": "{channel}", "message": "{message}"}',
        )
        try:
            self.payload_template = json.loads(raw_template)
        except json.JSONDecodeError as exc:
            LOG.error("WORKBUDDY_BOT_PAYLOAD 不是合法 JSON: %s", exc)
            self.payload_template = None

    @staticmethod
    def _env_float(name: str, default: float) -> float:
        try:
            return float(os.getenv(name)) if os.getenv(name) else default
        except ValueError:
            return default

    def _fill(self, value: Any, message: str, channel: str) -> Any:
        if isinstance(value, str):
            # 先替换 channel，再替换 message，避免消息正文里的 {channel} 被二次替换。
            return value.replace("{channel}", channel).replace("{message}", message)
        if isinstance(value, list):
            return [self._fill(item, message, channel) for item in value]
        if isinstance(value, dict):
            return {str(k): self._fill(v, message, channel) for k, v in value.items()}
        return value

    def send(self, message: str, channel: str) -> dict[str, Any]:
        if not self.url:
            return {
                "channel": channel,
                "ok": True,
                "mocked": True,
                "reason": "WORKBUDDY_BOT_URL 未设置，仅记录日志（mock）",
            }
        if self.payload_template is None:
            return {
                "channel": channel,
                "ok": False,
                "reason": "WORKBUDDY_BOT_PAYLOAD 不是合法 JSON，无法发送",
            }

        payload = self._fill(self.payload_template, message, channel)
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "smart-lock-notify-hub/1.0",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        request = Request(
            self.url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                body = response.read(8192).decode("utf-8", "replace")
                return {
                    "channel": channel,
                    "ok": True,
                    "http_status": response.status,
                    "response": body[:2000],
                }
        except HTTPError as exc:
            body = exc.read(8192).decode("utf-8", "replace")
            return {
                "channel": channel,
                "ok": False,
                "http_status": exc.code,
                "response": body[:2000],
            }
        except (URLError, TimeoutError, OSError) as exc:
            return {"channel": channel, "ok": False, "reason": str(exc)}


class NotifyHubApp:
    def __init__(self) -> None:
        self.token = os.getenv("NOTIFY_HUB_TOKEN", "")
        self.events_path = Path(
            os.getenv("NOTIFY_HUB_EVENTS_PATH", "logs/notify_hub_events.jsonl")
        )
        self.sender = WorkbuddyBotSender()

    def check_token(self, authorization: str) -> bool:
        if not self.token:
            return True
        return authorization == f"Bearer {self.token}"

    def record_event(self, event: dict[str, Any]) -> None:
        _append_jsonl(
            self.events_path,
            {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                **event,
            },
        )

    def handle_auth_failed(self, body: dict[str, Any]) -> dict[str, Any]:
        message = str(body.get("message") or "智能门锁认证失败")
        event_name = str(body.get("event") or "auth_failed")
        results = []
        for channel in self.sender.channels:
            LOG.info("sending %s via WorkBuddy channel=%s", event_name, channel)
            result = self.sender.send(message, channel)
            LOG.info("workbuddy send result: %s", result)
            results.append(result)

        ok = all(bool(item.get("ok")) for item in results) and bool(results)
        response = {
            "ok": ok,
            "backend": "workbuddy" if self.sender.url else "workbuddy-mock",
            "channels": self.sender.channels,
            "results": results,
        }
        self.record_event(
            {
                "type": "auth_failed_forward",
                "source_payload": body,
                "response": response,
            }
        )
        return response


class HubHandler(BaseHTTPRequestHandler):
    server_version = "SmartLockNotifyHub/1.0"

    @property
    def app(self) -> NotifyHubApp:
        return self.server.app  # type: ignore[attr-defined]

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        if self.path.split("?")[0] == "/health":
            self._send_json(
                200,
                {
                    "status": "ok",
                    "service": "smart-lock-notify-hub",
                    "workbuddy_bot_configured": bool(self.app.sender.url),
                    "channels": self.app.sender.channels,
                },
            )
        else:
            self._send_json(404, {"status": "not_found"})

    def do_POST(self) -> None:
        path = self.path.split("?")[0]
        if path not in ("/notify/auth_failed", "/test/notify"):
            self._send_json(404, {"status": "not_found"})
            return

        authorization = self.headers.get("Authorization", "")
        if not self.app.check_token(authorization):
            self._send_json(401, {"ok": False, "reason": "invalid token"})
            return

        try:
            length = int(self.headers.get("Content-Length", "0"))
            raw_body = self.rfile.read(length) if length else b"{}"
            body = json.loads(raw_body.decode("utf-8", "replace"))
            if not isinstance(body, dict):
                raise ValueError("body must be a JSON object")
        except (ValueError, json.JSONDecodeError) as exc:
            self._send_json(400, {"ok": False, "reason": f"invalid json: {exc}"})
            return

        if path == "/test/notify":
            body = {
                "event": "notify_hub_test",
                "message": body.get("message") or "这是一条来自智能门锁通知中心的测试消息",
                "device": "local",
            }

        result = self.app.handle_auth_failed(body)
        status = 200 if result.get("ok") else 502
        self._send_json(status, result)

    def log_message(self, format: str, *args: Any) -> None:
        LOG.info(
            "%s %s %s",
            self.address_string(),
            format % args,
            self.headers.get("Authorization", "") and "<token-present>",
        )


class SmartLockNotifyServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], app: NotifyHubApp) -> None:
        super().__init__(address, HubHandler)
        self.app = app


def run_server(args: argparse.Namespace) -> None:
    host = args.host or os.getenv("NOTIFY_HUB_HOST", "0.0.0.0")
    port = args.port if args.port is not None else int(os.getenv("NOTIFY_HUB_PORT", "8788"))
    app = NotifyHubApp()
    server = SmartLockNotifyServer((host, port), app)
    LOG.info("notify hub listening on %s:%s", host, port)
    LOG.info(
        "workbuddy bot %s; channels=%s",
        "configured" if app.sender.url else "NOT configured (mock)",
        app.sender.channels,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        LOG.info("interrupted")
    finally:
        server.server_close()


def run_test(args: argparse.Namespace) -> None:
    message = args.test_message or "这是一条来自智能门锁通知中心的测试消息"
    app = NotifyHubApp()
    results = []
    for channel in app.sender.channels:
        LOG.info("sending test message via WorkBuddy channel=%s", channel)
        result = app.sender.send(message, channel)
        LOG.info("result: %s", result)
        results.append(result)
    ok = all(bool(item.get("ok")) for item in results) and bool(results)
    print(json.dumps({"ok": ok, "results": results}, ensure_ascii=False, indent=2))
    raise SystemExit(0 if ok else 1)


def main() -> None:
    parser = argparse.ArgumentParser(description="Smart lock local notify hub")
    parser.add_argument("--host", help="bind address")
    parser.add_argument("--port", type=int, help="bind port")
    parser.add_argument("--test", action="store_true", help="send a test message and exit")
    parser.add_argument("--test-message", help="test message text")
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
        run_server(args)


if __name__ == "__main__":
    main()
