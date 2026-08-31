from __future__ import annotations

from pathlib import Path
import tempfile
from unittest.mock import patch

from lock_event_service import EventInput, EventStore
from smart_lock.config import EventReportingConfig
from smart_lock.event_reporting import build_auth_failure_event, report_auth_failure
from smart_lock.results import AuthResult


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="lock-event-test-") as tmpdir:
        path = Path(tmpdir) / "latest_event.json"
        store = EventStore(path)
        assert store.read() is None

        event = store.record(
            EventInput(
                event_type="stranger",
                identity="unknown",
                confidence=0.42,
                details={"reason": "face not recognized"},
            )
        )
        assert store.read() == event
        assert event["processed"] is False

        try:
            store.mark_processed("wrong-id")
        except ValueError:
            pass
        else:
            raise AssertionError("wrong event id must not be processed")

        processed = store.mark_processed(event["id"])
        assert processed["processed"] is True
        assert processed["processed_at"]

        results = [
            AuthResult("sensor", True, 1.0, "presence detected"),
            AuthResult(
                "face",
                False,
                0.2,
                "face not recognized",
                {"identity": "low-confidence-candidate"},
            ),
            AuthResult("liveness", False, 0.1, "blink not detected"),
        ]
        payload = build_auth_failure_event(0.31, results)
        assert payload["event_type"] == "abnormal_behavior"
        assert payload["identity"] == "unknown"
        assert payload["confidence"] == 0.31
        assert payload["details"]["failed_checks"] == ["face", "liveness"]

        with patch("smart_lock.event_reporting.urlopen", side_effect=OSError("offline")):
            assert report_auth_failure(EventReportingConfig(), 0.31, results) is False
        print("latest event record/read/processed test passed")


if __name__ == "__main__":
    main()
