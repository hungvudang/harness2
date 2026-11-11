# SPDX-License-Identifier: Apache-2.0

"""Isolation: rollouts load project settings only (`--setting-sources project`) and no MCP
servers (`--strict-mcp-config`), so the operator's own ~/.claude (CLAUDE.md, rules, skills,
plugins, MCP) never leaks into a probe. Permissions are an allow-list, never a bypass.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Iterable, Mapping

from ... import config
from ...core import runmark as RM
from ...core import tree as HT
from ...core.tree import SurfaceSpec
from ...utils import strings
from ..base import Install, Launcher, RunRequest

SURFACE = SurfaceSpec(
    name="claude",
    always_on=("CLAUDE.md",),
    always_on_max=12_000,
    always_on_cap_reason=(
        "CLAUDE.md is loaded whole into the context of EVERY session and every subagent spawn"
    ),
    skill_dir=".claude/skills",
    skill_name_rule="overrides_dir",
    trigger_hint=(
        'Fix it by adding a trigger line naming `{name}` to CLAUDE.md, e.g. "When '
        '<the situation this skill handles>, use the `{name}` skill."'
    ),
    autotrigger_file="CLAUDE.md",
    trigger_line="- When {desc}, use the `{name}` skill.\n",
    subagent_requires_mode=False,
    plugin_dir=None,
    hooks_file=".claude/settings.json",
    hook_events=frozenset(
        {
            "PreToolUse",
            "PostToolUse",
            "Notification",
            "UserPromptSubmit",
            "Stop",
            "SubagentStart",
            "SubagentStop",
            "PreCompact",
            "SessionStart",
            "SessionEnd",
        }
    ),
    compile_skill_scripts=True,
    reject_empty_always_on=True,
    stubs=(
        (
            "scripts/README.md",
            "<!-- c7: deterministic helpers, run as `python3 scripts/<name>.py`. "
            "Add one by emitting scripts/<name>.py. -->\n",
        ),
        (
            "sub_agents/README.md",
            "<!-- c5: delegated Claude Code subagents, each spawned in its OWN context. Add one "
            "as sub_agents/<name>/AGENT.md with frontmatter `description:`; the runner installs "
            "it as .claude/agents/<name>.md. -->\n",
        ),
        (
            ".claude/README.md",
            "<!-- c8: Claude Code lifecycle hooks -- the only lever that acts INSIDE the tool "
            "loop, so the model cannot skip it. Add ONE file, .claude/settings.json: "
            '{"hooks": {"PostToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", '
            '"command": "..."}]}]}}. A PostToolUse command that prints '
            '{"decision":"block","reason":"..."} feeds the reason back to the model. -->\n',
        ),
        (
            ".claude/skills/README.md",
            "<!-- c6: reusable workflows. Add one as .claude/skills/<name>/SKILL.md with YAML "
            "frontmatter: `description:` is REQUIRED; `name:` is optional and must match the "
            "directory. Only name+description are advertised; the body loads on demand. -->\n",
        ),
    ),
)


CLAUDE_ROOT_ENTRIES = ("CLAUDE.md", ".claude/skills", ".claude/settings.json", "scripts")

# Tools a rollout may use without a prompt. Bash is NOT here: each benchmark grants the
# exact command prefixes its work needs, so a rollout cannot run arbitrary commands.
TOOLS = ("Read", "Write", "Edit", "Glob", "Grep", "Skill", "Task", "TodoWrite")

_FRONTMATTER = re.compile(r"^---\n(.*?)\n---\n?", re.S)


def bash_rules(prefixes: Iterable[str]) -> list[str]:
    rules = []
    for prefix in prefixes:
        prefix = str(prefix).strip()
        if not prefix:
            raise ValueError("an allowed Bash prefix must not be empty")
        rules.append(f"Bash({prefix}:*)")
    return rules


def agent_file(name: str, body: str) -> str:
    """Claude Code skips an agent file without `name:`, so one is always written."""
    m = _FRONTMATTER.match(body)
    if m is None:
        return f"---\nname: {name}\ndescription: the {name} subagent\n---\n{body}"
    meta = m.group(1)
    if re.search(r"^name:\s*\S", meta, re.M):
        meta = re.sub(r"^name:.*$", f"name: {name}", meta, count=1, flags=re.M)
    else:
        meta = f"name: {name}\n{meta}"
    return f"---\n{meta}\n---\n{body[m.end() :]}"


class SubstrateImpl:
    name = "cc"

    def __init__(self, launcher: Launcher | None = None):
        self._launcher = launcher

    def surface(self) -> SurfaceSpec:
        return SURFACE

    def materialise(self, tree_dir: Path, spec, ctx: Mapping[str, str]) -> Install:
        """Stage the tree as Claude Code project files; sub_agents become .claude/agents."""
        if spec.install_mode != "claude_root":
            raise ValueError(
                f"install_mode {spec.install_mode!r} is not the Claude Code mode; this "
                f"substrate has exactly one: 'claude_root'"
            )
        tree_dir = Path(tree_dir)
        if not HT.is_tree(SURFACE, tree_dir):
            raise ValueError(f"{tree_dir} is not a Claude Code harness tree (no CLAUDE.md)")
        staged = Path(tempfile.mkdtemp(prefix="adapt-cc-root-"))
        for entry in CLAUDE_ROOT_ENTRIES:
            src = tree_dir / entry
            if not src.exists():
                continue
            dest = staged / entry
            dest.parent.mkdir(parents=True, exist_ok=True)
            if src.is_dir():
                shutil.copytree(src, dest, ignore=shutil.ignore_patterns("README.md"))
            else:
                shutil.copy2(src, dest)
        harness = HT.read_tree(SURFACE, tree_dir)
        for name in HT.subagent_names(SURFACE, harness):
            dest = staged / ".claude" / "agents" / f"{name}.md"
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(
                agent_file(name, harness[f"sub_agents/{name}/AGENT.md"]), encoding="utf-8"
            )
        return Install(
            staging_dir=staged,
            fingerprint=RM.fingerprint(tree_dir),
            extras={
                "install_mode": "claude_root",
                **{k: v for k, v in ctx.items() if not k.startswith("file:")},
            },
        )

    def install(self, staged: Install, workspace: Path) -> None:
        """The snapshot's own CLAUDE.md/.claude go first, or they would mix with the candidate."""
        workspace = Path(workspace)
        for stale in ("CLAUDE.md", ".claude"):
            p = workspace / stale
            if p.is_dir():
                shutil.rmtree(p)
            elif p.exists():
                p.unlink()
        shutil.copytree(staged.staging_dir, workspace, dirs_exist_ok=True)

    def exec_argv(
        self,
        workspace: Path,
        model: str,
        *,
        bash_prefixes: Iterable[str] = (),
        effort: str = "",
        max_turns: int = 0,
    ) -> list[str]:
        """The `claude -p` argv; the prompt goes on stdin, the stream to stdout."""
        cmd = [
            shutil.which("claude") or "claude",
            "-p",
            "--model",
            model,
            "--setting-sources",
            "project",
            "--strict-mcp-config",
            "--permission-mode",
            "acceptEdits",
            "--output-format",
            "stream-json",
            "--verbose",
            "--add-dir",
            str(workspace),
            "--allowedTools",
            *TOOLS,
            *bash_rules(bash_prefixes),
        ]
        if effort:
            cmd += ["--effort", effort]
        if max_turns > 0:
            cmd += ["--max-turns", str(max_turns)]
        return cmd

    def exec_env(self, base_env: Mapping[str, str] | None = None) -> dict[str, str]:
        """Scrubbed environment; the CLI authenticates from its own login, not env keys."""
        return config.scrub_env(base_env)

    def run(
        self,
        prompt: str,
        workspace: Path,
        stream_path: Path,
        model: str,
        *,
        timeout: int,
        bash_prefixes: Iterable[str] = (),
        effort: str = "",
        max_turns: int = 0,
    ) -> int:
        """One rollout: stream events to `stream_path`; returns the CLI exit code.

        A timeout is not raised: the partial stream is kept and an error event appended, so
        the trajectory still shows how far the run got.
        """
        stream_path = Path(stream_path)
        stream_path.parent.mkdir(parents=True, exist_ok=True)
        argv = self.exec_argv(
            workspace, model, bash_prefixes=bash_prefixes, effort=effort, max_turns=max_turns
        )
        with stream_path.open("w", encoding="utf-8") as out:
            try:
                p = subprocess.run(
                    argv,
                    input=prompt,
                    cwd=workspace,
                    env=self.exec_env(),
                    text=True,
                    stdout=out,
                    stderr=subprocess.PIPE,
                    timeout=timeout,
                )
            except subprocess.TimeoutExpired:
                out.write(json.dumps({"type": "_timeout", "seconds": timeout}) + "\n")
                return 124
        if p.returncode != 0 and p.stderr:
            with stream_path.open("a", encoding="utf-8") as out:
                out.write(json.dumps({"type": "_stderr", "text": p.stderr[-2000:]}) + "\n")
        return p.returncode

    def execute(
        self, task_ref: str, install: Install, run_ids: list[str], timeout: int
    ) -> list[Path]:
        if self._launcher is None:
            raise RuntimeError(
                "SubstrateImpl was constructed without a launcher body; the benchmark "
                "adapter must supply one (it owns the workspace and read-back locations)"
            )
        return self._launcher(
            RunRequest(task_ref=task_ref, run_ids=tuple(run_ids), timeout=int(timeout)), install
        )

    def read_trajectory(
        self, stream_path: Path, *, obs_trunc: int, max_chars: int, args_trunc: int = 2000
    ) -> str:
        return render_events(
            Path(stream_path), obs_trunc=obs_trunc, max_chars=max_chars, args_trunc=args_trunc
        )

    def preflight(self) -> None:
        """The CLI must be on PATH and runnable; never starts a model session."""
        cli = shutil.which("claude")
        if cli is None:
            raise RuntimeError("claude CLI not found; install Claude Code and run `claude` once")
        p = subprocess.run([cli, "--version"], capture_output=True, text=True, timeout=30)
        if p.returncode != 0:
            raise RuntimeError(f"`claude --version` failed: {(p.stderr or p.stdout)[-300:]}")


