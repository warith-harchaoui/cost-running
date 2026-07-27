"""
cost-running: measure the cost of running code, in the wide sense.

Module summary
--------------
cost-running produces a per-canonical-unit cost model for a piece of software and
reports it across dimensions (money, time, energy, carbon, water, and any a team
registers), with every number carrying an honesty status: measured, estimated,
placeholder, or TODO. The model is a YAML file committed next to the code, plus a
generated Markdown report. This package is the shared core; delivery surfaces
(CLI, HTTP API, MCP server, GUI, Agent Skill) are thin adapters over it.

Public API and stability
------------------------
Everything in ``__all__`` is the intended public surface. During the pre-1.0
line it may still change; from 1.0 it will change only additively, and the YAML
schema is versioned independently through ``SCHEMA_VERSION``.

Author
------
Project maintainers.
"""

from __future__ import annotations

from .application import (
    ValidationIssue,
    ValidationResult,
    dump_model_yaml,
    load_model,
    render_markdown,
    validate_model,
    write_text,
)
from .domain import (
    CANONICAL_DIMENSIONS,
    SCHEMA_VERSION,
    Dimension,
    DimensionRegistry,
    Quantity,
    is_valid_status,
    status_strength,
    weakest,
)
from .templates import get_template_text

# The installed package version. Kept in step with pyproject.toml at release.
__version__ = "0.1.0"

__all__ = [
    "__version__",
    # Domain.
    "SCHEMA_VERSION",
    "Dimension",
    "DimensionRegistry",
    "CANONICAL_DIMENSIONS",
    "Quantity",
    "is_valid_status",
    "status_strength",
    "weakest",
    # Application use cases.
    "load_model",
    "dump_model_yaml",
    "write_text",
    "validate_model",
    "render_markdown",
    "ValidationIssue",
    "ValidationResult",
    # Templates.
    "get_template_text",
]
