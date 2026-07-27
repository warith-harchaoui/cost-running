"""
Application layer: use cases that operate on a cost model.

Module summary
--------------
The application layer turns the domain vocabulary into the operations a user
actually asks for: load a model, validate it, render it to Markdown, and (in
later increments) diff, audit, and measure. Each use case is a plain function or
small class that a delivery surface (CLI, HTTP, MCP, Skill) calls without adding
business logic of its own. Nothing here parses command-line flags or HTTP
requests; that belongs to the adapters.

Author
------
Project maintainers.
"""

from __future__ import annotations

from .io import dump_model_yaml, load_model, write_text
from .render import render_markdown
from .results import ValidationIssue, ValidationResult
from .validate import validate_model

__all__ = [
    "load_model",
    "dump_model_yaml",
    "write_text",
    "render_markdown",
    "validate_model",
    "ValidationIssue",
    "ValidationResult",
]
