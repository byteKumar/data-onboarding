"""Column profiling: type, null rate, distinct count, samples and a pattern class.

The profile is the only thing the mapping engine (and the LLM) sees about a
column, besides its name. Full data never leaves this module.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import asdict, dataclass

import pandas as pd

from onboard.parsing import (
    EMAIL_RE,
    ID_RE,
    MONEY_MARK_RE,
    MONEY_RE,
    NUM_RE,
    PHONE_RE,
    ZIP_RE,
    infer_date_format,
    is_null,
    to_str,
    try_parse_date,
)

SAMPLE_LIMIT = 5000


@dataclass
class ColumnProfile:
    column: str
    dtype: str
    rows: int
    null_rate: float
    distinct_count: int
    distinct_ratio: float
    samples: list[str]
    pattern: str
    pattern_share: float
    shape: str
    date_format: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def _shape(s: str) -> str:
    return re.sub(r"[a-z]", "a", re.sub(r"[A-Z]", "A", re.sub(r"\d", "9", s)))[:24]


def _dtype(values: list) -> str:
    kinds = Counter()
    for v in values:
        if isinstance(v, bool):
            kinds["bool"] += 1
        elif isinstance(v, int):
            kinds["int"] += 1
        elif isinstance(v, float):
            kinds["float"] += 1
        elif hasattr(v, "year"):
            kinds["datetime"] += 1
        else:
            kinds["string"] += 1
    if not kinds:
        return "empty"
    if len(kinds) == 1:
        return next(iter(kinds))
    return "mixed(" + ",".join(sorted(kinds)) + ")"


def classify(values: list, distinct_ratio: float, distinct_count: int) -> tuple[str, float, str | None]:
    """Return (pattern class, share of sampled values matching it, inferred date format)."""
    n = len(values)
    if n == 0:
        return "empty", 0.0, None
    strs = [to_str(v) or "" for v in values]

    def share(pred) -> float:
        return sum(1 for s in strs if pred(s)) / n

    email = share(lambda s: bool(EMAIL_RE.match(s)))
    if email >= 0.8:
        return "email", email, None

    fmt = infer_date_format(values)
    date_hits = sum(1 for v in values if try_parse_date(v, fmt) is not None)
    # bare integers only count as dates when most of the column is clearly dates
    raw_date_like = sum(1 for v in values if not isinstance(v, (int, float)) or isinstance(v, bool))
    if date_hits / n >= 0.8 and raw_date_like / n >= 0.5:
        return "date", date_hits / n, fmt

    marked = share(lambda s: bool(MONEY_MARK_RE.search(s)))
    money = share(lambda s: bool(MONEY_RE.match(s)))
    if marked >= 0.3 and money >= 0.9:
        return "currency", money, None

    phone = share(lambda s: bool(PHONE_RE.match(s)))
    if phone >= 0.8:
        return "phone", phone, None

    zipc = share(lambda s: bool(ZIP_RE.match(s)))
    if zipc >= 0.9:
        return "zip", zipc, None

    numeric = share(lambda s: bool(NUM_RE.match(s)))
    if numeric >= 0.9:
        integral = all("." not in s for s in strs if NUM_RE.match(s))
        if integral and distinct_ratio >= 0.9:
            return "id", numeric, None
        if distinct_count <= 20:
            return "category", numeric, None
        return "number", numeric, None

    idlike = share(lambda s: bool(ID_RE.match(s)) and " " not in s and len(s) <= 40)
    if idlike >= 0.9 and distinct_ratio >= 0.3:
        return "id", idlike, None

    avg_len = sum(len(s) for s in strs) / n
    if distinct_count <= 50 or (distinct_ratio <= 0.02 and avg_len <= 30):
        return "category", 1.0, None
    return "text", 1.0, None


def profile_column(name: str, series: pd.Series) -> ColumnProfile:
    values = series.tolist()
    non_null = [v for v in values if not is_null(v)]
    rows = len(values)
    as_str = [to_str(v) for v in non_null]
    distinct = len(set(as_str))
    dratio = distinct / len(non_null) if non_null else 0.0
    sample = non_null[:SAMPLE_LIMIT] if len(non_null) <= SAMPLE_LIMIT else (
        pd.Series(non_null).sample(SAMPLE_LIMIT, random_state=0).tolist())
    pattern, pshare, fmt = classify(sample, dratio, distinct)
    shapes = Counter(_shape(s) for s in as_str[:SAMPLE_LIMIT])
    seen: list[str] = []
    for s in as_str:
        if s not in seen:
            seen.append(s)
        if len(seen) == 5:
            break
    return ColumnProfile(
        column=name, dtype=_dtype(non_null[:SAMPLE_LIMIT]), rows=rows,
        null_rate=round(1 - len(non_null) / rows, 4) if rows else 1.0,
        distinct_count=distinct, distinct_ratio=round(dratio, 4), samples=seen, pattern=pattern,
        pattern_share=round(pshare, 3), shape=shapes.most_common(1)[0][0] if shapes else "", date_format=fmt,
    )


def profile_table(df: pd.DataFrame) -> list[ColumnProfile]:
    return [profile_column(c, df[c]) for c in df.columns]
