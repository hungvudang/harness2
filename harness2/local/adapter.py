# SPDX-License-Identifier: Apache-2.0

"""Composition root and the BenchmarkAdapter facade (Facade); holds no domain rule."""

from __future__ import annotations

import logging
from pathlib import Path

from .. import config
from ..benchmarks.tasks import select
from ..core import tree as HT
from ..core.spec import BenchPrompts, BenchSpec, resolve_channel_model
from ..substrate import claude
from ..utils import strings
from .application.rollouts import Judge, Rollouts
from .domain.errors import InvalidTaskSet
from .infrastructure.claude.agent import AgentImpl, project_setup
from .infrastructure.fs.runs import DIFF_FILE, RunsImpl
from .infrastructure.git.workspace import WorkspaceImpl
from .infrastructure.toml.taskset import TaskSetImpl

log = logging.getLogger(__name__)

DIFF_CAP = 120_000
TRAJECTORY_CAP = 200_000


class LocalAdapter:
    # Read by the judge CLI: criteria are shell commands, so no judge model is needed.
    model_judged = False

    def __init__(
        self,
        *,
        substrate: str = "cc",
        root: Path | None = None,
        split_file: Path | None = None,
    ):
        if substrate != "cc":
            raise ValueError(f"--bench local runs on --substrate cc only, got {substrate!r}")
        self._taskset = TaskSetImpl(Path(root or config.LOCAL_TASKS_DIR))
        self.project = self._taskset.project
        self._split_file = split_file
        self.substrate = claude.SubstrateImpl()
        channel = resolve_channel_model(None, None, effort="high")
        self.spec = BenchSpec(
            name=f"local-{self.project.name}",
            runs_root=config.RUNS / "local" / self.project.name,
            install_mode="claude_root",
            prompts=BenchPrompts(
                work=self.project.work,
                domain_word=self.project.domain_word,
                deliverable_word="code change",
            ),
            channels={"proposer": channel, "selector": channel},
            domain_word=self.project.domain_word,
            allow_subagents=True,
            obs_trunc=50_000,
        )
        self._runs = RunsImpl(self.spec.runs_root / "rollouts")
        self._rollouts = Rollouts(
            self._taskset,
            AgentImpl(self.substrate, self.spec, self.project),
            WorkspaceImpl,
            self._runs,
        )
        self._judge = Judge(self._taskset, WorkspaceImpl, self._runs)

    @property
    def executor_model(self) -> str:
        return self.project.model

    def tasks(self, split: str = "all") -> list[str]:
        return select(self._taskset.domains(), split, self._split_file, benchmark=self.spec.name)

    def domain_of(self, task_id: str) -> str:
        return self._taskset.task(task_id).domain

    def task_prompt(self, task_id: str) -> str:
        return self._taskset.task(task_id).instruction

    def task_files(self, task_id: str) -> Path | None:
        return None

    def base_harness(self) -> Path:
        harness = project_setup(self.project.repo)
        if not harness.get("CLAUDE.md", "").strip():
            raise InvalidTaskSet(
                f"{self.project.repo}/CLAUDE.md is missing or empty: nothing to harness"
            )
        return HT.write_tree(claude.SURFACE, harness, self.spec.runs_root / "base", stubs=False)

    def validate_tree(self, tree: dict[str, str]) -> None:
        if not tree.get("CLAUDE.md", "").strip():
            raise ValueError("CLAUDE.md must stay nonempty: it is the only always-on file")

    def execute(
        self, task_id: str, harness_dir: Path | None, run_id: str, *, timeout: int | None = None
    ) -> Path:
        harness = Path(harness_dir or self.base_harness())
        return self._rollouts.run(task_id, harness, run_id, int(timeout or self.project.timeout))

    def delivered(self, run_dir: Path) -> bool:
        return bool(self._runs.diff(run_dir).strip())

    def trajectory(self, run_dir: Path, *, obs_trunc: int) -> str:
        """Paths read repo-relative: the improver needs the file, not the sandbox location."""
        text = self.substrate.read_trajectory(
            self._runs.stream(run_dir), obs_trunc=obs_trunc, max_chars=TRAJECTORY_CAP
        )
        workspace = str(self._runs.workspace(run_dir).resolve())
        return text.replace(workspace + "/", "").replace(workspace, ".")

    def deliverable_text(self, run_dir: Path) -> str:
        return strings.clip(self._runs.diff(run_dir), DIFF_CAP, reason="diff truncated")

    def deliverable_files(self, run_dir: Path) -> dict[str, str]:
        return {DIFF_FILE: self.deliverable_text(run_dir)}

    def preflight(self) -> None:
        self.substrate.preflight()
        problems = self._taskset.missing_commits()
        if problems:
            raise InvalidTaskSet("; ".join(problems))

    def readiness(self) -> list[str]:
        """Every fixable problem before a run; the preflight CLI defers here."""
        problems = []
        for validate in (self.tasks, self.preflight):
            try:
                validate()
            except (OSError, RuntimeError, ValueError) as exc:
                problems.append(str(exc))
        channel = self.spec.channels["proposer"]
        if not channel.model:
            problems.append("Set HARNESS2_IMPROVER_MODEL (e.g. openai/gpt-5.5) for the improver")
        if channel.kind != "opencode":
            return problems
        if not (config.OPENCODE_DIR / "node_modules").is_dir() or not config.BUN.exists():
            problems.append("OpenCode is not installed; run harness2-setup local")
        for key in config.provider_keys(channel.model)[:1]:
            if not config.env(key):
                problems.append(f"{key} is required for improver model {channel.model}")
        return problems

    def score(self, run_dir: Path, task_id: str) -> dict | None:
        verdict = self._judge.verdict(Path(run_dir), task_id)
        if verdict is None:
            return None
        return verdict.score()
