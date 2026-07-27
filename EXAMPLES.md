# Examples

A runnable cookbook. Every snippet works against the current version (0.1.0):
the shared core plus the `init`, `validate`, and `render` command-line verbs.

## 1. Scaffold, validate, and render a report

```bash
# Write a starter model (the annotated "full" template).
cost-running init --template full --output cost_of_running.yaml

# Check it against the schema and the honesty rules.
cost-running validate cost_of_running.yaml
# Validation passed (0 warning(s)).

# Produce the human-readable Markdown report.
cost-running render cost_of_running.yaml --output cost_of_running.md
# Wrote cost_of_running.md
```

The report is the human-readable companion to the YAML. Every number in it
carries its status, so a reader sees which values are measured, which are
estimated, and which still wait on a human.

## 2. Render to standard output and pipe it

The report is the command's deliberate result, so it goes to standard output and
can be redirected or piped.

```bash
cost-running render cost_of_running.yaml > report.md
cost-running render cost_of_running.yaml | less
```

## 3. Use the library directly

```python
from cost_running import load_model, validate_model, render_markdown, write_text

# Load a model from disk.
model = load_model("cost_of_running.yaml")

# Validate it. The result is data, not a printed message.
result = validate_model(model)
print(result.is_valid())          # True
print(len(result.warnings))       # 0

# Render the report and write it next to the model.
report = render_markdown(model)
write_text("cost_of_running.md", report)
```

## 4. Register an extra dimension

cost-running reports the canonical five dimensions by default (money, time,
energy, carbon, water). A team that tracks something else registers it once.

```python
from cost_running import Dimension, DimensionRegistry

registry = DimensionRegistry()                      # starts from the canonical five
registry.register(
    Dimension(
        key="egress",
        label="Network egress",
        unit="GB",
        description="Bytes leaving the deployment boundary per unit of work.",
    )
)
print(registry.keys())
# ('money', 'time', 'energy', 'carbon', 'water', 'egress')
```

## 5. Read a value's honesty

```python
from cost_running import Quantity, weakest

price = Quantity.from_mapping(
    {"value": 0.28, "unit": "USD/kWh", "status": "estimated"}
)
print(price.status)               # estimated

# A value derived from a measured runtime and an estimated price is, at best,
# estimated. That is the weakest-link rule.
print(weakest("measured", "estimated"))   # estimated
```

## Running the tests

```bash
python -m pip install -r requirements-dev.txt
python -m pytest
```
