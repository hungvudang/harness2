# SPDX-License-Identifier: Apache-2.0

"""Runs as directories: <root>/<run_id>/{workspace/, stream.jsonl, changes.diff, rollout.json}."""

from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path

from ....core import runmark as RM
from ...domain.rollout import Rollout

log = logging.getLogger(__name__)

WORKSPACE_DIR = "workspace"
STREAM_FILE = "stream.jsonl"
DIFF_FILE = "changes.diff"
ROLLOUT_FILE = "rollout.json"


class RunsImpl:
    def __init__(self, root: Path):
        self._root = Path(root)

    def path(self, run_id: str) -> Path:
        return self._root / run_id

    def finished(self, run_id: str, harness: Path, instruction: str) -> bool:
        """Finished by this harness on this instruction: a rollout of an older one is stale."""
        run_dir = self.path(run_id)
        if not (run_dir / ROLLOUT_FILE).is_file():
            return False
        inputs = RM.inputs_fingerprint(instruction, None)
        if RM.complete(run_dir, harness_dir=harness, inputs=inputs):
            return True
        log.info(
            "re-rolling %s: %s", run_id, RM.why_not(run_dir, harness_dir=harness, inputs=inputs)
        )
        return False

    def reset(self, run_id: str) -> Path:
        run_dir = self.path(run_id)
        if run_dir.exists():
            shutil.rmtree(run_dir)
        run_dir.mkdir(parents=True)
        return run_dir

    def workspace(self, run_dir: Path) -> Path:
        return Path(run_dir) / WORKSPACE_DIR

    def stream(self, run_dir: Path) -> Path:
        return Path(run_dir) / STREAM_FILE

    def save(self, rollout: Rollout, diff: str, harness: Path, instruction: str) -> None:
        run_dir = self.path(rollout.run_id)
        (run_dir / DIFF_FILE).write_text(diff, encoding="utf-8")
        (run_dir / ROLLOUT_FILE).write_text(
            json.dumps(rollout.record(), indent=2), encoding="utf-8"
        )
        # Written last: the mark is what makes the rollout reusable, and only for its inputs.
        RM.mark(
            run_dir,
            run_id=rollout.run_id,
            harness_dir=harness,
            inputs=RM.inputs_fingerprint(instruction, None),
        )

    def diff(self, run_dir: Path) -> str:
        p = Path(run_dir) / DIFF_FILE
        if not p.is_file():
            return ""
        return p.read_text(encoding="utf-8")
