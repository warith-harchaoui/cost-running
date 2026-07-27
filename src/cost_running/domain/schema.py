"""
Cost-model schema identity and compatibility policy.

Module summary
--------------
A cost model is a plain mapping (loaded from YAML) so it stays diffable and
tool-agnostic. What makes it a *contract* is the ``schema_version`` field and the
rule for interpreting it. This module owns the current version and the helper
that extracts a major number, so the validator can accept, warn on, or reject a
model without duplicating the parsing logic.

Compatibility policy: within a major line (``1.x``) the schema changes only by
addition. A model written today keeps validating against every later ``1.x``
release. A model written for a newer major than this build understands is
rejected rather than silently misread against the wrong rules.

Author
------
Project maintainers.
"""

from __future__ import annotations

# The current cost-model schema line. Emitted by the templates and the auditor,
# checked by the validator. Bump the minor for additive changes, the major only
# for a break.
SCHEMA_VERSION: str = "1.0"


def schema_major(version: object) -> int | None:
    """Return the integer major component of a ``schema_version`` value.

    Parameters
    ----------
    version : object
        The raw value of a model's ``schema_version`` field, expected to be a
        ``"MAJOR"`` or ``"MAJOR.MINOR"`` string but tolerant of anything.

    Returns
    -------
    int or None
        The major version as an integer, or ``None`` when the value is not a
        well-formed ``MAJOR[.MINOR]`` string. ``None`` lets the validator report
        a precise "malformed version" error rather than crashing.

    Examples
    --------
    >>> schema_major("1.0")
    1
    >>> schema_major("2")
    2
    >>> schema_major("abc") is None
    True
    """
    # Coerce to string and trim so an int or padded value still parses.
    text = str(version).strip()
    if not text:
        return None
    # The major is everything before the first dot.
    head = text.split(".", 1)[0]
    if not head.isdigit():
        return None
    return int(head)
