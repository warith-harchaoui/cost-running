"""
Accessors for the bundled cost-model templates.

Module summary
--------------
The package ships two starter models in ``cost_running/templates/``: ``min.yaml``
(a lean scaffold of required fields) and ``full.yaml`` (an annotated worked
example). The ``init`` use case and any surface that offers a starter reads them
through here, so the file locations live in one place.

Author
------
Project maintainers.
"""

from __future__ import annotations

from pathlib import Path

# The directory the template YAML files live in, resolved relative to this file
# so it works whether the package is installed or run from a checkout.
_TEMPLATES_DIR: Path = Path(__file__).resolve().parent / "templates"

# The known templates, keyed by the short name a user passes on the command line.
_TEMPLATES: dict[str, Path] = {
    "min": _TEMPLATES_DIR / "min.yaml",
    "full": _TEMPLATES_DIR / "full.yaml",
}


def get_template_text(name: str) -> str:
    """Return the UTF-8 text of a bundled template.

    Parameters
    ----------
    name : str
        Template identifier, ``"min"`` or ``"full"``.

    Returns
    -------
    str
        The template file's contents.

    Raises
    ------
    ValueError
        If ``name`` is not a known template.

    Examples
    --------
    >>> "canonical_unit_of_work" in get_template_text("min")
    True
    """
    try:
        path = _TEMPLATES[name]
    except KeyError as exc:
        # List the known names so the user can correct the typo immediately.
        known = ", ".join(sorted(_TEMPLATES))
        raise ValueError(f"Unknown template {name!r}. Known templates: {known}.") from exc
    return path.read_text(encoding="utf-8")
