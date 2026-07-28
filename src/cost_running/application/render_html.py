"""
Render a cost model to a self-contained HTML report with inline SVG figures.

Module summary
--------------
Produces a single HTML file that can be opened without a server, shared by
e-mail, or committed next to the YAML model. All assets (CSS, fonts via system
stacks, SVG figures) are inlined; there are no external dependencies at render
time.

Design conventions (sprezzature-*)
------------------------------------
- **Colors**: sprezzature curated palette (Apple-inspired, CVD-safe by
  construction — colour is never the sole encoding channel).
- **Typography**: three-Roboto rule with system-font fallback stacks (no CDN,
  no downloaded webfonts at render time; the system fonts cover the same
  metric slots as Roboto on the user's machine).
- **Dark mode**: ``@media (prefers-color-scheme: dark)`` with CSS custom
  properties on ``:root``.
- **Accessibility**: ``role="img"`` and ``<figcaption>`` on every ``<figure>``;
  ``aria-label`` on ``<svg>``; polarity annotated on every quantitative axis.
- **No framework, no JS**: all interactivity comes from CSS alone. The SVG
  figures are static; tooltips, if added later, will be CSS-only hover.

SVG figures
-----------
Each figure is a pure Python → SVG string. No chart library is required at
runtime. The figures follow sprezzature-figures conventions:

- No top/right spine.
- Roboto Mono (or ``ui-monospace``) for numeric tick labels.
- Polarity labelled on the value axis: ``(lower is better)`` for cost, energy,
  carbon; ``(higher is better)`` for throughput (unused here, but the rule
  is applied).
- ``role="img"`` + ``aria-label`` on the ``<svg>`` root.

Author
------
Project maintainers.
"""

from __future__ import annotations

import html
from typing import Any

from .validate import normalize_scenarios

# ---------------------------------------------------------------------------
# Sprezzature palette — apple-inspired, CVD-safe
# ---------------------------------------------------------------------------

# Primary per-status colours. Chosen for WCAG AA contrast on white (#F8F8F8)
# and on the dark background (#1A1A2E). Colour is never the sole channel:
# text labels always accompany it.
_STATUS_COLOR: dict[str, str] = {
    "measured": "#007AFF",  # Blue  — Trust, Dependable
    "estimated": "#FF9500",  # Orange — Confidence, Warmth
    "placeholder": "#808080",  # Gray  — Neutral
    "TODO": "#FF3B30",  # Red   — Warning, Action needed
}
_STATUS_COLOR_DARK: dict[str, str] = {
    "measured": "#4DA3FF",
    "estimated": "#FFB340",
    "placeholder": "#AAAAAA",
    "TODO": "#FF6B60",
}
_FALLBACK_COLOR: str = "#808080"

# Dimension display names and their polarity annotation.
_DIM_META: dict[str, tuple[str, str]] = {
    "runtime_seconds": ("Runtime", "lower is better"),
    "energy_kwh": ("Energy", "lower is better"),
    "electricity_cost_usd": ("Electricity cost", "lower is better"),
    "carbon_gco2e": ("Carbon", "lower is better"),
    "water_liters": ("Water", "lower is better"),
    "external_api_cost_usd": ("External API cost", "lower is better"),
    "total_cost_usd": ("Total cost", "lower is better"),
    "total_carbon_gco2e": ("Total carbon", "lower is better"),
}


# ---------------------------------------------------------------------------
# Deployment context data — countries and instance types
# ---------------------------------------------------------------------------

# Grid carbon intensities in gCO2e/kWh, approximate IEA 2023 values.
# 0 for "unknown" means "no carbon figure will be shown".
_COUNTRY_GRID_GCO2E: dict[str, int] = {
    "unknown": 0,
    "Australia": 580,
    "Austria": 107,
    "Belgium": 149,
    "Brazil": 100,
    "Canada": 120,
    "Chile": 310,
    "China": 581,
    "Czechia": 406,
    "Denmark": 155,
    "Egypt": 450,
    "Finland": 65,
    "France": 56,
    "Germany": 385,
    "India": 708,
    "Ireland": 295,
    "Israel": 470,
    "Italy": 295,
    "Japan": 474,
    "Mexico": 450,
    "Netherlands": 290,
    "Norway": 19,
    "Poland": 773,
    "Portugal": 181,
    "Romania": 237,
    "Russia": 330,
    "Saudi Arabia": 640,
    "Singapore": 408,
    "Slovakia": 119,
    "South Africa": 800,
    "South Korea": 415,
    "Spain": 173,
    "Sweden": 13,
    "Switzerland": 39,
    "Turkey": 430,
    "UAE": 550,
    "UK": 215,
    "Ukraine": 280,
    "USA": 386,
}

# Known instance types shown in the dropdown; the scaffold default is listed first.
_INSTANCE_TYPES: list[str] = [
    "single-CPU-node",
    "single-A100-node",
    "single-H100-node",
    "single-V100-node",
    "single-T4-node",
    "single-A10G-node",
    "single-RTX-4090",
    "single-RTX-3090",
    "aws-p4d.24xlarge (8× A100)",
    "aws-p3.2xlarge (1× V100)",
    "gcp-a2-highgpu-1g (1× A100)",
    "azure-NC24ads-A100-v4 (1× A100)",
]

# ---------------------------------------------------------------------------
# SVG helper primitives
# ---------------------------------------------------------------------------


def _esc(text: Any) -> str:
    """Return an HTML/SVG-safe string."""
    return html.escape(str(text) if text is not None else "")


def _svg_open(width: int, height: int, title: str) -> str:
    """Return an opening ``<svg>`` tag with accessibility attributes."""
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" '
        f'role="img" aria-label="{_esc(title)}" '
        f'style="font-family: Roboto, ui-sans-serif, system-ui, sans-serif; overflow: visible;">'
        f"<title>{_esc(title)}</title>"
    )


