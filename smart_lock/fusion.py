from __future__ import annotations

from dataclasses import dataclass

from .config import FusionConfig
from .results import AuthResult


@dataclass(frozen=True)
class FusionDecision:
    passed: bool
    score: float
    details: dict[str, AuthResult]


class FusionEngine:
    def __init__(self, config: FusionConfig) -> None:
        self._config = config

    def decide(self, results: list[AuthResult]) -> FusionDecision:
        by_name = {result.name: result for result in results}
        score = 0.0

        for name, weight in self._config.weights.items():
            result = by_name.get(name)
            if result is None:
                continue
            score += weight * result.score

        all_checks_passed = all(result.passed for result in results)
        if self._config.require_all:
            passed = all_checks_passed and score >= self._config.threshold
        else:
            passed = score >= self._config.threshold
        return FusionDecision(passed=passed, score=score, details=by_name)

