from datetime import datetime

import pandas as pd
import pytest

from onboard.parsing import normalize_phone, normalize_zip, parse_date_series, parse_money
from onboard.transform import canon_invoice_status, canon_segment, clean_name, normalize_table


@pytest.mark.parametrize("raw,amount,ccy", [
    ("$1,234.50", 1234.5, "USD"), ("€1.234,50", 1234.5, "EUR"), ("1234,50 EUR", 1234.5, "EUR"),
    ("GBP 99.00", 99.0, "GBP"), ("£1,000.00", 1000.0, "GBP"), ("-$12.00", -12.0, "USD"), ("88", 88.0, None),
])
def test_money(raw, amount, ccy):
    assert parse_money(raw) == (amount, ccy)


def test_dates_mixed_formats_and_types():
    s = pd.Series(["05.03.2024", "06.03.2024", datetime(2024, 3, 7), None], dtype=object)
    out = parse_date_series(s)
    assert out.iloc[0] == pd.Timestamp("2024-03-05")  # day-first chosen for the column
    assert out.iloc[2] == pd.Timestamp("2024-03-07")
    assert pd.isna(out.iloc[3])
    epoch = parse_date_series(pd.Series(["2025-01-05T10:00:00Z", 1736200000], dtype=object))
    assert epoch.notna().all()


def test_small_values():
    assert normalize_phone("+1 (617) 555-0101") == "6175550101"
    assert normalize_phone("555-0101") is None
    assert normalize_zip(2139) == "02139"
    assert clean_name("SMITH, JOHN A.") == "John A. Smith"
    assert canon_segment("Ent.") == "Enterprise" and canon_segment("MM") == "Mid-Market"
    assert canon_segment("S.M.B.") == "SMB"
    assert canon_invoice_status("OVERDUE") == "overdue"


def test_normalize_table_flags_bad_values_and_converts_currency():
    raw = pd.DataFrame({"mail": ["a@x.com", "n/a"], "amt": ["€100,00", "$5.00"], "ccy": [None, "USD"]}, dtype=object)
    df = normalize_table(raw, {"mail": "email", "amt": "amount", "ccy": "currency"})
    assert df["email"].tolist()[0] == "a@x.com" and pd.isna(df["email"].tolist()[1])
    assert df["_issues"].tolist()[1] == ["invalid_email"]
    assert df["currency"].tolist() == ["EUR", "USD"]
    assert df["amount_usd"].tolist()[0] == pytest.approx(108.0)
