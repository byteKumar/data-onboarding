from datetime import datetime

import pandas as pd

from onboard.profiling import profile_column


def pat(values):
    return profile_column("c", pd.Series(values, dtype=object)).pattern


def test_patterns():
    assert pat(["a@x.com", "b@y.org", "c@z.io", None]) == "email"
    assert pat(["(617) 555-0101", "617.555.0102", "+1 617 555 0103", "6175550104"]) == "phone"
    assert pat(["03/11/2023", "2023-01-05", "12/30/2022"]) == "date"
    assert pat(["05.03.2024", datetime(2024, 3, 6), "07.03.2024"]) == "date"
    assert pat(["$1,234.50", "€1.234,50", "GBP 99.00", "12.00"]) == "currency"
    assert pat(["02139", "10001", "94103", "2139"]) == "zip"
    assert pat([f"CRM-{i:07d}" for i in range(50)]) == "id"
    assert pat(["open", "closed", "open", "pending"] * 10) == "category"


def test_profile_stats():
    p = profile_column("c", pd.Series(["a", "b", None, "a"], dtype=object))
    assert p.null_rate == 0.25
    assert p.distinct_count == 2
    assert p.samples == ["a", "b"]


def test_epoch_ints_only_dates_when_column_is_mostly_dates():
    assert pat([1736000000, 1736100000, 1736200000]) != "date"
    assert pat(["2025-01-05T10:00:00Z", "2025-01-06T10:00:00Z", 1736200000]) == "date"
