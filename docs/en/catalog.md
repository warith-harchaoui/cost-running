# The catalog

cost-running needs two kinds of reference data: how much power and throughput a
device has, and where a paid service is priced. That data is kept in a catalog,
and the catalog is designed to grow into a community resource without ever
shipping a number that has forgotten where it came from.

## Two kinds of data, treated differently

Hardware and pricing age at opposite rates, so the catalog treats them
differently.

- **Hardware** (device TDP, peak throughput) is stable per chip. It changes only
  when a new chip ships. So the catalog carries the real numbers, each with a
  source URL and the date it was read.
- **Service pricing** is volatile and changes without notice. Shipping a
  hard-coded price would be a stale number presented as fact, which is exactly
  the dishonesty this tool exists to prevent. So the service catalog carries what
  a service is, how to detect its use in code, and the pricing page where the
  current number lives, but not the price itself. The price is read at the moment
  it is needed and stored with its own retrieval date, so it can go stale and say
  so.

Both live as YAML: `src/cost_running/data/hardware.yaml` and
`src/cost_running/data/services.yaml`. Every row carries provenance.

## How the catalog grows

There are two layers, and they share one schema so a row can move between them
unchanged.

1. **The bundled catalog** ships inside the package. It is the shared, curated
   baseline.
2. **A writable overlay** holds local additions and corrections. The registry
   loads the bundled catalog and merges the overlay on top, so a local row
   extends the baseline or overrides a key. The overlay lives at
   `COST_RUNNING_REGISTRY_DIR` when set (point it at the repository's `data/` to
   grow the shared catalog directly) or in a per-user config directory otherwise.

A new row enters through `hardware add` or `service add`. Both **refuse an entry
without a source URL and a retrieval date**: the catalog is only worth trusting
if every row says where it came from.

## You do not type `add`; the agent does

The `add` command is a deterministic primitive. In normal use you never run it by
hand. When an audit meets a device or a service that is not in the catalog, the
Agent Skill has the agent notice the gap, do a single web search for the
datasheet or pricing page, and run `add` for you with the sourced values. The one
web search happens at `add` time; every read after that is offline and
deterministic.

## It is a community thing

The bundled catalog is meant to be shared and grown by everyone who uses the
tool. When an agent adds a row that is not yet in the public catalog, it can open
a pull request against the catalog repository so the next person does not have to
rediscover it.

- **The pull request is authored by the user**, through their own GitHub
  identity. The human is the contributor; the agent only prepares the change.
- **The pull request carries its evidence**: the sourced row, its source URL and
  retrieval date, and the relevant snippet of code that triggered the detection
  (which import or call showed the service in use). A reviewer can see both what
  is being added and why.
- **A maintainer reviews before merge.** Provenance is required, duplicates are
  folded, and a questionable source is challenged. The overlay means the
  contributor already has the value locally, so nothing waits on the merge.

Over time the catalog becomes a shared, sourced record of what hardware and
services cost to run, built by the people who run them.

## Freshness

Every row carries a `retrieved_date`. A row older than the staleness threshold,
or one with no date at all, is reported as stale, so a value never quietly rots:

```bash
cost-running hardware list --stale
cost-running service list --stale
```

Pricing ages fastest and will be the common reason a refresh is due.

## Commands

```bash
# Inspect the catalog.
cost-running hardware list
cost-running service list

# Add a device (provenance required). Normally the agent runs this for you.
cost-running hardware add --kind gpu --key H20 \
  --tdp-w 400 --peak-bf16-tflops 148 \
  --source-url https://www.nvidia.com/en-us/data-center/h20/ \
  --retrieved-date 2026-07-27

# Add a service (pricing source required).
cost-running service add --key acme-llm --name "Acme LLM" \
  --category llm --pricing-source-url https://acme.example/pricing \
  --retrieved-date 2026-07-27
```
