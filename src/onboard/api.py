"""FastAPI backend for the dashboard: mapping review/confirm + analytics.

    uv run uvicorn onboard.api:app --port 8003
"""

from __future__ import annotations

import json
from pathlib import Path

import duckdb
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from onboard.analytics import RISK_MIN_RULES, RISK_RULES
from onboard.config import AS_OF_DATE
from onboard.mock_api import create_mock_app
from onboard.schema import FIELDS, IGNORE
from onboard.service import Workspace

app = FastAPI(title="data-onboarding API")
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
                   allow_methods=["*"], allow_headers=["*"])
app.mount("/mock/support", create_mock_app())

_ws: Workspace | None = None


def ws() -> Workspace:
    global _ws
    if _ws is None:
        _ws = Workspace()
    return _ws


def set_workspace(w: Workspace) -> None:
    """Used by tests to point the API at a temporary project."""
    global _ws
    _ws = w


def _spec(key: str):
    try:
        return ws().spec(key)
    except KeyError as e:
        raise HTTPException(404, f"unknown table {key}") from e


class ColumnChoice(BaseModel):
    column: str
    target: str


class ConfirmBody(BaseModel):
    mappings: list[ColumnChoice]
    confirmed_by: str = "dashboard"


@app.get("/api/health")
def health() -> dict:
    return {"ok": True}


@app.get("/api/fields")
def fields() -> list[dict]:
    out = [{"name": f.name, "description": f.description, "entities": list(f.entities)} for f in FIELDS.values()]
    return out + [{"name": IGNORE, "description": "Drop this column", "entities": ["*"]}]


@app.get("/api/tables")
def tables() -> list[dict]:
    w = ws()
    out = []
    for sp in w.specs():
        doc = w.load_suggestions(sp)
        out.append({"key": sp.key, "entity": sp.entity, "kind": sp.kind, "fingerprint": doc["fingerprint"],
                    "confirmed": doc["confirmed"], "provider": doc["provider"], "stats": doc["stats"]})
    return out


@app.get("/api/tables/{key}/suggestions")
def table_suggestions(key: str) -> dict:
    return ws().load_suggestions(_spec(key))


@app.post("/api/tables/{key}/confirm")
def confirm(key: str, body: ConfirmBody) -> dict:
    res = ws().confirm(_spec(key), {c.column: c.target for c in body.mappings}, body.confirmed_by)
    if not res["saved"]:
        raise HTTPException(422, res)
    return res


# ---------------------------------------------------------------- analytics

def _query(sql: str, params: list | None = None) -> list[dict]:
    db = Path(ws().s.db_path)
    if not db.exists():
        raise HTTPException(503, "no model yet: run `uv run onboard run` first")
    with duckdb.connect(str(db), read_only=True) as con:
        cur = con.execute(sql, params or [])
        cols = [d[0] for d in cur.description]
        rows = cur.fetchall()
    return [{c: (v.isoformat() if hasattr(v, "isoformat") else v) for c, v in zip(cols, r, strict=True)}
            for r in rows]


@app.get("/api/metrics/overview")
def overview() -> dict:
    counts = _query("""SELECT
        (SELECT COUNT(*) FROM customer_records) AS customer_records,
        (SELECT COUNT(*) FROM customers) AS customers,
        (SELECT COUNT(*) FROM accounts) AS accounts,
        (SELECT COUNT(*) FROM invoices) AS invoices,
        (SELECT COUNT(*) FROM tickets) AS tickets,
        (SELECT COUNT(*) FROM quarantine) AS quarantined,
        (SELECT COUNT(*) FROM at_risk) AS at_risk""")[0]
    run = _query("SELECT run_id, finished_at, total_s, summary_json FROM pipeline_runs "
                 "ORDER BY finished_at DESC LIMIT 1")
    last = run[0] if run else None
    if last:
        last["summary"] = json.loads(last.pop("summary_json"))
    return {"as_of": AS_OF_DATE, "counts": counts, "last_run": last}


@app.get("/api/metrics/revenue_by_segment")
def revenue_by_segment() -> list[dict]:
    return _query("SELECT * FROM revenue_by_segment")


@app.get("/api/metrics/open_tickets")
def open_tickets(limit: int = Query(15, ge=1, le=500)) -> list[dict]:
    return _query("SELECT * FROM open_tickets_by_account LIMIT ?", [limit])


@app.get("/api/metrics/at_risk")
def at_risk(limit: int = Query(25, ge=1, le=1000)) -> dict:
    rows = _query("""SELECT account_id, full_name, company_name, segment, plan, billed_usd, outstanding_usd,
        overdue_invoices, open_tickets, csat_180, billed_last_90, billed_prev_90, rules_fired,
        r_overdue, r_ticket_load, r_low_csat, r_revenue_drop FROM at_risk LIMIT ?""", [limit])
    total = _query("SELECT COUNT(*) AS n, ROUND(SUM(billed_usd), 2) AS billed FROM at_risk")[0]
    fired = _query("""SELECT SUM(r_overdue::INT) AS overdue, SUM(r_ticket_load::INT) AS ticket_load,
        SUM(r_low_csat::INT) AS low_csat, SUM(r_revenue_drop::INT) AS revenue_drop FROM at_risk""")[0]
    return {"min_rules": RISK_MIN_RULES, "rules": RISK_RULES, "total": total, "rule_counts": fired,
            "accounts": rows}


@app.get("/api/metrics/data_quality")
def data_quality() -> dict:
    summary = _query("""SELECT table_key, check_name, severity, SUM(rows_affected) AS rows_affected,
        MAX(rows_checked) AS rows_checked FROM dq_summary WHERE rows_affected > 0
        GROUP BY ALL ORDER BY table_key, rows_affected DESC""")
    by_reason = _query("""SELECT check_name, detail, COUNT(*) AS n FROM quarantine GROUP BY ALL ORDER BY n DESC""")
    sample = _query("SELECT table_key, row_number, row_key, check_name, detail, raw_json FROM quarantine LIMIT 8")
    er = _query("""SELECT COUNT(*) AS records, COUNT(DISTINCT customer_id) AS customers,
        COUNT(DISTINCT customer_id) FILTER (WHERE n > 1) AS merged_customers FROM (
          SELECT customer_id, COUNT(*) OVER (PARTITION BY customer_id) AS n FROM customer_records)""")[0]
    merges = _query("SELECT COUNT(*) AS links, MIN(score) AS min_score FROM merge_history "
                    "WHERE run_id = (SELECT MAX(run_id) FROM merge_history)")[0]
    return {"checks": summary, "quarantine_by_reason": by_reason, "quarantine_sample": sample,
            "entity_resolution": {**er, **merges}}
