"""Assemble the unified model from normalized source tables, running the checks on the way.

Tables produced (all keyed by source-qualified IDs so two systems never collide):
  customer_records  one row per customer-like record from any source (input to entity resolution)
  accounts          billing accounts -> customer record
  invoices          invoices -> account (amounts converted to USD)
  tickets           support tickets -> requester customer record
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from onboard.quality import MAX_INVOICE_USD, QualityLog, duplicated, missing, out_of_date_range

PERSON_FIELDS = ["full_name", "email", "phone", "street", "city", "state", "postal_code", "company_name",
                 "segment", "signup_date", "plan"]
RECORD_COLS = ["record_id", "source_table", "customer_ref", *PERSON_FIELDS]


@dataclass
class SourceFrame:
    key: str
    entity: str
    raw: pd.DataFrame
    norm: pd.DataFrame


def _col(df: pd.DataFrame, c: str) -> pd.Series:
    return df[c] if c in df else pd.Series(None, index=df.index, dtype=object)


def _records(sf: SourceFrame, df: pd.DataFrame) -> pd.DataFrame:
    out = pd.DataFrame({"customer_ref": _col(df, "customer_ref")}, index=df.index)
    out["source_table"] = sf.key
    out["record_id"] = sf.key + ":" + out["customer_ref"].astype(str)
    for f in PERSON_FIELDS:
        out[f] = _col(df, f)
    return out[RECORD_COLS]


def build(frames: list[SourceFrame], log: QualityLog) -> dict[str, pd.DataFrame]:
    records, accounts, invoices, tickets = [], [], [], []

    # customers and accounts first: everything else references them
    for sf in frames:
        if sf.entity not in ("customer", "account"):
            continue
        df = sf.norm.copy()
        log.soft_issues(sf.key, df)
        if sf.entity == "account" and "customer_ref" not in df and "account_ref" in df:
            df["customer_ref"] = df["account_ref"]
        df = log.hold(sf.key, df, sf.raw, missing(df, "customer_ref"), "missing_required",
                      "customer_ref is empty", None)
        no_identity = missing(df, "full_name") & missing(df, "email")
        df = log.hold(sf.key, df, sf.raw, no_identity, "missing_required", "neither name nor email", "customer_ref")
        df = log.hold(sf.key, df, sf.raw, duplicated(df, "customer_ref"), "duplicate_key",
                      "customer_ref seen earlier in the same table", "customer_ref")
        if sf.entity == "account":
            df = log.hold(sf.key, df, sf.raw, missing(df, "account_ref"), "missing_required",
                          "account_ref is empty", "customer_ref")
            df = log.hold(sf.key, df, sf.raw, duplicated(df, "account_ref"), "duplicate_key",
                          "account_ref seen earlier", "account_ref")
        rec = _records(sf, df)
        records.append(rec)
        if sf.entity == "account":
            accounts.append(pd.DataFrame({
                "account_id": df["account_ref"].values, "record_id": rec["record_id"].values,
                "plan": _col(df, "plan").values, "opened_at": _col(df, "signup_date").values,
                "source_table": sf.key,
            }))

    acc = pd.concat(accounts, ignore_index=True) if accounts else pd.DataFrame(
        columns=["account_id", "record_id", "plan", "opened_at", "source_table"])
    known_accounts = set(acc["account_id"])

    for sf in frames:
        if sf.entity == "invoice":
            df = sf.norm.copy()
            log.soft_issues(sf.key, df)
            for c in ("invoice_ref", "account_ref", "invoice_date", "amount"):
                df = log.hold(sf.key, df, sf.raw, missing(df, c), "missing_required", f"{c} is empty or unparseable",
                              "invoice_ref")
            df = log.hold(sf.key, df, sf.raw, duplicated(df, "invoice_ref"), "duplicate_key",
                          "invoice_ref seen earlier", "invoice_ref")
            bad_amt = (df["amount"] <= 0) | (df["amount_usd"] > MAX_INVOICE_USD)
            df = log.hold(sf.key, df, sf.raw, bad_amt, "out_of_range",
                          f"amount must be > 0 and <= {MAX_INVOICE_USD:,} USD", "invoice_ref")
            df = log.hold(sf.key, df, sf.raw, out_of_date_range(df["invoice_date"]), "out_of_range",
                          "invoice_date outside 2000..as_of+1y", "invoice_ref")
            if "due_date" in df:
                df = log.hold(sf.key, df, sf.raw, df["due_date"] < df["invoice_date"], "invalid",
                              "due_date before invoice_date", "invoice_ref")
            df = log.hold(sf.key, df, sf.raw, ~df["account_ref"].isin(known_accounts), "referential",
                          "account_ref not found in any accounts table", "invoice_ref")
            invoices.append(pd.DataFrame({
                "invoice_id": df["invoice_ref"].values, "account_id": df["account_ref"].values,
                "invoice_date": df["invoice_date"].values, "due_date": _col(df, "due_date").values,
                "amount_orig": df["amount"].values, "currency": df["currency"].values,
                "amount_usd": df["amount_usd"].values, "status": _col(df, "invoice_status").values,
                "source_table": sf.key,
            }))

    for sf in frames:
        if sf.entity == "ticket":
            df = sf.norm.copy()
            log.soft_issues(sf.key, df)
            for c in ("ticket_ref", "ticket_created_at", "customer_ref"):
                df = log.hold(sf.key, df, sf.raw, missing(df, c), "missing_required", f"{c} is empty or unparseable",
                              "ticket_ref")
            df = log.hold(sf.key, df, sf.raw, duplicated(df, "ticket_ref"), "duplicate_key",
                          "ticket_ref seen earlier", "ticket_ref")
            if "csat_score" in df:
                bad = df["csat_score"].notna() & ~df["csat_score"].between(1, 5)
                df = log.hold(sf.key, df, sf.raw, bad, "out_of_range", "csat_score must be 1..5", "ticket_ref")
            df = log.hold(sf.key, df, sf.raw, out_of_date_range(df["ticket_created_at"]), "out_of_range",
                          "ticket_created_at outside 2000..as_of+1y", "ticket_ref")
            # requesters become customer records (latest ticket wins for contact details)
            req = df.sort_values("ticket_created_at").drop_duplicates("customer_ref", keep="last")
            no_identity = missing(req, "full_name") & missing(req, "email")
            bad_req = set(req.loc[no_identity, "customer_ref"])
            df = log.hold(sf.key, df, sf.raw, df["customer_ref"].isin(bad_req), "referential",
                          "requester has neither name nor email", "ticket_ref")
            req = req[~no_identity]
            records.append(_records(sf, req))
            tickets.append(pd.DataFrame({
                "ticket_id": df["ticket_ref"].values, "record_id": (sf.key + ":" + df["customer_ref"]).values,
                "created_at": df["ticket_created_at"].values, "status": _col(df, "ticket_status").values,
                "priority": _col(df, "priority").values, "subject": _col(df, "subject").values,
                "csat_score": _col(df, "csat_score").values, "source_table": sf.key,
            }))

    def cat(parts, cols):
        return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(columns=cols)

    return {
        "customer_records": cat(records, RECORD_COLS),
        "accounts": acc,
        "invoices": cat(invoices, ["invoice_id", "account_id", "invoice_date", "due_date", "amount_orig",
                                   "currency", "amount_usd", "status", "source_table"]),
        "tickets": cat(tickets, ["ticket_id", "record_id", "created_at", "status", "priority", "subject",
                                 "csat_score", "source_table"]),
    }
