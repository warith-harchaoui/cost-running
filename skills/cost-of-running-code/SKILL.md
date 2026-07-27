---
name: cost-of-running-code
description: >-
  Builds a repository-native cost-of-running model for a codebase and produces
  professional reports from it: money, time, energy, carbon, and water per
  canonical unit of work, every number carrying an honesty status (measured /
  estimated / placeholder / TODO). Use when the user asks what it costs to run
  their code, wants a carbon or energy footprint per request or per run, asks to
  audit or estimate the running cost of a repo, or wants a cost-of-running report
  (Markdown, web, DOCX, or PDF). Do not use for cloud-billing dashboards, for
  observability or profiling of a live service, or for training-only questions
  with no per-unit cost.
license: Unlicense
compatibility: >-
  Requires Python 3.10+ and the cost-running package (pip install cost-running).
  Web reports use the sprezzature skills; DOCX and PDF use md2star.
metadata:
  author: Warith Harchaoui
  version: "0.1.0"
---

# Cost of running code

Produce a trustworthy, wide-sense cost model for a repository and turn it into
reports that look professional. The load-bearing rule is honesty: every number
states how well it is founded, and a derived value never claims more confidence
than its weakest input.

## When this applies

Trigger when the user wants the cost of *running* code: cost, energy, or carbon
per request, per inference, per job, or per CLI invocation, or a report of that
cost. Do not trigger for a cloud-billing breakdown, for live-service
observability, or for a pure model-training question that has no per-unit cost.

## The honesty taxonomy (never skip this)

Every value carries exactly one status:

- `measured`: recorded from an actual run on the target system.
- `estimated`: computed from a sourced assumption or a published formula.
- `placeholder`: a structural stand-in kept visible; not a real number.
- `TODO`: a human must supply this before the model is trusted.

Weakest-link rule: a derived value (energy from runtime and power, money from
energy and price, carbon from energy and grid intensity) takes the status of its
weakest input. Never present an estimate as measured. Never invent a country, a
tariff, or a price; mark it `TODO` and point at the source to fill in.

## Workflow

1. Identify the canonical unit of work (one inference, one CLI run, one request).
   If ambiguous, ask the user rather than guessing.
2. Scaffold a model: `cost-running init --template full --output cost_of_running.yaml`.
3. Fill the deployment (provider, region, country) from what the repo states.
   Do not guess the country from a locale.
4. Estimate energy and carbon with the Green Algorithms method (see
   `references/green-algorithms.md`) when no measurement exists; mark the results
   `estimated` and record the formula in `notes`.
5. For each sourced value (electricity price, grid intensity, API price) add
   `source_url` and `retrieved_date`. Read the source before citing it.
6. Validate: `cost-running validate cost_of_running.yaml`. Fix every error;
   weigh every warning.
7. Render the Markdown report: `cost-running render cost_of_running.yaml --output cost_of_running.md`.
8. Produce the professional reports when asked (see "Reports").
9. Hand off the YAML, the report, and a plain-language note of what is measured,
   what is estimated, and what still needs a human.

## Reports

The Markdown report is the source for three professional outputs:

- **Web report**: a self-contained HTML page with inline SVG figures (cost by
  dimension, scenario comparison, at-scale projection, an honesty legend). Build
  the figures and page with the `sprezzature` skills (figures, ui, publish,
  colours, accessibility). Figures are SVG not PNG, colour-vision-deficiency
  safe, and accessible.
- **DOCX and PDF**: compile the Markdown with `md2star`, using
  `assets/report/template.docx` as the DOCX reference template.

Every visual (each figure, the web page, the compiled DOCX and PDF) passes the
Ralph Eyeball Loop before it ships: render it, look at it against a checklist,
fix the source, look again. A chart that is right in the data can still be wrong
in the pixels.

## References

- `references/green-algorithms.md`: the energy and carbon formulas, the hardware
  power tables, and where to find live grid-intensity and price sources.
- `references/honesty-taxonomy.md`: the four statuses and the weakest-link rule
  in full.
- `references/reports.md`: how to build the web, DOCX, and PDF reports and run
  the Ralph Eyeball gate.

## Completion checklist

- Canonical unit of work is concrete and named.
- Every number has a status; no estimate is dressed as measured.
- Every sourced value has `source_url` and `retrieved_date`.
- `cost-running validate` passes with errors resolved.
- The requested reports are produced and, if visual, Ralph-Eyeball reviewed.
- The hand-off states plainly what is measured, estimated, and still TODO.
