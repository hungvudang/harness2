# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ...utils import numbers, strings


def _text(value: str, field: str) -> None:
    if strings.blank(value):
        raise ValueError(f"`{field}` must be a nonempty string")


def _texts(values: tuple[str, ...], field: str) -> None:
    if any(strings.blank(v) for v in values):
        raise ValueError(f"`{field}` must be a list of nonempty strings")


def _positive(value: int, field: str) -> None:
    if not numbers.positive(value):
        raise ValueError(f"`{field}` must be a positive number of seconds, got {value}")


@dataclass(frozen=True)
class Project:
    name: str
    repo: Path
    work: str
    commands: tuple[str, ...]
    model: str
    domain_word: str = "module"
    effort: str = ""
    timeout: int = 3600
    max_turns: int = 0

    def __post_init__(self) -> None:
        _text(self.name, "name")
        _text(self.work, "work")
        _text(self.model, "model")
        _texts(self.commands, "commands")
        _positive(self.timeout, "timeout")
        if not numbers.natural(self.max_turns):
            raise ValueError(f"`max_turns` must be 0 (unlimited) or more, got {self.max_turns}")


@dataclass(frozen=True)
class Task:
    id: str
    commit: str
    domain: str
    instruction: str
    criteria: tuple[str, ...] = ()
    verify_timeout: int = 1800

    def __post_init__(self) -> None:
        _text(self.id, "id")
        _text(self.commit, "commit")
        _text(self.domain, "domain")
        _text(self.instruction, "instruction")
        _texts(self.criteria, "criteria")
        _positive(self.verify_timeout, "verify_timeout")
