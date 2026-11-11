# SPDX-License-Identifier: Apache-2.0

"""Adapter pattern: the Agent port over the shared Claude substrate."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from ....core import tree as HT
from ....core.spec import BenchSpec
from ....substrate import claude
from ...domain.rollout import Outcome
from ...domain.task import Project


class AgentImpl:
    def __init__(self, substrate: claude.SubstrateImpl, spec: BenchSpec, project: Project):
        self._substrate = substrate
        self._spec = spec
        self._project = project

    def install(self, harness: Path, workspace: Path) -> str:
        staged = self._substrate.materialise(harness, self._spec, {})
        try:
            self._substrate.install(staged, workspace)
        finally:
            shutil.rmtree(staged.staging_dir, ignore_errors=True)
        return staged.fingerprint

    def run(self, instruction: str, workspace: Path, stream: Path, timeout: int) -> Outcome:
        rc = self._substrate.run(
            instruction,
            workspace,
            stream,
            self._project.model,
            timeout=timeout,
            bash_prefixes=self._project.commands,
            effort=self._project.effort,
            max_turns=self._project.max_turns,
        )
        result = claude.outcome(stream)
        return Outcome(
            rc=rc,
            cost_usd=result.get("cost_usd", 0.0),
            error=result.get("error", True),
        )


def project_setup(repo: Path) -> dict[str, str]:
    """Agents map to sub_agents/<name>/AGENT.md, the shared tree's subagent path."""
    harness: dict[str, str] = {}
    if (repo / "CLAUDE.md").is_file():
        harness["CLAUDE.md"] = (repo / "CLAUDE.md").read_text(encoding="utf-8")
    skills = repo / ".claude" / "skills"
    for p in sorted(skills.rglob("*")):
        rel = f".claude/skills/{p.relative_to(skills)}"
        if p.is_file() and HT.is_allowed(claude.SURFACE, rel):
            harness[rel] = p.read_text(encoding="utf-8")
    for p in sorted((repo / ".claude" / "agents").glob("*.md")):
        harness[f"sub_agents/{p.stem}/AGENT.md"] = p.read_text(encoding="utf-8")
    settings = repo / ".claude" / "settings.json"
    if settings.is_file() and "hooks" in json.loads(settings.read_text(encoding="utf-8")):
        harness[".claude/settings.json"] = settings.read_text(encoding="utf-8")
    return harness
