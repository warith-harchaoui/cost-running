"""
CLI surface for cost-running.

Module summary
--------------
This package is the command-line adapter. It translates argv into calls on the
shared application use cases and translates their results and exceptions into
stdout, stderr, and exit codes. It holds no business logic; every rule lives in
the domain and application layers so the CLI, HTTP, MCP, and Skill surfaces stay
in agreement.

Author
------
Project maintainers.
"""

from __future__ import annotations

from .app import main, make_parser

__all__ = ["main", "make_parser"]