def _format_value(value: float) -> str:
    """Return a compact human-readable number string.

    Uses scientific notation for very small values rather than SI prefixes
    (µ, n, p), since those prefixes have physical meaning that may not match
    the quantity's unit (e.g. "µ kWh" is misleading — the unit stays kWh).
    """
    if value == 0:
        return "0"
    abs_v = abs(value)
    if abs_v >= 1:
        return f"{value:.3g}"
    if abs_v >= 0.01:
        return f"{value:.4g}"
    return f"{value:.2e}"


# ---------------------------------------------------------------------------
# Figure 1: Cost breakdown by dimension (horizontal bar chart)
# ---------------------------------------------------------------------------


def _collect_scenario_dimensions(
    scenario: dict[str, Any],
) -> list[tuple[str, str, float, str, str | None]]:
    """Return (key, label, value, unit, status) for all numeric quantities in one scenario.

    Parameters
    ----------
    scenario : dict
        One scenario mapping from the cost model.

    Returns
    -------
    list
        Tuples of (key, label, value, unit_str, status).
    """
    rows: list[tuple[str, str, float, str, str | None]] = []

    def _add(key: str, obj: Any) -> None:
        if not isinstance(obj, dict):
            return
        value = obj.get("value")
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            return
        if value is None or value == 0:
            return
        label, _ = _DIM_META.get(key, (key.replace("_", " ").title(), ""))
        unit = str(obj.get("unit") or "")
        status = str(obj.get("status")) if obj.get("status") else None
        rows.append((key, label, float(value), unit, status))

    rt = scenario.get("runtime_seconds")
    if rt:
        _add("runtime_seconds", rt)

    compute = scenario.get("local_compute")
    if isinstance(compute, dict):
        for key in ("energy_kwh", "electricity_cost_usd", "carbon_gco2e", "water_liters"):
            if key in compute:
                _add(key, compute[key])

    ext = scenario.get("external_api_cost_usd")
    if ext:
        _add("external_api_cost_usd", ext)

    totals = scenario.get("totals")
    if isinstance(totals, dict):
        for key in ("total_cost_usd", "total_carbon_gco2e"):
            if key in totals:
                _add(key, totals[key])

    return rows


def figure_cost_breakdown(scenario: dict[str, Any]) -> str:
    """Return an SVG horizontal bar chart of all cost dimensions for one scenario.

    Parameters
    ----------
    scenario : dict
        One scenario mapping.

    Returns
    -------
    str
        Self-contained SVG string, or an empty string when the scenario has no
        numeric quantities.
    """
    rows = _collect_scenario_dimensions(scenario)
    if not rows:
        return ""

    # Layout: honesty inventory — fixed-width status swatch + value label.
    # Cross-unit normalization (e.g. seconds vs kWh vs gCO2e) is intentionally
    # avoided: bar length cannot encode relative magnitude across incomparable
    # units without misleading the reader. Instead every row shows the same
    # swatch width; status colour and the text label are the encoding channels.
    padding_left = 16
    padding_right = 16
    label_w = 180
    swatch_w = 16
    swatch_gap = 12
    value_col_x = padding_left + label_w + swatch_w + swatch_gap
    row_h = 34
    header_h = 48
    width = 620
    height = header_h + len(rows) * row_h + 8

    scenario_name = _esc(scenario.get("name", "scenario"))
    title = f"Honesty inventory — {scenario_name}"

    lines = [_svg_open(width, height, title)]
    lines.append(f'<rect width="{width}" height="{height}" fill="transparent"/>')

    lines.append(
        f'<text x="{padding_left}" y="22" '
        f'font-size="13" font-weight="600" fill="currentColor">'
        f"Honesty inventory — {scenario_name}"
        f"</text>"
    )
    lines.append(
        f'<text x="{padding_left}" y="40" '
        f'font-size="10" fill="currentColor" opacity="0.55">'
        f"Status colour encodes confidence; value and unit are always shown. "
        f"All cost and emission dimensions: lower is better."
        f"</text>"
    )

    for i, (key, label, value, unit, status) in enumerate(rows):
        y_base = header_h + i * row_h
        y_mid = y_base + row_h // 2
        color = _STATUS_COLOR.get(status or "", _FALLBACK_COLOR)
        x_swatch = padding_left + label_w

        # Alternating row background.
        if i % 2 == 0:
            lines.append(
                f'<rect x="{padding_left}" y="{y_base}" '
                f'width="{width - padding_left - padding_right}" height="{row_h}" '
                f'fill="currentColor" opacity="0.025" rx="2"/>'
            )

        # Dimension label.
        lines.append(
            f'<text x="{x_swatch - 8}" y="{y_mid + 5}" '
            f'font-size="12" text-anchor="end" fill="currentColor">'
            f"{_esc(label)}"
            f"</text>"
        )

        # Status swatch (colour + CVD-safe: also labelled in text below).
        lines.append(
            f'<rect x="{x_swatch}" y="{y_mid - 8}" '
            f'width="{swatch_w}" height="16" '
            f'fill="{color}" rx="3" opacity="0.9"/>'
        )

        # Value + unit.
        val_str = _format_value(value)
        unit_str = f" {unit}" if unit else ""
        lines.append(
            f'<text x="{value_col_x}" y="{y_mid + 5}" '
            f'font-size="12" font-family="ui-monospace, Roboto Mono, monospace" '
            f'fill="{color}">'
            f"{_esc(val_str + unit_str)}"
            f"</text>"
        )

        # Status label (text channel repeats colour channel — CVD-safe).
        status_x = value_col_x + 160
        lines.append(
            f'<text x="{status_x}" y="{y_mid + 5}" '
            f'font-size="10" fill="{color}" opacity="0.85">'
            f"{_esc(status or '')}"
            f"</text>"
        )

    lines.append("</svg>")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Figure 2: Honesty legend — status distribution across the whole model
