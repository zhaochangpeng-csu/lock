from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Iterable


ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description="Service lifecycle smoke tests")
    subparsers = parser.add_subparsers(dest="command", required=True)

    gateway = subparsers.add_parser("gateway", help="Test lock tool gateway start/restart/stop")
    gateway.add_argument("--host", default="127.0.0.1")
    gateway.add_argument("--port", type=int, default=9877)
    gateway.add_argument("--config", default=str(ROOT / "config.yaml"))
    gateway.add_argument("--token", default="lifecycle-test-token")
    gateway.add_argument("--python", default=sys.executable)

    fastgpt = subparsers.add_parser("fastgpt", help="Check or test FastGPT compose lifecycle")
    fastgpt.add_argument("--compose-dir", default="/mnt/c/users/hoyo/desktop/fastgpt-smart-lock")
    fastgpt.add_argument("--health-url", help="Defaults to FASTGPT_FE_DOMAIN or FASTGPT_HTTP_PORT in compose .env")
    fastgpt.add_argument("--ports", type=int, nargs="+", help="Defaults to FASTGPT_*_PORT values in compose .env")
    fastgpt.add_argument("--start", action="store_true", help="Run full up/restart/down lifecycle")
    fastgpt.add_argument("--allow-pull", action="store_true", help="Allow compose up to pull missing images")

    args = parser.parse_args()
    if args.command == "gateway":
        return test_gateway(args)
    if args.command == "fastgpt":
        return test_fastgpt(args)
    return 2


def test_gateway(args: argparse.Namespace) -> int:
    connect_host = "127.0.0.1" if args.host in {"0.0.0.0", "::"} else args.host
    if is_port_open(connect_host, args.port):
        print(f"FAIL port already in use before start: {connect_host}:{args.port}")
        return 2

    with tempfile.TemporaryDirectory(prefix="smart-lock-gateway-test-") as tmpdir:
        env = os.environ.copy()
        env.update(
            {
                "LOCK_TOOL_TOKEN": args.token,
                "LOCK_TOOL_GATEWAY_HOST": args.host,
                "LOCK_TOOL_GATEWAY_PORT": str(args.port),
                "LOCK_TOOL_CALL_LOG": str(Path(tmpdir) / "tool_calls.jsonl"),
                "LOCK_AUTH_CONTEXT_PATH": str(Path(tmpdir) / "auth_context.json"),
                "SMART_LOCK_NO_UNLOCK": "1",
            }
        )

        proc = start_gateway(args.python, args.config, args.host, args.port, env)
        try:
            wait_http(f"http://{connect_host}:{args.port}/health", expected_status=200)
            expect_http_status(f"http://{connect_host}:{args.port}/tools/current_auth_context", 401)
            get_json(
                f"http://{connect_host}:{args.port}/tools/current_auth_context",
                token=args.token,
            )
            unlock = post_json(
                f"http://{connect_host}:{args.port}/tools/request_unlock",
                {"reason": "lifecycle test"},
                token=args.token,
            )
            if unlock.get("allowed") is not False or unlock.get("dry_run") is not True:
                print(f"FAIL unexpected request_unlock response: {unlock}")
                return 3
            print(f"start ok: http://{connect_host}:{args.port}/health")

            stop_process(proc)
            wait_port_closed(connect_host, args.port)
            print("stop ok: port released")

            proc = start_gateway(args.python, args.config, args.host, args.port, env)
            wait_http(f"http://{connect_host}:{args.port}/health", expected_status=200)
            print("restart ok: service came back")

            proc.kill()
            proc.wait(timeout=10)
            wait_port_closed(connect_host, args.port)
            print("crash stop ok: port released")

            proc = start_gateway(args.python, args.config, args.host, args.port, env)
            wait_http(f"http://{connect_host}:{args.port}/health", expected_status=200)
            print("post-crash restart ok: service came back")

            stop_process(proc)
            wait_port_closed(connect_host, args.port)
            print("final stop ok: port released")
        finally:
            if proc.poll() is None:
                stop_process(proc)
            if is_port_open(connect_host, args.port):
                print(f"FAIL port still open after cleanup: {connect_host}:{args.port}")
                return 4

    leaked = {
        key: os.getenv(key)
        for key in (
            "LOCK_TOOL_TOKEN",
            "LOCK_TOOL_GATEWAY_HOST",
            "LOCK_TOOL_GATEWAY_PORT",
            "LOCK_TOOL_CALL_LOG",
            "SMART_LOCK_NO_UNLOCK",
        )
        if os.getenv(key)
    }
    if leaked:
        print(f"FAIL lifecycle env leaked into parent process: {leaked}")
        return 5

    print("env ok: parent process unchanged")
    return 0

