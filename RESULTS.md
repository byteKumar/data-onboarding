# Results

Everything below was run once and is reported as is. Machine for all measured numbers:
Apple M3, 16 GB RAM, macOS 26.6.2, Python 3.12.16. Date: 2026-11-21. Raw outputs are in
`eval/results/*.json` (each file records its command, time and machine).

Blank means **not measured yet**: anything that needs the paid LLM call or Chaman's own time.

## Spec section 5 template

```
DATA: sources=3 (+1 unseen) records=21,209 customer records (+39,365 invoices, 12,445 tickets) columns mapped=49 across 4 tables (hand-labeled ground truth: not measured yet)
MAPPING ACCURACY: rules only 95.9% (47/49, known sources) -> rules + LLM not measured yet (validators rejected: not measured yet for LLM; 2 rule proposals rejected)
ENTITY RESOLUTION at threshold 0.84: precision 99.78% recall 96.46% (validation) ; test 99.89% / 97.20%
NEW SOURCE ONBOARDING: by hand not measured yet -> with tool not measured yet (timed by Chaman)
RUNTIME: 20K records 2.42 s ; 200K records 22.85 s ; quarantined rows 952 (20K) by reason: see table below
TOTAL API SPEND: $0.00 so far ; CI passing? not run yet (workflow written, repo not pushed) ; demo video: not recorded yet
ANYTHING THAT FAILED OR SURPRISED YOU: see "Surprises and failures" below
REPO pushed? no
```

## 1. Mapping accuracy (rules only, no LLM, no human)

Command: `uv run python eval/mapping_eval.py --mode rules`
A column is correct when the final target equals the generator's ground truth; a column the rules
leave unresolved counts as `ignore`.

| Sources | Columns | Correct | Accuracy | Resolved by rules | Correct when resolved | Unresolved (truth = ignore) |
|---|---|---|---|---|---|---|
| CRM + billing (2 sheets) + support | 49 | 47 | **95.9%** | 39 | 39/39 | 10 (8) |
| Unseen ERP (`--unseen`), vs generator truth | 15 | 5 | **33.3%** | 3 | 2/3 | 12 (3) |

Misses on the known sources: `Tier` (truth `segment`, no rule matched) and `subject` (the rule
proposed `subject` but the validator refused it: the synthetic subjects are 14 templated strings, so
the profiler calls the column `category`, and `subject` only accepts `text`).

Unseen source: `KUNNR` (10-digit zero-padded customer number) matched the phone regex and was mapped to
`phone`, which then pushed the real phone column `TELF1` out on the one-column-per-field rule.
`NAME1` and `SMTP_ADDR` were right; everything else was left for the LLM / human.

Caveat: the same author wrote the generator's column names and the rule synonyms, so 95.9% on the
known sources is optimistic. The unseen source (33.3%) is the honest number for "rules on a new export".

History: rules-only was first run in commit `958d997` (data v1: 47/49). In milestone 5 the generator
gained household records (hard negatives for entity resolution), so the data changed and the rules
eval was run once more on data v2. Same result, 47/49. No rule or validator changed in between.

Rules + LLM: **not measured yet** (needs the paid call, see README "What Chaman must do").

## 2. Entity resolution

Command: `uv run python eval/er_eval.py` (protocol in the script's docstring)

* Records split by true customer: 60% train / 20% validation / 20% test.
* Logistic regression on rapidfuzz features fit on train pairs; threshold chosen on validation only
  (max pairwise F1 after clustering); test scored once.

| Split | Records | True pairs | Blocking recall | Precision | Recall | F1 |
|---|---|---|---|---|---|---|
| Validation | 4,299 | 2,884 | 94.73% | **99.78%** | **96.46%** | 98.10% |
| Test | 4,132 | 2,826 | 95.36% | **99.89%** | **97.20%** | 98.53% |
| All records (includes train, reference only) | 21,108 | 14,372 | - | 99.26% | 96.73% | 97.98% |

Threshold 0.84. 20,325 candidate pairs from blocking (vs ~222M for all pairs). Pairs that span two
splits (3,707, all non-matches by construction) are excluded from the split rows; the model would
wrongly link 23 of them at 0.84, which is why the all-records precision is lower.

Recall is capped by blocking (about 95%): true pairs that share no email, phone suffix, ZIP+name
prefix or domain+name prefix are never compared.

## 3. Runtime

Command: `uv run python scripts/benchmark.py --scales 1 10` (one run per scale; generation is timed
separately and excluded). Pipeline = load + normalize + checks + model + entity resolution + DuckDB write.

| Scale | Customer records (generated) | Invoices | Tickets | Pipeline total | Entity resolution | Candidate pairs |
|---|---|---|---|---|---|---|
| 1 (~20K) | 21,209 | 39,365 | 12,445 | **2.42 s** | 0.48 s | 20,325 |
| 10 (~200K) | 211,364 | 393,557 | 126,116 | **22.85 s** | 7.66 s | 711,854 |

At 200K one `domain+name` block exceeded the 100-record cap and was skipped (its records can still be
paired through the other three keys). The 200K generated files were deleted after the run (disk space);
re-create them with the same command.

## 4. Quarantine (20K run)

952 rows held back, by reason:

| Check | Detail | Rows |
|---|---|---|
| referential | account_ref not found in any accounts table | 215 |
| missing_required | amount is empty or unparseable | 188 |
| out_of_range | csat_score must be 1..5 | 131 |
| out_of_range | amount must be > 0 and <= 1,000,000 USD | 121 |
| invalid | due_date before invoice_date | 113 |
| missing_required | neither name nor email | 82 |
| duplicate_key | invoice_ref seen earlier | 67 |
| missing_required | ticket_created_at is empty or unparseable | 35 |

Plus 75 CRM emails such as `n/a` set to null (row kept). At 200K: 9,453 rows quarantined, same reasons.

## 5. Not measured yet

* Rules + LLM mapping accuracy, LLM validator rejections, API spend.
* Hand-labeled ground truth for the unseen source, and accuracy against it.
* Onboarding time, by hand vs with the tool (`eval/ONBOARDING_TIMING.md`).
* CI status (needs a push), demo video.

## Surprises and failures

1. The phone regex accepts any 10-digit number, so an ERP customer number became `phone`. Validators
   cannot catch it because the pattern check itself is what is wrong.
2. A validator rejected a correct rule (`subject`) because templated text looks like a category.
   Validators can be wrong in both directions.
3. Synthetic matching is easier than real data: precision is near 100% even with household hard
   negatives. Expect lower numbers on a real customer's data.
4. At 200K, entity resolution is a third of the runtime and candidate pairs grow ~35x for 10x records
   (more name collisions inside blocks).
