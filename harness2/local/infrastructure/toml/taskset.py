# SPDX-License-Identifier: Apache-2.0

"""TaskSet over a folder of TOML files.

<root>/bench.toml               -> Project
<root>/<task-id>/task.toml      -> Task (with instruction.md beside it)
"""

from __future__ import annotations

import logging
import subprocess
import tomllib
from pathlib import Path

from .... import config
from ...domain.errors import InvalidTaskSet
from ...domain.task import Project, Task

log = logging.getLogger(__name__)

BENCH_FILE = "bench.toml"
TASK_FILE = "task.toml"
INSTRUCTION_FILE = "instruction.md"


def _read(path: Path) -> dict:
    if not path.is_file():
        raise InvalidTaskSet(f"missing {path}")
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise InvalidTaskSet(f"{path}: invalid TOML: {exc}") from exc


def _strings(doc: dict, key: str, path: Path) -> tuple[str, ...]:
    value = doc.get(key, [])
    if not isinstance(value, list):
        raise InvalidTaskSet(f"{path}: `{key}` must be a list of strings")
    return tuple(value)


class TaskSetImpl:
    """Reads and validates on every access, so a fixed file needs no restart."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.project = self._project()

    def _project(self) -> Project:
        path = self.root / BENCH_FILE
        doc = _read(path)
        repo = Path(str(doc.get("repo", ""))).expanduser()
        if not repo.is_absolute():
            repo = (self.root / repo).resolve()
        if not (repo / ".git").exists():
            raise InvalidTaskSet(f"{path}: `repo` {repo} is not a git repository")
        try:
            return Project(
                name=str(doc.get("name", "")),
                repo=repo,
                work=str(doc.get("work", "")),
                commands=_strings(doc, "commands", path),
                model=str(doc.get("model") or config.SOLVER_MODEL_CC),
                domain_word=str(doc.get("domain_word") or "module"),
                effort=str(doc.get("effort") or ""),
                timeout=int(doc.get("timeout") or 3600),
                max_turns=int(doc.get("max_turns") or 0),
            )
        except ValueError as exc:
            raise InvalidTaskSet(f"{path}: {exc}") from exc

    def task(self, task_id: str) -> Task:
        path = self.root / task_id / TASK_FILE
        doc = _read(path)
        instruction = self.root / task_id / INSTRUCTION_FILE
        if not instruction.is_file():
            raise InvalidTaskSet(f"missing {instruction}")
        try:
            return Task(
                id=task_id,
                commit=str(doc.get("commit", "")),
                domain=str(doc.get("domain", "")),
                instruction=instruction.read_text(encoding="utf-8").strip(),
                criteria=_strings(doc, "criteria", path),
                verify_timeout=int(doc.get("verify_timeout") or 1800),
            )
        except ValueError as exc:
            raise InvalidTaskSet(f"{path}: {exc}") from exc

    def domains(self) -> dict[str, str]:
        found = {
            p.parent.name: self.task(p.parent.name).domain
            for p in sorted(self.root.glob(f"*/{TASK_FILE}"))
        }
        if not found:
            raise InvalidTaskSet(f"no tasks under {self.root}; add <task-id>/{TASK_FILE}")
        return found

    def missing_commits(self) -> list[str]:
        problems = []
        for task_id in self.domains():
            commit = self.task(task_id).commit
            probe = subprocess.run(
                ["git", "-C", str(self.project.repo), "cat-file", "-e", f"{commit}^{{commit}}"],
                capture_output=True,
            )
            if probe.returncode:
                problems.append(f"{task_id}: commit {commit} is not in {self.project.repo}")
        if problems:
            log.error("task set %s: %s", self.root, "; ".join(problems))
        return problems
