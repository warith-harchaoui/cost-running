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
import json
import logging
import sys
from collections.abc import Sequence
from pathlib import Path

from .. import __version__
from ..application import (
    diff_models,
    dump_model_yaml,
    load_model,
    render_html,
    render_markdown,
    validate_model,
    write_text,
)
from ..application.audit import audit_github_repo, audit_repo
from ..application.measure import measure_command
from ..infrastructure import registry
from ..infrastructure.github import is_github_ref
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
    """Render a cost model to Markdown or HTML.

    Parameters
    ----------
    args : argparse.Namespace
        Parsed arguments with ``input``, optional ``output``, and ``format``.

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

    fmt = args.format
    if fmt == "html":
        content = render_html(model)
    else:
        content = render_markdown(model)

    if args.output:
        write_text(args.output, content)
        logger.info("Rendered %s (%s)", args.output, fmt)
    else:
        print(content)

    result = validate_model(model)
    if not result.is_valid():
        for issue in result.errors:
            logger.error("error: %s", issue.message)
        return EXIT_OPERATIONAL
    return EXIT_OK


def _cmd_measure(args: argparse.Namespace) -> int:
    """Run a command and print its measured cost as JSON.

    Parameters
    ----------
    args : argparse.Namespace
        Parsed arguments with ``command`` (the argv to run) and an optional
        ``grid_gco2e_per_kwh``.

    Returns
    -------
    int
        ``0`` on success, ``2`` when no command was supplied.
    """
    # argparse.REMAINDER captures the command; a leading "--" separator is common
    # and should be dropped so `measure -- python x.py` works.
    command = list(args.command)
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        logger.error("Provide a command to measure, e.g. `cost-running measure -- python x.py`.")
        return EXIT_USAGE

    result = measure_command(command, grid_gco2e_per_kwh=args.grid_gco2e_per_kwh)
    # Diagnostics (warnings, the honesty of the power figure) go to stderr.
    for warning in result.warnings:
        logger.warning("warning: %s", warning)
    logger.info("power is %s (source: %s).", result.power_status, result.power_source)
    # The measurement itself is the machine-readable result: stdout, as JSON.
    print(json.dumps(result.to_dict(), indent=2, default=str))
    return EXIT_OK


def _cmd_diff(args: argparse.Namespace) -> int:
    """Compare two cost models and report dimensional drift.

    Parameters
    ----------
    args : argparse.Namespace
        Parsed arguments with ``old``, ``new``, ``threshold``, and ``json``.

    Returns
    -------
    int
        ``0`` when all quantities are within the threshold, ``1`` when at least
        one dimension drifted beyond it (the CI gate signal), ``2`` on a read
        error.
    """
    try:
        old_model = load_model(args.old)
        new_model = load_model(args.new)
    except (OSError, ValueError) as exc:
        logger.error("Cannot read model: %s", exc)
        return EXIT_USAGE

    result = diff_models(old_model, new_model)
    drifts = result.above_threshold(args.threshold)

    if args.json:
        print(json.dumps(result.report(args.threshold), indent=2, default=str))
    else:
        if drifts:
            logger.warning(
                "%d dimension(s) drifted beyond %.0f%% threshold:",
                len(drifts),
                args.threshold * 100,
            )
            for d in drifts:
                logger.warning(
                    "  %s: %+.1f%%  (%s → %s)",
                    d.path,
                    d.relative_change * 100,
                    d.old_value,
                    d.new_value,
                )
        else:
            logger.info("All quantities within %.0f%% threshold.", args.threshold * 100)
        if result.only_in_old:
            logger.warning("Removed from model: %s", ", ".join(result.only_in_old))
        if result.only_in_new:
            logger.warning("Added to model: %s", ", ".join(result.only_in_new))

    return EXIT_OPERATIONAL if drifts else EXIT_OK


def _cmd_audit(args: argparse.Namespace) -> int:
    """Scan a repository and write a scaffold cost model.

    Parameters
    ----------
    args : argparse.Namespace
        Parsed arguments with ``path``, ``output``, and ``json``.  ``path``
        may be a local directory or a GitHub URL / ``owner/repo`` slug; the
        latter is cloned automatically and deleted after analysis.

    Returns
    -------
    int
        ``0`` on success, ``2`` when the path is not a directory or the clone
        fails.
    """
    try:
        if is_github_ref(args.path):
            logger.info("Cloning %s …", args.path)
            result = audit_github_repo(args.path)
        else:
            result = audit_repo(args.path)
    except (NotADirectoryError, OSError, RuntimeError, ValueError) as exc:
        logger.error("Cannot audit %s: %s", args.path, exc)
        return EXIT_USAGE

    # Write the scaffold model so it can be reviewed and committed next to the code.
    write_text(args.output, dump_model_yaml(result.model))
    logger.info(
        "Scaffolded %s (archetype: %s, services: %s) -> %s",
        result.name,
        result.archetype,
        ", ".join(sorted({h.key for h in result.service_hits})) or "none",
        args.output,
    )
    # The report is the machine-readable summary; emit it to stdout on request.
    if args.json:
        print(json.dumps(result.report(), indent=2, default=str))
    return EXIT_OK


def _cmd_hardware_list(args: argparse.Namespace) -> int:
    """Print the hardware catalog as JSON, optionally only the stale rows.

    Parameters
    ----------
    args : argparse.Namespace
        Parsed arguments with ``stale``.

    Returns
    -------
    int
        Always ``0``.
    """
    catalog = registry.hardware_catalog()
    # When --stale is set, show only rows whose provenance needs a refresh.
    if args.stale:
        catalog = {
            kind: {k: row for k, row in rows.items() if registry.is_stale(row)}
            for kind, rows in catalog.items()
        }
    print(json.dumps(catalog, indent=2, default=str))
    return EXIT_OK


def _cmd_hardware_add(args: argparse.Namespace) -> int:
    """Add a hardware row to the overlay catalog (provenance required).

    Parameters
    ----------
    args : argparse.Namespace
        Parsed arguments carrying the device figures and provenance.

    Returns
    -------
    int
        ``0`` on success, ``2`` when the entry is rejected (missing provenance).
    """
    # Build the row from the flags, dropping the ones the user did not supply.
    entry: dict[str, object] = {"key": args.key}
    for field_name, value in (
        ("tdp_w", args.tdp_w),
        ("peak_bf16_tflops", args.peak_bf16_tflops),
        ("w_per_core", args.w_per_core),
        ("source_url", args.source_url),
        ("retrieved_date", args.retrieved_date),
        ("notes", args.notes),
    ):
        if value is not None:
            entry[field_name] = value
    device_kind = "gpus" if args.kind == "gpu" else "cpus"
    try:
        target = registry.add_hardware(device_kind, entry)
    except ValueError as exc:
        # A missing key or provenance is a usage error, reported clearly.
        logger.error("Cannot add hardware: %s", exc)
        return EXIT_USAGE
    logger.info("Added %s %r to %s", args.kind, args.key, target)
    return EXIT_OK


def _cmd_service_list(args: argparse.Namespace) -> int:
    """Print the service catalog as JSON, optionally only the stale rows."""
    catalog = registry.service_catalog()
    if args.stale:
        catalog = {k: row for k, row in catalog.items() if registry.is_stale(row)}
    print(json.dumps(catalog, indent=2, default=str))
    return EXIT_OK


def _cmd_service_add(args: argparse.Namespace) -> int:
    """Add a service row to the overlay catalog (pricing source required)."""
    entry: dict[str, object] = {"key": args.key, "name": args.name}
    for field_name, value in (
        ("category", args.category),
        ("pricing_source_url", args.pricing_source_url),
        ("unit_hint", args.unit_hint),
        ("retrieved_date", args.retrieved_date),
    ):
        if value is not None:
            entry[field_name] = value
    try:
        target = registry.add_service(entry)
    except ValueError as exc:
        logger.error("Cannot add service: %s", exc)
        return EXIT_USAGE
    logger.info("Added service %r to %s", args.key, target)
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

    # render: produce a Markdown or HTML report.
    render_parser = subparsers.add_parser("render", help="Render a cost model to Markdown or HTML.")
    render_parser.add_argument("input", help="Path to the YAML cost model.")
    render_parser.add_argument("--output", help="Write here instead of stdout.")
    render_parser.add_argument(
        "--format",
        choices=["md", "html"],
        default="md",
        help="Output format: md (default) or html (self-contained page with figures).",
    )
    render_parser.set_defaults(func=_cmd_render)

    # diff: compare two models and report dimensional drift.
    diff_parser = subparsers.add_parser(
        "diff", help="Compare two cost models and flag dimensional drift."
    )
    diff_parser.add_argument("old", help="Path to the baseline YAML cost model.")
    diff_parser.add_argument("new", help="Path to the updated YAML cost model.")
    diff_parser.add_argument(
        "--threshold",
        type=float,
        default=0.10,
        help=(
            "Relative drift threshold (default: 0.10 = 10%%). Exit 1 when any dimension exceeds it."
        ),
    )
    diff_parser.add_argument("--json", action="store_true", help="Print full diff as JSON.")
    diff_parser.set_defaults(func=_cmd_diff)

    # measure: run a command and report its measured cost as JSON.
    measure_parser = subparsers.add_parser(
        "measure", help="Run a command and measure its cost on this machine."
    )
    measure_parser.add_argument(
        "--grid-gco2e-per-kwh",
        type=float,
        default=None,
        help="Grid carbon intensity for the carbon figure (sourced value; omit to skip carbon).",
    )
    # REMAINDER captures the command and its flags verbatim after a `--`.
    measure_parser.add_argument(
        "command",
        nargs=argparse.REMAINDER,
        help="The command to run, e.g. `-- python detect.py`.",
    )
    measure_parser.set_defaults(func=_cmd_measure)

    # audit: scan a repository and scaffold a cost model.
    audit_parser = subparsers.add_parser(
        "audit", help="Scan a repository and scaffold a cost model."
    )
    audit_parser.add_argument(
        "path",
        help=(
            "Local repository path, GitHub URL "
            "(https://github.com/owner/repo), or bare owner/repo slug."
        ),
    )
    audit_parser.add_argument(
        "--output", default="cost_of_running.yaml", help="Where to write the scaffold model."
    )
    audit_parser.add_argument(
        "--json", action="store_true", help="Also print the audit report to stdout."
    )
    audit_parser.set_defaults(func=_cmd_audit)

    # hardware: inspect or grow the device catalog.
    hardware_parser = subparsers.add_parser(
        "hardware", help="Inspect or grow the hardware catalog."
    )
    hardware_sub = hardware_parser.add_subparsers(dest="hardware_command", required=True)
    hw_list = hardware_sub.add_parser("list", help="Print the hardware catalog.")
    hw_list.add_argument("--stale", action="store_true", help="Show only rows needing a refresh.")
    hw_list.set_defaults(func=_cmd_hardware_list)
    hw_add = hardware_sub.add_parser("add", help="Add a device (provenance required).")
    hw_add.add_argument("--kind", choices=["gpu", "cpu"], required=True)
    hw_add.add_argument("--key", required=True, help="Stable device key, e.g. H20.")
    hw_add.add_argument("--tdp-w", type=float, help="Whole-device TDP in watts (GPU).")
    hw_add.add_argument("--peak-bf16-tflops", type=float, help="Dense BF16 throughput (GPU).")
    hw_add.add_argument("--w-per-core", type=float, help="Power per core in watts (CPU).")
    hw_add.add_argument("--source-url", required=True, help="Datasheet or product-page URL.")
    hw_add.add_argument("--retrieved-date", required=True, help="YYYY-MM-DD the source was read.")
    hw_add.add_argument("--notes", help="Short provenance note.")
    hw_add.set_defaults(func=_cmd_hardware_add)

    # service: inspect or grow the paid-service catalog.
    service_parser = subparsers.add_parser(
        "service", help="Inspect or grow the paid-service catalog."
    )
    service_sub = service_parser.add_subparsers(dest="service_command", required=True)
    svc_list = service_sub.add_parser("list", help="Print the service catalog.")
    svc_list.add_argument("--stale", action="store_true", help="Show only rows needing a refresh.")
    svc_list.set_defaults(func=_cmd_service_list)
    svc_add = service_sub.add_parser("add", help="Add a service (pricing source required).")
    svc_add.add_argument("--key", required=True, help="Stable service key, e.g. openai.")
    svc_add.add_argument("--name", required=True, help="Human-readable service name.")
    svc_add.add_argument("--category", help="e.g. llm, cloud, payments.")
    svc_add.add_argument("--pricing-source-url", required=True, help="The pricing page URL.")
    svc_add.add_argument("--unit-hint", help="How it is metered, e.g. USD per 1M tokens.")
    svc_add.add_argument("--retrieved-date", required=True, help="YYYY-MM-DD the price was read.")
    svc_add.set_defaults(func=_cmd_service_add)

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
