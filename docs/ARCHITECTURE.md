# Architecture

cost-running keeps its business logic in one place and reaches it through
several thin adapters. This is what lets the tool grow to many delivery surfaces
without the surfaces drifting apart, and it is the plan the repository is built
against.

## Layers

```
domain/         Pure vocabulary and invariants. No I/O, no network, no framework.
                Taxonomy (statuses, weakest-link), dimensions (registry),
                schema identity, the Quantity value object.

application/    Use cases over a model: load, validate, render, and later diff,
                audit, measure. Plain functions that return data, never print.

infrastructure/ Adapters to the outside world: estimators (Green Algorithms),
                OS power profilers, the file system, the recommendation data.

cli/ api/ mcp/ gui/   Delivery surfaces. Each translates its input into an
                application call and the result into its own output. No surface
                holds business logic; none imports another.
```

The dependency direction only ever points inward: a surface depends on the
application layer, the application layer on the domain, and nothing in the
domain depends on anything above it.

## The wide-sense generalisation

The `nexteco` prototype hard-wired five dimensions (money, time, energy, carbon,
water) into its schema and renderer. cost-running promotes a dimension to a
first-class, registered object (`domain/dimensions.py`), so the same validation
and rendering machinery covers the canonical five and any extra a team needs,
for example network egress or a memory-hour budget, without a schema change.

## Surfaces: what ships when

| Surface | State | Notes |
|---|---|---|
| Library | Working | The core plus the application use cases. |
| CLI | Working | `init`, `validate`, `render`. `diff`, `audit`, `measure` next. |
| Reports (Markdown) | Working | `render` emits a Markdown report. |
| Reports (web, with figures) | Next | Self-contained HTML with embedded SVG figures. |
| HTTP API (FastAPI) | Planned | Thin routes over the application layer. |
| MCP server | Planned | A curated allowlist of the operations. |
| GUI | Planned | Browser front end, built with the `sprezzature` skills. |
| Agent Skill | Draft | `skills/cost-of-running-code/`. |

## Reports are the product, and reports want figures

A cost model is easier to trust when it is easy to read, and some things read
better as a picture than a table: the split of cost across dimensions, one
scenario against another, a projection at scale, and above all the honesty
status of each number. So the report surface has two forms:

1. **Markdown** (working): a plain report, every number carrying its status.
2. **Web report** (next): a single self-contained HTML page with embedded SVG
   figures.

The web report is built with the author's `sprezzature` front-* skills rather
than a new charting stack, so it inherits their conventions:

- Figures are authored as **SVG, not PNG** (`sprezzature-figures`), so they stay
  crisp and small and the text stays selectable.
- Figures are **colour-vision-deficiency safe by construction**
  (`sprezzature-colors`): colour is never the only channel, and diverging data
  uses a blue-to-red ramp that survives red-green deficiency.
- The page and its figures pass **accessibility** checks
  (`sprezzature-accessibility`): alt text, no colour-only state.
- The page shell and any interactivity come from `sprezzature-ui` and
  `sprezzature-publish` (self-contained page, meta tags, the figure-fullscreen
  pattern).

Planned figures for the web report:

- Cost broken down by dimension, per scenario.
- Scenario against scenario, for a comparison model.
- A projection at scale (cost per unit times a volume).
- An honesty legend that makes the measured / estimated / placeholder / TODO mix
  visible at a glance.

The report renderer stays in the application layer and emits an SVG string per
figure; the `sprezzature` skills are how those SVG generators and the page shell
are authored and reviewed, not a runtime dependency the installed package
carries.

## The full report pipeline

The Markdown report is the single source; three professional-grade outputs are
compiled from it, and every visual is reviewed before it ships.

```
model.yaml --render--> report.md ---------------------------> web report (HTML + inline SVG figures, sprezzature)
                            |
                            +--md2star (template.docx)-------> report.docx
                            +--md2star----------------------> report.pdf
```

- **Web report**: a self-contained HTML page with inline SVG figures, authored
  with the `sprezzature` skills (figures, ui, publish, colours, accessibility).
- **DOCX and PDF**: the Markdown report is compiled with the author's
  `~/md2star` tool. The DOCX uses `assets/report/template.docx`
  (from `deraison.ai/template.docx`) as its reference template, so headings,
  fonts, and spacing match the house style. The PDF follows from the same source.

Every output must look superb and professional, not merely correct.

### The Ralph Eyeball gate

A chart can be right in the data and wrong in the pixels: a clipped label, a
legend off the canvas, two series that collapse to one colour for a colour-blind
reader. Code checks never catch that. So every visual artefact cost-running
produces (each SVG figure, the web report, the compiled DOCX and PDF) passes the
Ralph Eyeball Loop from `sprezzature-figures`: render it to an image, have an
agent or a local vision model actually look at it against a checklist, fix the
source, render again, look again, until it is clean. This is a release gate for
the report surfaces, not an optional polish step.

## Testing the surfaces

The core has unit tests. Every advertised surface gets adapter tests, and an
operation exposed through more than one surface gets a parity test, so the CLI,
API, MCP, and web report cannot disagree about defaults, validation, or the
honesty of a number.
