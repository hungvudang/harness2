# SPDX-License-Identifier: Apache-2.0


from __future__ import annotations

import io
import logging
import subprocess
import tarfile
from pathlib import Path

from .... import config
from ...domain.rollout import Criterion

log = logging.getLogger(__name__)

BASELINE = "harness2-baseline"
# The agent may edit the installed harness; those edits are not the deliverable.
HARNESS_PATHS = ("CLAUDE.md", ".claude", "scripts")
OUTPUT_CAP = 1500


def _git(cwd: Path, *args: str, **kw) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, **kw)


class WorkspaceImpl:
    def __init__(self, path: Path):
        self.path = Path(path)

    def checkout(self, repo: Path, commit: str) -> None:
        """`git archive`, not a clone: later commits would show the agent the answer."""
        archive = _git(repo, "archive", "--format=tar", commit).stdout
        self.path.mkdir(parents=True, exist_ok=True)
        with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
            tar.extractall(self.path, filter="data")

    def seal(self) -> None:
        _git(self.path, "init", "-q")
        _git(self.path, "add", "-A")
        _git(
            self.path,
            "-c",
            "user.name=harness2",
            "-c",
            "user.email=harness2@local",
            "commit",
            "-q",
            "--no-verify",
            "-m",
            "baseline",
        )
        _git(self.path, "tag", BASELINE)

    def changes(self) -> str:
        _git(self.path, "add", "-A")
        excludes = [f":(exclude){p}" for p in HARNESS_PATHS]
        return _git(self.path, "diff", "--cached", BASELINE, "--", ".", *excludes, text=True).stdout

    def verify(self, commands: tuple[str, ...], timeout: int) -> tuple[Criterion, ...]:
        criteria = []
        for command in commands:
            try:
                p = subprocess.run(
                    command,
                    shell=True,
                    cwd=self.path,
                    env=config.scrub_env(),
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                )
            except subprocess.TimeoutExpired:
                log.warning("criterion %r timed out after %ss in %s", command, timeout, self.path)
                criteria.append(Criterion(command, False, f"timeout {timeout}s"))
                continue
            log.info("criterion %r: rc=%s", command, p.returncode)
            criteria.append(
                Criterion(command, p.returncode == 0, (p.stdout + p.stderr)[-OUTPUT_CAP:])
            )
        return tuple(criteria)
