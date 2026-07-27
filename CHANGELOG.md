# Changelog

All notable changes to cost-running are recorded here. Categories: `Added`,
`Changed`, `Fixed`, `Docs`, `Removed`.

## Unreleased

### Added (measurement increment)

- **`measure` use case and CLI verb.** Runs a command, times it, and reads what
  the machine can physically report: CPU time and peak memory from `getrusage`,
  and package energy from the Intel RAPL counter when available. Power is
  labelled `measured` only when a counter produced it, `estimated` otherwise,
  and the result records the local hardware it ran on.
- **Local hardware detection** (`infrastructure/hardware.py`) via low-level OS
  commands (sysctl, `/proc`, `lscpu`, `nvidia-smi`), mapped to the power tables.
- **Green Algorithms estimator** (`infrastructure/green_algorithms.py`): active
  power, energy, and carbon, with CPU/GPU TDP tables (including Blackwell
  B100/B200 and AMD MI325X) and BF16 throughput figures.
- **Grounded extrapolation** (`application/extrapolate.py`): re-bases a measured
  GPU runtime onto another accelerator by the peak-throughput ratio, not a naive
  power swap. Every result carries its model, assumptions, and limits, and the
  model refuses to answer outside its compute-bound regime.

### Added

- **Project foundation.** A fresh, wide-sense successor to the `nexteco`
  prototype, laid out as a shared core with thin delivery-surface adapters.
- **Domain layer.** The honesty taxonomy (`measured` / `estimated` /
  `placeholder` / `TODO`) with the weakest-link rule; an extensible
  `DimensionRegistry` that promotes a dimension to a first-class object; the
  `Quantity` value object; and the versioned schema identity (`SCHEMA_VERSION`).
- **Application layer.** `load_model`, `validate_model` (schema-version gate,
  required structure, status vocabulary, weakest-link and provenance-freshness
  checks), and `render_markdown`.
- **Command-line surface.** `cost-running` with `init`, `validate`, and
  `render`; `--version`; documented exit codes; result on stdout, diagnostics on
  stderr.
- **Bundled templates.** A minimal scaffold and an annotated worked example,
  both carrying `schema_version`.
- **Tests and CI.** A pytest suite for the domain, validation, rendering,
  templates, and the CLI contract; a CI workflow running ruff and pytest.
- **Report-pipeline design.** `docs/ARCHITECTURE.md` records the plan: a
  Markdown base report compiled into a web report with SVG figures (via the
  `sprezzature` skills) and into DOCX and PDF (via `md2star`, using
  `assets/report/template.docx`), with a Ralph Eyeball visual-review gate.

### Notes

This is version 0.1.0. The library and the `init` / `validate` / `render`
command-line verbs are working and tested. The `diff`, `audit`, and `measure`
verbs, the web and DOCX/PDF reports, and the HTTP, MCP, and GUI surfaces are
planned and tracked in the architecture document.
