# Customer brief: one trusted customer view from CRM, billing and support

*Written for the customer's operations lead. Numbers are from the synthetic pilot dataset
(RESULTS.md); items marked TBD are waiting on measurements.*

## What you gave us

Three exports that describe the same customers in different ways:

| System | Format | Rows | What was messy |
|---|---|---|---|
| CRM | CSV | 8,939 contacts | "Last, First" names, mixed date formats, segment spelled 12 ways |
| Billing | Excel (Accounts + Invoices) | 6,610 accounts, 39,365 invoices | USD/EUR/GBP with local formatting, ZIP codes that lost leading zeros, day-first dates |
| Support | JSON API | 12,445 tickets from 5,660 requesters | Nested fields, some timestamps as epoch numbers, out-of-range satisfaction scores |

## What you get

1. **One customer list.** 21,108 source records resolve to 10,613 customers. On held-out test data the
   matching was right 99.89% of the time when it merged two records (precision), and found 97.20% of
   true duplicates (recall). Every merge is logged with its score, so any merge can be explained (undoing one is a manual step today).
2. **A mapping you approved.** Each column of each export is mapped to one shared schema. Rules handle
   the obvious ones; an AI model suggests the rest (TBD: accuracy with the model); your team accepts
   or fixes each suggestion on one screen. Approved mappings are saved: next month's export with the
   same columns loads with no review.
3. **Nothing dropped silently.** 952 of 67,359 input rows (1.4%) were held back in a quarantine table with the reason
   and the original row, e.g. 215 invoices pointing to billing accounts that do not exist, 188 invoices
   with no amount, 131 tickets with a satisfaction score outside 1-5. Your team can fix them at the source.
4. **A dashboard.** Revenue by segment, open tickets per account, data quality, and **281 accounts at
   risk** ($15.2M billed), each flagged by plain rules you can read and change: overdue invoice, 3+ open
   tickets, low satisfaction, revenue drop (two or more must fire).

## How long a new source takes

| | By hand | With the tool |
|---|---|---|
| New ERP export, 15 columns | TBD (timed by Chaman) | TBD |

Rules alone recognised 5 of 15 columns of an ERP export with SAP-style names; the rest go to the AI
suggestion step and human review.

## Speed

The full load (read, clean, check, match, write) runs in 2.4 s for ~21K customer records and 22.9 s
for ~211K on a laptop (Apple M3).

## Decisions we need from you

1. **Segment for customers missing from the CRM.** We fall back to the billing plan (ENT -> Enterprise,
   PRO -> Mid-Market, BAS/STARTER -> SMB). Is that right?
2. **Currency.** We convert with fixed rates (EUR 1.08, GBP 1.27). Do you want month-end rates instead?
3. **Risk rules.** Are these four the right signals, and is "two or more" the right bar?
4. **Quarantine owners.** Who fixes orphan invoices in billing and bad scores in support?

## Limits, stated plainly

* Pilot data is synthetic. Real data will have problems we did not simulate; expect matching accuracy
  to be lower until it is re-measured on your records.
* About 5% of true duplicates never get compared (they share no email, phone, ZIP+name or
  domain+name key). Adding keys raises recall at the cost of runtime.
* The AI step sees column names, statistics and a few sample values (emails and phones masked), never
  full records.
