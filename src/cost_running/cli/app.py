"""
The cost-running command-line application.

Module summary
--------------
Builds the argument parser and dispatches subcommands to the application use
cases. The parser is constructed in :func:`make_parser` with no side effects, so
it can be imported for testing, documentation, or GUI generation without running
anything. Machine-readable results go to stdout; diagnostics and errors go to
stderr; exit codes are documented and stable.

Exit codes
----------
- ``0``: success.
- ``1``: expected operational failure (for example, validation found errors).
- ``2``: invalid command-line usage or an unreadable input file.

Author
------
Project maintainers.
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Sequence
from pathlib import Path

from .. import __version__
from ..application import load_model, render_markdown, validate_model, write_text
from ..templates import get_template_text

# Diagnostics go through the logger to stderr; the documented result of a command
# goes to stdout via print. This split lets `cost-running render ... > out.md`
# and pipes capture exactly the payload and nothing else.
logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)
logger = logging.getLogger("cost_running")

# Documented, stable exit statuses.
EXIT_OK = 0
EXIT_OPERATIONAL = 1
EXIT_USAGE = 2


def _cmd_init(args: argparse.Namespace) -> int:
    """Write a starter cost model to disk.

    Parameters
    ----------
    args : argparse.Namespace
        Parsed arguments with ``template``, ``output`` and ``force``.

    Returns
    -------
    int
        ``0`` on success, ``2`` when refusing to overwrite without ``--force``.
    """
    target = Path(args.output)
    # Refuse to clobber an existing model unless the user opted in explicitly;
    # a cost model is hand-maintained and overwriting it silently loses work.
    if target.exists() and not args.force:
        logger.error("Refusing to overwrite existing file: %s (use --force).", target)
        return EXIT_USAGE
    write_text(target, get_template_text(args.template))
    logger.info("Wrote %s", target)
    return EXIT_OK


def _cmd_validate(args: argparse.Namespace) -> int:
    """Validate a cost model and report issues.

    Parameters
    ----------
    args : argparse.Namespace
        Parsed arguments with ``input``.

    Returns
    -------
    int
        ``0`` when valid (warnings allowed), ``1`` when errors remain, ``2`` when
        the file cannot be read.
    """
    # A missing or malformed file is a usage error, distinct from a model that
    # loads but fails its rules.
    try:
        model = load_model(args.input)
    except (OSError, ValueError) as exc:
        logger.error("Cannot read model %s: %s", args.input, exc)
        return EXIT_USAGE

    result = validate_model(model)
    # Surface warnings and errors on stderr so a redirect of stdout stays clean.
    for issue in result.warnings:
        logger.warning("warning: %s", issue.message)
    for issue in result.errors:
        logger.error("error: %s", issue.message)

    if result.is_valid():
        logger.info("Validation passed (%d warning(s)).", len(result.warnings))
        return EXIT_OK
    logger.error("Validation failed (%d error(s)).", len(result.errors))
    return EXIT_OPERATIONAL


def _cmd_render(args: argparse.Namespace) -> int:
    """Render a cost model to Markdown.

    Parameters
    ----------
    args : argparse.Namespace
        Parsed arguments with ``input`` and optional ``output``.

    Returns
    -------
    int
        ``0`` on success, ``1`` when the model has validation errors (the report
        is still produced so the author can inspect it), ``2`` on a read error.
    """
    try:
        model = load_model(args.input)
    except (OSError, ValueError) as exc:
        logger.error("Cannot read model %s: %s", args.input, exc)
        return EXIT_USAGE

    markdown = render_markdown(model)
    # With --output, write the file; without it, the report is the stdout payload
    # so it can be piped. Either way the report is the deliberate result.
    if args.output:
        write_text(args.output, markdown)
        logger.info("Rendered %s", args.output)
    else:
        print(markdown)

    # Report but do not hide validation errors; the render still succeeds so the
    # author can see the offending output.
    result = validate_model(model)
    if not result.is_valid():
        for issue in result.errors:
            logger.error("error: %s", issue.message)
        return EXIT_OPERATIONAL
    return EXIT_OK


def make_parser() -> argparse.ArgumentParser:
    """Build the CLI parser without executing anything.

    Returns
    -------
    argparse.ArgumentParser
        A parser wired with every subcommand and its handler, safe to import for
        testing, documentation, or GUI generation.
    """
    parser = argparse.ArgumentParser(
        prog="cost-running",
        description="Measure the cost of running code: money, time, energy, carbon, water.",
    )
    # A distributed CLI reports its version, as the standard requires.
    parser.add_argument("--version", action="version", version=f"cost-running {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # init: write a starter model.
    init_parser = subparsers.add_parser("init", help="Write a starter cost model.")
    init_parser.add_argument("--template", choices=["min", "full"], default="min")
    init_parser.add_argument("--output", default="cost_of_running.yaml")
    init_parser.add_argument("--force", action="store_true", help="Overwrite an existing file.")
    init_parser.set_defaults(func=_cmd_init)

    # validate: check a model against schema and honesty rules.
    validate_parser = subparsers.add_parser("validate", help="Validate a cost model.")
    validate_parser.add_argument("input", help="Path to the YAML cost model.")
    validate_parser.set_defaults(func=_cmd_validate)

    # render: produce the Markdown report.
    render_parser = subparsers.add_parser("render", help="Render a cost model to Markdown.")
    render_parser.add_argument("input", help="Path to the YAML cost model.")
    render_parser.add_argument("--output", help="Write here instead of stdout.")
    render_parser.set_defaults(func=_cmd_render)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Parse arguments and run the selected command.

    Parameters
    ----------
    argv : Sequence[str] or None, optional
        Argument vector without the program name. ``None`` uses ``sys.argv``.

    Returns
    -------
    int
        The process exit status.
    """
    parser = make_parser()
    args = parser.parse_args(argv)
    # Every subparser sets ``func``; call it and return its exit status.
    return int(args.func(args))


if __name__ == "__main__":  # pragma: no cover - module execution entry point.
    raise SystemExit(main())