def test_fastgpt(args: argparse.Namespace) -> int:
    compose_dir = Path(args.compose_dir)
    if not (compose_dir / "docker-compose.yml").exists():
        print(f"FAIL compose file not found: {compose_dir / 'docker-compose.yml'}")
        return 2
    compose_dotenv = read_dotenv(compose_dir / ".env")
    ports = args.ports or [
        int(compose_dotenv.get("FASTGPT_HTTP_PORT", "3000")),
        int(compose_dotenv.get("FASTGPT_MCP_PORT", "3003")),
        int(compose_dotenv.get("FASTGPT_SANDBOX_PORT", "3006")),
        int(compose_dotenv.get("FASTGPT_MINIO_PORT", "9000")),
        int(compose_dotenv.get("FASTGPT_MINIO_CONSOLE_PORT", "9001")),
    ]
    health_url = args.health_url or compose_dotenv.get(
        "FASTGPT_FE_DOMAIN",
        f"http://127.0.0.1:{ports[0]}",
    ).replace("localhost", "127.0.0.1")

    compose_env = without_proxy(os.environ.copy())
    config = run(["docker", "compose", "config", "--quiet"], cwd=compose_dir, env=compose_env)
    if config.returncode != 0:
        print(config.stderr.strip() or config.stdout.strip())
        return config.returncode
    print("compose ok")

    occupied = [port for port in ports if is_port_open("127.0.0.1", port)]
    if occupied:
        print(f"FAIL ports already in use before FastGPT start: {occupied}")
        return 3

    images = run(["docker", "compose", "config", "--images"], cwd=compose_dir, env=compose_env)
    if images.returncode != 0:
        print(images.stderr.strip() or images.stdout.strip())
        return images.returncode
    missing = missing_images(images.stdout.splitlines())
    if missing and not args.allow_pull:
        print("FAIL missing images; rerun with --allow-pull or pull first:")
        for image in missing:
            print(f"  {image}")
        return 4

    if not args.start:
        print("fastgpt preflight ok")
        return 0

    proc = run(["docker", "compose", "up", "-d"], cwd=compose_dir, env=compose_env)
    if proc.returncode != 0:
        print(proc.stderr.strip() or proc.stdout.strip())
        cleanup_fastgpt(compose_dir, compose_env)
        return proc.returncode
    try:
        wait_http(health_url, expected_status=200, timeout_seconds=120)
        print(f"start ok: {health_url}")
        restart = run(["docker", "compose", "restart"], cwd=compose_dir, env=compose_env)
        if restart.returncode != 0:
            print(restart.stderr.strip() or restart.stdout.strip())
            return restart.returncode
        wait_http(health_url, expected_status=200, timeout_seconds=120)
        print("restart ok")
    finally:
        cleanup_fastgpt(compose_dir, compose_env)

    still_open = [port for port in ports if is_port_open("127.0.0.1", port)]
    if still_open:
        print(f"FAIL ports still open after FastGPT stop: {still_open}")
        return 5
    print("stop ok: ports released")
    return 0


def start_gateway(
    python: str,
    config: str,
    host: str,
    port: int,
    env: dict[str, str],
) -> subprocess.Popen[str]:
    return subprocess.Popen(
        [
            python,
            str(ROOT / "lock_tool_gateway.py"),
            "--config",
            config,
            "--host",
            host,
            "--port",
            str(port),
        ],
        cwd=ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def stop_process(proc: subprocess.Popen[str]) -> None:
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=10)


def cleanup_fastgpt(compose_dir: Path, env: dict[str, str]) -> None:
    run(["docker", "compose", "down", "--remove-orphans"], cwd=compose_dir, env=env)


def run(command: list[str], cwd: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=cwd, env=env, text=True, capture_output=True)


def without_proxy(env: dict[str, str]) -> dict[str, str]:
    for key in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY"):
        env.pop(key, None)
    return env


def read_dotenv(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def missing_images(images: Iterable[str]) -> list[str]:
    missing: list[str] = []
    for image in sorted({item.strip() for item in images if item.strip()}):
        result = subprocess.run(["docker", "image", "inspect", image], text=True, capture_output=True)
        if result.returncode != 0:
            missing.append(image)
    return missing


def get_json(url: str, token: str | None = None) -> dict[str, object]:
    request = urllib.request.Request(url)
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(request, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


def post_json(url: str, payload: dict[str, object], token: str | None = None) -> dict[str, object]:
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=body, method="POST")
    request.add_header("Content-Type", "application/json")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(request, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


def expect_http_status(url: str, status: int) -> None:
    try:
        urllib.request.urlopen(url, timeout=5)
    except urllib.error.HTTPError as exc:
        if exc.code == status:
            return
        raise
    raise RuntimeError(f"{url} did not return HTTP {status}")


def wait_http(url: str, expected_status: int, timeout_seconds: float = 30) -> None:
    deadline = time.monotonic() + timeout_seconds
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                if response.status == expected_status:
                    return
        except Exception as exc:  # noqa: BLE001 - command-line smoke test reports final error.
            last_error = exc
        time.sleep(0.25)
    raise TimeoutError(f"Timed out waiting for {url}: {last_error}")


def wait_port_closed(host: str, port: int, timeout_seconds: float = 10) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if not is_port_open(host, port):
            return
        time.sleep(0.25)
    raise TimeoutError(f"Timed out waiting for port to close: {host}:{port}")


def is_port_open(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex((host, port)) == 0


if __name__ == "__main__":
    raise SystemExit(main())