_CLIP_REASON = "render truncation"


def _clip(value, limit: int) -> str:
    return strings.clip(value, limit, reason=_CLIP_REASON)


def parse_events(path: Path) -> list[dict]:
    try:
        raw = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return [{"type": "_read_error", "message": str(e)}]
    events = []
    for ln in raw.replace("\x00", "").splitlines():
        ln = ln.strip()
        if not ln.startswith("{"):
            continue
        try:
            ev = json.loads(ln)
        except json.JSONDecodeError:
            continue
        if isinstance(ev, dict):
            events.append(ev)
    return events


def _tool_input(name: str, args: dict, args_trunc: int) -> str:
    if name == "Bash":
        return _clip(args.get("command"), args_trunc)
    if name in ("Read", "Write", "Edit", "MultiEdit", "NotebookEdit"):
        path = args.get("file_path") or args.get("notebook_path") or ""
        rest = {k: v for k, v in args.items() if k not in ("file_path", "notebook_path")}
        if not rest:
            return str(path)
        return f"{path} {_clip(json.dumps(rest, ensure_ascii=False), args_trunc)}"
    return _clip(json.dumps(args, ensure_ascii=False), args_trunc)


def _result_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for c in content:
            if isinstance(c, dict) and c.get("type") == "text":
                parts.append(str(c.get("text", "")))
            elif isinstance(c, dict):
                parts.append(f"<{c.get('type', 'block')}>")
        return "\n".join(parts)
    if content is None:
        return ""
    return str(content)


