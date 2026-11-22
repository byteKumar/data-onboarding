"""Dashboard metrics as DuckDB views over the unified model.

Accounts at risk use four plain rules a customer success lead can read and argue with.
An account is flagged when at least RISK_MIN_RULES of them fire; the UI shows which ones.
"""

from __future__ import annotations

import duckdb

from onboard.config import AS_OF_DATE

RISK_MIN_RULES = 2
RISK_RULES = [
    {"id": "overdue", "label": "Overdue invoice",
     "explain": "At least one invoice is marked overdue, or is unpaid past its due date."},
    {"id": "ticket_load", "label": "3+ open tickets",
     "explain": "The customer has 3 or more support tickets that are open or pending."},
    {"id": "low_csat", "label": "Low satisfaction",
     "explain": "Average CSAT over the last 180 days is 2.5 or lower (at least 2 ratings)."},
    {"id": "revenue_drop", "label": "Revenue drop",
     "explain": "Billed in the last 90 days is under half of the 90 days before (a downgrade or churn signal)."},
]

VIEWS = f"""
CREATE OR REPLACE VIEW account_facts AS
WITH inv AS (
  SELECT account_id,
    SUM(amount_usd) FILTER (WHERE status = 'paid') AS paid_usd,
    SUM(amount_usd) FILTER (WHERE status <> 'void' OR status IS NULL) AS billed_usd,
    SUM(amount_usd) FILTER (WHERE status IN ('unpaid','overdue')) AS outstanding_usd,
    COUNT(*) FILTER (WHERE status = 'overdue'
                     OR (status = 'unpaid' AND due_date < DATE '{AS_OF_DATE}')) AS overdue_invoices,
    SUM(amount_usd) FILTER (WHERE invoice_date > DATE '{AS_OF_DATE}' - INTERVAL 90 DAY
                            AND status IS DISTINCT FROM 'void') AS billed_last_90,
    SUM(amount_usd) FILTER (WHERE invoice_date <= DATE '{AS_OF_DATE}' - INTERVAL 90 DAY
                            AND invoice_date > DATE '{AS_OF_DATE}' - INTERVAL 180 DAY
                            AND status IS DISTINCT FROM 'void') AS billed_prev_90
  FROM invoices GROUP BY account_id
),
-- tickets belong to a customer; attribute them to the customer's largest account
primary_acct AS (
  SELECT a.customer_id, a.account_id
  FROM accounts a LEFT JOIN inv USING (account_id)
  QUALIFY ROW_NUMBER() OVER (PARTITION BY a.customer_id ORDER BY COALESCE(inv.billed_usd, 0) DESC,
                                                                a.account_id) = 1
),
tix AS (
  SELECT customer_id,
    COUNT(*) AS total_tickets,
    COUNT(*) FILTER (WHERE status IN ('open','pending')) AS open_tickets,
    MAX(DATE '{AS_OF_DATE}' - CAST(created_at AS DATE)) FILTER (WHERE status IN ('open','pending'))
      AS oldest_open_days,
    AVG(csat_score) FILTER (WHERE created_at > DATE '{AS_OF_DATE}' - INTERVAL 180 DAY) AS csat_180,
    COUNT(csat_score) FILTER (WHERE created_at > DATE '{AS_OF_DATE}' - INTERVAL 180 DAY) AS csat_n
  FROM tickets GROUP BY customer_id
)
SELECT a.account_id, a.customer_id, c.full_name, c.company_name, c.segment, a.plan,
  COALESCE(inv.paid_usd, 0) AS paid_usd, COALESCE(inv.billed_usd, 0) AS billed_usd,
  COALESCE(inv.outstanding_usd, 0) AS outstanding_usd, COALESCE(inv.overdue_invoices, 0) AS overdue_invoices,
  COALESCE(inv.billed_last_90, 0) AS billed_last_90, COALESCE(inv.billed_prev_90, 0) AS billed_prev_90,
  CASE WHEN p.account_id IS NOT NULL THEN COALESCE(t.open_tickets, 0) ELSE 0 END AS open_tickets,
  CASE WHEN p.account_id IS NOT NULL THEN COALESCE(t.total_tickets, 0) ELSE 0 END AS total_tickets,
  CASE WHEN p.account_id IS NOT NULL THEN t.oldest_open_days END AS oldest_open_days,
  CASE WHEN p.account_id IS NOT NULL THEN t.csat_180 END AS csat_180,
  CASE WHEN p.account_id IS NOT NULL THEN COALESCE(t.csat_n, 0) ELSE 0 END AS csat_n
FROM accounts a
JOIN customers c USING (customer_id)
LEFT JOIN inv USING (account_id)
LEFT JOIN primary_acct p ON p.account_id = a.account_id
LEFT JOIN tix t ON t.customer_id = a.customer_id;

CREATE OR REPLACE VIEW revenue_by_segment AS
SELECT segment, COUNT(DISTINCT customer_id) AS customers, COUNT(*) AS accounts,
  ROUND(SUM(paid_usd), 2) AS paid_usd, ROUND(SUM(billed_usd), 2) AS billed_usd,
  ROUND(SUM(outstanding_usd), 2) AS outstanding_usd
FROM account_facts GROUP BY segment ORDER BY paid_usd DESC;

CREATE OR REPLACE VIEW open_tickets_by_account AS
SELECT account_id, customer_id, full_name, company_name, segment, open_tickets, total_tickets, oldest_open_days
FROM account_facts WHERE open_tickets > 0 ORDER BY open_tickets DESC, oldest_open_days DESC NULLS LAST;

CREATE OR REPLACE VIEW at_risk AS
WITH r AS (
  SELECT *,
    overdue_invoices > 0 AS r_overdue,
    open_tickets >= 3 AS r_ticket_load,
    (csat_n >= 2 AND csat_180 <= 2.5) AS r_low_csat,
    (billed_prev_90 > 0 AND billed_last_90 < 0.5 * billed_prev_90) AS r_revenue_drop
  FROM account_facts
)
SELECT *, (r_overdue::INT + r_ticket_load::INT + r_low_csat::INT + r_revenue_drop::INT) AS rules_fired
FROM r
WHERE (r_overdue::INT + r_ticket_load::INT + r_low_csat::INT + r_revenue_drop::INT) >= {RISK_MIN_RULES}
ORDER BY rules_fired DESC, billed_usd DESC;
"""


def create_views(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(VIEWS)
