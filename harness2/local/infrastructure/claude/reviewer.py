# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import tempfile

from .... import config
from ....utils import strings
from ...domain.rollout import Criterion
from ...domain.task import Task

log = logging.getLogger(__name__)

DIFF_CAP = 150_000
TIMEOUT = 900

PROMPT = """\
You review one code change against a list of requirements. You cannot run anything; judge
from the diff alone.

# Request given to the developer
{instruction}

# Requirements
{rubric}

# Reference change
A change that once did this work. It is ONE acceptable design, not the answer key: a
requirement is met when the developer's diff satisfies it in any reasonable way. Where the
request and the reference disagree, the request wins.

```diff
{reference}
```

# Developer's change

```diff
{diff}
```

Reply with ONLY a JSON array, one object per requirement in the order given:
[{{"id": 1, "met": true, "evidence": "<file and what in it meets or misses the requirement>"}}]
Mark a requirement met only when the diff shows it; an untested claim in a comment is not
evidence. Keep each evidence under 40 words.
"""


class ReviewError(RuntimeError):
    pass


def _items(text: str, expected: int) -> list[dict]:
    start, end = text.find("["), text.rfind("]")
    if start < 0 or end <= start:
        raise ReviewError(f"reviewer reply has no JSON array: {strings.clip(text, 300)}")
    items = json.loads(text[start : end + 1])
    if not isinstance(items, list) or len(items) != expected:
        raise ReviewError(f"reviewer judged {len(items)} requirements, expected {expected}")
    return items


class ReviewerImpl:
    def __init__(self, model: str):
        self._model = model

    def review(self, task: Task, diff: str, reference: str) -> tuple[Criterion, ...]:
        prompt = PROMPT.format(
            instruction=task.instruction,
            rubric="\n".join(f"{i}. {item}" for i, item in enumerate(task.rubric, 1)),
            reference=strings.clip(reference, DIFF_CAP, reason="reference truncated"),
            diff=strings.clip(diff, DIFF_CAP, reason="diff truncated") or "(no change)",
        )
        # An empty directory, no tools, project settings only: the reviewer sees the prompt
        # and nothing else, so neither the workspace nor ~/.claude can sway it.
        cwd = tempfile.mkdtemp(prefix="harness2-review-")
        try:
            p = subprocess.run(
                [
                    shutil.which("claude") or "claude",
                    "-p",
                    "--model",
                    self._model,
                    "--setting-sources",
                    "project",
                    "--strict-mcp-config",
                    "--tools",
                    "",
                    "--output-format",
                    "json",
                ],
                input=prompt,
                cwd=cwd,
                env=config.scrub_env(),
                capture_output=True,
                text=True,
                timeout=TIMEOUT,
            )
        finally:
            shutil.rmtree(cwd, ignore_errors=True)
        if p.returncode != 0:
            raise ReviewError(f"claude reviewer rc={p.returncode}: {strings.clip(p.stderr, 300)}")
        reply = json.loads(p.stdout)
        if reply.get("is_error"):
            raise ReviewError(f"claude reviewer: {strings.clip(reply.get('result'), 300)}")
        items = _items(str(reply.get("result", "")), len(task.rubric))
        log.info("review of %s cost $%.2f", task.id, float(reply.get("total_cost_usd") or 0))
        return tuple(
            Criterion(item, bool(got.get("met")), str(got.get("evidence", "")))
            for item, got in zip(task.rubric, items)
        )
