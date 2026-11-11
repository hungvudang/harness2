# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from pathlib import Path
from typing import Callable, Protocol

from ..domain.rollout import Criterion, Outcome, Rollout
from ..domain.task import Project, Task


class TaskSet(Protocol):
    project: Project

    def task(self, task_id: str) -> Task: ...

    def domains(self) -> dict[str, str]: ...

    def missing_commits(self) -> list[str]: ...


class Workspace(Protocol):
    """One rollout's sandbox; its diff since the seal is the agent's work."""

    path: Path

    def checkout(self, repo: Path, commit: str) -> None: ...

    def seal(self) -> None: ...

    def changes(self) -> str: ...

    def verify(self, commands: tuple[str, ...], timeout: int) -> tuple[Criterion, ...]: ...


Workspaces = Callable[[Path], Workspace]


class Agent(Protocol):
    def install(self, harness: Path, workspace: Path) -> str:
        """Returns the harness fingerprint, which identifies the rollout's harness in reports."""
        ...

    def run(self, instruction: str, workspace: Path, stream: Path, timeout: int) -> Outcome: ...


class Runs(Protocol):
    def path(self, run_id: str) -> Path: ...

    def finished(self, run_id: str) -> bool: ...

    def reset(self, run_id: str) -> Path: ...

    def workspace(self, run_dir: Path) -> Path: ...

    def stream(self, run_dir: Path) -> Path: ...

    def save(self, rollout: Rollout, diff: str) -> None: ...

    def diff(self, run_dir: Path) -> str: ...
