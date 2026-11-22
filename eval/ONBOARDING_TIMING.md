# Onboarding-time test: a new, unseen source

Question: how long does it take to get a new export from "file received" to "mapping confirmed,
pipeline-ready", by hand versus with this tool? Chaman times both. Nothing here has been
measured yet; RESULTS.md says "not measured yet" until this is done.

## The source

`data/unseen/erp_customer_master.csv` (about 3,200 rows at scale 1): an ERP customer master with
SAP-style column names (`KUNNR`, `NAME1`, `STRAS`, `ORT01`, `PSTLZ`, `TELF1`, `ERDAT`, ...). It is
written by `uv run onboard generate` and is NOT in `sources.yaml`. Its generator ground truth sits
in `data/ground_truth_unseen/`. **Do not open that folder until step 4.**

The rules alone already got 5 of 15 columns right on this source (`eval/results/mapping_rules_unseen.json`),
which is why it is a fair test of the LLM + confirm screen.

## What "done" means (same for both conditions)

A mapping exists for all 15 columns (each column has a target field or `ignore`), and you believe it
is correct enough to load into the customer's model. The stopwatch stops at that moment, not when
you later find out whether you were right.

Allowed targets for a customer table: `customer_ref, full_name, first_name, last_name, email, phone,
street, city, state, postal_code, country, company_name, segment, signup_date, plan, ignore`.

## Order and why

1. **By hand first.** Familiarity helps whichever condition goes second. Doing the tool second
   would favour the tool, so leave **at least one day** between the sessions and report the order
   with the result.
2. Same machine, same time of day if you can, no other help (no ChatGPT, no Claude).

## Condition A: by hand (timed)

1. Copy the template: `cp eval/hand_labeled_mapping_template.csv eval/by_hand_attempt.csv`
2. Start a stopwatch.
3. Open `data/unseen/erp_customer_master.csv` in any viewer (Numbers, VS Code, `head`), and fill the
   `target_field` column of `eval/by_hand_attempt.csv` for all 15 rows. Look things up if you need to
   (e.g. what `KUNNR` means), that is part of real work.
4. Stop when every row is filled. Write down minutes:seconds.

## Condition B: with the tool (timed, at least one day later)

Before starting the stopwatch (setup, not timed): add the `erp` block from `eval/unseen_source.yaml`
to `sources.yaml`, start the API (`uv run uvicorn onboard.api:app --port 8003`) and the dashboard
(`cd dashboard && npm run dev`).

1. Start the stopwatch.
2. `LLM_PROVIDER=anthropic uv run onboard suggest --table erp.customer_master --dry-run` (read the estimate)
3. `LLM_PROVIDER=anthropic uv run onboard suggest --table erp.customer_master --llm --yes` (paid, about a tenth of a cent)
4. In the dashboard, Mapping review -> `erp.customer_master`: check each row, fix what is wrong, Accept,
   then Confirm mapping.
5. Stop when it says "Saved". Write down minutes:seconds.

## Step 3: the hand-labeled ground truth (untimed)

Take your by-hand file, go through every row again slowly against the raw data, and fix anything you
now think is wrong. Save it as `eval/hand_labeled_unseen_mapping.csv`. This is the hand-labeled ground
truth. Do it before step 4 so the generator's answer cannot influence it.

## Step 4: score everything

```bash
# rules only, vs your hand labels
uv run python eval/mapping_eval.py --mode rules --unseen --truth eval/hand_labeled_unseen_mapping.csv
# rules + LLM, vs your hand labels (paid, asks for --yes)
LLM_PROVIDER=anthropic uv run python eval/mapping_eval.py --mode llm --unseen --truth eval/hand_labeled_unseen_mapping.csv --yes
# your timed by-hand attempt, vs your hand labels: how many did you get right under the clock?
diff <(sort eval/by_hand_attempt.csv) <(sort eval/hand_labeled_unseen_mapping.csv)
```

Only now compare your labels with `data/ground_truth_unseen/column_mapping.csv`. If they disagree,
look at the data and decide; do not just copy the generator. Report any label you changed.

## What to write in RESULTS.md

```
NEW SOURCE ONBOARDING: by hand __ min __ s -> with tool __ min __ s (order: by hand first, gap __ days)
  by-hand attempt correct: __/15 ; tool's final confirmed mapping correct: __/15 (vs hand labels)
  rules only __/15 -> rules + LLM __/15 (vs hand labels) ; LLM cost $__ (from response.usage)
```
