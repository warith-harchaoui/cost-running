"""
The hardware and service catalog: our own growing, provenance-carrying data.

Module summary
--------------
Device power and throughput, and the list of paid services and where to price
them, are data, not code. This module loads that data from the bundled catalog
(``data/hardware.yaml``, ``data/services.yaml``), merges an optional writable
overlay on top, and exposes it as the tables the estimator needs. It also owns
the growth path: :func:`add_hardware` and :func:`add_service` append a new row to
the overlay, and they refuse any entry without a source URL and a retrieval date,
because the catalog is only worth trusting if every row says where it came from.

At runtime nothing here touches the network. The one-time web search that finds a
new datasheet or pricing page happens at ``add`` time, outside this module (the
agent does it); once a row is stored, reads are offline and deterministic.

Author
------
Project maintainers.
"""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path
from typing import Any

import yaml

# The bundled catalog ships inside the package and is read-only once installed.
_BUNDLED_DIR: Path = Path(__file__).resolve().parent.parent / "data"

# Rows older than this many days are flagged stale, measured from their
# ``retrieved_date``. The default is one month, because the fast-moving data
# (prices, grid carbon intensities, provider and instance offerings) is what the
# tool leans on most and it drifts within weeks.
STALE_DAYS: int = 30

# Some data ages far more slowly than a price. A GPU's thermal design power and
# peak throughput are physical facts of the silicon; once sourced they need
# re-confirming yearly, not monthly. Categories absent here fall back to the
# monthly :data:`STALE_DAYS`. Keys match the catalog's ``device_kind`` / row
# category (``gpus``, ``cpus``, ``services``, ``country``, ``provider``,
# ``instance``).
STALE_DAYS_BY_KIND: dict[str, int] = {
    "gpus": 365,
    "cpus": 365,
    "services": 30,
    "country": 30,
    "provider": 30,
    "instance": 30,
}

# Provenance is mandatory on every catalog row. These are the keys `add` requires.
_PROVENANCE_KEYS: tuple[str, str] = ("source_url", "retrieved_date")


def overlay_dir() -> Path:
    """Return the writable overlay directory for catalog additions.

    Resolution order: the ``COST_RUNNING_REGISTRY_DIR`` environment variable, so a
    checkout can point it at the repository's ``data/`` to grow the shared
    catalog; otherwise a per-user config directory.

    Returns
    -------
    pathlib.Path
        The directory `add` writes overlay YAML into. Not guaranteed to exist yet.
    """
    env = os.environ.get("COST_RUNNING_REGISTRY_DIR")
    if env:
        return Path(env)
    # Fall back to a per-user location so an installed package can still grow.
    from platformdirs import user_config_dir

    return Path(user_config_dir("cost-running")) / "registry"


def _load_yaml(path: Path) -> dict[str, Any]:
    """Load a YAML file into a mapping, returning ``{}`` when it is absent.

    Parameters
    ----------
    path : pathlib.Path
        The file to load.

    Returns
    -------
    dict
        The parsed mapping, or an empty mapping when the file does not exist.
    """
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def _merge_rows(bundled: list[dict], overlay: list[dict]) -> dict[str, dict]:
    """Merge two lists of ``key``-bearing rows, overlay winning on a shared key.

    Parameters
    ----------
    bundled : list of dict
        Rows from the bundled catalog.
    overlay : list of dict
        Rows from the writable overlay; these extend or override by ``key``.

    Returns
    -------
    dict
        Mapping of ``key`` to row, overlay entries taking precedence.
    """
    merged: dict[str, dict] = {}
    # Bundled first, then overlay so a later addition replaces or extends it.
    for row in [*bundled, *overlay]:
        key = row.get("key")
        if key:
            merged[key] = row
    return merged


def hardware_catalog(overlay: Path | None = None) -> dict[str, dict[str, dict]]:
    """Return the merged hardware catalog, GPUs and CPUs keyed by device key.

    Parameters
    ----------
    overlay : pathlib.Path or None, optional
        Overlay directory. Defaults to :func:`overlay_dir`.

    Returns
    -------
    dict
        ``{"gpus": {key: row}, "cpus": {key: row}}`` with provenance preserved.
    """
    overlay = overlay if overlay is not None else overlay_dir()
    bundled = _load_yaml(_BUNDLED_DIR / "hardware.yaml")
    extra = _load_yaml(overlay / "hardware.yaml")
    return {
        "gpus": _merge_rows(bundled.get("gpus", []), extra.get("gpus", [])),
        "cpus": _merge_rows(bundled.get("cpus", []), extra.get("cpus", [])),
    }


