"""Value-level parsing shared by profiling (to detect patterns) and transform (to clean)."""

from __future__ import annotations

import math
import re
from datetime import UTC, date, datetime

import pandas as pd

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")
PHONE_RE = re.compile(r"^\+?1?[\s\-.]?\(?\d{3}\)?[\s\-.]?\d{3}[\s\-.]?\d{4}$")
ZIP_RE = re.compile(r"^\d{4,5}(-\d{4})?$")
NUM_RE = re.compile(r"^-?\d+(\.\d+)?$")
MONEY_RE = re.compile(r"^-?\s*(?:[$€£]|USD|EUR|GBP)?\s*-?\d[\d.,\s]*\s*(?:USD|EUR|GBP|[$€£])?$")
MONEY_MARK_RE = re.compile(r"[$€£]|\b(USD|EUR|GBP)\b")
ID_RE = re.compile(r"^[A-Za-z]{0,8}[-_]?[A-Za-z0-9]*\d[A-Za-z0-9\-_]*$")

DATE_FORMATS = [
    "%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y", "%d.%m.%Y", "%Y/%m/%d", "%d %b %Y", "%b %d, %Y",
    "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y%m%d",
]
EPOCH_MIN, EPOCH_MAX = 946_684_800, 2_000_000_000  # 2000-01-01 .. 2033


def is_null(v) -> bool:
    if v is None:
        return True
    if isinstance(v, float) and math.isnan(v):
        return True
    if v is pd.NaT:
        return True
    if isinstance(v, str) and v.strip() == "":
        return True
    try:
        return bool(pd.isna(v)) if not isinstance(v, (list, dict, tuple)) else False
    except (TypeError, ValueError):
        return False


def to_str(v) -> str | None:
    """Canonical string form of a raw cell. Integral floats lose their '.0' (Excel ints)."""
    if is_null(v):
        return None
    if isinstance(v, bool):
        return str(v)
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    if isinstance(v, (datetime, pd.Timestamp)):
        return v.isoformat()
    if isinstance(v, date):
        return v.isoformat()
    return str(v).strip()


def try_parse_date(v, fmt: str | None = None) -> datetime | None:
    if is_null(v):
        return None
    if isinstance(v, pd.Timestamp):
        return v.to_pydatetime().replace(tzinfo=None)
    if isinstance(v, datetime):
        return v.replace(tzinfo=None)
    if isinstance(v, date):
        return datetime(v.year, v.month, v.day)
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        if EPOCH_MIN <= v <= EPOCH_MAX:
            return datetime.fromtimestamp(float(v), tz=UTC).replace(tzinfo=None)
        return None
    s = str(v).strip()
    if s.isdigit() and len(s) == 10 and EPOCH_MIN <= int(s) <= EPOCH_MAX:
        return datetime.fromtimestamp(int(s), tz=UTC).replace(tzinfo=None)
    fmts = [fmt] + DATE_FORMATS if fmt else DATE_FORMATS
    for f in fmts:
        try:
            d = datetime.strptime(s, f)
        except ValueError:
            continue
        if 1990 <= d.year <= 2035:
            return d
    return None


def infer_date_format(values: list) -> str | None:
    """Pick the single format that parses the most string values (column-level inference).

    Column-level matters for ambiguous values like 03/05/2024: the format that wins
    on the whole column decides, instead of each value guessing on its own.
    """
    strs = [str(v).strip() for v in values if isinstance(v, str) and v.strip()]
    if not strs:
        return None
    best, best_n = None, 0
    for f in DATE_FORMATS:
        n = 0
        for s in strs[:500]:
            try:
                d = datetime.strptime(s, f)
                n += 1990 <= d.year <= 2035
            except ValueError:
                pass
        if n > best_n:
            best, best_n = f, n
    return best


def parse_date_series(s: pd.Series) -> pd.Series:
    fmt = infer_date_format(s.dropna().tolist())
    out = [try_parse_date(v, fmt) for v in s.tolist()]
    return pd.Series(pd.to_datetime(out, errors="coerce"), index=s.index)


def parse_money(v) -> tuple[float | None, str | None]:
    """'$1,234.50' -> (1234.5, 'USD'); '€1.234,50' -> (1234.5, 'EUR'); '1234,50 EUR' -> (1234.5, 'EUR')."""
    if is_null(v):
        return None, None
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return float(v), None
    s = str(v).strip()
    ccy = None
    if "$" in s or "USD" in s:
        ccy = "USD"
    elif "€" in s or "EUR" in s:
        ccy = "EUR"
    elif "£" in s or "GBP" in s:
        ccy = "GBP"
    neg = "-" in s
    num = re.sub(r"[^\d.,]", "", s)
    if not num:
        return None, ccy
    if "," in num and "." in num:
        # whichever separator comes last is the decimal mark
        if num.rfind(",") > num.rfind("."):
            num = num.replace(".", "").replace(",", ".")
        else:
            num = num.replace(",", "")
    elif "," in num:
        head, _, tail = num.rpartition(",")
        num = head.replace(",", "") + "." + tail if len(tail) == 2 else num.replace(",", "")
    try:
        x = float(num)
    except ValueError:
        return None, ccy
    return (-x if neg else x), ccy


def normalize_phone(v) -> str | None:
    s = to_str(v)
    if not s:
        return None
    digits = re.sub(r"\D", "", s)
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    return digits if len(digits) == 10 else None


def normalize_email(v) -> str | None:
    s = to_str(v)
    if not s:
        return None
    s = s.strip().lower()
    return s if EMAIL_RE.match(s) else None


def normalize_zip(v) -> str | None:
    s = to_str(v)
    if not s:
        return None
    s = s.split("-")[0].strip()
    if not s.isdigit() or len(s) > 5:
        return None
    return s.zfill(5)
