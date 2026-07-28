"""
Clone a public GitHub repository for local analysis.

Module summary
--------------
Provides a thin wrapper around ``git clone --depth 1`` that accepts a GitHub
URL in any common form (HTTPS URL, ``github.com/owner/repo``, or bare
``owner/repo`` slug) and returns the local path.  A context-manager form
handles cleanup automatically so callers do not have to manage temporary
directories.  Nothing here depends on the GitHub API or a token; only public
read access via HTTPS is required.

Author
------
Project maintainers.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Generator

# Patterns accepted as GitHub references.
# 1. Full HTTPS URL: https://github.com/owner/repo[.git][/]
# 2. No-scheme URL:  github.com/owner/repo[.git][/]
# 3. Bare slug:      owner/repo  (no dots or slashes in owner or repo name)
_FULL_URL_RE = re.compile(
    r"(?:https?://)?github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?/?$"
)
_SLUG_RE = re.compile(r"^([A-Za-z0-9][\w.-]*)/([A-Za-z0-9][\w.-]*)$")


def parse_github_ref(ref: str) -> tuple[str, str]:
    """Return ``(owner, repo)`` from a GitHub URL or ``owner/repo`` slug.

    Parameters
    ----------
    ref : str
        A GitHub HTTPS URL, a ``github.com/owner/repo`` string, or a bare
        ``owner/repo`` slug.

    Returns
    -------
    tuple of (str, str)
        ``(owner, repo)`` with the ``.git`` suffix and trailing slashes removed.

    Raises
    ------
    ValueError
        If the string does not match any recognised form.
    """
    ref = ref.strip()
    m = _FULL_URL_RE.match(ref) or _SLUG_RE.match(ref)
    if not m:
        raise ValueError(
            f"Cannot parse GitHub reference {ref!r}. "
            "Expected 'https://github.com/owner/repo', 'github.com/owner/repo', "
            "or 'owner/repo'."
        )
    return m.group(1), m.group(2)


def is_github_ref(s: str) -> bool:
    """Return ``True`` when ``s`` looks like a GitHub URL or slug.

    Parameters
    ----------
    s : str
        The string to test.

    Returns
    -------
    bool
        ``True`` when ``s`` matches a full GitHub URL or a bare ``owner/repo``
        slug.  ``False`` for local paths.
    """
    s = s.strip()
    return bool(_FULL_URL_RE.match(s) or _SLUG_RE.match(s))


def clone_repo(ref: str, dest: Path | None = None) -> Path:
    """Shallow-clone a public GitHub repository and return the local path.

    Uses ``git clone --depth 1`` so only the latest commit is fetched;
    sufficient for language, archetype, and service detection.

    Parameters
    ----------
    ref : str
        A GitHub URL or ``owner/repo`` slug.
    dest : pathlib.Path, optional
        Where to place the clone.  A fresh temporary directory is created when
        omitted.  The caller owns and must clean up this directory.

    Returns
    -------
    pathlib.Path
        The root of the cloned repository.

    Raises
    ------
    ValueError
        If ``ref`` cannot be parsed as a GitHub reference.
    RuntimeError
        If ``git clone`` fails (network error, repo not found, etc.).
    """
    owner, repo = parse_github_ref(ref)
    clone_url = f"https://github.com/{owner}/{repo}.git"
    if dest is None:
        dest = Path(tempfile.mkdtemp(prefix=f"cost-running-{repo}-"))
    try:
        subprocess.run(
            ["git", "clone", "--depth", "1", "--quiet", clone_url, str(dest)],
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as exc:
        shutil.rmtree(dest, ignore_errors=True)
        raise RuntimeError(f"Failed to clone {clone_url}: {exc.stderr.strip() or exc}") from exc
    return dest


@contextmanager
def cloned_repo(ref: str) -> Generator[Path, None, None]:
    """Context manager that clones a GitHub repo, yields its path, then deletes it.

    Parameters
    ----------
    ref : str
        A GitHub URL or ``owner/repo`` slug.

    Yields
    ------
    pathlib.Path
        The root of the temporary clone.
    """
    dest = clone_repo(ref)
    try:
        yield dest
    finally:
        shutil.rmtree(dest, ignore_errors=True)
