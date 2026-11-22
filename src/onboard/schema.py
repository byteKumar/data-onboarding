"""The unified target schema: every field a source column can be mapped to.

Each field says which table entities it may appear in and which profiled
pattern classes are compatible with it. Validators use both.
"""

from __future__ import annotations

from dataclasses import dataclass

ENTITIES = ("customer", "account", "invoice", "ticket")

# Pattern classes produced by profiling.py
PATTERNS = ("email", "phone", "date", "currency", "zip", "id", "number", "category", "text", "empty")

PERSON = ("customer", "account", "ticket")


@dataclass(frozen=True)
class TargetField:
    name: str
    entities: tuple[str, ...]
    patterns: tuple[str, ...]  # compatible pattern classes
    description: str
    max_distinct: int | None = None  # category-like fields
    min_distinct_ratio: float | None = None  # key-like fields


FIELDS: dict[str, TargetField] = {
    f.name: f
    for f in [
        TargetField("customer_ref", PERSON, ("id", "number", "text", "category"),
                    "Source system's identifier for the customer/contact/requester"),
        TargetField("full_name", PERSON, ("text", "category"), "Person's full name"),
        TargetField("first_name", PERSON, ("text", "category"), "Given name"),
        TargetField("last_name", PERSON, ("text", "category"), "Family name"),
        TargetField("email", PERSON, ("email",), "Email address"),
        TargetField("phone", PERSON, ("phone",), "Phone number"),
        TargetField("street", PERSON, ("text",), "Street address line"),
        TargetField("city", PERSON, ("text", "category"), "City"),
        TargetField("state", PERSON, ("category", "text"), "State or region code", max_distinct=80),
        TargetField("postal_code", PERSON, ("zip", "number"), "ZIP / postal code"),
        TargetField("country", PERSON, ("category", "text"), "Country", max_distinct=300),
        TargetField("company_name", PERSON, ("text", "category"), "Customer's company / organization"),
        TargetField("segment", ("customer", "account"), ("category",), "Market segment (Enterprise, Mid-Market, SMB)",
                    max_distinct=30),
        TargetField("signup_date", ("customer", "account"), ("date",), "Date the customer or account was created"),
        TargetField("account_ref", ("account", "invoice"), ("id", "text", "number"),
                    "Billing account identifier"),
        TargetField("plan", ("account", "customer"), ("category",), "Subscription plan / tier code", max_distinct=50),
        TargetField("invoice_ref", ("invoice",), ("id", "text", "number"), "Invoice number",
                    min_distinct_ratio=0.95),
        TargetField("invoice_date", ("invoice",), ("date",), "Invoice issue date"),
        TargetField("due_date", ("invoice",), ("date",), "Invoice due date"),
        TargetField("amount", ("invoice",), ("currency", "number"), "Invoice amount (any currency)"),
        TargetField("currency", ("invoice",), ("category",), "ISO currency code", max_distinct=20),
        TargetField("invoice_status", ("invoice",), ("category",), "Paid / unpaid / overdue / void",
                    max_distinct=20),
        TargetField("ticket_ref", ("ticket",), ("id", "text", "number"), "Support ticket identifier",
                    min_distinct_ratio=0.95),
        TargetField("ticket_created_at", ("ticket",), ("date",), "When the ticket was opened"),
        TargetField("ticket_status", ("ticket",), ("category",), "open / pending / solved / closed",
                    max_distinct=20),
        TargetField("priority", ("ticket",), ("category",), "Ticket priority", max_distinct=20),
        TargetField("subject", ("ticket",), ("text",), "Ticket subject line"),
        TargetField("csat_score", ("ticket",), ("number", "category"), "Satisfaction score 1-5", max_distinct=20),
    ]
}

IGNORE = "ignore"
ALL_TARGETS = (*FIELDS.keys(), IGNORE)


def fields_for_entity(entity: str) -> list[str]:
    return [f.name for f in FIELDS.values() if entity in f.entities]