def service_catalog(overlay: Path | None = None) -> dict[str, dict]:
    """Return the merged service catalog keyed by service key.

    Parameters
    ----------
    overlay : pathlib.Path or None, optional
        Overlay directory. Defaults to :func:`overlay_dir`.

    Returns
    -------
    dict
        Mapping of service key to its row (name, detect hints, pricing URL, ...).
    """
    overlay = overlay if overlay is not None else overlay_dir()
    bundled = _load_yaml(_BUNDLED_DIR / "services.yaml")
    extra = _load_yaml(overlay / "services.yaml")
    return _merge_rows(bundled.get("services", []), extra.get("services", []))


def gpu_tdp_w(overlay: Path | None = None) -> dict[str, float]:
    """Return the GPU whole-device TDP table (watts) from the catalog."""
    # Only rows that actually carry a TDP contribute to the power table.
    return {
        key: row["tdp_w"]
        for key, row in hardware_catalog(overlay)["gpus"].items()
        if "tdp_w" in row
    }


def gpu_peak_bf16_tflops(overlay: Path | None = None) -> dict[str, float]:
    """Return the GPU dense-BF16 peak-throughput table from the catalog."""
    return {
        key: row["peak_bf16_tflops"]
        for key, row in hardware_catalog(overlay)["gpus"].items()
        if "peak_bf16_tflops" in row
    }


def cpu_w_per_core(overlay: Path | None = None) -> dict[str, float]:
    """Return the CPU per-core power table (watts per core) from the catalog."""
    return {
        key: row["w_per_core"]
        for key, row in hardware_catalog(overlay)["cpus"].items()
        if "w_per_core" in row
    }


def days_since(retrieved_date: str) -> int | None:
    """Return the age in days of a ``YYYY-MM-DD`` retrieval date, or ``None``."""
    try:
        then = date.fromisoformat(str(retrieved_date))
    except ValueError:
        return None
    return (date.today() - then).days


def stale_threshold_days(kind: str | None = None) -> int:
    """Return the staleness threshold in days for a catalog category.

    Parameters
    ----------
    kind : str or None, optional
        The row category (``gpus``, ``cpus``, ``services``, ``country``,
        ``provider``, ``instance``). ``None`` or an unknown kind uses the monthly
        default :data:`STALE_DAYS`.

    Returns
    -------
    int
        The number of days after which a row of this kind is considered stale.

    Examples
    --------
    >>> stale_threshold_days("gpus")
    365
    >>> stale_threshold_days("services")
    30
    >>> stale_threshold_days(None)
    30
    """
    if kind is None:
        return STALE_DAYS
    return STALE_DAYS_BY_KIND.get(kind, STALE_DAYS)


def is_stale(row: dict, kind: str | None = None) -> bool:
    """Return whether a catalog row's provenance is older than its threshold.

    Parameters
    ----------
    row : dict
        A catalog row that should carry ``retrieved_date``.
    kind : str or None, optional
        The row's category, so the right threshold applies: a monthly window for
        prices and grid data, a yearly one for physical hardware specs. ``None``
        uses the monthly default, which is the safe (more eager) choice.

    Returns
    -------
    bool
        ``True`` when the row has no retrieval date or it is older than the
        staleness threshold, so a caller can nudge a refresh.
    """
    age = days_since(row.get("retrieved_date", ""))
    # A row with no date is treated as stale: unverifiable provenance is not fresh.
    return age is None or age > stale_threshold_days(kind)


