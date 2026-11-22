"""Data-quality checks and the quarantine.

Two severities:
* quarantine: the row is held back from the unified model, with the reason, the check name
  and the raw row as JSON, so someone can fix it at the source and re-run.
* fixed: one value was unusable (e.g. email 'n/a') and was set to null; the row stays.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

import pandas as pd

from onboard.config import AS_OF_DATE

MIN_DATE = pd.Timestamp("2000-01-01")
MAX_DATE = pd.Timestamp(AS_OF_DATE) + pd.Timedelta(days=366)
MAX_INVOICE_USD = 1_000_000


@dataclass
class QualityLog:
    quarantine: list[dict] = field(default_factory=list)
    summary: list[dict] = field(default_factory=list)

    def hold(self, table_key: str, df: pd.DataFrame, raw: pd.DataFrame, mask: pd.Series, check: str,
             detail: str, key_col: str | None) -> pd.DataFrame:
        """Move rows where mask is True into quarantine; return the remaining rows."""
        mask = mask.fillna(False).astype(bool)
        if mask.any():
            for idx, row in df[mask].iterrows():
                self.quarantine.append({
                    "table_key": table_key, "row_number": int(row["_row"]),
                    "row_key": None if key_col is None or pd.isna(row.get(key_col)) else str(row.get(key_col)),
                    "check_name": check, "detail": detail,
                    "raw_json": json.dumps(raw.loc[idx].to_dict(), default=str),
                })
        self.summary.append({"table_key": table_key, "check_name": check, "severity": "quarantine",
                             "rows_affected": int(mask.sum()), "rows_checked": int(len(df))})
        return df[~mask]

    def soft_issues(self, table_key: str, df: pd.DataFrame) -> None:
        counts: dict[str, int] = {}
        for lst in df["_issues"]:
            for i in lst:
                counts[i] = counts.get(i, 0) + 1
        for check, n in sorted(counts.items()):
            self.summary.append({"table_key": table_key, "check_name": check, "severity": "fixed (set to null)",
                                 "rows_affected": n, "rows_checked": int(len(df))})

    def frames(self, run_id: str) -> tuple[pd.DataFrame, pd.DataFrame]:
        q = pd.DataFrame(self.quarantine, columns=["table_key", "row_number", "row_key", "check_name", "detail",
                                                   "raw_json"])
        q.insert(0, "run_id", run_id)
        s = pd.DataFrame(self.summary, columns=["table_key", "check_name", "severity", "rows_affected", "rows_checked"])
        s.insert(0, "run_id", run_id)
        return q, s


def missing(df: pd.DataFrame, col: str) -> pd.Series:
    if col not in df:
        return pd.Series(True, index=df.index)
    return df[col].isna()


def duplicated(df: pd.DataFrame, col: str) -> pd.Series:
    return df[col].duplicated(keep="first") & df[col].notna()


def out_of_date_range(s: pd.Series) -> pd.Series:
    return s.notna() & ((s < MIN_DATE) | (s > MAX_DATE))
