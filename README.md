# data-onboarding

The first week of a Forward Deployed Engineer engagement, as a tool: a customer hands over messy
exports from their CRM, billing and support systems, and you turn them into one clean model they can
trust. This repo ingests CSV, Excel and a JSON API, profiles every column, proposes a mapping to a
unified schema (rules first, an LLM for the rest, validators on everything, a human confirms),
quarantines bad rows with reasons, resolves duplicate customers across systems, and serves a small
dashboard.

All data is synthetic (fixed seed) with a hidden ground truth, so every accuracy number can be
checked. Measured results are in [RESULTS.md](RESULTS.md); the customer-facing summary is
[CUSTOMER_BRIEF.md](CUSTOMER_BRIEF.md); interview prep per milestone is
[docs/INTERVIEW_NOTES.md](docs/INTERVIEW_NOTES.md).

## Architecture

```
 CRM CSV ─┐                      ┌─ rules (synonyms, tokens, patterns) ─┐
 Billing  ├─ connectors ─ profile ┤                                      ├─ validators ─ confirm screen ─ mapping store
 .xlsx    │  (raw, no coercion)   └─ LLM for unresolved columns only ───┘   (React)       (keyed by column fingerprint)
 Support ─┘
 JSON API          │
                   ▼  (confirmed mapping only)
            normalize values ─ quality checks ─ quarantine (with reasons)
                   │
                   ▼
   unified model: customer_records, accounts, invoices, tickets
                   │
                   ▼
   entity resolution: blocking ─ rapidfuzz features ─ logistic regression ─ threshold ─ clusters
                   │                                                            └─ merge_history
                   ▼
   DuckDB (data/onboard.duckdb) ─ analytics views ─ FastAPI :8003 ─ React dashboard :5173
```

| Piece | File |
|---|---|
| Synthetic sources + hidden ground truth | `src/onboard/synth.py` |
| Mock support JSON API (paginated) | `src/onboard/mock_api.py` |
| Connectors (CSV, Excel, JSON API) | `src/onboard/connectors.py`, `sources.yaml` |
| Profiling (type, nulls, distinct, samples, pattern class) | `src/onboard/profiling.py`, `parsing.py` |
| Unified schema | `src/onboard/schema.py` |
| Rules, validators, LLM, engine, mapping store | `src/onboard/mapping/` |
| Normalization, checks, quarantine, model | `transform.py`, `quality.py`, `model.py` |
| Entity resolution | `src/onboard/er.py`, `config/er_model.json` |
| Pipeline into DuckDB | `src/onboard/pipeline.py` |
| Metrics views, API | `src/onboard/analytics.py`, `src/onboard/api.py` |
| Dashboard (Overview + Mapping review) | `dashboard/` |
| Evaluations (the only code that reads ground truth) | `eval/` |
| Benchmarks, public dataset fetch | `scripts/` |

Ground truth lives in `data/ground_truth/` and `data/ground_truth_unseen/`. Only `eval/` reads it;
`tests/test_no_ground_truth_leak.py` fails if any pipeline module mentions it.

## Run it

Requirements: macOS or Linux, [uv](https://docs.astral.sh/uv/), Node 24. No Docker, no database server.

```bash
uv sync
uv run onboard generate                 # ~21K customer records + unseen ERP source, seed 42
uv run onboard profile                  # see what each column looks like
uv run onboard suggest                  # rules-only mapping suggestions (free)

# confirm mappings: either in the dashboard (below), or simulate the human step from ground truth
uv run python eval/seed_confirmed_mappings.py

uv run onboard run                      # pipeline -> data/onboard.duckdb
uv run uvicorn onboard.api:app --port 8003
cd dashboard && npm install && npm run dev    # http://localhost:5173
```

Tests and lint: `uv run pytest -q` and `uv run ruff check .`. Dashboard build: `cd dashboard && npm run build`.

### LLM mapping (paid, opt-in)

The default is `LLM_PROVIDER=none` (rules only). `LLM_PROVIDER=fake` is an offline stand-in for
demos and tests; its output is not a measurement. The real call needs `LLM_PROVIDER=anthropic`,
`ANTHROPIC_API_KEY`, and two explicit steps:

```bash
export LLM_PROVIDER=anthropic ANTHROPIC_API_KEY=...      # model: LLM_MODEL, default claude-haiku-5-5
uv run onboard suggest --dry-run      # prints a token + dollar estimate, makes no call
uv run onboard suggest --llm --yes    # makes the call; prints spend from response.usage
```

Only column names, profile stats and up to 3 sample values per unresolved column are sent; samples
of email and phone columns are replaced by their shape (`aaaa@aaaa.aaa`). The dry-run estimate for
the three sources is about $0.0006 (prices set in `src/onboard/config.py`).

### Public dataset (optional realism check)

`uv run python scripts/fetch_public_tickets.py` pulls up to 2,000 rows of the Hugging Face dataset
`Tobi-Bueck/customer-support-tickets` through the public datasets-server API into `data/public/`
(gitignored). **Licence: not verified here. Read the dataset card
(https://huggingface.co/datasets/Tobi-Bueck/customer-support-tickets) before using it.** Then
`uv run onboard profile --manifest eval/public_source.yaml` and
`uv run onboard suggest --manifest eval/public_source.yaml`. The pipeline does not need it.

## What Chaman must do next (in order)

1. **Read and run it.** `uv sync`, generate, run the tests, start the API and dashboard, click
   through both tabs. Read `docs/INTERVIEW_NOTES.md` and do the "explain or modify yourself" item per milestone.
2. **API key.** Put `ANTHROPIC_API_KEY` in your shell or a local `.env` (copy `.env.example`; never commit it).
3. **Paid mapping run.** `LLM_PROVIDER=anthropic uv run python eval/mapping_eval.py --mode llm`
   (prints the estimate), then add `--yes`. Copy accuracy, LLM rejections and spend into RESULTS.md.
4. **Onboarding timing test + hand-labeling.** Follow `eval/ONBOARDING_TIMING.md` exactly (by hand
   first, then with the tool a day later, then hand-label `eval/hand_labeled_unseen_mapping.csv`, then score).
5. **Public dataset (optional).** Check the licence, run `scripts/fetch_public_tickets.py`, note how
   rules + LLM did on it.
6. **Push.** `gh repo create byteKumar/data-onboarding --public --source . --push`, then confirm CI is green
   and fill "CI passing?" in RESULTS.md.
7. **Demo video (3 min).** Problem -> messy files -> mapping review (fix one LLM suggestion, watch a
   validator reject an impossible one) -> pipeline run -> dashboard -> RESULTS.md numbers.
8. Fill the remaining blanks in RESULTS.md and CUSTOMER_BRIEF.md.