# ---------------------------------------------------------------------------


def _count_statuses(data: dict[str, Any]) -> dict[str, int]:
    """Count the number of quantity mappings per status across the model.

    Parameters
    ----------
    data : dict
        The cost model.

    Returns
    -------
    dict
        Mapping from status label to count. Only statuses that appear are
        included.
    """
    counts: dict[str, int] = {}

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            if "value" in node and isinstance(node.get("value"), (int, float)):
                status = str(node.get("status", "unknown"))
                counts[status] = counts.get(status, 0) + 1
                return
            for child in node.values():
                walk(child)
        elif isinstance(node, list):
            for child in node:
                walk(child)

    walk(data)
    return counts


def figure_honesty_legend(data: dict[str, Any]) -> str:
    """Return an SVG horizontal stacked bar showing the status distribution.

    The figure makes the honesty mix of the model visible at a glance:
    how many quantities are ``measured``, ``estimated``, ``placeholder``,
    or ``TODO``.

    Parameters
    ----------
    data : dict
        The full cost model.

    Returns
    -------
    str
        Self-contained SVG string.
    """
    counts = _count_statuses(data)
    if not counts:
        return ""

    ordered = ["measured", "estimated", "placeholder", "TODO"]
    statuses = [s for s in ordered if counts.get(s, 0) > 0]
    statuses += [s for s in counts if s not in ordered]

    total = sum(counts.values())
    width = 560
    bar_h = 28
    label_h = 24
    legend_h = 32
    height = 16 + bar_h + label_h + legend_h + 16
    title = "Honesty status distribution"

    lines = [_svg_open(width, height, title)]
    lines.append(f'<rect width="{width}" height="{height}" fill="transparent"/>')

    # Title.
    lines.append(
        f'<text x="16" y="26" font-size="13" font-weight="600" fill="currentColor">{title}</text>'
    )

    # Stacked bar.
    x = 16
    bar_y = 36
    bar_total_w = width - 32

    for status in statuses:
        n = counts.get(status, 0)
        seg_w = (n / total) * bar_total_w
        color = _STATUS_COLOR.get(status, _FALLBACK_COLOR)
        lines.append(
            f'<rect x="{x:.1f}" y="{bar_y}" width="{seg_w:.1f}" height="{bar_h}" '
            f'fill="{color}" opacity="0.85"/>'
        )
        # Percentage label inside the bar (only when wide enough).
        if seg_w > 30:
            pct = f"{n / total * 100:.0f}%"
            lines.append(
                f'<text x="{x + seg_w / 2:.1f}" y="{bar_y + bar_h / 2 + 5:.1f}" '
                f'font-size="11" text-anchor="middle" fill="white" font-weight="600">'
                f"{_esc(pct)}"
                f"</text>"
            )
        x += seg_w

    # Legend row below the bar.
    leg_y = bar_y + bar_h + legend_h - 4
    x_leg = 16
    for status in statuses:
        n = counts.get(status, 0)
        color = _STATUS_COLOR.get(status, _FALLBACK_COLOR)
        # Colour swatch.
        lines.append(
            f'<rect x="{x_leg:.1f}" y="{leg_y - 10}" width="10" height="10" fill="{color}" rx="2"/>'
        )
        label = f"{status} ({n})"
        lines.append(
            f'<text x="{x_leg + 14:.1f}" y="{leg_y}" '
            f'font-size="11" fill="currentColor">'
            f"{_esc(label)}"
            f"</text>"
        )
        x_leg += len(label) * 7 + 24

    lines.append("</svg>")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Figure 3: Scenario comparison (grouped bar, only when >1 scenario)
# ---------------------------------------------------------------------------


