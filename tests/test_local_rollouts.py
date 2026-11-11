# SPDX-License-Identifier: Apache-2.0

"""Integration: the local use cases over the real TOML, git and filesystem implementations,
with a scripted agent in place of the `claude` CLI."""

import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from harness2.local.application.rollouts import Judge, Rollouts
from harness2.local.domain.errors import InvalidTaskSet
from harness2.local.domain.rollout import Criterion, Outcome
from harness2.local.infrastructure.fs.runs import RunsImpl
from harness2.local.infrastructure.git.workspace import WorkspaceImpl
from harness2.local.infrastructure.toml.taskset import TaskSetImpl

pytestmark = pytest.mark.skipif(not shutil.which("git"), reason="git required")


class ScriptedReviewer:
    """Meets the first rubric item only, and keeps what it was shown."""

    def __init__(self):
        self.seen = {}

    def review(self, task, diff, reference):
        self.seen = {"diff": diff, "reference": reference}
        return tuple(Criterion(item, i == 0, "scripted") for i, item in enumerate(task.rubric))


class ScriptedAgent:
    """Installs the harness by copy and 'works' by writing Feature.java."""

    def __init__(self):
        self.runs = 0

    def install(self, harness: Path, workspace: Path) -> str:
        shutil.copy(harness / "CLAUDE.md", workspace / "CLAUDE.md")
        return "fp"

    def run(self, instruction, workspace, stream, timeout) -> Outcome:
        self.runs += 1
        (workspace / "Feature.java").write_text("class Feature {}\n")
        (workspace / "CLAUDE.md").write_text("the agent edited its harness\n")
        stream.write_text("")
        return Outcome(rc=0, cost_usd=0.01)


@pytest.fixture
def world():
    root = Path(tempfile.mkdtemp())
    repo = root / "repo"
    repo.mkdir()
    (repo / "CLAUDE.md").write_text("Project rules.\n")
    (repo / "App.java").write_text("class App {}\n")
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init"],
        cwd=repo,
        check=True,
    )
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    (repo / "Feature.java").write_text("class Feature { /* reference */ }\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "feature"],
        cwd=repo,
        check=True,
    )
    reference = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    tasks = root / "tasks"
    (tasks / "t1").mkdir(parents=True)
    (tasks / "bench.toml").write_text(
        f'name = "demo"\nrepo = "{repo}"\nwork = "write Java"\ncommands = ["./gradlew"]\n'
    )
    (tasks / "t1" / "task.toml").write_text(
        f'commit = "{commit}"\ndomain = "core"\ncriteria = ["test -f Feature.java", "false"]\n'
        f'reference = "{reference}"\nrubric = ["adds Feature", "documents Feature"]\n'
    )
    (tasks / "t1" / "instruction.md").write_text("Add Feature.java.\n")
    harness = root / "harness"
    harness.mkdir()
    (harness / "CLAUDE.md").write_text("Candidate rules.\n")
    yield {"root": root, "tasks": tasks, "harness": harness}
    shutil.rmtree(root, ignore_errors=True)


class TestTaskSet:
    def test_reads_folder(self, world):
        taskset = TaskSetImpl(world["tasks"])
        assert taskset.project.name == "demo"
        assert taskset.domains() == {"t1": "core"}
        assert taskset.task("t1").instruction == "Add Feature.java."
        assert taskset.missing_commits() == []

    def test_names_broken_file(self, world):
        (world["tasks"] / "t1" / "task.toml").write_text('commit = ""\ndomain = "core"\n')
        with pytest.raises(InvalidTaskSet, match="task.toml.*commit"):
            TaskSetImpl(world["tasks"]).task("t1")

    def test_unknown_commit(self, world):
        (world["tasks"] / "t1" / "task.toml").write_text('commit = "deadbeef"\ndomain = "core"\n')
        assert TaskSetImpl(world["tasks"]).missing_commits()


class TestRollouts:
    def _wire(self, world):
        self.reviewer = ScriptedReviewer()
        taskset = TaskSetImpl(world["tasks"])
        runs = RunsImpl(world["root"] / "runs")
        agent = ScriptedAgent()
        return (
            Rollouts(taskset, agent, WorkspaceImpl, runs),
            Judge(taskset, WorkspaceImpl, runs, self.reviewer),
            runs,
            agent,
        )

    def test_diff_is_the_agents_work(self, world):
        rollouts, _, runs, _ = self._wire(world)
        run_dir = rollouts.run("t1", world["harness"], "r1", timeout=60)
        diff = runs.diff(run_dir)
        assert "Feature.java" in diff
        assert "CLAUDE.md" not in diff
        assert runs.finished("r1")

    def test_snapshot_has_no_history(self, world):
        rollouts, _, runs, _ = self._wire(world)
        run_dir = rollouts.run("t1", world["harness"], "r1", timeout=60)
        log = subprocess.check_output(
            ["git", "log", "--oneline"], cwd=runs.workspace(run_dir), text=True
        )
        assert len(log.splitlines()) == 1

    def test_idempotent(self, world):
        rollouts, _, _, agent = self._wire(world)
        rollouts.run("t1", world["harness"], "r1", timeout=60)
        rollouts.run("t1", world["harness"], "r1", timeout=60)
        assert agent.runs == 1

    def test_judged_by_criteria(self, world):
        rollouts, judge, _, _ = self._wire(world)
        run_dir = rollouts.run("t1", world["harness"], "r1", timeout=60)
        verdict = judge.verdict(run_dir, "t1")
        assert (verdict.met, verdict.total) == (2, 4)

    def test_reviewer_sees_both_diffs(self, world):
        rollouts, judge, _, _ = self._wire(world)
        run_dir = rollouts.run("t1", world["harness"], "r1", timeout=60)
        judge.verdict(run_dir, "t1")
        assert "class Feature {}" in self.reviewer.seen["diff"]
        assert "/* reference */" in self.reviewer.seen["reference"]

    def test_ungraded_without_workspace(self, world):
        _, judge, runs, _ = self._wire(world)
        assert judge.verdict(runs.path("missing"), "t1") is None
