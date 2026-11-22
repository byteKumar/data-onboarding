import pandas as pd

from onboard.mapping.engine import suggest
from onboard.mapping.rules import normalize, rule_candidates
from onboard.mapping.validators import validate
from onboard.profiling import profile_column


def prof(name, values):
    return profile_column(name, pd.Series(values, dtype=object))


EMAILS = ["a@x.com", "b@y.com", "c@z.com", "d@w.com"]
DATES = ["03/11/2023", "04/11/2023", "05/11/2023", "06/11/2023"]


def test_normalize():
    assert normalize("E-mail Address") == "e mail address"
    assert normalize("Phone #") == "phone no"
    assert normalize("requester.emailAddress") == "requester email address"
    assert normalize("addr_line1") == "addr line 1"


def test_validator_rejects_email_into_date():
    reason = validate("signup_date", prof("x", EMAILS), "customer")
    assert reason and "pattern mismatch" in reason


def test_validator_rejects_wrong_entity():
    assert "does not belong" in validate("invoice_date", prof("x", DATES), "customer")


def test_validator_rejects_non_unique_key():
    assert "unique" in validate("invoice_ref", prof("x", ["A1", "A1", "A2", "A2"]), "invoice")


def test_entity_aware_rules():
    p = prof("created", DATES)
    assert rule_candidates("created", p, "customer")[0].target == "signup_date"
    p = prof("id", [f"T-{i}" for i in range(20)])
    assert rule_candidates("id", p, "ticket")[0].target == "ticket_ref"


def test_rule_with_wrong_pattern_falls_through_and_is_counted():
    # a column called "email" that actually holds dates must not be mapped to email
    res = suggest("customer", [prof("email", DATES)])
    s = res.suggestions[0]
    assert s.target is None
    assert res.stats()["rule_proposals_rejected"] >= 1


def test_one_column_per_field():
    res = suggest("ticket", [prof("requester.name", ["Ann Lee", "Bo Chu", "Cy Dee"] * 30),
                             prof("assignee.name", ["Kim", "Ravi", "Owen"] * 30)])
    by = {s.column: s.target for s in res.suggestions}
    assert by["requester.name"] == "full_name"
    assert by["assignee.name"] is None
