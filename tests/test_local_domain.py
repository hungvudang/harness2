# SPDX-License-Identifier: Apache-2.0

"""Domain invariants of the local-tasks context."""

from pathlib import Path

import pytest

from harness2.local.domain.rollout import Criterion, Outcome, Verdict
from harness2.local.domain.task import Project, Task


def _project(**overrides) -> Project:
    fields = dict(
        name="svc", repo=Path("/repo"), work="write Java", commands=("./gradlew",), model="sonnet"
    )
    fields.update(overrides)
    return Project(**fields)


def _task(**overrides) -> Task:
    fields = dict(id="t1", commit="abc", domain="order", instruction="Add X.")
    fields.update(overrides)
    return Task(**fields)


class TestProject:
    class TestCreate:
        def test_defaults(self):
            project = _project()
            assert (project.timeout, project.max_turns, project.domain_word) == (3600, 0, "module")

        @pytest.mark.parametrize("field", ["name", "work", "model"])
        def test_blank_text(self, field):
            with pytest.raises(ValueError, match=field):
                _project(**{field: " "})

        def test_blank_command(self):
            with pytest.raises(ValueError, match="commands"):
                _project(commands=("./gradlew", ""))

        def test_no_timeout(self):
            with pytest.raises(ValueError, match="timeout"):
                _project(timeout=0)

        def test_negative_turns(self):
            with pytest.raises(ValueError, match="max_turns"):
                _project(max_turns=-1)


class TestTask:
    class TestCreate:
        def test_without_criteria(self):
            assert _task().criteria == ()

        @pytest.mark.parametrize("field", ["id", "commit", "domain", "instruction"])
        def test_blank_text(self, field):
            with pytest.raises(ValueError, match=field):
                _task(**{field: ""})

        def test_blank_criterion(self):
            with pytest.raises(ValueError, match="criteria"):
                _task(criteria=(" ",))

        def test_no_verify_timeout(self):
            with pytest.raises(ValueError, match="verify_timeout"):
                _task(verify_timeout=-5)

        def test_rubric_without_reference(self):
            with pytest.raises(ValueError, match="reference"):
                _task(rubric=("adds a job",))

        def test_blank_rubric_item(self):
            with pytest.raises(ValueError, match="rubric"):
                _task(reference="abc", rubric=(" ",))

    class TestJudged:
        def test_nothing_to_judge(self):
            assert not _task().judged

        def test_rubric_only(self):
            assert _task(reference="abc", rubric=("adds a job",)).judged


class TestOutcome:
    class TestFailed:
        def test_clean_exit(self):
            assert not Outcome(rc=0).failed

        def test_nonzero_exit(self):
            assert Outcome(rc=124).failed

        def test_session_error(self):
            assert Outcome(rc=0, error=True).failed


class TestVerdict:
    class TestCreate:
        def test_no_criteria(self):
            with pytest.raises(ValueError):
                Verdict(())

    class TestScore:
        def test_counts_met_criteria(self):
            verdict = Verdict((Criterion("build", True), Criterion("lint", False, "E501")))
            score = verdict.score()
            assert (score["n_passed"], score["n_criteria"]) == (1, 2)
            assert score["criteria"][1] == {"text": "lint", "met": False, "output": "E501"}

        def test_all_met(self):
            assert Verdict((Criterion("build", True),)).met == 1
