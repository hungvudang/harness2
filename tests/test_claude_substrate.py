# SPDX-License-Identifier: Apache-2.0

import shutil
import tempfile
import unittest
from pathlib import Path

from harness2.core import prompts as P
from harness2.core import tree as HT
from harness2.core.spec import BenchPrompts, BenchSpec, ChannelSpec
from harness2.substrate.claude import (
    SURFACE,
    SubstrateImpl,
    agent_file,
    bash_rules,
    outcome,
    render_events,
)

FIXTURE = Path(__file__).parent / "fixtures" / "claude" / "stream.jsonl"


def _spec(install_mode: str = "claude_root") -> BenchSpec:
    channel = ChannelSpec("claude_cli", "sonnet")
    return BenchSpec(
        name="t",
        runs_root=Path(tempfile.mkdtemp()),
        install_mode=install_mode,
        prompts=BenchPrompts(work="write code"),
        channels={"proposer": channel, "selector": channel},
    )


class Surface(unittest.TestCase):
    def test_editable_paths(self):
        for rel in (
            "CLAUDE.md",
            ".claude/settings.json",
            ".claude/skills/review/SKILL.md",
            ".claude/skills/review/scripts/check.py",
            "sub_agents/explorer/AGENT.md",
            "scripts/build_check.py",
        ):
            self.assertTrue(HT.is_allowed(SURFACE, rel), rel)

    def test_off_surface_paths(self):
        for rel in (".claude/settings.local.json", "src/Main.java", "AGENTS.md", "../CLAUDE.md"):
            self.assertFalse(HT.is_allowed(SURFACE, rel), rel)

    def test_prompt_text_names_claude_paths_only(self):
        text = P._st(SURFACE)
        for part in (text.surface_md, text.tts_surface, text.inv, text.dom):
            self.assertNotIn("AGENTS.md", part)
            self.assertNotIn(".codex", part)
            self.assertNotIn(".agents/", part)
        self.assertIn("CLAUDE.md", text.surface_md)
        self.assertIn(".claude/settings.json", text.surface_md)


class Materialise(unittest.TestCase):
    def setUp(self):
        self.tree_dir = Path(tempfile.mkdtemp())
        HT.write_tree(
            SURFACE,
            {
                "CLAUDE.md": "Build with ./gradlew build.\n- When reviewing, use the `review` skill.\n",
                ".claude/skills/review/SKILL.md": "---\ndescription: review a diff\n---\nSteps.\n",
                "sub_agents/explorer/AGENT.md": "---\ndescription: map the code\n---\nExplore.\n",
                ".claude/settings.json": '{"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "true"}]}]}}',
            },
            self.tree_dir,
        )
        self.substrate = SubstrateImpl()

    def tearDown(self):
        shutil.rmtree(self.tree_dir, ignore_errors=True)

    def test_stages_layout(self):
        install = self.substrate.materialise(self.tree_dir, _spec(), {})
        staged = install.staging_dir
        self.assertTrue((staged / "CLAUDE.md").is_file())
        self.assertTrue((staged / ".claude/skills/review/SKILL.md").is_file())
        self.assertTrue((staged / ".claude/settings.json").is_file())
        agent = (staged / ".claude/agents/explorer.md").read_text()
        self.assertIn("name: explorer", agent)
        self.assertIn("description: map the code", agent)
        self.assertFalse((staged / "sub_agents").exists())
        self.assertFalse((staged / ".claude/skills/README.md").exists())

    def test_codex_mode(self):
        with self.assertRaises(ValueError):
            self.substrate.materialise(self.tree_dir, _spec("codex_root"), {})

    def test_install_replaces_project_files(self):
        install = self.substrate.materialise(self.tree_dir, _spec(), {})
        ws = Path(tempfile.mkdtemp())
        (ws / "CLAUDE.md").write_text("old project rules")
        (ws / ".claude/commands").mkdir(parents=True)
        (ws / ".claude/commands/old.md").write_text("stale")
        (ws / "Main.java").write_text("class Main {}")
        self.substrate.install(install, ws)
        self.assertIn("gradlew", (ws / "CLAUDE.md").read_text())
        self.assertFalse((ws / ".claude/commands").exists())
        self.assertTrue((ws / "Main.java").is_file())


class Argv(unittest.TestCase):
    def test_isolated_and_allow_listed(self):
        argv = SubstrateImpl().exec_argv(Path("/ws"), "sonnet", bash_prefixes=["./gradlew"])
        self.assertIn("--strict-mcp-config", argv)
        self.assertEqual(argv[argv.index("--setting-sources") + 1], "project")
        self.assertEqual(argv[argv.index("--permission-mode") + 1], "acceptEdits")
        self.assertIn("Bash(./gradlew:*)", argv)
        self.assertNotIn("--dangerously-skip-permissions", argv)
        self.assertNotIn("Bash", argv)

    def test_empty_bash_prefix(self):
        with self.assertRaises(ValueError):
            bash_rules([" "])


class AgentFile(unittest.TestCase):
    def test_adds_name(self):
        self.assertTrue(
            agent_file("x", "---\ndescription: d\n---\nbody").startswith("---\nname: x\n")
        )

    def test_overrides_mismatched_name(self):
        out = agent_file("x", "---\nname: y\ndescription: d\n---\nbody")
        self.assertIn("name: x", out)
        self.assertNotIn("name: y", out)

    def test_no_frontmatter(self):
        self.assertIn("name: x", agent_file("x", "body"))


class Trajectory(unittest.TestCase):
    def test_renders_shared_grammar(self):
        text = render_events(FIXTURE, obs_trunc=10_000, max_chars=100_000)
        self.assertIn("[HARNESS] session start", text)
        self.assertIn("[ACTION] Bash: ls -a", text)
        self.assertIn("[OBSERVATION ERROR] Exit code 1", text)
        self.assertIn("[ACTION] Write:", text)
        self.assertIn("[ASSISTANT] done", text)
        self.assertIn("[HARNESS] result success", text)

    def test_truncates(self):
        text = render_events(FIXTURE, obs_trunc=10_000, max_chars=50)
        self.assertIn("render truncation", text)

    def test_missing_stream(self):
        self.assertIn("failed to read", render_events(Path("/nope"), obs_trunc=1, max_chars=1))

    def test_timeout_marker(self):
        p = Path(tempfile.mkdtemp()) / "s.jsonl"
        p.write_text('{"type": "_timeout", "seconds": 5}\n')
        self.assertIn(
            "[ERROR] rollout killed after 5s", render_events(p, obs_trunc=100, max_chars=1000)
        )

    def test_outcome(self):
        got = outcome(FIXTURE)
        self.assertEqual(got["text"], "done")
        self.assertFalse(got["error"])
        self.assertGreater(got["cost_usd"], 0)


if __name__ == "__main__":
    unittest.main()
