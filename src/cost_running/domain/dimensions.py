"""
The dimensions of the cost of running code.

Module summary
--------------
This is where cost-running widens the scope of its `nexteco` predecessor. There,
five dimensions (money, time, energy, carbon, water) were hard-wired into the
schema and the renderer. Here a dimension is a first-class, registered object, so
the same machinery covers the canonical five and any extra a team needs to track
(for example memory-hours, network egress, or a domain-specific unit), without
touching the validator or the renderer.

A :class:`Dimension` is deliberately small: an identity, a human label, a unit,
and a one-line description of what is and is not counted. It carries no numbers;
values live in the cost model and reference a dimension by its ``key``.

Author
------
Project maintainers.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Dimension:
    """One measurable axis of the cost of running a unit of work.

    Parameters
    ----------
    key : str
        Stable machine identifier used in YAML and code (for example ``money``).
        Lowercase, snake or single words; it never changes once published.
    label : str
        Human-readable name shown in reports (for example ``Money``).
    unit : str
        The default unit values on this dimension are expressed in (for example
        ``USD``). Individual values may override it, but this documents intent.
    description : str
        One sentence stating what the dimension counts and its boundary, so a
        reader knows what a number on this axis does and does not include.
    higher_is_worse : bool
        Whether a larger value is a regression. ``True`` for every cost-like
        dimension; exposed so tooling (diff gates, dashboards) can reason about
        direction without hard-coding dimension names.

    Examples
    --------
    >>> Dimension("money", "Money", "USD", "Electricity plus API price.").unit
    'USD'
    """

    key: str
    label: str
    unit: str
    description: str
    higher_is_worse: bool = True


# The canonical dimensions carried over from the proven nexteco model. They are
# the default set every cost model reports; a project may register more.
MONEY = Dimension(
    key="money",
    label="Money",
    unit="USD",
    description="Local electricity cost plus external API price, per unit.",
)
TIME = Dimension(
    key="time",
    label="Time",
    unit="s",
    description="Wall-clock runtime of one canonical unit of work.",
)
ENERGY = Dimension(
    key="energy",
    label="Energy",
    unit="kWh",
    description="Local compute electricity drawn to run one unit.",
)
CARBON = Dimension(
    key="carbon",
    label="CO2e",
    unit="gCO2e",
    description="Grid emissions for the energy of one unit, by region intensity.",
)
WATER = Dimension(
    key="water",
    label="Water",
    unit="L",
    description="Datacenter cooling water for one unit, when a sourced WUE exists.",
)

# The default registry, keyed by ``Dimension.key`` and ordered as reports read.
CANONICAL_DIMENSIONS: tuple[Dimension, ...] = (MONEY, TIME, ENERGY, CARBON, WATER)


class DimensionRegistry:
    """A mutable, ordered collection of dimensions a cost model reports.

    The registry starts from the canonical five and lets a caller add more. It
    preserves insertion order so reports are stable, and rejects duplicate keys
    so two dimensions can never collide silently.

    Parameters
    ----------
    dimensions : tuple of Dimension, optional
        Initial dimensions. Defaults to :data:`CANONICAL_DIMENSIONS`.

    Examples
    --------
    >>> registry = DimensionRegistry()
    >>> registry.get("energy").unit
    'kWh'
    >>> registry.register(Dimension("egress", "Egress", "GB", "Bytes leaving."))
    >>> "egress" in registry
    True
    """

    def __init__(self, dimensions: tuple[Dimension, ...] = CANONICAL_DIMENSIONS) -> None:
        # Store by key for O(1) lookup while keeping the tuple's order.
        self._by_key: dict[str, Dimension] = {}
        for dimension in dimensions:
            self.register(dimension)

    def register(self, dimension: Dimension) -> None:
        """Add a dimension, rejecting a duplicate key.

        Parameters
        ----------
        dimension : Dimension
            The dimension to add.

        Raises
        ------
        ValueError
            If a dimension with the same ``key`` is already registered.
        """
        # A duplicate key would make report order and lookups ambiguous; refuse.
        if dimension.key in self._by_key:
            raise ValueError(f"Dimension {dimension.key!r} is already registered.")
        self._by_key[dimension.key] = dimension

    def get(self, key: str) -> Dimension:
        """Return the dimension registered under ``key``.

        Parameters
        ----------
        key : str
            The dimension key to look up.

        Returns
        -------
        Dimension
            The registered dimension.

        Raises
        ------
        KeyError
            If no dimension is registered under ``key``.
        """
        return self._by_key[key]

    def __contains__(self, key: object) -> bool:
        """Return whether a dimension is registered under ``key``."""
        return key in self._by_key

    def __iter__(self):
        """Iterate dimensions in registration order."""
        return iter(self._by_key.values())

    def keys(self) -> tuple[str, ...]:
        """Return the registered keys in order."""
        return tuple(self._by_key.keys())
