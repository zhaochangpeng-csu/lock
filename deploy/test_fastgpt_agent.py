from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from smart_lock.config import load_config
from voice_agent import VoiceAgent


def main() -> int:
    parser = argparse.ArgumentParser(description="Test a real FastGPT smart-lock app and tool calls")
    parser.add_argument("--config", default=str(ROOT / "config.yaml"))
    parser.add_argument("--host", default="0.0.0.0", help="Gateway bind host")
    parser.add_argument("--port", type=int, default=None, help="Gateway port used by the FastGPT HTTP tools")
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--chat-id", default=f"smart-lock-e2e-{int(time.time())}")
    parser.add_argument(
        "--scenario",
        default="我是快递员，我来送快递，请帮我联系业主。",
        help="Chinese user message sent to the FastGPT app",
    )
    parser.add_argument("--expect-tool", action="append", default=[])
    parser.add_argument("--forbid-tool", action="append", default=[])
    parser.add_argument(
        "--auth-context",
        choices=("valid", "invalid"),
        help="Inject a fresh hardware auth credential into a temporary config",
    )
    args = parser.parse_args()
    config = load_config(args.config)

    missing = [
        name
        for name in ("FASTGPT_API_BASE", "FASTGPT_APP_API_KEY", "LOCK_TOOL_TOKEN")
        if not os.getenv(name)
    ]
    if missing:
        print(f"SKIP missing environment variables: {', '.join(missing)}")
        print("Set them after creating the FastGPT smart-lock app and registering HTTP tools.")
        return 0

    port = args.port if args.port is not None else config.agent.tool_gateway.port

    with tempfile.TemporaryDirectory(prefix="smart-lock-fastgpt-agent-") as tmpdir:
        call_log = Path(tmpdir) / "tool_calls.jsonl"
        runtime_config = prepare_runtime_config(args.config, Path(tmpdir), args.auth_context)
        env = os.environ.copy()
        env.update(
            {
                "LOCK_TOOL_GATEWAY_HOST": args.host,
                "LOCK_TOOL_GATEWAY_PORT": str(port),
                "LOCK_TOOL_CALL_LOG": str(call_log),
                "LOCK_AUTH_CONTEXT_PATH": str(Path(tmpdir) / "auth_context.json"),
                "SMART_LOCK_NO_UNLOCK": "1",
            }
        )

        proc = subprocess.Popen(
            [
                sys.executable,
                str(ROOT / "lock_tool_gateway.py"),
                "--config",
                str(runtime_config),
                "--host",
                args.host,
                "--port",
                str(port),
            ],
            cwd=ROOT,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            wait_port("127.0.0.1", port, args.timeout)
            print(f"gateway ok: 127.0.0.1:{port}")

            agent = VoiceAgent(load_config(runtime_config), require_fastgpt=True)
            reply = agent.handle_text(args.scenario, chat_id=args.chat_id)
            print(f"agent reply: {reply.text}")

            calls = read_jsonl(call_log)
            if not calls:
                print("FAIL FastGPT replied but did not call any Jetson tool.")
                print("Check FastGPT app system prompt, HTTP tool registration, base URL, and Authorization header.")
                return 3

            tools = [str(item.get("tool", "")) for item in calls]
            print(f"tool calls: {', '.join(tools)}")
            expected = args.expect_tool or ["notify_owner"]
            missing_expected = [tool for tool in expected if tool not in tools]
            called_forbidden = [tool for tool in args.forbid_tool if tool in tools]
            if missing_expected:
                print(f"FAIL expected tools were not called: {missing_expected}")
                return 4
            if called_forbidden:
                print(f"FAIL forbidden tools were called: {called_forbidden}")
                return 5
            if "request_unlock" in tools:
                if "current_auth_context" not in tools or tools.index("current_auth_context") > tools.index(
                    "request_unlock"
                ):
                    print("FAIL request_unlock ran before current_auth_context")
                    return 6
                unlock_result = next(
                    item.get("result", {}) for item in calls if item.get("tool") == "request_unlock"
                )
                if unlock_result.get("allowed") is not False or unlock_result.get("dry_run") is not True:
                    print(f"FAIL dry-run safety gate did not block unlock: {unlock_result}")
                    return 7
            print("fastgpt agent tool-call test passed")
            return 0
        finally:
            stop_process(proc)


def prepare_runtime_config(config_path: str, tmpdir: Path, auth_context: str | None) -> Path:
    source = Path(config_path).resolve()
    if auth_context is None:
        return source
    raw = yaml.safe_load(source.read_text(encoding="utf-8-sig"))
    auth_context_path = tmpdir / "auth_context.json"
    raw["agent"]["safety"]["auth_context_path"] = str(auth_context_path)
    runtime_config = tmpdir / "config.yaml"
    runtime_config.write_text(
        yaml.safe_dump(raw, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    auth_context_path.write_text(
        json.dumps(
            {
                "credential_id": f"e2e-{auth_context}",
                "time": datetime.now(timezone.utc).isoformat(),
                "fusion_passed": auth_context == "valid",
                "fusion_score": 0.95,
                "consumed": False,
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    return runtime_config


def wait_port(host: str, port: int, timeout_seconds: float) -> None:
    import socket

    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.5)
            if sock.connect_ex((host, port)) == 0:
                return
        time.sleep(0.25)
    raise TimeoutError(f"Timed out waiting for {host}:{port}")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def stop_process(proc: subprocess.Popen[str]) -> None:
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=10)


if __name__ == "__main__":
    raise SystemExit(main())
