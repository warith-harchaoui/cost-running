# Gallery

Scaffold cost models produced by `cost-running audit <github-url>` on real public
repositories.  Every number is a starting point a human refines; no figure here
is a final cost claim.

Generate any of them yourself:

```bash
cost-running audit https://github.com/karpathy/nanoGPT  --output nanoGPT.yaml
cost-running audit openai/whisper                        --output whisper.yaml
cost-running audit apache/airflow                        --output airflow.yaml
```

---

## karpathy/nanoGPT

| Field | Value |
|---|---|
| **Archetype** | training |
| **Languages** | Python (15 files) |
| **Detected services** | OpenAI |

A minimal GPT training run in pure Python.  Correctly detected as a training
workload with an OpenAI dependency (used for tokenisation and comparison
baselines).

---

## openai/whisper

| Field | Value |
|---|---|
| **Archetype** | cli-tool |
| **Languages** | Python (20 files) |
| **Detected services** | OpenAI |

Whisper is a speech-recognition model served via a command-line interface
(`__main__.py` is the primary entrypoint), so `cli-tool` is the right archetype.
The OpenAI service hit reflects the model weights and the API comparison paths.

---

## tiangolo/fastapi

| Field | Value |
|---|---|
| **Archetype** | cli-tool |
| **Languages** | Python (1134 files), Bash (5), JavaScript (4) |
| **Detected services** | OpenAI (in documentation examples) |

FastAPI is a framework, not a user-space service, so the archetype reflects its
own development CLI rather than a deployment pattern.  A real project *built with*
FastAPI would have a different archetype.

---

## warith-harchaoui/cost-running *(this repo)*

| Field | Value |
|---|---|
| **Archetype** | inference |
| **Languages** | Python (38 files) |
| **Detected services** | Anthropic, OpenAI |

Self-referential audit.  `detect.py` in `infrastructure/` triggers the
`inference` archetype — a reminder that filename-based detection is a heuristic.
Anthropic and OpenAI hits come from the skills and documentation referencing their
APIs.

---

## apache/airflow

| Field | Value |
|---|---|
| **Archetype** | api-service |
| **Languages** | Python (7663), TypeScript (1021), Go (96), Bash (90), JavaScript (54), Kotlin (30), Java (10), Scala (1) |
| **Detected services** | Anthropic, AWS, Azure, Cohere, GCP, Google Gemini, OpenAI, Replicate, SendGrid, Stripe, Twilio |

Airflow integrates with virtually every cloud provider and AI service; 11 paid
services detected in one scan.  The `api-service` archetype is correct: Airflow
exposes a REST API and a web UI as its primary interface.  This is also a good
stress-test of the language detector — eight languages across 9000+ files.