def figure_scenario_comparison(
    scenarios: list[dict[str, Any]], dimension: str = "total_cost_usd"
) -> str:
    """Return an SVG bar chart comparing one dimension across scenarios.

    Parameters
    ----------
    scenarios : list of dict
        All scenario mappings from the model.
    dimension : str
        The quantity key to compare (default: ``total_cost_usd``).

    Returns
    -------
    str
        Self-contained SVG string, or empty when there is nothing to compare.
    """
    if len(scenarios) < 2:
        return ""

    values: list[tuple[str, float, str | None]] = []
    for sc in scenarios:
        name = sc.get("name", "?")
        obj = None
        if dimension == "total_cost_usd":
            obj = (sc.get("totals") or {}).get("total_cost_usd")
        elif dimension == "total_carbon_gco2e":
            obj = (sc.get("totals") or {}).get("total_carbon_gco2e")
        elif dimension == "runtime_seconds":
            obj = sc.get("runtime_seconds")
        if isinstance(obj, dict) and isinstance(obj.get("value"), (int, float)):
            values.append((str(name), float(obj["value"]), str(obj.get("status") or "")))

    if len(values) < 2:
        return ""

    max_val = max(abs(v) for _, v, _ in values) or 1.0
    dim_label, polarity = _DIM_META.get(dimension, (dimension, ""))
    title = f"Scenario comparison — {dim_label}"

    bar_group_w = 80
    padding_left = 32
    padding_right = 32
    bar_h_max = 160
    label_h = 40
    header_h = 56
    width = padding_left + len(values) * bar_group_w + padding_right
    height = header_h + bar_h_max + label_h

    lines = [_svg_open(width, height, title)]
    lines.append(f'<rect width="{width}" height="{height}" fill="transparent"/>')

    lines.append(
        f'<text x="{padding_left}" y="26" '
        f'font-size="14" font-weight="600" fill="currentColor">'
        f"{_esc(title)}"
        f"</text>"
    )
    if polarity:
        lines.append(
            f'<text x="{padding_left}" y="44" '
            f'font-size="10" fill="currentColor" opacity="0.6">'
            f"({_esc(polarity)})"
            f"</text>"
        )

    # Baseline.
    y_base = header_h + bar_h_max
    lines.append(
        f'<line x1="{padding_left}" y1="{y_base}" x2="{width - padding_right}" y2="{y_base}" '
        f'stroke="currentColor" stroke-width="1" opacity="0.2"/>'
    )

    bar_w = bar_group_w - 16
    for i, (name, value, status) in enumerate(values):
        bar_frac = abs(value) / max_val
        bar_h = int(bar_h_max * bar_frac)
        x_bar = padding_left + i * bar_group_w + 8
        y_bar = y_base - bar_h
        color = _STATUS_COLOR.get(status, _FALLBACK_COLOR)

        lines.append(
            f'<rect x="{x_bar}" y="{y_bar}" width="{bar_w}" height="{bar_h}" '
            f'fill="{color}" rx="4" opacity="0.85"/>'
        )

        # Value label above bar.
        lines.append(
            f'<text x="{x_bar + bar_w // 2}" y="{y_bar - 4}" '
            f'font-size="10" text-anchor="middle" '
            f'font-family="ui-monospace, Roboto Mono, monospace" '
            f'fill="{color}">'
            f"{_esc(_format_value(value))}"
            f"</text>"
        )

        # Scenario name below baseline.
        lines.append(
            f'<text x="{x_bar + bar_w // 2}" y="{y_base + 16}" '
            f'font-size="11" text-anchor="middle" fill="currentColor">'
            f"{_esc(name)}"
            f"</text>"
        )

    lines.append("</svg>")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# HTML page shell
# ---------------------------------------------------------------------------

