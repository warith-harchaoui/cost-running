"""
Domain layer: the vocabulary and invariants of a cost model.

Module summary
--------------
This package holds the pure, dependency-free heart of cost-running: the honesty
taxonomy, the extensible dimension registry, the schema identity, and the
quantity value object. Nothing here imports a delivery surface (CLI, HTTP, MCP),
a file system, or a network client, so every adapter and use case can build on
one shared definition of what a cost model *means*.

Author
------
Project maintainers.
"""

from __future__ import annotations

from .dimensions import (
    CANONICAL_DIMENSIONS,
    CARBON,
    ENERGY,
    MONEY,
    TIME,
    WATER,
    Dimension,
    DimensionRegistry,
)
from .quantity import Quantity
from .schema import SCHEMA_VERSION, schema_major
from .taxonomy import (
    ALLOWED_STATUSES,
    ESTIMATED,
    MEASURED,
    PLACEHOLDER,
    TODO,
    is_valid_status,
    status_strength,
    weakest,
)

__all__ = [
    # Taxonomy.
    "ALLOWED_STATUSES",
    "MEASURED",
    "ESTIMATED",
    "PLACEHOLDER",
    "TODO",
    "is_valid_status",
    "status_strength",
    "weakest",
    # Dimensions.
    "Dimension",
    "DimensionRegistry",
    "CANONICAL_DIMENSIONS",
    "MONEY",
    "TIME",
    "ENERGY",
    "CARBON",
    "WATER",
    # Schema.
    "SCHEMA_VERSION",
    "schema_major",
    # Quantity.
    "Quantity",
]
