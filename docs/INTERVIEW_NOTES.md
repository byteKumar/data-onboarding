# Interview notes, per milestone

For each milestone: what was built, why, the trade-offs, and one thing Chaman should explain or
change himself before calling this his project. Do those "you" items; they are what make the
difference between "I ran it" and "I built it" in an interview.

## 1. Sources and profiling (`synth.py`, `connectors.py`, `mock_api.py`, `profiling.py`, `parsing.py`)

**Built.** A seeded generator for ~10.7K true customers rendered into three exports: CRM CSV, billing
Excel (two sheets), support tickets served by a paginated mock JSON API. Messiness is deliberate:
typos, nicknames, "Last, First", different ID formats, three date conventions, EUR/GBP formatting,
Excel ZIPs without leading zeros, within- and cross-system duplicates, households (added in M5), and
rows that must be quarantined. Ground truth for mappings and clusters is written to a separate folder.
The profiler gives each column a dtype, null rate, distinct count, samples and one pattern class.

**Why.** Without a hidden ground truth, "accuracy" is a guess. Connectors return raw values with no
type coercion so the profiler sees exactly what the customer sent (pandas would happily turn
`02139` into `2139`, which is the bug we want to catch).

**Trade-offs.** Synthetic data is cleaner than real data and was written by the same person as the
rules (bias, see M2). Pattern classification is ordered and threshold-based (80% email, 80% date,
...), which is simple and explainable but brittle at the edges (see M2 and M7 surprises). Profiling
samples 5,000 values per column for speed; null rate and distinct counts use the full column.

**You:** explain why date format is inferred per column (`infer_date_format`) instead of per value,
using `03/05/2024` as the example.

## 2. Rule-based mapping (`mapping/rules.py`, `validators.py`, `engine.py`, `eval/mapping_eval.py`)

**Built.** Three rule tiers: exact synonym of the normalized name (0.95), synonym after dropping
qualifiers like "requester"/"billing" (0.85), name tokens (0.45-0.75), value pattern for email/phone
(0.55-0.6). Entity-aware placeholders: `id` in a ticket table means `ticket_ref`, `created` in a
customer table means `signup_date`. Every proposal goes through validators (entity allowed, pattern
compatible, category cardinality, key uniqueness), and one column per target field (highest confidence wins).

**Measured.** 47/49 (95.9%) on the known sources, 5/15 (33.3%) on the unseen ERP source.

**Trade-offs.** Rules are free, deterministic and explainable, but only as good as the vocabulary.
The 95.9% is inflated because the rules author also named the columns; the unseen source is the
honest number. Counting "unresolved" as "ignore" is generous when the truth is ignore; the eval
reports unresolved columns separately so nobody is misled.

**You:** fix the bug the unseen source exposed: a 10-digit zero-padded customer number (`KUNNR`)
passes `PHONE_RE`. Decide on a fix (e.g. reject all-digit values with leading zeros, or require a
non-trivial distinct count of area codes), add a test, and be ready to say why you did not re-run
the measurement to "improve" the reported number (or re-run it and report both, labelled).

## 3. LLM-assisted mapping and confirm screen (`mapping/llm.py`, `store.py`, `service.py`, `api.py`, `dashboard/src/pages/Mapping.tsx`)

**Built.** A `MappingLLM` interface with `FakeLLM` (deterministic, offline) and `AnthropicLLM`
(official SDK, JSON-schema structured output with `additionalProperties: false`, checks
`stop_reason` for refusal/max_tokens before parsing, cost from `response.usage`). Only unresolved
columns are sent, with profiles and 3 samples; email/phone samples are masked to their shape. The
paid client refuses to run unless `allow_paid=True`, which the CLI sets only with `--yes`; `--dry-run`
prints a token and dollar estimate first. LLM proposals go through the same validators plus a conflict
check against fields rules already took. Confirmed mappings are stored per fingerprint
(sha256 of entity + sorted column names) with history. The React page shows column, profile, proposed
field, confidence, rule/LLM/stored, validator status and refusals; LLM rows start unaccepted.

**Why.** The LLM handles the long tail (SAP names, odd abbreviations) where rules give up, but it can
be confidently wrong, so it never writes to the model directly: validators + a human sit in between.

**Trade-offs.** The fingerprint ignores column order (good) but any renamed column forces a full
review (safe, sometimes annoying; a per-column reuse would be the next step). Token estimate is
chars/4, not a tokenizer: fine for a go/no-go on a sub-cent call. Not measured yet: real LLM accuracy.

