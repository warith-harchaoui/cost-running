"""
Reading and writing cost models on disk.

Module summary
--------------
A cost model lives as a YAML file so it is diffable and reviewable in a pull
request. This module is the only place that touches YAML text, keeping the parse
and dump conventions (safe loading, insertion-order preservation, UTF-8) in one
spot. It stays thin on purpose: no validation, no rendering, just bytes to a
mapping and back.

Author
------
Project maintainers.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


def load_model(path: str | Path) -> dict[str, Any]:
    r"""Load a cost model from a YAML file into a mapping.

    Parameters
    ----------
    path : str or pathlib.Path
        Path to the YAML cost model.

    Returns
    -------
    dict
        The parsed model. An empty file yields an empty mapping.

    Raises
    ------
    ValueError
        If the top-level YAML node is not a mapping, which no valid cost model
        ever is.

    Examples
    --------
    >>> import tempfile, pathlib
    >>> p = pathlib.Path(tempfile.mkdtemp()) / "m.yaml"
    >>> _ = p.write_text("schema_version: '1.0'\\n")
    >>> load_model(p)["schema_version"]
    '1.0'
    """
    # safe_load refuses arbitrary Python object construction, so an untrusted
    # model file cannot execute code on load.
    with open(path, "r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    # A cost model is always a mapping at the top level; anything else is a
    # malformed file and should fail loudly rather than confuse the validator.
    if not isinstance(data, dict):
        raise ValueError("Top-level YAML of a cost model must be a mapping.")
    return data


def dump_model_yaml(model: dict[str, Any]) -> str:
    """Serialise a cost-model mapping to YAML text.

    Parameters
    ----------
    model : dict
        The cost model to serialise.

    Returns
    -------
    str
        YAML text with keys in insertion order (not alphabetised), so a
        generated model reads top-down the way a human wrote it.
    """
    # sort_keys=False keeps schema_version and date_updated at the top where a
    # reader expects them; allow_unicode keeps accented source names intact.
    return yaml.dump(model, sort_keys=False, allow_unicode=True, default_flow_style=False)


def write_text(path: str | Path, content: str) -> None:
    """Write UTF-8 text to a file, creating parent directories as needed.

    Parameters
    ----------
    path : str or pathlib.Path
        Destination path.
    content : str
        Text to write.
    """
    target = Path(path)
    # Create the parent directory so callers can write into a fresh output tree
    # without a separate mkdir step.
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, "w", encoding="utf-8") as handle:
        handle.write(content)
