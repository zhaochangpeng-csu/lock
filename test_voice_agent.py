from __future__ import annotations

import json
import os
import threading
import tempfile
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from smart_lock.config import load_config
from voice_agent import VoiceAgent, _extract_asr_text, _local_model_or_id, _sounddevice_device


class _FastGPTHandler(BaseHTTPRequestHandler):
    seen: dict[str, Any] = {}

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        _FastGPTHandler.seen = {
            "path": self.path,
            "authorization": self.headers.get("Authorization", ""),
            "body": json.loads(body.decode("utf-8")),
        }
        payload = {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "收到，门锁语音 Agent 测试通过。",
                    }
                }
            ]
        }
        encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, format: str, *args: object) -> None:
        return


def main() -> None:
    test_local_model_resolution()
    assert _sounddevice_device("2") == 2
    assert _sounddevice_device("USB Microphone") == "USB Microphone"
    assert _extract_asr_text([{"text": "测试语音"}]) == "测试语音"
    base_config = load_config("config.yaml")
    fallback_config = replace(
        base_config,
        agent=replace(
            base_config.agent,
            fastgpt=replace(
                base_config.agent.fastgpt,
                app_api_key_env="FASTGPT_TEST_MISSING_API_KEY",
            ),
        ),
    )
    fallback = VoiceAgent(fallback_config).handle_text("你好", chat_id="test")
    assert fallback.text == "Local fallback received: 你好"

    server = ThreadingHTTPServer(("127.0.0.1", 0), _FastGPTHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        os.environ["FASTGPT_API_BASE"] = f"http://127.0.0.1:{port}"
        os.environ["FASTGPT_APP_API_KEY"] = "fastgpt-test-key"
        os.environ["FASTGPT_APP_ID"] = "app-test-id"
        os.environ["HTTP_PROXY"] = "http://127.0.0.1:1"
        reply = VoiceAgent(load_config("config.yaml"), require_fastgpt=True).handle_text(
            "请测试语音 Agent",
            chat_id="voice-test",
        )
        assert reply.text == "收到，门锁语音 Agent 测试通过。"
        seen = _FastGPTHandler.seen
        assert seen["path"] == "/api/v1/chat/completions"
        assert seen["authorization"] == "Bearer fastgpt-test-key"
        assert seen["body"]["appId"] == "app-test-id"
        assert seen["body"]["chatId"] == "voice-test"
        assert seen["body"]["messages"][0]["role"] == "system"
        assert "Before any unlock request, call current_auth_context" in seen["body"]["messages"][0]["content"]
        assert seen["body"]["messages"][-1]["content"] == "请测试语音 Agent"
        print("voice_agent text and FastGPT API smoke test passed")
    finally:
        server.shutdown()
        thread.join(timeout=5)
        for key in ("FASTGPT_API_BASE", "FASTGPT_APP_API_KEY", "FASTGPT_APP_ID", "HTTP_PROXY"):
            os.environ.pop(key, None)


def test_local_model_resolution() -> None:
    with tempfile.TemporaryDirectory(prefix="smart-lock-model-") as tmpdir:
        root = Path(tmpdir)
        model_id = "iic/SenseVoiceSmall"
        assert _local_model_or_id(str(root), model_id) == model_id
        model_dir = root / "SenseVoiceSmall"
        model_dir.mkdir()
        (model_dir / "config.yaml").write_text("model: test\n", encoding="utf-8")
        assert _local_model_or_id(str(root), model_id) == str(model_dir)


if __name__ == "__main__":
    main()