def _render_message(ev: dict, lines: list[str], obs_trunc: int, args_trunc: int) -> None:
    msg = ev.get("message")
    if not isinstance(msg, dict):
        return
    content = msg.get("content")
    if isinstance(content, str):
        if content.strip():
            tag = "ASSISTANT"
            if ev.get("type") == "user":
                tag = "USER"
            lines.append(f"[{tag}] {_clip(content, obs_trunc)}")
        return
    if not isinstance(content, list):
        return
    for block in content:
        if not isinstance(block, dict):
            continue
        kind = block.get("type")
        if kind == "text" and str(block.get("text", "")).strip():
            tag = "ASSISTANT"
            if ev.get("type") == "user":
                tag = "USER"
            lines.append(f"[{tag}] {_clip(block['text'], obs_trunc)}")
        elif kind == "thinking" and str(block.get("thinking", "")).strip():
            lines.append(f"[REASONING] {_clip(block['thinking'], obs_trunc)}")
        elif kind == "tool_use":
            name = str(block.get("name", "tool"))
            args = block.get("input")
            if not isinstance(args, dict):
                args = {"input": args}
            lines.append(f"[ACTION] {name}: {_tool_input(name, args, args_trunc)}")
        elif kind == "tool_result":
            tag = "OBSERVATION"
            if block.get("is_error"):
                tag = "OBSERVATION ERROR"
            lines.append(f"[{tag}] {_clip(_result_text(block.get('content')), obs_trunc)}")


def render_events(path: Path, *, obs_trunc: int, max_chars: int, args_trunc: int = 2000) -> str:
    lines: list[str] = []
    for ev in parse_events(Path(path)):
        et = ev.get("type", "")
        if et == "_read_error":
            return f"(failed to read trajectory: {ev.get('message')})"
        if et in ("assistant", "user"):
            _render_message(ev, lines, obs_trunc, args_trunc)
        elif et == "system" and ev.get("subtype") == "init":
            lines.append(f"[HARNESS] session start | model={ev.get('model')}")
        elif et == "result":
            lines.append(
                "[HARNESS] result {s} | turns={t} cost_usd={c}".format(
                    s=ev.get("subtype"), t=ev.get("num_turns"), c=ev.get("total_cost_usd")
                )
            )
            if ev.get("is_error"):
                lines.append(f"[ERROR] {_clip(ev.get('result'), 1000)}")
        elif et == "_timeout":
            lines.append(f"[ERROR] rollout killed after {ev.get('seconds')}s timeout")
        elif et == "_stderr":
            lines.append(f"[ERROR] {_clip(ev.get('text'), 1000)}")
    rendered = "\n".join(lines).strip()
    if not rendered:
        return "(no parseable events in trajectory)"
    if len(rendered) > max_chars:
        dropped = len(rendered) - max_chars
        rendered = rendered[:max_chars] + "\n" + strings.omission(dropped, _CLIP_REASON)
    return rendered


def outcome(path: Path) -> dict:
    for ev in reversed(parse_events(Path(path))):
        if ev.get("type") == "result":
            return {
                "text": str(ev.get("result") or ""),
                "cost_usd": float(ev.get("total_cost_usd") or 0.0),
                "error": bool(ev.get("is_error")),
                "subtype": str(ev.get("subtype") or ""),
            }
    return {}
