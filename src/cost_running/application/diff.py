"""
Diff two cost models and report dimensional drift.

Module summary
--------------
The diff use case compares two versions of a cost model — typically the current
model against the one committed before the last change — and reports the relative
change in every numeric quantity. A drift gate (default 10%) turns this into a
CI check: if any dimension changed by more than the threshold, the gate fails and
the author must either update the model or explicitly acknowledge the change.

The diff operates on the *reported values* in the model, not on assumptions or
deployment metadata. It walks the scenarios and their sub-fields, computes the
relative change for each numeric quantity, and returns a structured result. The
caller decides how to present it (plain text, JSON, or a CI exit code).

Author
------
Project maintainers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .validate import normalize_scenarios


@dataclass(frozen=True, slots=True)
class DriftItem:
    """One diffed numeric quantity and its relative change.

    Parameters
    ----------
    path : str
        Dot-separated path to the quantity in the model (e.g.
        ``default.local_compute.energy_kwh``).
    old_value : float
        Value in the old model.
    new_value : float
        Value in the new model.
    relative_change : float
        ``(new - old) / old`` when ``old != 0``; ``inf`` when the old value
        was zero and the new value is not.
    old_status : str or None
        Honesty status in the old model.
    new_status : str or None
        Honesty status in the new model.
    """

    path: str
    old_value: float
    new_value: float
    relative_change: float
    old_status: str | None
    new_status: str | None


@dataclass(slots=True)
class DiffResult:
    """The full diff between two cost models.

    Parameters
    ----------
    items : list of DriftItem
        All quantities that appeared in both models with numeric values.
    only_in_old : list of str
        Paths present in the old model but missing from the new one.
    only_in_new : list of str
        Paths present in the new model but missing from the old one.
    """

    items: list[DriftItem] = field(default_factory=list)
    only_in_old: list[str] = field(default_factory=list)
    only_in_new: list[str] = field(default_factory=list)

    def above_threshold(self, threshold: float = 0.10) -> list[DriftItem]:
        """Return items whose absolute relative change exceeds ``threshold``.

        Parameters
        ----------
        threshold : float
            Fractional threshold, e.g. ``0.10`` for 10 percent.

        Returns
        -------
        list of DriftItem
            Items where ``abs(relative_change) > threshold``.
        """
        return [item for item in self.items if abs(item.relative_change) > threshold]

    def is_within_threshold(self, threshold: float = 0.10) -> bool:
        """Return whether all quantities are within the drift threshold.

        Parameters
        ----------
        threshold : float
            Fractional threshold passed to :meth:`above_threshold`.

        Returns
        -------
        bool
            ``True`` when no quantity drifted beyond the threshold.
        """
        return not self.above_threshold(threshold)

    def report(self, threshold: float = 0.10) -> dict[str, Any]:
        """Return a JSON-serialisable summary of the diff.

        Parameters
        ----------
        threshold : float
            The drift gate threshold.

        Returns
        -------
        dict
            Summary suitable for ``json.dumps``.
        """
        drifts = self.above_threshold(threshold)
        return {
            "within_threshold": not drifts,
            "threshold": threshold,
            "drifts_above_threshold": [
                {
                    "path": d.path,
                    "old_value": d.old_value,
                    "new_value": d.new_value,
                    "relative_change_pct": round(d.relative_change * 100, 2),
                    "old_status": d.old_status,
                    "new_status": d.new_status,
                }
                for d in drifts
            ],
            "all_changes": [
                {
                    "path": d.path,
                    "old_value": d.old_value,
                    "new_value": d.new_value,
                    "relative_change_pct": round(d.relative_change * 100, 2),
                }
                for d in self.items
            ],
            "only_in_old": self.only_in_old,
            "only_in_new": self.only_in_new,
        }


def _collect_numeric_quantities(
    data: dict[str, Any],
) -> dict[str, tuple[float, str | None]]:
    """Return a flat dict of path → (value, status) for all numeric quantities.

    Only walks scenarios and their sub-mappings, since those are the runtime
    cost figures the diff gate monitors. Assumptions and deployment are static
    context and are not diffed.

    Parameters
    ----------
    data : dict
        The cost model.

    Returns
    -------
    dict
        Mapping from dotted path to (numeric value, status or None).
    """
    result: dict[str, tuple[float, str | None]] = {}

    def walk(node: Any, path: str) -> None:
        if isinstance(node, dict):
            value = node.get("value")
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                status = node.get("status")
                result[path] = (float(value), str(status) if status is not None else None)
                return
            for key, child in node.items():
                walk(child, f"{path}.{key}" if path else str(key))
        elif isinstance(node, list):
            for index, child in enumerate(node):
                walk(child, f"{path}[{index}]")

    for scenario in normalize_scenarios(data):
        label = scenario.get("name") or "scenario"
        for key, child in scenario.items():
            if key == "name":
                continue
            walk(child, f"{label}.{key}")

    return result


def diff_models(
    old: dict[str, Any],
    new: dict[str, Any],
) -> DiffResult:
    """Compare two cost models and report the drift in every numeric quantity.

    Parameters
    ----------
    old : dict
        The baseline cost model (e.g. the committed version).
    new : dict
        The updated cost model (e.g. the working-tree version).

    Returns
    -------
    DiffResult
        All drifts, including which quantities appeared in only one model.

    Examples
    --------
    >>> old = {"scenario": {"name": "default",
    ...                     "runtime_seconds": {"value": 1.0, "status": "measured"}}}
    >>> new = {"scenario": {"name": "default",
    ...                     "runtime_seconds": {"value": 1.2, "status": "measured"}}}
    >>> result = diff_models(old, new)
    >>> result.items[0].relative_change
    0.2
    """
    old_quantities = _collect_numeric_quantities(old)
    new_quantities = _collect_numeric_quantities(new)

    old_paths = set(old_quantities)
    new_paths = set(new_quantities)
    common = old_paths & new_paths

    items: list[DriftItem] = []
    for path in sorted(common):
        old_value, old_status = old_quantities[path]
        new_value, new_status = new_quantities[path]
        if old_value == 0:
            relative_change = 0.0 if new_value == 0 else float("inf")
        else:
            relative_change = (new_value - old_value) / old_value
        items.append(
            DriftItem(
                path=path,
                old_value=old_value,
                new_value=new_value,
                relative_change=relative_change,
                old_status=old_status,
                new_status=new_status,
            )
        )

    return DiffResult(
        items=items,
        only_in_old=sorted(old_paths - new_paths),
        only_in_new=sorted(new_paths - old_paths),
    )
