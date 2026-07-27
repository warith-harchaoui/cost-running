"""
Prepare a catalog contribution: the row, its evidence, and the pull-request body.

Module summary
--------------
When an audit meets a device or service the catalog does not know, the fix is not
only to add it locally but to offer it back so the next person does not have to
rediscover it. This module builds that offer deterministically: it validates the
new row's provenance, inserts it into the catalog file without disturbing the
comments already there, names a branch, and renders a pull-request body that
carries both the sourced row and the snippet of code that triggered the
detection. It performs no git or network action; the side-effecting executor does
that, so everything here is pure and testable.

Author
------
Project maintainers.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

import yaml

# The catalog file and its list section for each kind of contribution.
_KIND_TO_FILE: dict[str, str] = {
    "gpus": "hardware.yaml",
    "cpus": "hardware.yaml",
    "services": "services.yaml",
}


@dataclass(frozen=True, slots=True)
class EvidenceSnippet:
    """A bit of the audited code that motivated a catalog addition.

    Parameters
    ----------
    path : str
        Repository-relative path the snippet came from.
    text : str
        The relevant lines (an import, a client construction, a call).
    note : str or None
        Optional one-line explanation of what the snippet shows.
    """

    path: str
    text: str
    note: str | None = None


@dataclass(frozen=True, slots=True)
class Contribution:
    """A proposed addition to the community catalog.

    Parameters
    ----------
    kind : str
        ``gpus``, ``cpus``, or ``services``.
    row : dict
        The provenance-carrying catalog row to add.
    rationale : str
        One line on why this belongs in the catalog.
    evidence : list of EvidenceSnippet
        Code snippets that show the detection was real.
    """

    kind: str
    row: dict[str, Any]
    rationale: str
    evidence: list[EvidenceSnippet] = field(default_factory=list)


def catalog_file_for(kind: str) -> str:
    """Return the catalog file name a kind is written to.

    Parameters
    ----------
    kind : str
        ``gpus``, ``cpus``, or ``services``.

    Returns
    -------
    str
        ``hardware.yaml`` or ``services.yaml``.

    Raises
    ------
    ValueError
        If ``kind`` is not a known catalog section.
    """
    if kind not in _KIND_TO_FILE:
        raise ValueError(f"Unknown catalog kind {kind!r}; expected one of {sorted(_KIND_TO_FILE)}.")
    return _KIND_TO_FILE[kind]


def build_contribution(
    kind: str,
    row: dict[str, Any],
    *,
    rationale: str,
    evidence: list[EvidenceSnippet] | None = None,
) -> Contribution:
    """Validate and assemble a catalog contribution.

    Parameters
    ----------
    kind : str
        ``gpus``, ``cpus``, or ``services``.
    row : dict
        The catalog row. Must carry ``key`` and provenance: ``source_url`` (or, for
        a service, ``pricing_source_url``) and ``retrieved_date``.
    rationale : str
        Why the row belongs in the catalog.
    evidence : list of EvidenceSnippet or None, optional
        Supporting code snippets.

    Returns
    -------
    Contribution
        The validated contribution.

    Raises
    ------
    ValueError
        If the kind is unknown, the key is missing, or provenance is absent.
    """
    catalog_file_for(kind)  # validates the kind
    if not row.get("key"):
        raise ValueError("A catalog row needs a 'key'.")
    # Provenance is mandatory, exactly as for the local `add`. A service's source
    # is its pricing page; accept either spelling so the caller need not translate.
    source = row.get("source_url") or row.get("pricing_source_url")
    if not source or not row.get("retrieved_date"):
        raise ValueError("A contribution needs a source URL and a retrieved_date.")
    return Contribution(kind=kind, row=row, rationale=rationale, evidence=list(evidence or []))


def branch_name(contribution: Contribution) -> str:
    """Return a git branch name for a contribution.

    Parameters
    ----------
    contribution : Contribution
        The contribution.

    Returns
    -------
    str
        A stable, filesystem-safe branch name such as ``catalog/add-gpus-h20``.
    """
    # Lower-case the key and strip anything that is not safe in a ref name.
    key = re.sub(r"[^a-z0-9._-]+", "-", str(contribution.row["key"]).lower()).strip("-")
    return f"catalog/add-{contribution.kind}-{key}"


def insert_row_into_catalog(text: str, section: str, row: dict[str, Any]) -> str:
    """Insert a row at the end of a catalog section, preserving the file.

    Rather than re-dump the whole file (which would drop its comments and
    ordering), this finds the section and splices the new row in as text.

    Parameters
    ----------
    text : str
        The current catalog file contents.
    section : str
        The top-level list key to append under (``gpus``, ``cpus``, ``services``).
    row : dict
        The row to insert.

    Returns
    -------
    str
        The file contents with the row added under ``section``.
    """
    # Serialise the row as a one-item list, then indent it two spaces to sit under
    # the section key the way the catalog files are formatted.
    block = yaml.dump([row], sort_keys=False, allow_unicode=True)
    block = "".join(
        ("  " + line if line.strip() else line) for line in block.splitlines(keepends=True)
    )
    if not block.endswith("\n"):
        block += "\n"

    lines = text.splitlines(keepends=True)
    # Locate the section header (a top-level `section:` line).
    header_idx = next(
        (i for i, line in enumerate(lines) if re.match(rf"^{re.escape(section)}\s*:", line)),
        None,
    )
    if header_idx is None:
        # The section is absent; append a fresh one at the end of the file.
        return text.rstrip("\n") + f"\n\n{section}:\n{block}"

    # The section ends at the next top-level line (unindented, not blank, not a
    # comment), or the end of the file.
    end_idx = len(lines)
    for j in range(header_idx + 1, len(lines)):
        line = lines[j]
        if line.strip() and not line.startswith((" ", "\t", "#")):
            end_idx = j
            break
    return "".join(lines[:end_idx] + [block] + lines[end_idx:])


def render_pr_body(contribution: Contribution) -> str:
    """Render the pull-request body for a contribution.

    Parameters
    ----------
    contribution : Contribution
        The contribution.

    Returns
    -------
    str
        A Markdown body carrying the rationale, the sourced row, its provenance,
        and the code evidence, so a reviewer sees what is added and why.
    """
    row = contribution.row
    source = row.get("source_url") or row.get("pricing_source_url")
    lines = [
        f"## Catalog addition: `{row['key']}` ({contribution.kind})",
        "",
        contribution.rationale,
        "",
        "### Provenance",
        "",
        f"- Source: {source}",
        f"- Retrieved: {row.get('retrieved_date')}",
        "",
        "### Row",
        "",
        "```yaml",
        yaml.dump([row], sort_keys=False, allow_unicode=True).rstrip("\n"),
        "```",
    ]
    # Include the code that triggered the detection so the reviewer can verify it.
    if contribution.evidence:
        lines += ["", "### Evidence (code that triggered the detection)", ""]
        for snippet in contribution.evidence:
            if snippet.note:
                lines.append(f"_{snippet.note}_")
            lines += [f"`{snippet.path}`:", "", "```", snippet.text.rstrip("\n"), "```", ""]
    return "\n".join(lines).rstrip("\n") + "\n"
