"""Synthetic customer exports with a hidden ground truth.

Three sources describe the same ~10K "true" customers in different ways:

* CRM contacts      -> CSV         (data/raw/crm_contacts.csv)
* Billing           -> Excel .xlsx (data/raw/billing.xlsx, sheets Accounts + Invoices)
* Support tickets   -> JSON served by a mock API (data/raw/support_tickets.json)

Messiness injected on purpose: households (different people sharing an address, last
name and often a phone: hard negatives for entity resolution), typos, nicknames, "Last, First" names, different ID
formats, renamed columns, mixed date formats, three currencies with locale
formatting, missing values, ZIP codes that lost their leading zero in Excel,
cross-system and within-system duplicates, and a few rows that must be quarantined.

The ground truth (column -> unified field, record -> true customer) is written to
data/ground_truth/. Only eval/ may read it; tests/test_no_ground_truth_leak.py
enforces that for src/onboard/.

This module is the only one in src/onboard/ that knows about ground truth,
because it creates it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from onboard.config import FX_TO_USD

BASE_ENTITIES = 10_300  # ~20K customer records across the three sources at scale 1
WINDOW_START = date(2024, 1, 1)
AS_OF = date(2025, 6, 30)

FIRST = (
    "James Mary Robert Patricia John Jennifer Michael Linda William Elizabeth David Barbara Richard Susan "
    "Joseph Jessica Thomas Sarah Charles Karen Christopher Lisa Daniel Nancy Matthew Betty Anthony Margaret "
    "Mark Sandra Donald Ashley Steven Kimberly Paul Emily Andrew Donna Joshua Michelle Kenneth Carol Kevin "
    "Amanda Brian Dorothy George Melissa Timothy Deborah Ronald Stephanie Edward Rebecca Jason Sharon Jeffrey "
    "Laura Ryan Cynthia Jacob Kathleen Gary Amy Nicholas Angela Eric Shirley Jonathan Anna Stephen Brenda "
    "Larry Pamela Justin Emma Scott Nicole Brandon Helen Benjamin Samantha Samuel Katherine Gregory Christine "
    "Alexander Debra Frank Rachel Patrick Carolyn Raymond Janet Jack Catherine Dennis Maria Jerry Heather "
    "Tyler Diane Aaron Ruth Jose Julie Adam Olivia Nathan Joyce Henry Virginia Douglas Victoria Zachary Kelly "
    "Peter Lauren Kyle Christina Ethan Joan Walter Evelyn Noah Judith Jeremy Megan Christian Andrea Keith "
    "Cheryl Roger Hannah Terry Jacqueline Gerald Martha Harold Gloria Sean Teresa Austin Ann Carl Sara Arthur "
    "Madison Lawrence Frances Dylan Kathryn Jesse Janice Jordan Jean Bryan Abigail Billy Alice Joe Julia "
    "Bruce Judy Gabriel Sophia Logan Grace Albert Denise Alan Doris Juan Marilyn Wayne Danielle Elijah "
    "Beverly Randy Isabella Roy Theresa Vincent Diana Ralph Natalie Eugene Brittany Russell Charlotte Mason "
    "Kayla Philip Alexis Louis Lori Priya Wei Arjun Mei Rahul Yuki Omar Fatima Carlos Sofia Diego Ana Luis "
    "Chen Aisha Raj Ling Hiro Amir Leila Kwame Ngozi Ivan Olga Lars Ingrid Mateo Lucia"
).split()

LAST = (
    "Smith Johnson Williams Brown Jones Garcia Miller Davis Rodriguez Martinez Hernandez Lopez Gonzalez Wilson "
    "Anderson Thomas Taylor Moore Jackson Martin Lee Perez Thompson White Harris Sanchez Clark Ramirez Lewis "
    "Robinson Walker Young Allen King Wright Scott Torres Nguyen Hill Flores Green Adams Nelson Baker Hall "
    "Rivera Campbell Mitchell Carter Roberts Gomez Phillips Evans Turner Diaz Parker Cruz Edwards Collins "
    "Reyes Stewart Morris Morales Murphy Cook Rogers Gutierrez Ortiz Morgan Cooper Peterson Bailey Reed Kelly "
    "Howard Ramos Kim Cox Ward Richardson Watson Brooks Chavez Wood James Bennett Gray Mendoza Ruiz Hughes "
    "Price Alvarez Castillo Sanders Patel Myers Long Ross Foster Jimenez Powell Jenkins Perry Russell "
    "Sullivan Bell Coleman Butler Henderson Barnes Gonzales Fisher Vasquez Simmons Romero Jordan Patterson "
    "Alexander Hamilton Graham Reynolds Griffin Wallace Moreno West Cole Hayes Bryant Herrera Gibson Ellis "
    "Tran Medina Aguilar Stevens Murray Ford Castro Marshall Owens Harrison Fernandez McDonald Woods "
    "Washington Kennedy Wells Vargas Henry Chen Freeman Webb Tucker Guzman Burns Crawford Olson Simpson "
    "Porter Hunter Gordon Mendez Silva Shaw Snyder Mason Dixon Munoz Hunt Hicks Holmes Palmer Wagner Black "
    "Robertson Boyd Rose Stone Salazar Fox Warren Mills Meyer Rice Schmidt Garza Daniels Ferguson Nichols "
    "Stephens Soto Weaver Ryan Gardner Payne Grant Dunn Kelley Spencer Hawkins Arnold Pierce Vazquez Hansen "
    "Peters Santos Hart Bradley Knight Elliott Cunningham Duncan Armstrong Hudson Carroll Lane Riley Andrews "
    "Alvarado Ray Delgado Berry Perkins Hoffman Johnston Matthews Pena Richards Contreras Willis Carpenter "
    "Lawrence Sandoval Guerrero George Chapman Rios Estrada Ortega Watkins Greene Nunez Wheeler Valdez Harper "
    "Burke Larson Santiago Maldonado Morrison Franklin Carlson Austin Dominguez Carr Lawson Jacobs Obrien "
    "Lynch Singh Vega Bishop Montgomery Oliver Jensen Harvey Williamson Gilbert Dean Sims Espinoza Howell Li "
    "Wong Reid Hanson Le McCoy Garrett Burton Fuller Wang Weber Welch Rojas Lucas Marquez Fields Park Yang "
    "Little Banks Padilla Day Walsh Bowman Schultz Luna Fowler Mejia Davidson Acosta Brewer May Holland Juarez"
).split()

NICK = {
    "Robert": "Bob", "William": "Bill", "Elizabeth": "Liz", "James": "Jim", "Michael": "Mike",
    "Richard": "Rick", "Joseph": "Joe", "Thomas": "Tom", "Charles": "Chuck", "Christopher": "Chris",
    "Daniel": "Dan", "Matthew": "Matt", "Anthony": "Tony", "Steven": "Steve", "Andrew": "Andy",
    "Joshua": "Josh", "Kenneth": "Ken", "Timothy": "Tim", "Ronald": "Ron", "Edward": "Ed",
    "Jonathan": "Jon", "Nicholas": "Nick", "Jennifer": "Jen", "Patricia": "Pat", "Katherine": "Kate",
    "Margaret": "Peggy", "Samantha": "Sam", "Benjamin": "Ben", "Alexander": "Alex", "Gregory": "Greg",
    "Kimberly": "Kim", "Deborah": "Deb", "Rebecca": "Becky", "Victoria": "Vicky", "Abigail": "Abby",
}

CITIES = [
    ("Boston", "MA", "021", "617"), ("Cambridge", "MA", "021", "617"), ("Worcester", "MA", "016", "508"),
    ("New York", "NY", "100", "212"), ("Brooklyn", "NY", "112", "718"), ("Buffalo", "NY", "142", "716"),
    ("Newark", "NJ", "071", "973"), ("Hartford", "CT", "061", "860"), ("Providence", "RI", "029", "401"),
    ("Philadelphia", "PA", "191", "215"), ("Pittsburgh", "PA", "152", "412"), ("Baltimore", "MD", "212", "410"),
    ("Washington", "DC", "200", "202"), ("Richmond", "VA", "232", "804"), ("Charlotte", "NC", "282", "704"),
    ("Raleigh", "NC", "276", "919"), ("Atlanta", "GA", "303", "404"), ("Miami", "FL", "331", "305"),
    ("Orlando", "FL", "328", "407"), ("Tampa", "FL", "336", "813"), ("Chicago", "IL", "606", "312"),
    ("Detroit", "MI", "482", "313"), ("Columbus", "OH", "432", "614"), ("Cleveland", "OH", "441", "216"),
    ("Indianapolis", "IN", "462", "317"), ("Minneapolis", "MN", "554", "612"), ("St. Louis", "MO", "631", "314"),
    ("Nashville", "TN", "372", "615"), ("Dallas", "TX", "752", "214"), ("Houston", "TX", "770", "713"),
    ("Austin", "TX", "787", "512"), ("San Antonio", "TX", "782", "210"), ("Denver", "CO", "802", "303"),
    ("Phoenix", "AZ", "850", "602"), ("Salt Lake City", "UT", "841", "801"), ("Las Vegas", "NV", "891", "702"),
    ("Los Angeles", "CA", "900", "213"), ("San Diego", "CA", "921", "619"), ("San Francisco", "CA", "941", "415"),
    ("San Jose", "CA", "951", "408"), ("Sacramento", "CA", "958", "916"), ("Portland", "OR", "972", "503"),
    ("Seattle", "WA", "981", "206"),
]
STREET_NAMES = (
    "Main Oak Pine Maple Cedar Elm Washington Lake Hill Park Sunset Highland Church Mill River Spring Ridge "
    "Forest Meadow Union Franklin Jefferson Lincoln Madison Center Walnut Chestnut Willow Broad Market Water "
    "School Bridge Harbor Prospect Summit Valley Grove Adams Jackson"
).split()
SUFFIXES = [("Street", "St"), ("Avenue", "Ave"), ("Road", "Rd"), ("Boulevard", "Blvd"), ("Lane", "Ln"),
            ("Drive", "Dr"), ("Court", "Ct"), ("Place", "Pl")]
CO_A = ("Blue Bright North Summit Apex Clear Iron Silver Green Red Quantum Pioneer Harbor Atlas Nova Vertex "
        "Cedar Granite Lumen Orbit Prairie Coastal Metro Union Polar Copper Falcon Beacon").split()
CO_B = ("Labs Systems Logistics Analytics Health Foods Media Partners Works Dynamics Solutions Ventures Studio "
        "Supply Robotics Energy Capital Networks Retail Bio").split()
FREE_DOMAINS = ["gmail.com", "yahoo.com", "outlook.com", "hotmail.com", "icloud.com"]
FREE_P = [0.45, 0.15, 0.15, 0.1, 0.15]
SEGMENTS = ["Enterprise", "Mid-Market", "SMB"]
SEGMENT_SPELLINGS = {
    "Enterprise": ["Enterprise", "ENT", "enterprise", "Ent."],
    "Mid-Market": ["Mid-Market", "MM", "mid market", "Midmarket"],
    "SMB": ["SMB", "Small Business", "smb", "S.M.B."],
}
PLANS = {"Enterprise": ["ENT", "ENT-XL"], "Mid-Market": ["PRO"], "SMB": ["BAS", "STARTER"]}
QUARTERLY_PRICE = {"STARTER": (90, 300), "BAS": (150, 600), "PRO": (1500, 6000), "ENT": (15000, 60000),
                   "ENT-XL": (40000, 120000)}
REPS = ["Dana Whitfield", "Marcus Lee", "Priya Raman", "Tom Becker", "Elena Rossi", "Sam Okafor",
        "Grace Liu", "Jon Mercer", "Aisha Khan", "Victor Hale", "Nora Quinn", "Leo Park"]
AGENTS = ["Kim (Support)", "Ravi S.", "Megan T.", "Lucas B.", "Ines M.", "Owen P.", "Hana Y."]
SUBJECTS = ["Cannot log in", "Invoice question", "Feature request: bulk export", "Error when exporting report",
            "How do I add users?", "API rate limit exceeded", "Password reset not working",
            "Integration with Salesforce failing", "Dashboard is slow", "SSO setup help"]
TROUBLE_SUBJECTS = ["Billing discrepancy", "Cancel subscription", "Data missing after sync", "Outage again?"]

# Ground truth: column -> unified field, per (source, table)
CRM_MAP = {
    "Contact ID": "customer_ref", "Full Name": "full_name", "E-mail Address": "email", "Phone #": "phone",
    "Mailing Street": "street", "Mailing City": "city", "Mailing State": "state", "Zip/Postal": "postal_code",
    "Account Name": "company_name", "Tier": "segment", "Created Date": "signup_date",
    "Lead Source": "ignore", "Owner": "ignore", "Last Activity": "ignore",
}
ACCOUNTS_MAP = {
    "cust_no": "customer_ref", "acct_id": "account_ref", "bill_to_name": "full_name",
    "billing_email": "email", "tel": "phone", "addr_line1": "street", "town": "city", "region": "state",
    "zip": "postal_code", "company": "company_name", "plan_code": "plan", "opened": "signup_date",
    "currency_pref": "ignore",
}
INVOICES_MAP = {
    "Inv #": "invoice_ref", "acct_id": "account_ref", "Inv Date": "invoice_date", "Due": "due_date",
    "Amt": "amount", "CCY": "currency", "Status": "invoice_status", "memo": "ignore",
}
TICKETS_MAP = {
    "id": "ticket_ref", "created_at": "ticket_created_at", "status": "ticket_status", "priority": "priority",
    "subject": "subject", "tags": "ignore", "requester.id": "customer_ref", "requester.name": "full_name",
    "requester.email": "email", "requester.phone": "phone", "organization.name": "company_name",
    "via.channel": "ignore", "assignee.name": "ignore", "satisfaction_rating.score": "csat_score",
}


@dataclass
class Entity:
    eid: str
    first: str
    middle: str
    last: str
    email: str
    alt_email: str
    phone: str
    alt_phone: str
    street_no: int
    street_name: str
    suffix: tuple[str, str]
    city: str
    state: str
    zip: str
    company: str
    segment: str
    signup: date
    troubled: bool


class Gen:
    def __init__(self, seed: int):
        self.rng = np.random.default_rng(seed)

    def r(self) -> float:
        return float(self.rng.random())

    def pick(self, seq, p=None):
        return seq[int(self.rng.choice(len(seq), p=p))]

    def typo(self, s: str) -> str:
        if len(s) < 4:
            return s
        i = int(self.rng.integers(1, len(s) - 2))
        op = int(self.rng.integers(3))
        if op == 0:
            return s[:i] + s[i + 1] + s[i] + s[i + 2:]
        if op == 1:
            return s[:i] + s[i + 1:]
        return s[:i] + s[i] + s[i:]


def _make_entities(g: Gen, n: int) -> list[Entity]:
    ents = []
    used_emails: set[str] = set()
    for i in range(n):
        first, last = g.pick(FIRST), g.pick(LAST)
        middle = g.pick("ABCDEFGHJKLMNPRSTW") if g.r() < 0.35 else ""
        city, state, zip3, area = g.pick(CITIES)
        company = f"{g.pick(CO_A)} {g.pick(CO_B)}"
        if g.r() < 0.5:
            company += " " + g.pick(["Inc", "LLC", "Co", "Corp"])
        segment = g.pick(SEGMENTS, p=[0.15, 0.3, 0.55])
        work_domain = company.split()[0].lower() + company.split()[1].lower() + ".com"
        styles = [f"{first}.{last}", f"{first[0]}{last}", f"{first}{last[0]}", f"{first}_{last}",
                  f"{first}{last}{int(g.rng.integers(1, 99))}"]
        local = g.pick(styles).lower()
        domain = work_domain if g.r() < 0.5 else g.pick(FREE_DOMAINS, p=FREE_P)
        email = f"{local}@{domain}"
        while email in used_emails:
            email = f"{local}{int(g.rng.integers(100, 999))}@{domain}"
        used_emails.add(email)
        alt_email = ""
        if g.r() < 0.12:
            alt_email = f"{first.lower()}.{last.lower()}{int(g.rng.integers(1, 999))}@{g.pick(FREE_DOMAINS)}"
        phone = area + str(int(g.rng.integers(2, 10))) + f"{int(g.rng.integers(0, 1_000_000)):06d}"
        alt_phone = ""
        if g.r() < 0.1:
            alt_phone = area + str(int(g.rng.integers(2, 10))) + f"{int(g.rng.integers(0, 1_000_000)):06d}"
        signup = date(2019, 1, 1) + timedelta(days=int(g.rng.integers(0, 365 * 6 - 200)))
        ents.append(Entity(
            eid=f"E{i:07d}", first=first, middle=middle, last=last, email=email, alt_email=alt_email,
            phone=phone, alt_phone=alt_phone, street_no=int(g.rng.integers(1, 9999)),
            street_name=g.pick(STREET_NAMES), suffix=g.pick(SUFFIXES), city=city, state=state,
            zip=zip3 + f"{int(g.rng.integers(0, 100)):02d}", company=company, segment=segment, signup=signup,
            troubled=g.r() < 0.08,
        ))
    # Households: a second, different person at the same address, same last name, often the same
    # landline. These are the hard negatives an entity resolver must NOT merge.
    for j in range(int(n * 0.04)):
        base = ents[int(g.rng.integers(n))]
        first = g.pick([f for f in FIRST if f != base.first])
        domain = g.pick(FREE_DOMAINS, p=FREE_P)
        email = f"{first.lower()}.{base.last.lower()}{int(g.rng.integers(1, 99))}@{domain}"
        while email in used_emails:
            email = f"{first.lower()}{int(g.rng.integers(100, 999))}@{domain}"
        used_emails.add(email)
        company = f"{g.pick(CO_A)} {g.pick(CO_B)}"
        phone = base.phone if g.r() < 0.5 else base.phone[:3] + str(int(g.rng.integers(2, 10))) + \
            f"{int(g.rng.integers(0, 1_000_000)):06d}"
        ents.append(Entity(
            eid=f"H{j:07d}", first=first, middle="", last=base.last, email=email, alt_email="", phone=phone,
            alt_phone="", street_no=base.street_no, street_name=base.street_name, suffix=base.suffix,
            city=base.city, state=base.state, zip=base.zip, company=company,
            segment=g.pick(SEGMENTS, p=[0.15, 0.3, 0.55]),
            signup=date(2019, 1, 1) + timedelta(days=int(g.rng.integers(0, 365 * 6 - 200))), troubled=g.r() < 0.08,
        ))
    return ents


# ---------------------------------------------------------------- messy renderers

def messy_name(g: Gen, e: Entity, last_first_p: float = 0.0, upper_p: float = 0.05) -> str | None:
    first, last = e.first, e.last
    if first in NICK and g.r() < 0.1:
        first = NICK[first]
    x = g.r()
    if x < 0.06:
        last = g.typo(last)
    elif x < 0.10:
        first = g.typo(first)
    mid = f" {e.middle}." if e.middle and g.r() < 0.3 else ""
    name = f"{first}{mid} {last}"
    if g.r() < last_first_p:
        name = f"{last}, {first}{mid}"
    x = g.r()
    if x < upper_p:
        name = name.upper()
    elif x < upper_p + 0.03:
        name = name.lower()
    if g.r() < 0.03:
        name = " " + name.replace(" ", "  ") + " "
    return name


def messy_email(g: Gen, e: Entity, missing_p: float) -> str | None:
    if g.r() < missing_p:
        return None
    email = e.alt_email if e.alt_email and g.r() < 0.35 else e.email
    x = g.r()
    if x < 0.06:
        email = email.upper()
    elif x < 0.12:
        email = email[0].upper() + email[1:]
    if g.r() < 0.02:
        email = email.replace("gmail.com", "gmial.com").replace("yahoo.com", "yaho.com")
    if g.r() < 0.03:
        email = email + " "
    return email


def fmt_phone(g: Gen, digits: str, preferred: int) -> str:
    a, b, c = digits[:3], digits[3:6], digits[6:]
    fmts = [f"({a}) {b}-{c}", f"{a}-{b}-{c}", f"{a}.{b}.{c}", f"+1 {a} {b} {c}", digits, f"1-{a}-{b}-{c}"]
    return fmts[preferred] if g.r() < 0.75 else g.pick(fmts)


def messy_phone(g: Gen, e: Entity, missing_p: float, preferred: int) -> str | None:
    if g.r() < missing_p:
        return None
    digits = e.alt_phone if e.alt_phone and g.r() < 0.4 else e.phone
    return fmt_phone(g, digits, preferred)


def messy_street(g: Gen, e: Entity, abbrev_p: float, missing_p: float = 0.05) -> str | None:
    if g.r() < missing_p:
        return None
    suf = e.suffix[1] if g.r() < abbrev_p else e.suffix[0]
    if suf == e.suffix[1] and g.r() < 0.3:
        suf += "."
    s = f"{e.street_no} {e.street_name} {suf}"
    if g.r() < 0.08:
        s += g.pick([f" Apt {int(g.rng.integers(1, 40))}", f" #{int(g.rng.integers(100, 999))}",
                     f" Suite {int(g.rng.integers(100, 999))}"])
    if g.r() < 0.05:
        s = s.upper()
    return s


# ---------------------------------------------------------------- sources

def _crm(g: Gen, ents: list[Entity]) -> tuple[pd.DataFrame, list[tuple[str, str]]]:
    rows, truth = [], []
    chosen = [e for e in ents if g.r() < 0.80]
    extra = [e for e in chosen if g.r() < 0.04]  # within-CRM duplicates
    recs = chosen + extra
    ids = g.rng.permutation(len(recs) * 3)[: len(recs)] + 1000
    for e, cid in zip(recs, ids, strict=True):
        ref = f"CRM-{int(cid):07d}"
        name = messy_name(g, e, last_first_p=0.12)
        email = messy_email(g, e, missing_p=0.06)
        x = g.r()
        if x < 0.01:  # no identity at all -> must be quarantined
            name, email = None, None
        elif x < 0.02 and email:  # broken email -> nulled by the pipeline
            email = g.pick(["n/a", email.split("@")[0] + "@", "unknown"])
        d = e.signup
        created = d.strftime("%m/%d/%Y") if g.r() < 0.85 else d.isoformat()
        last_act = datetime(2024, 1, 1) + timedelta(minutes=int(g.rng.integers(0, 60 * 24 * 540)))
        rows.append({
            "Contact ID": ref,
            "Full Name": name,
            "E-mail Address": email,
            "Phone #": messy_phone(g, e, 0.10, preferred=0),
            "Mailing Street": messy_street(g, e, abbrev_p=0.3),
            "Mailing City": e.city if g.r() > 0.04 else None,
            "Mailing State": e.state if g.r() > 0.04 else None,
            "Zip/Postal": e.zip if g.r() > 0.05 else None,
            "Account Name": e.company if g.r() > 0.1 else None,
            "Tier": g.pick(SEGMENT_SPELLINGS[e.segment]) if g.r() > 0.05 else None,
            "Created Date": created,
            "Lead Source": g.pick(["Web", "Referral", "Event", "Partner", "Outbound"]),
            "Owner": g.pick(REPS),
            "Last Activity": last_act.strftime("%Y-%m-%d %H:%M"),
        })
        truth.append((f"crm.contacts:{ref}", e.eid))
    df = pd.DataFrame(rows).sample(frac=1.0, random_state=int(g.rng.integers(1 << 31))).reset_index(drop=True)
    return df, truth


def _fmt_amount(g: Gen, amount: float, ccy: str) -> str:
    if ccy == "USD":
        return f"${amount:,.2f}" if g.r() < 0.7 else f"{amount:.2f}"
    if ccy == "EUR":
        eu = f"{amount:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        return f"€{eu}" if g.r() < 0.6 else f"{eu} EUR"
    return f"£{amount:,.2f}" if g.r() < 0.6 else f"GBP {amount:.2f}"


def _billing(g: Gen, ents: list[Entity]):
    acc_rows, inv_rows, truth = [], [], []
    chosen = [e for e in ents if g.r() < 0.60]
    extra = [e for e in chosen if g.r() < 0.03]
    recs = chosen + extra
    cust_nos = g.rng.permutation(len(recs) * 2)[: len(recs)] + 400_000
    acct_nos = g.rng.permutation(900_000)[: len(recs)] + 100_000
    inv_seq = 0
    for e, cno, ano in zip(recs, cust_nos, acct_nos, strict=True):
        acct = f"AC-{int(ano):06d}"
        plan = g.pick(PLANS[e.segment])
        ccy = g.pick(["USD", "EUR", "GBP"], p=[0.7, 0.2, 0.1])
        opened = max(e.signup + timedelta(days=int(g.rng.integers(0, 60))), date(2019, 1, 2))
        name = messy_name(g, e, upper_p=0.30)
        acc_rows.append({
            "cust_no": int(cno),
            "acct_id": acct,
            "bill_to_name": name,
            "billing_email": messy_email(g, e, missing_p=0.04),
            "tel": messy_phone(g, e, 0.15, preferred=3),
            "addr_line1": messy_street(g, e, abbrev_p=0.8),
            "town": e.city.upper() if g.r() < 0.3 else e.city,
            "region": e.state,
            "zip": int(e.zip),  # Excel number -> leading zero lost
            "company": e.company if g.r() > 0.05 else None,
            "plan_code": plan,
            "opened": opened.strftime("%d.%m.%Y"),
            "currency_pref": ccy,
        })
        truth.append((f"billing.accounts:{int(cno)}", e.eid))
        lo, hi = QUARTERLY_PRICE[plan]
        base = float(g.rng.uniform(lo, hi))
        downgrade = e.troubled and g.r() < 0.5
        q = max(opened, WINDOW_START)
        q = date(q.year, ((q.month - 1) // 3) * 3 + 1, 1)
        quarters = []
        while q <= AS_OF:
            quarters.append(q)
            q = date(q.year + (q.month + 3 > 12), (q.month + 3 - 1) % 12 + 1, 1)
        for qi, qd in enumerate(quarters):
            inv_seq += 1
            inv_date = qd + timedelta(days=int(g.rng.integers(0, 10)))
            due = inv_date + timedelta(days=30)
            usd = base * (0.4 if downgrade and qi >= len(quarters) - 2 else 1.0) * float(g.rng.uniform(0.95, 1.05))
            amount = round(usd / FX_TO_USD[ccy], 2)
            if due < AS_OF:
                paid_p = 0.70 if e.troubled else 0.97
                status = "Paid" if g.r() < paid_p else "Overdue"
            else:
                status = "Unpaid"
            if g.r() < 0.01:
                status = "Void"
            status = g.pick([status, status.upper(), status.lower()], p=[0.7, 0.15, 0.15])
            inv_ref = f"INV-{inv_date.year}-{inv_seq:07d}"
            amt_str: str | None = _fmt_amount(g, amount, ccy)
            x = g.r()
            acct_ref = acct
            if x < 0.005:
                acct_ref = f"AC-99{int(g.rng.integers(0, 9999)):04d}"  # orphan
            elif x < 0.008:
                amt_str = "-" + amt_str
            elif x < 0.013:
                amt_str = None
            elif x < 0.016:
                due = inv_date - timedelta(days=int(g.rng.integers(5, 40)))
            inv_cell: object = inv_date.strftime("%d.%m.%Y")
            if g.r() < 0.2:
                inv_cell = datetime(inv_date.year, inv_date.month, inv_date.day)  # real Excel date cell
            row = {
                "Inv #": inv_ref, "acct_id": acct_ref, "Inv Date": inv_cell, "Due": due.strftime("%d.%m.%Y"),
                "Amt": amt_str, "CCY": ccy if g.r() < 0.85 else None, "Status": status,
                "memo": g.pick(["", f"Q{(qd.month - 1) // 3 + 1} subscription", "seats true-up", "PO attached"]),
            }
            inv_rows.append(row)
            if g.r() < 0.002:
                inv_rows.append(dict(row))  # exact duplicate invoice row
    accounts = pd.DataFrame(acc_rows).sample(frac=1.0, random_state=int(g.rng.integers(1 << 31)))
    invoices = pd.DataFrame(inv_rows)
    return accounts.reset_index(drop=True), invoices, truth


def _support(g: Gen, ents: list[Entity]):
    tickets, truth = [], []
    chosen = [e for e in ents if g.r() < 0.50]
    extra = [e for e in chosen if g.r() < 0.05]  # second requester profile
    used: set[str] = set()
    t_seq = 0
    start = datetime(2025, 1, 1, tzinfo=UTC)
    span_min = int((datetime(2025, 6, 30, 23, 0, tzinfo=UTC) - start).total_seconds() // 60)
    for e in chosen + extra:
        uid = f"usr_{int(g.rng.integers(16**6)):06x}"
        while uid in used:
            uid = f"usr_{int(g.rng.integers(16**6)):06x}"
        used.add(uid)
        truth.append((f"support.tickets:{uid}", e.eid))
        req = {
            "id": uid,
            "name": messy_name(g, e),
            "email": messy_email(g, e, missing_p=0.02),
            "phone": messy_phone(g, e, 0.6, preferred=2),
        }
        org = e.company if g.r() < 0.7 else None
        n = int(g.rng.poisson(4.0 if e.troubled else 1.0)) + 1
        for _ in range(n):
            t_seq += 1
            created = start + timedelta(minutes=int(g.rng.integers(0, span_min)))
            age_days = (datetime(2025, 6, 30, tzinfo=UTC) - created).days
            open_p = (0.7 if e.troubled else 0.25) if age_days < 45 else (0.3 if e.troubled else 0.04)
            status = g.pick(["open", "pending"]) if g.r() < open_p else g.pick(["solved", "closed"])
            score = None
            if status in ("solved", "closed") and g.r() < 0.6:
                score = int(g.rng.integers(1, 3)) if (e.troubled and g.r() < 0.7) else int(g.rng.integers(3, 6))
            if g.r() < 0.01:
                score = g.pick([0, 6, 10])  # out of range -> quarantine
            created_val: object = created.strftime("%Y-%m-%dT%H:%M:%SZ")
            if g.r() < 0.10:
                created_val = int(created.timestamp())
            if g.r() < 0.003:
                created_val = None
            subj = g.pick(TROUBLE_SUBJECTS) if (e.troubled and g.r() < 0.4) else g.pick(SUBJECTS)
            tickets.append({
                "id": f"tkt_{t_seq:07d}",
                "created_at": created_val,
                "status": status,
                "priority": g.pick(["low", "normal", "high", "urgent"], p=[0.3, 0.45, 0.2, 0.05]),
                "subject": subj,
                "requester": dict(req),
                "organization": {"name": org},
                "via": {"channel": g.pick(["email", "web", "phone", "chat"])},
                "assignee": {"name": g.pick(AGENTS) if status != "open" or g.r() < 0.5 else None},
                "satisfaction_rating": {"score": score},
                "tags": [g.pick(["billing", "bug", "how-to", "sso", "api", "vip"]) for _ in
                         range(int(g.rng.integers(0, 3)))],
            })
    order = g.rng.permutation(len(tickets))
    return [tickets[i] for i in order], truth


# The unseen 4th source: an ERP customer master with SAP-style column names. Kept out of
# sources.yaml and with its own ground truth dir, for the onboarding-time test.
ERP_MAP = {
    "KUNNR": "customer_ref", "NAME1": "full_name", "NAME2": "company_name", "STRAS": "street", "ORT01": "city",
    "REGIO": "state", "PSTLZ": "postal_code", "LAND1": "country", "TELF1": "phone", "SMTP_ADDR": "email",
    "ERDAT": "signup_date", "KDGRP": "segment", "KTOKD": "ignore", "BRSCH": "ignore", "SPERR": "ignore",
}


def _erp(g: Gen, ents: list[Entity]):
    rows, truth = [], []
    chosen = [e for e in ents if g.r() < 0.30]
    nums = g.rng.permutation(len(chosen) * 4)[: len(chosen)] + 10_000
    for e, num in zip(chosen, nums, strict=True):
        kunnr = f"{int(num):010d}"
        phone = messy_phone(g, e, 0.2, preferred=1)
        rows.append({
            "KUNNR": kunnr,
            "NAME1": messy_name(g, e, upper_p=0.5),
            "NAME2": e.company if g.r() > 0.1 else None,
            "STRAS": messy_street(g, e, abbrev_p=0.9),
            "ORT01": e.city.upper(),
            "REGIO": e.state,
            "PSTLZ": e.zip,
            "LAND1": "US",
            "TELF1": ("+1-" + phone.replace(" ", "-").replace(".", "-").lstrip("+1-")) if phone else None,
            "SMTP_ADDR": messy_email(g, e, missing_p=0.08),
            "ERDAT": e.signup.strftime("%Y%m%d"),
            "KDGRP": {"Enterprise": "ENT", "Mid-Market": "MM", "SMB": "SMB"}[e.segment],
            "KTOKD": g.pick(["Z001", "Z002", "ZCPD"]),
            "BRSCH": g.pick(["IT01", "RETL", "HLTH", "MANU", "FINS"]),
            "SPERR": "X" if g.r() < 0.03 else None,
        })
        truth.append((f"erp.customer_master:{kunnr}", e.eid))
    return pd.DataFrame(rows), truth


def generate_unseen(out_dir: Path, ents: list[Entity], seed: int) -> dict:
    g = Gen(seed + 7919)  # own RNG stream: adding this source does not change the other three
    df, truth = _erp(g, ents)
    (out_dir / "unseen").mkdir(parents=True, exist_ok=True)
    gt = out_dir / "ground_truth_unseen"
    gt.mkdir(parents=True, exist_ok=True)
    df.to_csv(out_dir / "unseen" / "erp_customer_master.csv", index=False)
    pd.DataFrame(_mapping_rows("erp", "customer_master", ERP_MAP)).to_csv(gt / "column_mapping.csv", index=False)
    pd.DataFrame(truth, columns=["record_id", "true_customer_id"]).to_csv(gt / "clusters.csv", index=False)
    return {"erp.customer_master": len(df)}


def _mapping_rows(source: str, table: str, m: dict[str, str]):
    return [{"source": source, "table": table, "column": c, "target_field": t} for c, t in m.items()]


def generate(out_dir: Path, scale: float = 1.0, seed: int = 42, unseen: bool = True) -> dict:
    """Write the three raw sources plus hidden ground truth. Returns a summary dict."""
    out_dir = Path(out_dir)
    raw = out_dir / "raw"
    gt = out_dir / "ground_truth"
    raw.mkdir(parents=True, exist_ok=True)
    gt.mkdir(parents=True, exist_ok=True)
    g = Gen(seed)
    ents = _make_entities(g, int(BASE_ENTITIES * scale))
    crm, crm_truth = _crm(g, ents)
    accounts, invoices, bill_truth = _billing(g, ents)
    tickets, sup_truth = _support(g, ents)

    crm.to_csv(raw / "crm_contacts.csv", index=False)
    with pd.ExcelWriter(raw / "billing.xlsx", engine="xlsxwriter") as xw:
        accounts.to_excel(xw, sheet_name="Accounts", index=False)
        invoices.to_excel(xw, sheet_name="Invoices", index=False)
    (raw / "support_tickets.json").write_text(json.dumps(tickets))

    mapping = (_mapping_rows("crm", "contacts", CRM_MAP) + _mapping_rows("billing", "accounts", ACCOUNTS_MAP)
               + _mapping_rows("billing", "invoices", INVOICES_MAP) + _mapping_rows("support", "tickets", TICKETS_MAP))
    pd.DataFrame(mapping).to_csv(gt / "column_mapping.csv", index=False)
    clusters = pd.DataFrame(crm_truth + bill_truth + sup_truth, columns=["record_id", "true_customer_id"])
    clusters.to_csv(gt / "clusters.csv", index=False)
    summary = {
        "seed": seed, "scale": scale, "true_customers": len(ents),
        "customer_records": len(clusters),
        "records_by_source": {"crm.contacts": len(crm), "billing.accounts": len(accounts),
                              "support.requesters": len(sup_truth)},
        "invoices": len(invoices), "tickets": len(tickets),
        "true_customers_present": int(clusters["true_customer_id"].nunique()),
    }
    if unseen:
        summary["unseen_source"] = generate_unseen(out_dir, ents, seed)
    (gt / "summary.json").write_text(json.dumps(summary, indent=2))
    return summary