_CSS = """\
/* ===== CSS custom properties — sprezzature palette ===== */
:root {
  --color-bg: #F8F8F8;
  --color-surface: #FFFFFF;
  --color-text: #1A1A1A;
  --color-text-muted: #555555;
  --color-border: #E0E0E0;
  --color-accent: #007AFF;
  --color-measured: #007AFF;
  --color-estimated: #FF9500;
  --color-placeholder: #808080;
  --color-todo: #FF3B30;
  --font-sans: Roboto, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  --font-serif: "Roboto Serif", ui-serif, Georgia, Cambria, serif;
  --font-mono: "Roboto Mono", ui-monospace, SFMono-Regular, Menlo, "Courier New", monospace;
  --radius: 8px;
  --shadow: 0 1px 4px rgba(0,0,0,0.08);
}
@media (prefers-color-scheme: dark) {
  :root {
    --color-bg: #111118;
    --color-surface: #1E1E2A;
    --color-text: #EFEFEF;
    --color-text-muted: #999999;
    --color-border: #2E2E3E;
    --color-accent: #4DA3FF;
    --color-measured: #4DA3FF;
    --color-estimated: #FFB340;
    --color-placeholder: #AAAAAA;
    --color-todo: #FF6B60;
    --shadow: 0 1px 4px rgba(0,0,0,0.4);
  }
}

/* ===== Reset & base ===== */
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
html { font-size: 16px; -webkit-text-size-adjust: 100%; }
body {
  font-family: var(--font-sans);
  background: var(--color-bg);
  color: var(--color-text);
  line-height: 1.6;
  padding: 2rem 1rem 4rem;
}

/* ===== Layout ===== */
.page { max-width: 860px; margin: 0 auto; }
header { margin-bottom: 2.5rem; border-bottom: 2px solid var(--color-accent); padding-bottom: 1rem; }
header h1 { font-size: 2rem; font-weight: 700; color: var(--color-text); }
header p { color: var(--color-text-muted); margin-top: 0.25rem; }
section { margin-bottom: 2rem; }
h2 { font-size: 1.25rem; font-weight: 600; margin-bottom: 0.75rem; color: var(--color-text); border-left: 4px solid var(--color-accent); padding-left: 0.75rem; }
h3 { font-size: 1rem; font-weight: 600; margin: 1rem 0 0.5rem; color: var(--color-text); }
p { margin-bottom: 0.5rem; }

/* ===== Cards ===== */
.card {
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius);
  padding: 1.25rem 1.5rem;
  margin-bottom: 1.25rem;
  box-shadow: var(--shadow);
}

/* ===== Key-value list ===== */
.kv-list { list-style: none; display: grid; grid-template-columns: 180px 1fr; gap: 0.25rem 0.75rem; }
.kv-list dt { font-weight: 500; color: var(--color-text-muted); font-size: 0.875rem; }
.kv-list dd { color: var(--color-text); font-size: 0.875rem; }

/* ===== Tables ===== */
table { border-collapse: collapse; width: 100%; font-size: 0.875rem; }
th { text-align: left; padding: 0.4rem 0.75rem; background: var(--color-border); font-weight: 600; }
td { padding: 0.35rem 0.75rem; border-bottom: 1px solid var(--color-border); font-family: var(--font-mono); }
tr:last-child td { border-bottom: none; }

/* ===== Status badges ===== */
.badge {
  display: inline-block;
  font-size: 0.7rem;
  font-weight: 600;
  padding: 0.1rem 0.45rem;
  border-radius: 4px;
  letter-spacing: 0.02em;
  text-transform: uppercase;
}
.badge-measured   { background: #CCE4FF; color: #004999; }
.badge-estimated  { background: #FFF0CC; color: #7A4800; }
.badge-placeholder{ background: #EEEEEE; color: #444444; }
.badge-todo       { background: #FFD8D6; color: #8B0000; }
@media (prefers-color-scheme: dark) {
  .badge-measured   { background: #003366; color: #7FC3FF; }
  .badge-estimated  { background: #5A3400; color: #FFD080; }
  .badge-placeholder{ background: #333333; color: #BBBBBB; }
  .badge-todo       { background: #5C0000; color: #FF9999; }
}

/* ===== Figures ===== */
figure {
  margin: 1.25rem 0;
  padding: 1rem;
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius);
  box-shadow: var(--shadow);
  overflow-x: auto;
}
figcaption {
  font-size: 0.8rem;
  color: var(--color-text-muted);
  margin-top: 0.5rem;
  font-style: italic;
}
figure svg { display: block; color: var(--color-text); }

/* ===== Exclusions ===== */
.exclusions ul { list-style: disc; padding-left: 1.5rem; font-size: 0.875rem; color: var(--color-text-muted); }
.exclusions li { margin-bottom: 0.25rem; }

/* ===== Footer ===== */
footer { margin-top: 3rem; padding-top: 1rem; border-top: 1px solid var(--color-border); font-size: 0.8rem; color: var(--color-text-muted); }

/* ===== Site nav ===== */
.site-nav {
  position: sticky;
  top: 0;
  z-index: 100;
  background: var(--color-surface);
  border-bottom: 1px solid var(--color-border);
  padding: 0 1.5rem;
  display: flex;
  align-items: center;
  justify-content: space-between;
  height: 52px;
  box-shadow: 0 1px 4px rgba(0,0,0,0.07);
}
.site-nav-brand {
  font-size: 0.9375rem;
  font-weight: 700;
  color: var(--color-accent);
  text-decoration: none;
  letter-spacing: -0.01em;
}
.site-nav-brand span { color: var(--color-text); font-weight: 400; }
.site-nav-links { display: flex; align-items: center; gap: 1.25rem; list-style: none; }
.site-nav-links a {
  font-size: 0.8125rem;
  color: var(--color-text-muted);
  text-decoration: none;
}
.site-nav-links a:hover { color: var(--color-accent); }
.btn-theme {
  background: none;
  border: 1px solid var(--color-border);
  border-radius: 6px;
  padding: 0.25rem 0.55rem;
  font-size: 1rem;
  cursor: pointer;
  color: var(--color-text);
  line-height: 1;
  transition: border-color 0.15s;
}
.btn-theme:hover { border-color: var(--color-accent); }
@media print { .site-nav { display: none; } }

/* ===== Deployment dropdowns ===== */
.deploy-select {
  font-family: var(--font-mono);
  font-size: 0.875rem;
  color: var(--color-text);
  background: var(--color-bg);
  border: 1px solid var(--color-border);
  border-radius: 5px;
  padding: 0.2rem 0.5rem;
  cursor: pointer;
  outline: none;
  transition: border-color 0.15s;
}
.deploy-select:focus { border-color: var(--color-accent); }
.deploy-hint {
  font-size: 0.8rem;
  color: var(--color-text-muted);
  margin-left: 0.5rem;
  font-style: italic;
}
@media print { .deploy-select { border: none; appearance: none; -webkit-appearance: none; } }

/* ===== Report toolbar ===== */
.report-toolbar {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  flex-wrap: wrap;
  margin-bottom: 1.75rem;
}
.report-toolbar a {
  font-size: 0.875rem;
  color: var(--color-accent);
  text-decoration: none;
}
.report-toolbar a:hover { text-decoration: underline; }
.btn-pdf {
  display: inline-flex;
  align-items: center;
  gap: 0.35rem;
  padding: 0.45rem 1rem;
  background: var(--color-accent);
  color: #fff;
  font-size: 0.875rem;
  font-weight: 600;
  border: none;
  border-radius: 6px;
  cursor: pointer;
  font-family: var(--font-sans);
  transition: opacity 0.15s;
}
.btn-pdf:hover { opacity: 0.85; }

/* ===== Theme toggle overrides (JS sets data-theme on <html>) ===== */
html[data-theme="light"] {
  --color-bg: #F8F8F8; --color-surface: #FFFFFF; --color-text: #1A1A1A;
  --color-text-muted: #555555; --color-border: #E0E0E0; --color-accent: #007AFF;
  --color-measured: #007AFF; --color-estimated: #FF9500;
  --color-placeholder: #808080; --color-todo: #FF3B30;
  --shadow: 0 1px 4px rgba(0,0,0,0.08);
}
html[data-theme="dark"] {
  --color-bg: #111118; --color-surface: #1E1E2A; --color-text: #EFEFEF;
  --color-text-muted: #999999; --color-border: #2E2E3E; --color-accent: #4DA3FF;
  --color-measured: #4DA3FF; --color-estimated: #FFB340;
  --color-placeholder: #AAAAAA; --color-todo: #FF6B60;
  --shadow: 0 1px 4px rgba(0,0,0,0.4);
}
html[data-theme="dark"] .badge-measured   { background: #003366; color: #7FC3FF; }
html[data-theme="dark"] .badge-estimated  { background: #5A3400; color: #FFD080; }
html[data-theme="dark"] .badge-placeholder{ background: #333333; color: #BBBBBB; }
html[data-theme="dark"] .badge-todo       { background: #5C0000; color: #FF9999; }

/* ===== Print / PDF ===== */
@media print {
  .report-toolbar { display: none; }
  body { background: #fff; color: #000; padding: 0; }
  .page { max-width: 100%; }
  .card, figure { box-shadow: none; border: 1px solid #ccc; break-inside: avoid; }
  header { border-bottom-color: #000; }
  h2 { border-left-color: #000; }
  .badge-measured   { background: #e0ecff; color: #00308F; }
  .badge-estimated  { background: #fff3cd; color: #664d00; }
  .badge-placeholder{ background: #eee; color: #333; }
  .badge-todo       { background: #ffe4e3; color: #8B0000; }
  footer { margin-top: 1.5rem; }
}
"""


