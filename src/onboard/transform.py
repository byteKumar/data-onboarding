"""Apply a confirmed mapping and normalize values field by field.

Output: one DataFrame per source table with unified field names, cleaned values,
the original row number (`_row`) and per-row soft issues (`_issues`, values that were
unusable and set to null, e.g. an email of 'n/a').
"""

from __future__ import annotations

import re

import pandas as pd

from onboard.config import FX_TO_USD
from onboard.parsing import (
    is_null,
    normalize_email,
    normalize_phone,
    normalize_zip,
    parse_date_series,
    parse_money,
    to_str,
)
from onboard.schema import IGNORE

DATE_FIELDS = ("signup_date", "invoice_date", "due_date", "ticket_created_at")
REF_FIELDS = ("customer_ref", "account_ref", "invoice_ref", "ticket_ref")


def clean_name(v) -> str | None:
    s = to_str(v)
    if not s:
        return None
    s = " ".join(s.split())
    if "," in s:  # "Last, First M."
        last, _, first = s.partition(",")
        s = f"{first.strip()} {last.strip()}".strip()
    if s.isupper() or s.islower():
        s = s.title()
    return s or None


def canon_segment(v) -> str | None:
    s = to_str(v)
    if not s:
        return None
    k = re.sub(r"[^a-z]", "", s.lower())
    if k.startswith("ent"):
        return "Enterprise"
    if k.startswith("mid") or k == "mm":
        return "Mid-Market"
    if k.startswith("small") or k == "smb":
        return "SMB"
    return s


def canon_invoice_status(v) -> str | None:
    s = (to_str(v) or "").lower()
    if not s:
        return None
    if s in ("paid", "settled"):
        return "paid"
    if s in ("unpaid", "open", "due", "pending"):
        return "unpaid"
    if s.startswith("overdue") or s == "late":
        return "overdue"
    if s in ("void", "cancelled", "canceled"):
        return "void"
    return s


def _soft(series: pd.Series, cleaned: pd.Series, issues: pd.Series, label: str) -> None:
    bad = series.map(lambda v: not is_null(v)) & cleaned.isna()
    for i in series.index[bad]:
        issues.at[i] = issues.at[i] + [label]


def normalize_table(raw: pd.DataFrame, mapping: dict[str, str]) -> pd.DataFrame:
    cols = {c: t for c, t in mapping.items() if t != IGNORE and c in raw.columns}
    df = pd.DataFrame(index=raw.index)
    df["_row"] = range(len(raw))
    issues = pd.Series([[] for _ in range(len(raw))], index=raw.index, dtype=object)
    for col, field in cols.items():
        s = raw[col]
        if field in REF_FIELDS:
            out = s.map(to_str)
        elif field in ("full_name", "first_name", "last_name"):
            out = s.map(clean_name)
        elif field == "email":
            out = s.map(normalize_email)
            _soft(s, out, issues, "invalid_email")
        elif field == "phone":
            out = s.map(normalize_phone)
            _soft(s, out, issues, "invalid_phone")
        elif field == "postal_code":
            out = s.map(normalize_zip)
            _soft(s, out, issues, "invalid_postal_code")
        elif field == "city":
            out = s.map(lambda v: (to_str(v) or "").title() or None)
        elif field == "state":
            out = s.map(lambda v: (to_str(v) or "").upper() or None)
        elif field == "segment":
            out = s.map(canon_segment)
        elif field in ("plan", "currency"):
            out = s.map(lambda v: (to_str(v) or "").upper() or None)
        elif field in DATE_FIELDS:
            out = parse_date_series(s)
            _soft(s, out, issues, f"unparseable_{field}")
        elif field == "amount":
            parsed = s.map(parse_money)
            out = parsed.map(lambda t: t[0])
            df["_amount_ccy"] = parsed.map(lambda t: t[1])
            _soft(s, out, issues, "unparseable_amount")
        elif field == "invoice_status":
            out = s.map(canon_invoice_status)
        elif field in ("ticket_status", "priority"):
            out = s.map(lambda v: (to_str(v) or "").lower() or None)
        elif field == "csat_score":
            out = pd.to_numeric(s.map(to_str), errors="coerce")
        else:
            out = s.map(to_str)
        df[field] = out
    if "full_name" not in df and ("first_name" in df or "last_name" in df):
        first = df.get("first_name", pd.Series(None, index=df.index))
        last = df.get("last_name", pd.Series(None, index=df.index))
        df["full_name"] = [" ".join(x for x in (f, la) if x) or None for f, la in zip(first, last, strict=True)]
    if "amount" in df:
        ccy = df["currency"] if "currency" in df else pd.Series(None, index=df.index, dtype=object)
        ccy = ccy.where(ccy.notna(), df["_amount_ccy"]).fillna("USD")
        df["currency"] = ccy
        df["amount_usd"] = [a * FX_TO_USD.get(c, float("nan")) if a is not None and not pd.isna(a) else None
                            for a, c in zip(df["amount"], ccy, strict=True)]
        df = df.drop(columns=["_amount_ccy"])
    df["_issues"] = issues
    return df
