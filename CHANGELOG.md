# Changelog

All notable changes to cost-running are recorded here. Categories: `Added`,
`Changed`, `Fixed`, `Docs`, `Removed`.

## Unreleased

### Added (audit increment)

- **`audit` use case and CLI verb.** Scans a repository and scaffolds a cost
  model: it counts languages, infers an archetype (inference, training, service,
  CLI, ETL, frontend), seeds the compute assumptions from archetype defaults
  (labelled estimated), and marks the whole output `scaffold`.
- **Paid-service detection wired to the catalog.** Code is matched against the
  service catalog's detection hints; each detected service gets a pricing block
  prefilled with the provider's pricing page and left with the price and the
  per-unit consumption as sourced TODOs, so completing it is a short, guided
  step. Detection keeps the matching line of code as evidence for a later
  contribution.

### Added (catalog increment)

- **Data-driven catalog.** Device power and throughput, and the paid-service
  registry, moved from hard-coded dicts into provenance-carrying YAML
  (`data/hardware.yaml`, `data/services.yaml`). Every row states its source and
  retrieval date. The service catalog deliberately ships no prices, only where to
  price each service, because pricing is volatile.
- **A growing, community catalog.** A writable overlay merges over the bundled
  catalog using the same schema, so a locally added row can be promoted upstream
  unchanged. `cost-running hardware add` and `service add` refuse any entry
  without provenance; `hardware list --stale` / `service list --stale` surface
  rows due for a refresh. The intended growth path is an agent-prepared,
  user-authored pull request carrying the sourced row plus the code snippet that
  triggered the detection, reviewed before merge. Documented in
  `docs/en/catalog.md`.

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