def _badge(status: str | None) -> str:
    """Return an HTML status badge span."""
    if not status:
        return ""
    cls = {
        "measured": "badge-measured",
        "estimated": "badge-estimated",
        "placeholder": "badge-placeholder",
        "TODO": "badge-todo",
    }.get(status, "badge-placeholder")
    return f'<span class="badge {cls}">{_esc(status)}</span>'


def _quantity_cell(obj: Any) -> str:
    """Return an HTML table cell content for a quantity."""
    if not isinstance(obj, dict):
        return _esc(obj)
    value = obj.get("value")
    unit = obj.get("unit")
    status = obj.get("status")
    text = _esc(value)
    if unit:
        text = f"{text}&thinsp;{_esc(unit)}"
    if status:
        text = f"{text}&nbsp;{_badge(status)}"
    return text


def _render_header(data: dict[str, Any]) -> str:
    parts = ["<header>", f"<h1>{_esc(data.get('project_name', 'Cost of running'))}</h1>"]
    metas = []
    if data.get("date_updated"):
        metas.append(f"Last updated: {_esc(data['date_updated'])}")
    if data.get("schema_version"):
        metas.append(f"Schema: v{_esc(data['schema_version'])}")
    if metas:
        parts.append(f"<p>{' &middot; '.join(metas)}</p>")
    parts.append("</header>")
    return "\n".join(parts)


def _render_unit_section(data: dict[str, Any]) -> str:
    cuow = data.get("canonical_unit_of_work")
    if not isinstance(cuow, dict):
        return ""
    parts = [
        '<section class="unit">',
        '<h2 data-i18n="h2.unit">Canonical unit of work</h2>',
        '<div class="card">',
    ]
    parts.append('<dl class="kv-list">')
    parts.append(
        f"<dt>Unit</dt><dd>{_esc(cuow.get('name'))}&nbsp;{_badge(cuow.get('status'))}</dd>"
    )
    if cuow.get("description"):
        parts.append(f"<dt>Description</dt><dd>{_esc(cuow['description'])}</dd>")
    parts.append("</dl>")
    out_of_scope = cuow.get("out_of_scope")
    if isinstance(out_of_scope, list) and out_of_scope:
        parts.append(
            '<p style="margin-top:0.75rem;font-size:0.85rem;font-weight:600;">Out of scope</p>'
        )
        parts.append("<ul>")
        for item in out_of_scope:
            parts.append(f"<li>{_esc(item)}</li>")
        parts.append("</ul>")
    parts.append("</div>")
    parts.append("</section>")
    return "\n".join(parts)


def _country_select(current: str) -> str:
    """Return a <select> for the country field with carbon-intensity hint."""
    options = []
    for name in sorted(_COUNTRY_GRID_GCO2E):
        selected = " selected" if name == current else ""
        options.append(f'<option value="{_esc(name)}"{selected}>{_esc(name)}</option>')
    # If the current value isn't in the list, add it as a custom entry.
    if current not in _COUNTRY_GRID_GCO2E:
        options.insert(0, f'<option value="{_esc(current)}" selected>{_esc(current)}</option>')
    intensity = _COUNTRY_GRID_GCO2E.get(current, 0)
    hint = f"≈ {intensity} gCO₂e/kWh" if intensity else "set a country to see grid intensity"
    return (
        '<select id="cr-country" class="deploy-select" onchange="crCountryChange(this)">'
        + "".join(options)
        + f'</select><span id="cr-country-hint" class="deploy-hint">{hint}</span>'
    )


def _instance_select(current: str) -> str:
    """Return a <select> for the instance_type field."""
    options = []
    found = False
    for name in _INSTANCE_TYPES:
        selected = " selected" if name == current else ""
        if name == current:
            found = True
        options.append(f'<option value="{_esc(name)}"{selected}>{_esc(name)}</option>')
    if not found:
        options.insert(0, f'<option value="{_esc(current)}" selected>{_esc(current)}</option>')
    return '<select id="cr-instance" class="deploy-select">' + "".join(options) + "</select>"


def _deployment_js() -> str:
    """Return the inline JS that powers the country carbon-intensity hint."""
    data_js = "{" + ",".join(f'"{k}":{v}' for k, v in _COUNTRY_GRID_GCO2E.items()) + "}"
    return f"""<script>
var _CR_GRID={data_js};
function crCountryChange(sel){{
  var v=_CR_GRID[sel.value];
  var hint=document.getElementById('cr-country-hint');
  hint.textContent=v?'\\u2248 '+v+' gCO₂e/kWh':'set a country to see grid intensity';
}}
</script>"""


def _render_deployment_section(data: dict[str, Any]) -> str:
    deployment = data.get("deployment")
    if not isinstance(deployment, dict) or not deployment:
        return ""
    parts = [
        '<section class="deployment">',
        '<h2 data-i18n="h2.deployment">Deployment</h2>',
        '<div class="card">',
    ]
    parts.append('<dl class="kv-list">')
    for key, value in deployment.items():
        if key == "country":
            parts.append(f"<dt>{_esc(key)}</dt><dd>{_country_select(str(value))}</dd>")
        elif key == "instance_type":
            parts.append(f"<dt>{_esc(key)}</dt><dd>{_instance_select(str(value))}</dd>")
        else:
            parts.append(f"<dt>{_esc(key)}</dt><dd>{_esc(value)}</dd>")
    parts.append("</dl></div>")
    parts.append(_deployment_js())
    parts.append("</section>")
    return "\n".join(parts)


