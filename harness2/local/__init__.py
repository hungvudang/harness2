# SPDX-License-Identifier: Apache-2.0

"""Local tasks: harness a project's own Claude Code setup on its own history.

Layers: domain (entities, invariants) <- application (ports, use cases) <- infrastructure
(toml, git, claude, fs implementations) <- adapter (composition root, BenchmarkAdapter facade).
"""
