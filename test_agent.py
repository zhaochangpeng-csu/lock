from __future__ import annotations

from datetime import datetime, timedelta
import tempfile
from pathlib import Path

from lock_tool_gateway import _auth_context_summary
from smart_lock.auth_context import consume_auth_context, read_auth_context, write_auth_context
from smart_lock.config import load_config
from smart_lock.fusion import FusionEngine
from smart_lock.results import AuthResult


def main() -> None:
    test_auth_context_expiry()
    config = load_config("config.yaml")
    with tempfile.TemporaryDirectory(prefix="smart-lock-agent-test-") as tmpdir:
        test_hardware_credential(config, Path(tmpdir))


def test_auth_context_expiry() -> None:
    fresh = {
        "credential_id": "fresh-test",
        "time": datetime.now().isoformat(timespec="seconds"),
        "fusion_passed": True,
        "fusion_score": 0.95,
        "consumed": False,
    }
    stale = {
        "credential_id": "stale-test",
        "time": (datetime.now() - timedelta(seconds=60)).isoformat(timespec="seconds"),
        "fusion_passed": True,
        "fusion_score": 0.95,
        "consumed": False,
    }
    fresh_summary = _auth_context_summary(fresh, 15)
    assert fresh_summary["fresh"] is True
    assert fresh_summary["authorized"] is True
    assert fresh_summary["fusion_passed"] is True
    stale_summary = _auth_context_summary(stale, 15)
    assert stale_summary["fresh"] is False
    assert stale_summary["reason"] == "auth context expired"


def test_hardware_credential(config, tmpdir: Path) -> None:
    fusion = FusionEngine(config.fusion)
    decision = fusion.decide(
        [
            AuthResult("sensor", True, 1.0),
            AuthResult("face", True, 0.95),
            AuthResult("liveness", True, 0.95),
            AuthResult("speaker", True, 0.95),
        ]
    )
    path = tmpdir / "auth_context.json"
    credential = write_auth_context(str(path), decision)
    assert read_auth_context(str(path)) == credential
    assert consume_auth_context(str(path), credential["credential_id"]) is True
    consumed = read_auth_context(str(path))
    assert consumed is not None and consumed["consumed"] is True
    assert _auth_context_summary(consumed, 15)["authorized"] is False


if __name__ == "__main__":
    main()
