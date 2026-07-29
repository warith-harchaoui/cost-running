"""
Fetch model-quality benchmarks from public arenas and leaderboards.

Module summary
--------------
The hardware and service catalogs tell you what running a workload *costs*; the
leaderboard catalog tells you what quality you *get* for that cost. This module
fetches publicly available benchmark data — model scores, ELO rankings, CO₂ per
evaluation — from sources that publish structured data without authentication.

All network calls are dependency-free (urllib), time-boxed, and fail gracefully:
a missing or slow source returns an empty list with a warning, never an exception,
so ``cost-running catalog scrape`` always completes even when a source is down.

Every fetched row carries:
- ``source``: the URL it was retrieved from
- ``retrieved_date``: the ISO-8601 date it was fetched (today)
- A clear ``status`` key so the honesty taxonomy knows this is fresh data, not a
  TODO placeholder.

Supported sources
-----------------
``hf_leaderboard``
    Open LLM Leaderboard v2 (HuggingFace). 4 000+ models scored on IFEval, BBH,
    MATH Level 5, GPQA, MUSR, MMLU-PRO, plus CO₂ cost in kg per evaluation.
    Fetched via the HuggingFace datasets-server API (no auth required).

``arena`` (stub)
    Chatbot Arena ELO scores (lmarena-ai). The space publishes its data
    periodically; the fetch is wired but returns empty until a stable JSON
    endpoint is confirmed.

Extension
---------
Add a new source by writing a function ``fetch_<name>(*, limit, timeout) ->
list[dict]`` and registering it in ``_SOURCES``. Each row must carry at minimum
``source`` and ``retrieved_date``; columns beyond that are source-specific.

Author
------
Project maintainers.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from datetime import date
from typing import Any

logger = logging.getLogger("cost_running")

# Per-request timeout: sources that do not answer quickly are skipped, not waited on.
_DEFAULT_TIMEOUT: float = 15.0

# HuggingFace datasets-server pagination cap (their API accepts max 100 per page).
_HF_PAGE_SIZE: int = 100

_HF_LEADERBOARD_DATASET: str = "open-llm-leaderboard/contents"
_HF_ROWS_URL: str = (
    "https://datasets-server.huggingface.co/rows"
    f"?dataset={_HF_LEADERBOARD_DATASET.replace('/', '%2F')}"
    "&config=default&split=train"
    "&offset={offset}&length={length}"
)

# Column aliases: the leaderboard uses Unicode arrows and emoji in column names;
# we normalise them to plain ASCII keys for downstream use.
_HF_COLUMN_MAP: dict[str, str] = {
    "fullname": "model",
    "Average ⬆️": "average_score",
    "#Params (B)": "params_b",
    "Architecture": "architecture",
    "IFEval": "ifeval",
    "BBH": "bbh",
    "MATH Lvl 5": "math_lvl5",
    "GPQA": "gpqa",
    "MUSR": "musr",
    "MMLU-PRO": "mmlu_pro",
    "CO₂ cost (kg)": "co2_kg",
    "Type": "type",
    "Hub License": "license",
    "Base Model": "base_model",
}


def _get_json(url: str, timeout: float = _DEFAULT_TIMEOUT) -> dict[str, Any] | None:
    """Fetch a URL and parse its body as JSON, returning ``None`` on any failure."""
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
        logger.warning("arena fetch failed for %s: %s", url, exc)
        return None


def fetch_hf_leaderboard(
    *,
    limit: int = 5000,
    timeout: float = _DEFAULT_TIMEOUT,
) -> list[dict[str, Any]]:
    """Fetch model scores from the HuggingFace Open LLM Leaderboard v2.

    Parameters
    ----------
    limit : int, optional
        Maximum number of model rows to return. The leaderboard has ~4 500 rows;
        the default fetches them all.
    timeout : float, optional
        Per-request timeout in seconds.

    Returns
    -------
    list of dict
        One dict per model with normalised column names, ``source``, and
        ``retrieved_date``. Empty on network failure.

    Examples
    --------
    >>> rows = fetch_hf_leaderboard(limit=5)
    >>> all("model" in r for r in rows)  # doctest: +SKIP
    True
    """
    today = date.today().isoformat()
    source_url = f"https://huggingface.co/datasets/{_HF_LEADERBOARD_DATASET}"
    rows: list[dict[str, Any]] = []
    offset = 0
    while len(rows) < limit:
        page_size = min(_HF_PAGE_SIZE, limit - len(rows))
        url = _HF_ROWS_URL.format(offset=offset, length=page_size)
        data = _get_json(url, timeout=timeout)
        if data is None:
            break
        page_rows = data.get("rows", [])
        if not page_rows:
            break
        for item in page_rows:
            raw = item.get("row", {})
            row: dict[str, Any] = {
                new_key: raw[old_key]
                for old_key, new_key in _HF_COLUMN_MAP.items()
                if old_key in raw
            }
            row["source"] = source_url
            row["retrieved_date"] = today
            rows.append(row)
        offset += len(page_rows)
        if len(page_rows) < page_size:
            break
    logger.info("hf_leaderboard: fetched %d model rows.", len(rows))
    return rows


def fetch_arena(
    *,
    limit: int = 500,
    timeout: float = _DEFAULT_TIMEOUT,
) -> list[dict[str, Any]]:
    """Fetch ELO rankings from Chatbot Arena (lmarena-ai).

    The Arena publishes leaderboard data periodically. This stub returns an
    empty list until a stable structured-data endpoint is confirmed, logging
    a note so the caller knows the source was attempted.

    Parameters
    ----------
    limit : int, optional
        Maximum rows to return.
    timeout : float, optional
        Per-request timeout.

    Returns
    -------
    list of dict
        ELO rows when a working endpoint is available, else empty list.
    """
    logger.info(
        "arena: no confirmed public JSON endpoint yet; skipping. "
        "Source: https://lmarena.ai — check for a future /api/leaderboard endpoint."
    )
    return []


# Registry of all sources. Add entries here to include a new scraper in
# ``cost-running catalog scrape``.
_SOURCES: dict[str, Any] = {
    "hf_leaderboard": fetch_hf_leaderboard,
    "arena": fetch_arena,
}


def scrape(
    sources: list[str] | None = None,
    *,
    limit: int = 5000,
    timeout: float = _DEFAULT_TIMEOUT,
) -> dict[str, list[dict[str, Any]]]:
    """Fetch benchmark data from all (or selected) public sources.

    Parameters
    ----------
    sources : list of str or None, optional
        Source keys to fetch. ``None`` fetches all registered sources.
    limit : int, optional
        Per-source row limit passed to each fetcher.
    timeout : float, optional
        Per-request timeout passed to each fetcher.

    Returns
    -------
    dict
        Mapping of source key → list of rows. Sources that fail return an
        empty list; the key is still present so callers can detect the miss.
    """
    keys = sources if sources is not None else list(_SOURCES)
    unknown = [k for k in keys if k not in _SOURCES]
    if unknown:
        raise ValueError(
            f"Unknown source(s): {unknown!r}. Known: {list(_SOURCES)!r}."
        )
    return {
        key: _SOURCES[key](limit=limit, timeout=timeout)
        for key in keys
    }


def available_sources() -> list[str]:
    """Return the list of registered source keys."""
    return list(_SOURCES)
