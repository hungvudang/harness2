# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Callable

from ..domain.rollout import Rollout, Verdict
from .ports import Agent, Reviewer, Runs, TaskSet, Workspaces

log = logging.getLogger(__name__)


class Rollouts:
    def __init__(
        self,
        taskset: TaskSet,
        agent: Agent,
        workspaces: Workspaces,
        runs: Runs,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._taskset = taskset
        self._agent = agent
        self._workspaces = workspaces
        self._runs = runs
        self._clock = clock

    def run(self, task_id: str, harness: Path, run_id: str, timeout: int) -> Path:
        """Idempotent by run_id, harness and instruction: a finished rollout of the same inputs
        is returned, not repeated; one of an older harness or instruction is rolled again."""
        task = self._taskset.task(task_id)
        if self._runs.finished(run_id, harness, task.instruction):
            log.debug("rollout %s already finished", run_id)
            return self._runs.path(run_id)
        run_dir = self._runs.reset(run_id)
        workspace = self._workspaces(self._runs.workspace(run_dir))
        workspace.checkout(self._taskset.project.repo, task.commit)
        fingerprint = self._agent.install(harness, workspace.path)
        # Sealed after the harness is installed, so the diff holds only the agent's work.
        workspace.seal()
        started = self._clock()
        outcome = self._agent.run(
            task.instruction, workspace.path, self._runs.stream(run_dir), timeout
        )
        diff = workspace.changes()
        rollout = Rollout(
            task=task.id,
            run_id=run_id,
            outcome=outcome,
            wall_s=round(self._clock() - started, 1),
            fingerprint=fingerprint,
        )
        self._runs.save(rollout, diff, harness, task.instruction)
        if outcome.failed:
            log.warning(
                "rollout %s/%s failed (rc=%s); see %s", task.id, run_id, outcome.rc, run_dir
            )
        log.info("rollout %s/%s: %d chars of diff", task.id, run_id, len(diff))
        return run_dir


class Judge:
    def __init__(self, taskset: TaskSet, workspaces: Workspaces, runs: Runs, reviewer: Reviewer):
        self._taskset = taskset
        self._workspaces = workspaces
        self._runs = runs
        self._reviewer = reviewer

    def verdict(self, run_dir: Path, task_id: str) -> Verdict | None:
        """None when the task has nothing to judge or the workspace is gone: ungraded, not zero.

        A reviewer failure raises: core's judge records it as ungraded, never as a zero.
        """
        task = self._taskset.task(task_id)
        path = self._runs.workspace(run_dir)
        if not task.judged or not path.is_dir():
            log.warning("rollout %s of %s cannot be judged", run_dir, task_id)
            return None
        criteria = self._workspaces(path).verify(task.criteria, task.verify_timeout)
        if task.rubric:
            reference = self._taskset.reference(task)
            criteria += self._reviewer.review(task, self._runs.diff(run_dir), reference)
        return Verdict(criteria)
