"""
A small, dependency-free client for a local Ollama server.

Module summary
--------------
The audit pipeline can ask a language model to read a repository and describe
what it computes: is the work compute-bound, how much of it is there, what one
command runs a representative slice. That reading is a *structural* judgement
about code, not a cost figure, and it is always the weakest kind of evidence the
tool carries (see :mod:`.code_analysis`). To keep it free, private, and offline,
the model runs locally through Ollama rather than a paid remote API.

This module talks to the Ollama HTTP endpoint (``http://localhost:11434`` by
default) using only the standard library, so it adds no dependency to a project
that deliberately keeps its runtime requirements to two packages. Every call has
a short timeout and swallows connection errors into a plain ``None``/``False``:
Ollama not being installed or running is the normal case, not an exception, and
the caller falls back to static analysis without noticing a stack trace.

Configuration
-------------
``COST_RUNNING_OLLAMA_HOST``
    Base URL of the Ollama server. Defaults to ``http://localhost:11434``.
``COST_RUNNING_OLLAMA_MODEL``
    Preferred model tag. Defaults to ``qwen2.5-coder:latest``, a code-specialised
    model; if it is absent, :func:`pick_model` falls back to another installed
    code model, then to any installed model.

Author
------
Project maintainers.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

# The local server and preferred model, overridable by environment so a user can
# point at a remote Ollama or force a particular model without code changes.
DEFAULT_HOST: str = "http://localhost:11434"
DEFAULT_MODEL: str = "qwen2.5-coder:latest"

# Model tags we prefer for code reading, in order, when the configured default is
# not installed. Substring match against installed tags, so ``qwen2.5-coder`` also
# matches ``qwen2.5-coder:latest``.
_CODE_MODEL_PREFERENCES: tuple[str, ...] = (
    "qwen2.5-coder",
    "qwen3",
    "codellama",
    "deepseek-coder",
    "mistral",
    "llama3",
)


def host() -> str:
    """Return the configured Ollama base URL (no trailing slash).

    Returns
    -------
    str
        ``COST_RUNNING_OLLAMA_HOST`` if set, otherwise :data:`DEFAULT_HOST`.
    """
    return os.environ.get("COST_RUNNING_OLLAMA_HOST", DEFAULT_HOST).rstrip("/")


def is_available(timeout: float = 1.5) -> bool:
    """Return whether a local Ollama server answers within ``timeout`` seconds.

    Parameters
    ----------
    timeout : float, optional
        Seconds to wait for the tags endpoint before giving up.

    Returns
    -------
    bool
        ``True`` when the server responds, ``False`` on any connection error,
        timeout, or non-200 status. Never raises: an absent server is normal.
    """
    return bool(available_models(timeout=timeout))


def available_models(timeout: float = 1.5) -> list[str]:
    """Return the tags of installed models, or ``[]`` when the server is absent.

    Parameters
    ----------
    timeout : float, optional
        Seconds to wait for the tags endpoint.

    Returns
    -------
    list of str
        Installed model tags (for example ``qwen2.5-coder:latest``), or an empty
        list on any failure.
    """
    try:
        req = urllib.request.Request(f"{host()}/api/tags", method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (local host)
            payload = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, ValueError, OSError):
        return []
    models = payload.get("models", []) if isinstance(payload, dict) else []
    return [m["name"] for m in models if isinstance(m, dict) and m.get("name")]


def pick_model(preferred: str | None = None, timeout: float = 1.5) -> str | None:
    """Return the best installed model tag to use, or ``None`` when none is.

    Resolution order: the caller's ``preferred`` (or the configured default) when
    it is installed, then the first installed tag matching a code-model
    preference, then any installed tag.

    Parameters
    ----------
    preferred : str or None, optional
        A specific tag to prefer. Defaults to ``COST_RUNNING_OLLAMA_MODEL`` or
        :data:`DEFAULT_MODEL`.
    timeout : float, optional
        Seconds to wait for the tags endpoint.

    Returns
    -------
    str or None
        A model tag to pass to :func:`generate`, or ``None`` when no model is
        installed (or the server is absent).
    """
    installed = available_models(timeout=timeout)
    if not installed:
        return None

    want = preferred or os.environ.get("COST_RUNNING_OLLAMA_MODEL", DEFAULT_MODEL)
    # Exact tag, then bare-name match (want "qwen2.5-coder" matches "…:latest").
    if want in installed:
        return want
    for tag in installed:
        if tag.split(":", 1)[0] == want.split(":", 1)[0]:
            return tag

    # Fall back to the first installed model matching a code-reading preference.
    # A code-specialised model reads structure better than a general or vision
    # model, so these come before the author's suite default.
    for pref in _CODE_MODEL_PREFERENCES:
        for tag in installed:
            if pref in tag:
                return tag

    # Then honour the os-helper suite default, when os-helper is installed, so the
    # tool stays consistent with the rest of the author's toolchain. Guarded: the
    # package is optional and absent on many machines and CI runners.
    suite = _suite_model()
    if suite:
        if suite in installed:
            return suite
        for tag in installed:
            if tag.split(":", 1)[0] == suite.split(":", 1)[0]:
                return tag

    # Last resort: any installed model is better than none for a structural read.
    return installed[0]


def _suite_model() -> str | None:
    """Return the os-helper suite-wide Ollama model, or ``None`` when unavailable.

    Returns
    -------
    str or None
        ``os_helper.llm_model()`` when os-helper is importable and answers,
        otherwise ``None``. Never raises: os-helper is an optional convenience,
        not a dependency of this module.
    """
    try:
        import os_helper as osh

        model = osh.llm_model()
        return model if isinstance(model, str) and model else None
    except Exception:
        # Any import or call failure just means "no suite preference available".
        return None


def generate(
    prompt: str,
    *,
    model: str | None = None,
    timeout: float = 120.0,
    json_format: bool = True,
) -> str | None:
    """Run one non-streaming completion and return its text, or ``None``.

    Parameters
    ----------
    prompt : str
        The full prompt to send.
    model : str or None, optional
        Model tag. Defaults to :func:`pick_model`.
    timeout : float, optional
        Seconds to wait for the completion. Local models are slow, so this is
        generous; the caller sets it lower for a quick probe.
    json_format : bool, optional
        When ``True``, ask Ollama to constrain output to valid JSON (its
        ``format: json`` mode), which makes the reply parseable without scraping.

    Returns
    -------
    str or None
        The model's response text, or ``None`` on any connection error, timeout,
        or if no model is installed. Never raises.
    """
    chosen = model or pick_model()
    if chosen is None:
        return None

    body: dict[str, object] = {
        "model": chosen,
        "prompt": prompt,
        "stream": False,
        # Low temperature: a structural reading should be stable, not creative.
        "options": {"temperature": 0.0},
    }
    if json_format:
        body["format"] = "json"

    data = json.dumps(body).encode("utf-8")
    try:
        req = urllib.request.Request(
            f"{host()}/api/generate",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (local host)
            payload = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, ValueError, OSError):
        return None
    if isinstance(payload, dict):
        text = payload.get("response")
        return text if isinstance(text, str) else None
    return None
