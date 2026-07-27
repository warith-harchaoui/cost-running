# cost-running

Measure the cost of running code, in the wide sense: money, time, energy,
carbon, water, and any dimension your team registers, reported per canonical
unit of work and always carrying an honesty label.

cost-running is the successor to the `nexteco` prototype. It keeps that tool's
load-bearing idea, that every number states how much it can be trusted, and
generalises it: dimensions are a registry rather than a fixed five, and the same
core is reached through several delivery surfaces (library, command line, HTTP,
MCP, GUI, and an Agent Skill).

## The honesty taxonomy

Every value carries one of four statuses, and a derived value can never claim to
be better founded than its weakest input (the weakest-link rule).

| Status | Meaning |
|---|---|
| `measured` | Recorded from an actual run on the target system. |
| `estimated` | Computed from sourced assumptions or a published formula. |
| `placeholder` | A structural stand-in kept visible; not a real number. |
| `TODO` | A human must supply this before the model can be trusted. |

The artefacts are two files committed next to the code: a machine-readable
`cost_of_running.yaml` (the source of truth) and a generated `cost_of_running.md`
report.

## Status of this repository

cost-running is early (version 0.1.0). The shared core and the command-line
surface are working and tested. The other surfaces are planned and tracked in
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

| Surface | State |
|---|---|
| Python library (core + application) | Working, tested |
| Command line (`cost-running`) | Working, tested: `init`, `validate`, `render`, `measure` |
| Local hardware detection and grounded extrapolation | Working, tested |
| Command line: `diff`, `audit` | Planned |
| HTTP API (FastAPI) | Planned |
| MCP server | Planned |
| GUI | Planned |
| Agent Skill | Draft (`skills/cost-of-running-code/`) |

## Install

```bash
# Runtime install.
python -m pip install -r requirements.txt

# Contributor, test, and CI install.
python -m pip install -r requirements-dev.txt
```

Conda users can create the small wrapper environment:

```bash
conda env create -f environment.yaml
conda activate env-for-cost-running
python -m pip install -r requirements-dev.txt
```

There is no system-level dependency for the core and the command line. Power
measurement, when it lands, will need an OS profiler; those instructions will
name the package for macOS, Ubuntu, and Windows at that point.

## Quick start

```bash
# Write a starter model, validate it, and render the report.
cost-running init --template full --output cost_of_running.yaml
cost-running validate cost_of_running.yaml
cost-running render cost_of_running.yaml --output cost_of_running.md
```

See [`EXAMPLES.md`](EXAMPLES.md) for a runnable cookbook, including the library
API.

## Architecture

The business logic lives once, in a core and an application layer that know
nothing about how they are called. Each delivery surface is a thin adapter over
that shared core, so the surfaces cannot drift apart. The full design, and the
plan for the surfaces still to come, is in
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

## Development

```bash
python -m pip install -r requirements-dev.txt
python -m pytest            # run the test suite
ruff check src tests        # lint (PEP 8 plus NumPy docstrings)
ruff format --check src tests
```

## License

Released into the public domain under the [Unlicense](LICENSE).
