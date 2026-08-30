from __future__ import annotations

import json
import logging
import os
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .config import EventReportingConfig
from .results import AuthResult


LOGGER = logging.getLogger(__name__)


def build_auth_failure_event(
    fusion_score: float,
    results: list[AuthResult],
) -> dict[str, Any]:
    identity = "unknown"
    for result in results:
        candidate = result.metadata.get("identity")
        if candidate:
            identity = str(candidate)
            break

    checks = {
        result.name: {
            "passed": bool(result.passed),
            "score": round(float(result.score), 4),
            "reason": result.reason,
        }
        for result in results
    }
    failed_checks = [result.name for result in results if not result.passed]
    return {
        "event_type": "abnormal_behavior",
        "identity": identity,
        "confidence": max(0.0, min(1.0, float(fusion_score))),
        "details": {
            "reason": "authentication_failed",
            "failed_checks": failed_checks,
            "checks": checks,
        },
    }


def report_auth_failure(
    config: EventReportingConfig,
    fusion_score: float,
    results: list[AuthResult],
) -> bool:
    if not config.enabled:
        return False

    url = f"{config.service_url.rstrip('/')}/event"
    headers = {"Content-Type": "application/json"}
    token = os.getenv(config.token_env, "")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = Request(
        url,
        data=json.dumps(build_auth_failure_event(fusion_score, results)).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urlopen(request, timeout=config.timeout_seconds) as response:
            if response.status < 200 or response.status >= 300:
                raise RuntimeError(f"event service returned HTTP {response.status}")
    except (HTTPError, URLError, OSError, RuntimeError) as exc:
        LOGGER.warning("Could not report authentication failure: %s", exc)
        return False

    LOGGER.info("Authentication failure recorded by event service")
    return True
