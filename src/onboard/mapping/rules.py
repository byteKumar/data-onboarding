"""Deterministic mapping rules: name synonyms, then name tokens, then value patterns.

The synonym lists are generic export vocabulary (Salesforce, HubSpot, Zendesk, Stripe,
QuickBooks style names). They were NOT derived from the ground-truth file, but the same
person wrote both the generator and these rules, so rules-only accuracy on the three
known sources is optimistic. The unseen 4th source is the honest test.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from onboard.profiling import ColumnProfile
from onboard.schema import FIELDS

PRIMARY_REF = {"customer": "customer_ref", "account": "account_ref", "invoice": "invoice_ref",
               "ticket": "ticket_ref"}
ENTITY_DATE = {"customer": "signup_date", "account": "signup_date", "invoice": "invoice_date",
               "ticket": "ticket_created_at"}
ENTITY_STATUS = {"invoice": "invoice_status", "ticket": "ticket_status"}

# Exact normalized name -> field. "@ref", "@date", "@status" resolve per table entity.
SYNONYMS: dict[str, str] = {}
_SYN = {
    "@ref": ["id", "key", "ref", "reference", "record id"],
    "customer_ref": ["customer id", "cust id", "customer number", "customer no", "cust no", "contact id",
                     "client id", "user id", "requester id", "customer ref", "lead id", "client no"],
    "full_name": ["name", "full name", "contact name", "customer name", "client name", "requester name",
                  "display name"],
    "first_name": ["first name", "firstname", "given name", "fname", "forename"],
    "last_name": ["last name", "lastname", "surname", "family name", "lname"],
    "email": ["email", "e mail", "email address", "e mail address", "mail", "contact email", "billing email",
              "requester email", "customer email", "work email", "email id"],
    "phone": ["phone", "phone number", "telephone", "tel", "mobile", "phone no", "contact phone",
              "requester phone", "cell", "phone num"],
    "street": ["street", "address", "address line 1", "address1", "street address", "billing street",
               "mailing street", "addr line 1", "addr1"],
    "city": ["city", "town", "billing city", "mailing city"],
    "state": ["state", "province", "region", "state province", "billing state", "mailing state", "state code"],
    "postal_code": ["zip", "zip code", "zipcode", "postal code", "postcode", "zip postal", "postal", "billing zip"],
    "country": ["country", "country code", "nation"],
    "company_name": ["company", "company name", "organization", "organization name", "org", "org name",
                     "account name", "business name", "employer"],
    "segment": ["segment", "customer segment", "market segment"],
    "@date": ["created", "created date", "created at", "date created", "creation date", "created on"],
    "signup_date": ["signup date", "sign up date", "customer since", "start date", "opened", "open date",
                    "account opened", "since"],
    "account_ref": ["account id", "acct id", "account number", "acct no", "account no", "billing account",
                    "account ref", "acct"],
    "plan": ["plan", "plan name", "plan code", "subscription plan", "product plan", "package"],
    "invoice_ref": ["invoice id", "invoice number", "invoice no", "inv no", "inv number", "invoice ref",
                    "invoice"],
    "invoice_date": ["invoice date", "inv date", "issue date", "issued", "billing date", "date issued",
                     "issued on"],
    "due_date": ["due date", "due", "payment due", "due on", "due by"],
    "amount": ["amount", "total", "amount due", "invoice amount", "amt", "total amount", "grand total"],
    "currency": ["currency", "currency code", "ccy", "curr"],
    "@status": ["status", "state status"],
    "invoice_status": ["invoice status", "payment status"],
    "ticket_ref": ["ticket id", "ticket number", "ticket no", "case id", "case number"],
    "ticket_created_at": ["opened at", "date opened", "submitted at", "submitted"],
    "ticket_status": ["ticket status", "case status"],
    "priority": ["priority", "severity", "urgency"],
    "subject": ["subject", "title", "summary"],
    "csat_score": ["csat", "csat score", "satisfaction", "satisfaction score", "rating"],
}
for _field, _names in _SYN.items():
    for _n in _names:
        SYNONYMS[_n] = _field

QUALIFIERS = {"requester", "customer", "contact", "client", "billing", "bill", "to", "mailing", "primary",
              "user", "main", "the"}

# (tokens that must all appear, field, confidence)
TOKEN_RULES: list[tuple[frozenset[str], str, float]] = [
    (frozenset({"email"}), "email", 0.75), (frozenset({"mail"}), "email", 0.7),
    (frozenset({"phone"}), "phone", 0.75), (frozenset({"tel"}), "phone", 0.7),
    (frozenset({"mobile"}), "phone", 0.7),
    (frozenset({"zip"}), "postal_code", 0.75), (frozenset({"postal"}), "postal_code", 0.75),
    (frozenset({"city"}), "city", 0.75), (frozenset({"town"}), "city", 0.7),
    (frozenset({"province"}), "state", 0.7), (frozenset({"region"}), "state", 0.6),
    (frozenset({"street"}), "street", 0.7), (frozenset({"addr"}), "street", 0.6),
    (frozenset({"address"}), "street", 0.6),
    (frozenset({"country"}), "country", 0.7),
    (frozenset({"company"}), "company_name", 0.7), (frozenset({"organization"}), "company_name", 0.7),
    (frozenset({"segment"}), "segment", 0.7),
    (frozenset({"plan"}), "plan", 0.7),
    (frozenset({"currency"}), "currency", 0.7), (frozenset({"ccy"}), "currency", 0.7),
    (frozenset({"amount"}), "amount", 0.7), (frozenset({"amt"}), "amount", 0.7),
    (frozenset({"total"}), "amount", 0.6),
    (frozenset({"due"}), "due_date", 0.7),
    (frozenset({"priority"}), "priority", 0.7),
    (frozenset({"subject"}), "subject", 0.7),
    (frozenset({"csat"}), "csat_score", 0.75), (frozenset({"satisfaction"}), "csat_score", 0.7),
    (frozenset({"status"}), "@status", 0.65),
    (frozenset({"first", "name"}), "first_name", 0.7), (frozenset({"last", "name"}), "last_name", 0.7),
    (frozenset({"created"}), "@date", 0.6), (frozenset({"opened"}), "@date", 0.55),
    (frozenset({"name"}), "full_name", 0.5),
    (frozenset({"id"}), "@ref", 0.5), (frozenset({"no"}), "@ref", 0.45), (frozenset({"number"}), "@ref", 0.45),
]

PATTERN_RULES = {"email": ("email", 0.6), "phone": ("phone", 0.55)}


@dataclass
class RuleHit:
    target: str
    confidence: float
    method: str
    why: str


def normalize(col: str) -> str:
    s = col.replace("#", " no ").replace("&", " and ")
    s = re.sub(r"([a-z])([A-Z])", r"\1 \2", s)  # camelCase
    s = re.sub(r"([A-Za-z])(\d)", r"\1 \2", s)
    s = re.sub(r"[^A-Za-z0-9]+", " ", s).strip().lower()
    return re.sub(r"\s+", " ", s)


def _resolve(target: str, entity: str) -> str | None:
    if target == "@ref":
        return PRIMARY_REF.get(entity)
    if target == "@date":
        return ENTITY_DATE.get(entity)
    if target == "@status":
        return ENTITY_STATUS.get(entity)
    return target if entity in FIELDS[target].entities else None


def rule_candidates(col: str, profile: ColumnProfile, entity: str) -> list[RuleHit]:
    """All rule hits for a column, strongest first. The engine validates them in order."""
    hits: list[RuleHit] = []
    name = normalize(col)
    if name in SYNONYMS and (t := _resolve(SYNONYMS[name], entity)):
        hits.append(RuleHit(t, 0.95, "rule:synonym", f"name '{name}' is a known synonym"))
    stripped = " ".join(w for w in name.split() if w not in QUALIFIERS)
    if stripped and stripped != name and stripped in SYNONYMS and (t := _resolve(SYNONYMS[stripped], entity)):
        hits.append(RuleHit(t, 0.85, "rule:synonym", f"'{stripped}' after dropping qualifiers"))
    tokens = set(name.split())
    for need, target, conf in TOKEN_RULES:
        if need <= tokens and (t := _resolve(target, entity)):
            hits.append(RuleHit(t, conf, "rule:token", f"name contains {sorted(need)}"))
    if profile.pattern in PATTERN_RULES:
        t, conf = PATTERN_RULES[profile.pattern]
        if entity in FIELDS[t].entities:
            hits.append(RuleHit(t, conf, "rule:pattern", f"values look like {profile.pattern}"))
    seen, out = set(), []
    for h in hits:
        if h.target not in seen:
            seen.add(h.target)
            out.append(h)
    return out