**You:** run the paid eval yourself (README step 3) and explain one suggestion the validators
rejected, or, if none was rejected, explain which validator would have caught a plausible LLM mistake.

## 4. Unified model and checks (`transform.py`, `quality.py`, `model.py`, `pipeline.py`)

**Built.** Value normalization per target field (names, emails, phones, ZIPs, money with locale
decimal marks and FX to USD, column-level date parsing, status canonicalization). Two severities:
`quarantine` (row held back, with check name, detail and raw JSON) and `fixed` (one bad value nulled,
row kept). Checks: required fields, duplicate keys, ranges (amount, CSAT, dates), due-before-issue,
referential integrity (invoice -> account, ticket -> requester). The pipeline refuses to run a table
whose column set has no confirmed mapping.

**Trade-offs.** A ticket with a CSAT of 10 is quarantined whole, which removes a possibly real open
ticket from the dashboard; nulling just the score would be the alternative. Missing currency defaults
to USD. Fixed FX rates make runs reproducible but not financially exact.

**You:** change the CSAT rule to null the score and keep the ticket (severity `fixed`), update the
test, and explain which number on the dashboard moves and why.

## 5. Entity resolution (`er.py`, `eval/er_eval.py`, `config/er_model.json`)

**Built.** Normalization (nicknames, initials, street abbreviations), four blocking keys (exact email,
last 7 phone digits, ZIP + last-name prefix, email domain + name prefix; blocks over 100 skipped),
rapidfuzz features via `cpdist`, logistic regression trained on the train split, threshold tuned on
validation, connected components via union-find (strongest links first), merge history, golden record
by source priority (CRM > billing > support).

**Measured.** Threshold 0.84: validation P 99.78% / R 96.46%; test P 99.89% / R 97.20%. Blocking
recall ~95% caps recall.

**Trade-offs.** Splitting by true customer prevents leakage but drops cross-split pairs from split
metrics; the eval reports those separately (23 wrong links out of 3,707). Connected components can
chain (A~B, B~C => A=C) and over-merge; a cluster-size cap or correlation clustering would be the fix.
Households were added to the generator after a trial on a different seed showed the data was too easy.

**You:** explain on a whiteboard why recall after clustering can exceed blocking recall (transitivity),
and what you would add to blocking to raise the ~95% ceiling, with the runtime cost.

## 6. Dashboard (`analytics.py`, `api.py`, `dashboard/`)

**Built.** DuckDB views: `account_facts`, `revenue_by_segment`, `open_tickets_by_account`, `at_risk`
(four readable rules, flagged at 2+), plus a data-quality panel (checks, quarantine by reason, raw
samples, ER counts). FastAPI reads the DuckDB file read-only per request. React + Vite + TypeScript, no
chart library (plain bars).

**Trade-offs.** Tickets are attributed to a customer's largest account, so multi-account customers do
not double count. "As of" is fixed (2025-06-30) for reproducibility. Rules over a model: less clever,
but a CS lead can argue with each one.

**You:** add a fifth risk rule (e.g. "urgent ticket open more than 14 days") end to end: SQL view,
`RISK_RULES`, API field, UI tag. Be ready to explain why the threshold is 2 rules and not 1.

## 7. Benchmarks and CI (`scripts/benchmark.py`, `.github/workflows/ci.yml`, `eval/ONBOARDING_TIMING.md`)

**Built.** One-shot runtime benchmark at scale 1 and 10 (generation excluded), reusing the confirmed
mapping store (same fingerprints at any scale). The unseen ERP source with separate ground truth and a
timing protocol (by hand first, tool a day later, hand labels before looking at the generator's).
CI: ruff + pytest + dashboard build, with `LLM_PROVIDER=none`.

**Measured.** 2.42 s for 21K records, 22.85 s for 211K (ER is a third of it at 200K).

**Trade-offs.** One run per scale is noisy; it is reported as one run, not best-of. Excel parsing uses
the calamine engine (much faster than openpyxl) at the cost of one more dependency.

**You:** run the onboarding timing test, and explain why the order (by hand first) biases the result
toward the tool and how the one-day gap reduces it.

## 8. Results and customer brief

**Built.** RESULTS.md with only measured numbers (commands, machine, date), blanks for LLM and timing;
CUSTOMER_BRIEF.md written for an operations lead, with the decisions they must make.

**You:** record the 3-minute demo, and rewrite the brief's first paragraph in your own words after
the paid run and the timing test are in.