def _render_scenario_section(scenario: dict[str, Any]) -> str:
    name = _esc(scenario.get("name", "scenario"))
    parts = [f"<h3>Scenario: {name}</h3>"]
    if scenario.get("description"):
        parts.append(
            f'<p style="color:var(--color-text-muted);font-size:0.9rem;">{_esc(scenario["description"])}</p>'
        )

    # Figure: cost breakdown.
    svg = figure_cost_breakdown(scenario)
    if svg:
        parts.append(
            f'<figure role="img" aria-label="Cost breakdown for {name}">'
            f"{svg}"
            f"<figcaption>Cost breakdown for scenario <em>{name}</em>. "
            f"Bar colour encodes honesty status; text labels repeat it. "
            f"All cost and emission dimensions: lower is better.</figcaption>"
            f"</figure>"
        )

    # Metrics table.
    parts.append('<div class="card"><table>')
    parts.append("<thead><tr><th>Metric</th><th>Value</th></tr></thead><tbody>")

    def row(label: str, obj: Any) -> str:
        return f"<tr><td>{_esc(label)}</td><td>{_quantity_cell(obj)}</td></tr>"

    parts.append(row("Runtime", scenario.get("runtime_seconds")))
    compute = scenario.get("local_compute")
    if isinstance(compute, dict):
        for key, label in (
            ("energy_kwh", "Energy"),
            ("electricity_cost_usd", "Electricity cost"),
            ("carbon_gco2e", "Carbon"),
            ("water_liters", "Water"),
        ):
            if key in compute:
                parts.append(row(label, compute[key]))
    if "external_api_cost_usd" in scenario:
        parts.append(row("External API cost", scenario["external_api_cost_usd"]))
    totals = scenario.get("totals")
    if isinstance(totals, dict):
        if "total_cost_usd" in totals:
            parts.append(row("<strong>Total cost</strong>", totals["total_cost_usd"]))
        if "total_carbon_gco2e" in totals:
            parts.append(row("<strong>Total carbon</strong>", totals["total_carbon_gco2e"]))

    parts.append("</tbody></table></div>")
    return "\n".join(parts)


def _render_scenarios_section(data: dict[str, Any]) -> str:
    scenarios = normalize_scenarios(data)
    if not scenarios:
        return ""
    parts = ['<section class="scenarios">', '<h2 data-i18n="h2.scenarios">Scenarios</h2>']
    for sc in scenarios:
        parts.append(_render_scenario_section(sc))
    if len(scenarios) > 1:
        svg = figure_scenario_comparison(scenarios)
        if svg:
            parts.append(
                f'<figure role="img" aria-label="Scenario comparison">'
                f"{svg}"
                f"<figcaption>Scenario comparison — total cost across all scenarios. "
                f"Lower is better.</figcaption>"
                f"</figure>"
            )
    parts.append("</section>")
    return "\n".join(parts)


def _render_honesty_section(data: dict[str, Any]) -> str:
    svg = figure_honesty_legend(data)
    if not svg:
        return ""
    parts = [
        '<section class="honesty">',
        '<h2 data-i18n="h2.honesty">Honesty summary</h2>',
        f'<figure role="img" aria-label="Honesty status distribution">'
        f"{svg}"
        f'<figcaption data-i18n="honesty.caption">Distribution of honesty labels across all numeric quantities in this model. '
        f"<em>measured</em> = real observation; "
        f"<em>estimated</em> = computed from sourced assumptions; "
        f"<em>placeholder</em> = structural stand-in; "
        f"<em>TODO</em> = must be filled before the model can be trusted.</figcaption>"
        f"</figure>",
        "</section>",
    ]
    return "\n".join(parts)


def _render_exclusions_section(data: dict[str, Any]) -> str:
    exclusions = data.get("exclusions")
    if not isinstance(exclusions, list) or not exclusions:
        return ""
    parts = [
        '<section class="exclusions">',
        '<h2 data-i18n="h2.exclusions">Exclusions</h2>',
        '<div class="card">',
    ]
    parts.append("<ul>")
    for item in exclusions:
        parts.append(f"<li>{_esc(item)}</li>")
    parts.append("</ul></div></section>")
    return "\n".join(parts)


