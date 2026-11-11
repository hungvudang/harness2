# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class Outcome:
    rc: int
    cost_usd: float = 0.0
    error: bool = False

    @property
    def failed(self) -> bool:
        return self.error or self.rc != 0


@dataclass(frozen=True)
class Rollout:
    task: str
    run_id: str
    outcome: Outcome
    wall_s: float
    fingerprint: str

    def record(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class Criterion:
    command: str
    met: bool
    output: str = ""


@dataclass(frozen=True)
class Verdict:
    criteria: tuple[Criterion, ...]

    def __post_init__(self) -> None:
        if not self.criteria:
            raise ValueError("a verdict needs at least one criterion")

    @property
    def met(self) -> int:
        return sum(1 for c in self.criteria if c.met)

    @property
    def total(self) -> int:
        return len(self.criteria)

    def score(self) -> dict:
        """The judge record the shared report reads: n_passed over n_criteria."""
        return {
            "n_passed": self.met,
            "n_criteria": self.total,
            "criteria": [asdict(c) for c in self.criteria],
        }
