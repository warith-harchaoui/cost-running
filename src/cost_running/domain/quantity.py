"""
The quantity value object: a number that remembers where it came from.

Module summary
--------------
Everywhere a cost model states a number, it states four things with it: the
value, its unit, its honesty status, and its provenance (a source URL and the
date it was retrieved). Bundling them keeps the honesty taxonomy and the
weakest-link rule enforceable, because a bare float has forgotten whether it was
measured or guessed. :class:`Quantity` is that bundle. It maps directly to the
``{value, unit, status, source_url, retrieved_date, notes}`` shape used in the
YAML, so parsing and serialising are lossless.

Author
------
Project maintainers.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .taxonomy import is_valid_status


@dataclass(frozen=True, slots=True)
class Quantity:
    """A single value with its unit, honesty status, and provenance.

    Parameters
    ----------
    value : float or int or None
        The number itself. ``None`` is allowed for a structural placeholder or a
        field awaiting human input.
    unit : str or None
        The unit the value is expressed in (for example ``USD/kWh``).
    status : str
        One of the honesty labels (``measured`` / ``estimated`` / ``placeholder``
        / ``TODO``). Not validated here; :func:`is_status_valid` reports it so the
        caller controls whether a bad label is a warning or an error.
    source_url : str or None
        A live reference establishing the value, for a sourced ``estimated``.
    retrieved_date : str or None
        The ``YYYY-MM-DD`` date the source was read, so staleness is checkable.
    notes : str or None
        A short human note, typically the formula or the reasoning.

    Examples
    --------
    >>> q = Quantity.from_mapping({"value": 0.16, "unit": "USD/kWh", "status": "estimated"})
    >>> q.value, q.status
    (0.16, 'estimated')
    >>> q.to_mapping()["unit"]
    'USD/kWh'
    """

    value: float | int | None
    unit: str | None
    status: str
    source_url: str | None = None
    retrieved_date: str | None = None
    notes: str | None = None

    @classmethod
    def from_mapping(cls, mapping: dict[str, Any]) -> "Quantity":
        """Build a quantity from a YAML-shaped mapping.

        Parameters
        ----------
        mapping : dict
            A mapping with at least ``status``; ``value``, ``unit``,
            ``source_url``, ``retrieved_date`` and ``notes`` are optional.

        Returns
        -------
        Quantity
            The parsed quantity. Missing optional keys become ``None``.
        """
        # Read each field defensively so a partial mapping still parses; the
        # validator, not the parser, decides whether a missing field is a fault.
        return cls(
            value=mapping.get("value"),
            unit=mapping.get("unit"),
            status=str(mapping.get("status", "")),
            source_url=mapping.get("source_url"),
            retrieved_date=mapping.get("retrieved_date"),
            notes=mapping.get("notes"),
        )

    def to_mapping(self) -> dict[str, Any]:
        """Serialise back to a YAML-shaped mapping, dropping empty fields.

        Returns
        -------
        dict
            A mapping carrying only the fields that are set, so round-tripping a
            minimal quantity does not sprout empty keys.
        """
        # Always include value and status (the load-bearing pair); include the
        # rest only when present so serialised models stay clean.
        mapping: dict[str, Any] = {"value": self.value, "status": self.status}
        if self.unit is not None:
            mapping["unit"] = self.unit
        if self.source_url is not None:
            mapping["source_url"] = self.source_url
        if self.retrieved_date is not None:
            mapping["retrieved_date"] = self.retrieved_date
        if self.notes is not None:
            mapping["notes"] = self.notes
        return mapping

    def is_status_valid(self) -> bool:
        """Return whether this quantity's status is an allowed label.

        Returns
        -------
        bool
            ``True`` when :attr:`status` is one of the four honesty labels.
        """
        return is_valid_status(self.status)