_THEME_JS = """<script>
(function(){
  /* ---- theme ---- */
  var TICONS={light:'🌞',dark:'🌛'};
  function applyTheme(t){
    document.documentElement.setAttribute('data-theme',t);
    var btn=document.getElementById('cr-theme-btn');
    if(btn) btn.textContent=t==='dark'?TICONS.light:TICONS.dark;
    try{localStorage.setItem('cr-theme',t);}catch(e){}
  }
  function toggleTheme(){
    var cur=document.documentElement.getAttribute('data-theme')||
      (window.matchMedia('(prefers-color-scheme: dark)').matches?'dark':'light');
    applyTheme(cur==='dark'?'light':'dark');
  }
  window.crToggleTheme=toggleTheme;
  var savedTheme;
  try{savedTheme=localStorage.getItem('cr-theme');}catch(e){}
  if(savedTheme) applyTheme(savedTheme);

  /* ---- i18n ---- */
  var T={
    en:{
      'nav.gallery':'← Gallery','nav.github':'⭐️ on GitHub','nav.pdf':'⬇ Save as PDF',
      'h2.honesty':'Honesty summary','h2.unit':'Canonical unit of work',
      'h2.deployment':'Deployment','h2.scenarios':'Scenarios','h2.exclusions':'Exclusions',
      'honesty.caption':'Distribution of honesty labels across all numeric quantities in this model. <em>measured</em> = real observation; <em>estimated</em> = computed from sourced assumptions; <em>placeholder</em> = structural stand-in; <em>TODO</em> = must be filled before the model can be trusted.',
      'footer':'Generated by <code>cost-running</code> from the YAML cost model. Edit the YAML, then re-render.',
      'deploy.hint.none':'set a country to see grid intensity',
      'scenario.caption.suffix':'Bar colour encodes honesty status; text labels repeat it. All cost and emission dimensions: lower is better.'
    },
    fr:{
      'nav.gallery':'← Galerie','nav.github':'⭐️ sur GitHub','nav.pdf':'⬇ Enregistrer en PDF',
      'h2.honesty':'Résumé d\'honnêteté','h2.unit':'Unité de travail canonique',
      'h2.deployment':'Déploiement','h2.scenarios':'Scénarios','h2.exclusions':'Exclusions',
      'honesty.caption':'Distribution des statuts d\'honnêteté sur toutes les quantités numériques du modèle. <em>measured</em> = observation réelle ; <em>estimated</em> = calculé à partir d\'hypothèses sourcées ; <em>placeholder</em> = espace réservé structurel ; <em>TODO</em> = doit être rempli avant que le modèle soit fiable.',
      'footer':'Généré par <code>cost-running</code> depuis le modèle YAML. Modifiez le YAML, puis régénérez.',
      'deploy.hint.none':'choisissez un pays pour voir l\'intensité carbone',
      'scenario.caption.suffix':'La couleur encode le statut d\'honnêteté ; les étiquettes textuelles le répètent. Toutes les dimensions de coût et d\'émission : plus bas = mieux.'
    }
  };
  var _lang='en';
  function applyLang(l){
    _lang=l;
    var d=T[l]||T.en;
    document.querySelectorAll('[data-i18n]').forEach(function(el){
      var k=el.getAttribute('data-i18n');
      if(d[k]!==undefined) el.innerHTML=d[k];
    });
    var btn=document.getElementById('cr-lang-btn');
    if(btn) btn.textContent=l==='fr'?'🇬🇧':'🇫🇷';
    try{localStorage.setItem('cr-lang',l);}catch(e){}
  }
  function toggleLang(){ applyLang(_lang==='fr'?'en':'fr'); }
  window.crToggleLang=toggleLang;
  var savedLang;
  try{savedLang=localStorage.getItem('cr-lang');}catch(e){}
  if(savedLang&&T[savedLang]) applyLang(savedLang);
})();
</script>"""


def _render_nav(back_url: str | None = None) -> str:
    """Return the sticky site nav consistent with the project website."""
    gallery_href = (
        _esc(back_url) if back_url else "https://github.com/warith-harchaoui/cost-running"
    )
    return (
        '<nav class="site-nav" aria-label="Site navigation">'
        '<a class="site-nav-brand" href="https://github.com/warith-harchaoui/cost-running">'
        "cost<span>-running</span></a>"
        '<ul class="site-nav-links">'
        f'<li><a href="{gallery_href}" data-i18n="nav.gallery">← Gallery</a></li>'
        '<li><a href="https://github.com/warith-harchaoui/cost-running" data-i18n="nav.github">⭐️ on GitHub</a></li>'
        '<li><button id="cr-lang-btn" class="btn-theme" onclick="crToggleLang()" '
        'aria-label="Toggle language">🇫🇷</button></li>'
        '<li><button id="cr-theme-btn" class="btn-theme" onclick="crToggleTheme()" '
        'aria-label="Toggle dark/light theme">🌛</button></li>'
        "</ul>"
        "</nav>"
    )


def _render_toolbar(back_url: str | None = None) -> str:
    """Return the report toolbar (PDF button only — back link is in the nav)."""
    return (
        '<div class="report-toolbar">'
        '<button class="btn-pdf" onclick="window.print()" data-i18n="nav.pdf">⬇ Save as PDF</button>'
        "</div>"
    )


def render_html(data: dict[str, Any], back_url: str | None = None) -> str:
    """Render a cost model to a self-contained HTML report.

    Parameters
    ----------
    data : dict
        The parsed cost model.

    Returns
    -------
    str
        A complete HTML5 document with inline CSS and SVG figures, requiring no
        external assets.

    Examples
    --------
    >>> report = render_html({
    ...     "date_updated": "2026-07-27",
    ...     "canonical_unit_of_work": {"name": "one run", "status": "estimated"},
    ...     "deployment": {"provider": "local"},
    ...     "scenario": {"name": "default",
    ...                  "runtime_seconds": {"value": 1.0, "status": "measured"}},
    ... })
    >>> "<!DOCTYPE html>" in report
    True
    >>> "Cost of running" in report
    True
    """
    body_parts = [
        _render_toolbar(back_url),
        _render_header(data),
        _render_honesty_section(data),
        _render_unit_section(data),
        _render_deployment_section(data),
        _render_scenarios_section(data),
        _render_exclusions_section(data),
    ]
    body = "\n".join(p for p in body_parts if p)

    project_name = _esc(data.get("project_name", "Cost of running"))
    date_updated = _esc(data.get("date_updated", ""))

    nav = _render_nav(back_url)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="description" content="Cost-of-running report — {date_updated}">
  <title>{project_name} — cost report</title>
  <style>
{_CSS}
  </style>
{_THEME_JS}
</head>
<body>
{nav}
<div class="page">
{body}
<footer>
  <p data-i18n="footer">Generated by <code>cost-running</code> from the YAML cost model. Edit the YAML, then re-render.</p>
</footer>
</div>
</body>
</html>
"""