def stale_rows(overlay: Path | None = None) -> dict[str, list[str]]:
    """Return the keys of every stale row in the catalog, grouped by category.

    This is the freshness scan behind ``cost-running catalog freshness``: it
    reports which stored values a month (or, for hardware, a year) has passed
    since they were sourced, so a maintainer knows exactly what to re-confirm.
    It never fetches anything or invents a fresher value; deciding a row is stale
    is pure date arithmetic, and refreshing it is a separate, sourced step.

    Parameters
    ----------
    overlay : pathlib.Path or None, optional
        Overlay directory. Defaults to :func:`overlay_dir`.

    Returns
    -------
    dict
        ``{category: [stale_key, ...]}`` for ``gpus``, ``cpus`` and ``services``.
        Categories with nothing stale still appear, mapped to an empty list, so
        the caller can report "all fresh" per category without a second lookup.
    """
    hw = hardware_catalog(overlay)
    services = service_catalog(overlay)
    return {
        "gpus": sorted(k for k, row in hw["gpus"].items() if is_stale(row, "gpus")),
        "cpus": sorted(k for k, row in hw["cpus"].items() if is_stale(row, "cpus")),
        "services": sorted(k for k, row in services.items() if is_stale(row, "services")),
    }


def _require_provenance(entry: dict) -> None:
    """Raise unless an entry carries both a source URL and a retrieval date.

    Parameters
    ----------
    entry : dict
        The row about to be added.

    Raises
    ------
    ValueError
        If either provenance key is missing or empty. This is the guard that
        keeps the catalog honest: no row enters without saying where it came from.
    """
    for key in _PROVENANCE_KEYS:
        if not entry.get(key):
            raise ValueError(
                f"A catalog entry needs {key!r}; the catalog only stores sourced values."
            )


def _append_row(path: Path, top_key: str, entry: dict) -> None:
    """Append a row under ``top_key`` in a YAML catalog file, creating it if new.

    Parameters
    ----------
    path : pathlib.Path
        The overlay YAML file to write.
    top_key : str
        The list key to append under (``gpus``, ``cpus``, or ``services``).
    entry : dict
        The row to append.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    data = _load_yaml(path)
    # Preserve any existing rows and add ours; keep the file a simple list-of-rows.
    rows = data.get(top_key, [])
    rows.append(entry)
    data[top_key] = rows
    with open(path, "w", encoding="utf-8") as handle:
        yaml.dump(data, handle, sort_keys=False, allow_unicode=True)


def add_hardware(
    device_kind: str,
    entry: dict,
    *,
    overlay: Path | None = None,
) -> Path:
    """Add a hardware row (GPU or CPU) to the writable overlay catalog.

    Parameters
    ----------
    device_kind : str
        Either ``"gpus"`` or ``"cpus"``.
    entry : dict
        The row, which must carry ``key`` and provenance (``source_url`` and
        ``retrieved_date``), plus the relevant figures (``tdp_w`` and optionally
        ``peak_bf16_tflops`` for a GPU, ``w_per_core`` for a CPU).
    overlay : pathlib.Path or None, optional
        Overlay directory to write into. Defaults to :func:`overlay_dir`.

    Returns
    -------
    pathlib.Path
        The file the row was written to.

    Raises
    ------
    ValueError
        If ``device_kind`` is unknown, ``key`` is missing, or provenance is absent.
    """
    if device_kind not in {"gpus", "cpus"}:
        raise ValueError("device_kind must be 'gpus' or 'cpus'.")
    if not entry.get("key"):
        raise ValueError("A hardware entry needs a 'key'.")
    # Provenance is non-negotiable, exactly as it is for a value in a cost model.
    _require_provenance(entry)
    overlay = overlay if overlay is not None else overlay_dir()
    target = overlay / "hardware.yaml"
    _append_row(target, device_kind, entry)
    return target


def add_service(entry: dict, *, overlay: Path | None = None) -> Path:
    """Add a service row to the writable overlay catalog.

    Parameters
    ----------
    entry : dict
        The row, which must carry ``key`` and provenance. For a service the
        provenance is the ``pricing_source_url`` (aliased to ``source_url`` here)
        and the ``retrieved_date`` of the price that was read.
    overlay : pathlib.Path or None, optional
        Overlay directory. Defaults to :func:`overlay_dir`.

    Returns
    -------
    pathlib.Path
        The file the row was written to.

    Raises
    ------
    ValueError
        If ``key`` is missing or provenance is absent.
    """
    if not entry.get("key"):
        raise ValueError("A service entry needs a 'key'.")
    # A service row's source is its pricing page; accept either spelling.
    if entry.get("source_url") is None and entry.get("pricing_source_url"):
        entry = {**entry, "source_url": entry["pricing_source_url"]}
    _require_provenance(entry)
    overlay = overlay if overlay is not None else overlay_dir()
    target = overlay / "services.yaml"
    _append_row(target, "services", entry)
    return target
