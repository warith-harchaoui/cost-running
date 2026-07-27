"""
Honesty taxonomy for cost-of-running values.

Module summary
--------------
Every number in a cost model carries a *status* that states how much it can be
trusted, and derived numbers obey a *weakest-link* rule: a value computed from
inputs can never claim to be better-founded than the worst of those inputs. This
module owns that vocabulary and the ordering that makes the weakest-link rule
computable. It has no dependencies on the rest of the package so every other
layer (validation, rendering, estimators, adapters) can share one definition.

The four statuses, from strongest to weakest:

- ``measured``: recorded from an actual run on the target system.
- ``estimated``: computed from sourced assumptions or a published formula.
- ``placeholder``: a structural stand-in kept visible; not a real number.
- ``TODO``: a human must supply this before the model can be trusted.

Author
------
Project maintainers.
"""

from __future__ import annotations

# The canonical status labels. Kept as a frozenset so callers can validate an
# arbitrary string against the vocabulary in one membership test.
MEASURED: str = "measured"
ESTIMATED: str = "estimated"
PLACEHOLDER: str = "placeholder"
TODO: str = "TODO"

ALLOWED_STATUSES: frozenset[str] = frozenset({MEASURED, ESTIMATED, PLACEHOLDER, TODO})

# Numeric strength of each status. Higher means better-founded. The gaps are not
# meaningful; only the ordering is, because the weakest-link rule compares them.
_STRENGTH: dict[str, int] = {
    MEASURED: 3,
    ESTIMATED: 2,
    PLACEHOLDER: 1,
    TODO: 0,
}


def is_valid_status(status: object) -> bool:
    """Return whether ``status`` is one of the four allowed labels.

    Parameters
    ----------
    status : object
        Any value; typically a string parsed from a YAML cost model.

    Returns
    -------
    bool
        ``True`` when ``status`` is exactly one of the allowed labels.

    Examples
    --------
    >>> is_valid_status("measured")
    True
    >>> is_valid_status("guessed")
    False
    """
    # Membership on a frozenset is O(1) and rejects non-strings safely.
    return status in ALLOWED_STATUSES


def status_strength(status: str) -> int:
    """Return the numeric strength of a status for weakest-link comparison.

    Parameters
    ----------
    status : str
        One of the allowed labels.

    Returns
    -------
    int
        Strength where ``measured`` is highest and ``TODO`` is lowest. An
        unknown label is treated as the weakest possible so a typo can never
        accidentally *raise* a derived value's trust.

    Examples
    --------
    >>> status_strength("measured") > status_strength("estimated")
    True
    """
    # Default to -1 (below TODO) for unknown labels: an unrecognised status must
    # never let a derived value claim more confidence than it has earned.
    return _STRENGTH.get(status, -1)


def weakest(*statuses: str | None) -> str | None:
    """Return the weakest of several statuses, ignoring missing ones.

    This is the engine of the weakest-link rule. A derived quantity (energy from
    runtime and power, cost from energy and price) may claim at most the status
    returned here for its inputs.

    Parameters
    ----------
    *statuses : str or None
        Input statuses. ``None`` entries are skipped so a caller can pass the
        status of optional inputs without special-casing them.

    Returns
    -------
    str or None
        The weakest present status, or ``None`` when every argument was ``None``.

    Examples
    --------
    >>> weakest("measured", "estimated")
    'estimated'
    >>> weakest("measured", None, "TODO")
    'TODO'
    >>> weakest(None, None) is None
    True
    """
    # Drop the optional/absent inputs so they do not participate in the minimum.
    present = [s for s in statuses if s is not None]
    if not present:
        return None
    # The weakest input is the one with the smallest strength.
    return min(present, key=status_strength)
