"""Entity resolution: which customer records from different systems are the same customer.

1. Normalize (names, nicknames, emails, phones, street abbreviations).
2. Blocking: only compare records sharing a cheap key (exact email, last 7 phone digits,
   ZIP + last-name prefix, email domain + name prefix). Oversized blocks are skipped.
   This turns O(n^2) comparisons into a few per record. Blocking recall is measured.
3. Score each candidate pair with rapidfuzz similarities fed to a logistic regression
   trained on the train split (eval/er_eval.py). Without a trained model, fixed weights.
4. Link pairs at or above the threshold tuned on the validation split; connected
   components become customers. Every link is written to merge_history.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd
from rapidfuzz import fuzz
from rapidfuzz.process import cpdist

from onboard.config import PLAN_SEGMENT

BLOCK_MAX = 100
FEATURES = ["name_sim", "email_sim", "email_missing", "phone_eq", "phone_missing", "street_sim",
            "street_missing", "zip_eq", "zip_missing", "company_sim", "company_missing"]
DEFAULT_MODEL = {
    # Hand-set fallback used before eval/er_eval.py has trained and tuned a model.
    "kind": "default-weights",
    "features": FEATURES,
    "coef": [6.0, 6.0, 0.0, 3.0, 0.0, 2.0, 0.0, 1.0, 0.0, 0.5, 0.0],
    "intercept": -10.0,
    "threshold": 0.8,
}

NICKNAMES = {
    "bob": "robert", "rob": "robert", "bobby": "robert", "bill": "william", "will": "william", "liz": "elizabeth",
    "beth": "elizabeth", "jim": "james", "jimmy": "james", "mike": "michael", "rick": "richard", "dick": "richard",
    "joe": "joseph", "tom": "thomas", "chuck": "charles", "charlie": "charles", "chris": "christopher",
    "dan": "daniel", "danny": "daniel", "matt": "matthew", "tony": "anthony", "steve": "steven", "andy": "andrew",
    "drew": "andrew", "josh": "joshua", "ken": "kenneth", "tim": "timothy", "ron": "ronald", "ed": "edward",
    "eddie": "edward", "jon": "jonathan", "nick": "nicholas", "jen": "jennifer", "jenny": "jennifer",
    "pat": "patricia", "kate": "katherine", "katie": "katherine", "peggy": "margaret", "maggie": "margaret",
    "sam": "samantha", "ben": "benjamin", "alex": "alexander", "greg": "gregory", "kim": "kimberly",
    "deb": "deborah", "debbie": "deborah", "becky": "rebecca", "vicky": "victoria", "abby": "abigail",
    "sue": "susan", "dave": "david", "larry": "lawrence", "jerry": "gerald", "ray": "raymond",
}
STREET_ABBR = {"street": "st", "avenue": "ave", "road": "rd", "boulevard": "blvd", "lane": "ln", "drive": "dr",
               "court": "ct", "place": "pl", "north": "n", "south": "s", "east": "e", "west": "w"}


def norm_name(s) -> tuple[str, str, str]:
    """-> (normalized full name, first, last). Drops initials and punctuation, maps nicknames."""
    if not isinstance(s, str) or not s.strip():
        return "", "", ""
    toks = [t for t in re.sub(r"[^a-z ]", " ", s.lower()).split() if len(t) > 1]
    if not toks:
        return "", "", ""
    toks[0] = NICKNAMES.get(toks[0], toks[0])
    return " ".join(toks), toks[0], toks[-1]


def norm_street(s) -> str:
    if not isinstance(s, str) or not s.strip():
        return ""
    s = re.split(r"\b(apt|suite|unit)\b|#", s.lower())[0]
    toks = re.sub(r"[^a-z0-9 ]", " ", s).split()
    return " ".join(STREET_ABBR.get(t, t) for t in toks)


def prepare(records: pd.DataFrame) -> pd.DataFrame:
    p = pd.DataFrame(index=range(len(records)))
    p["record_id"] = records["record_id"].values
    names = [norm_name(x) for x in records["full_name"].tolist()]
    p["n_name"] = [n[0] for n in names]
    p["n_first"] = [n[1] for n in names]
    p["n_last"] = [n[2] for n in names]
    email = records["email"].fillna("").astype(str).str.strip().str.lower().values
    p["n_email"] = email
    p["email_domain"] = [e.split("@")[1] if "@" in e else "" for e in email]
    p["n_phone"] = records["phone"].fillna("").astype(str).values
    p["n_street"] = [norm_street(x) for x in records["street"].tolist()]
    p["n_zip"] = records["postal_code"].fillna("").astype(str).values
    p["n_company"] = records["company_name"].fillna("").astype(str).str.lower().values
    return p


def _block(p: pd.DataFrame, key: pd.Series, name: str, stats: dict) -> pd.DataFrame:
    k = pd.DataFrame({"i": p.index, "k": key.values})
    k = k[k["k"] != ""]
    sizes = k.groupby("k")["i"].transform("size")
    stats[f"blocks_skipped_over_{BLOCK_MAX}:{name}"] = int(k.loc[sizes > BLOCK_MAX, "k"].nunique())
    k = k[(sizes > 1) & (sizes <= BLOCK_MAX)]
    m = k.merge(k, on="k")
    m = m[m["i_x"] < m["i_y"]]
    return pd.DataFrame({"a": m["i_x"].values, "b": m["i_y"].values, "via": name})


def candidate_pairs(p: pd.DataFrame, stats: dict | None = None) -> pd.DataFrame:
    stats = stats if stats is not None else {}
    phone7 = p["n_phone"].str[-7:]
    zip_last = np.where((p["n_zip"] != "") & (p["n_last"] != ""), p["n_zip"] + "|" + p["n_last"].str[:3], "")
    dom_name = np.where((p["email_domain"] != "") & (p["n_last"] != ""),
                        p["email_domain"] + "|" + p["n_last"].str[:3] + p["n_first"].str[:1], "")
    parts = [
        _block(p, p["n_email"], "email", stats),
        _block(p, phone7, "phone7", stats),
        _block(p, pd.Series(zip_last), "zip+last3", stats),
        _block(p, pd.Series(dom_name), "domain+name", stats),
    ]
    allp = pd.concat(parts, ignore_index=True)
    pairs = allp.groupby(["a", "b"], as_index=False)["via"].agg(lambda v: "+".join(sorted(set(v))))
    stats["candidate_pairs"] = len(pairs)
    return pairs


def _sim(a: np.ndarray, b: np.ndarray, scorer) -> np.ndarray:
    if len(a) == 0:
        return np.zeros(0)
    return cpdist(list(a), list(b), scorer=scorer, workers=-1).astype(float) / 100.0


def features(p: pd.DataFrame, pairs: pd.DataFrame) -> pd.DataFrame:
    a, b = pairs["a"].to_numpy(), pairs["b"].to_numpy()
    f = pd.DataFrame(index=pairs.index)

    def col(c):
        return p[c].to_numpy()[a], p[c].to_numpy()[b]

    na, nb = col("n_name")
    f["name_sim"] = _sim(na, nb, fuzz.token_sort_ratio)
    ea, eb = col("n_email")
    miss = (ea == "") | (eb == "")
    f["email_sim"] = np.where(miss, 0.0, _sim(ea, eb, fuzz.ratio))
    f["email_missing"] = miss.astype(float)
    pa, pb = col("n_phone")
    miss = (pa == "") | (pb == "")
    f["phone_eq"] = np.where(miss, 0.0, (pa == pb).astype(float))
    f["phone_missing"] = miss.astype(float)
    sa, sb = col("n_street")
    miss = (sa == "") | (sb == "")
    f["street_sim"] = np.where(miss, 0.0, _sim(sa, sb, fuzz.token_set_ratio))
    f["street_missing"] = miss.astype(float)
    za, zb = col("n_zip")
    miss = (za == "") | (zb == "")
    f["zip_eq"] = np.where(miss, 0.0, (za == zb).astype(float))
    f["zip_missing"] = miss.astype(float)
    ca, cb = col("n_company")
    miss = (ca == "") | (cb == "")
    f["company_sim"] = np.where(miss, 0.0, _sim(ca, cb, fuzz.token_set_ratio))
    f["company_missing"] = miss.astype(float)
    return f[FEATURES]


def load_model(path: Path | None) -> dict:
    if path and Path(path).exists():
        return json.loads(Path(path).read_text())
    return DEFAULT_MODEL


def score(feats: pd.DataFrame, model: dict) -> np.ndarray:
    x = feats[model["features"]].to_numpy()
    z = x @ np.asarray(model["coef"]) + model["intercept"]
    return 1.0 / (1.0 + np.exp(-z))


class _UF:
    def __init__(self, n: int):
        self.parent = list(range(n))

    def find(self, x: int) -> int:
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: int, b: int) -> bool:
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return False
        self.parent[max(ra, rb)] = min(ra, rb)
        return True


def cluster(n: int, a: np.ndarray, b: np.ndarray, s: np.ndarray, threshold: float):
    """Union pairs >= threshold, strongest first. Returns (root per record, merge events)."""
    uf = _UF(n)
    order = np.argsort(-s)
    events = []
    for k in order:
        if s[k] < threshold:
            break
        if uf.union(int(a[k]), int(b[k])):
            events.append((int(a[k]), int(b[k]), float(s[k])))
    roots = np.array([uf.find(i) for i in range(n)])
    return roots, events


def customer_id_for(record_ids: list[str]) -> str:
    return "CUS-" + hashlib.sha1(min(record_ids).encode()).hexdigest()[:10]


SOURCE_PRIORITY = ["customer", "account", "ticket"]  # CRM first for names/segment, then billing, then support


@dataclass
class EROutput:
    tables: dict[str, pd.DataFrame]
    stats: dict = field(default_factory=dict)


def _golden(records: pd.DataFrame) -> pd.DataFrame:
    """Survivorship: per customer, first non-null value in source priority order."""
    r = records.copy()
    r["_prio"] = r["source_table"].map(lambda k: 0 if k.startswith("crm") else 1 if "account" in k else 2)
    r = r.sort_values(["customer_id", "_prio", "record_id"])
    cols = ["full_name", "email", "phone", "street", "city", "state", "postal_code", "company_name", "segment",
            "plan"]
    g = r.groupby("customer_id", sort=False)
    gold = g[cols].first()
    gold["segment_source"] = np.where(gold["segment"].notna(), "crm", None)
    from_plan = gold["plan"].map(lambda x: PLAN_SEGMENT.get(x) if isinstance(x, str) else None)
    use_plan = gold["segment"].isna() & from_plan.notna()
    gold.loc[use_plan, "segment"] = from_plan[use_plan]
    gold.loc[use_plan, "segment_source"] = "plan_rule"
    gold["segment"] = gold["segment"].fillna("Unknown")
    gold["segment_source"] = gold["segment_source"].fillna("unknown")
    gold["record_count"] = g.size()
    gold["sources"] = g["source_table"].agg(lambda v: ",".join(sorted(set(v))))
    gold["first_seen"] = g["signup_date"].min()
    return gold.drop(columns=["plan"]).reset_index()


def resolve(records: pd.DataFrame, model_path: Path | None, run_id: str) -> EROutput:
    model = load_model(model_path)
    stats: dict = {"model": model.get("kind", "logreg"), "threshold": model["threshold"], "records": len(records)}
    records = records.reset_index(drop=True)
    p = prepare(records)
    pairs = candidate_pairs(p, stats)
    s = score(features(p, pairs), model) if len(pairs) else np.zeros(0)
    roots, events = cluster(len(p), pairs["a"].to_numpy(), pairs["b"].to_numpy(), s, model["threshold"])
    rid = records["record_id"].tolist()
    groups: dict[int, list[str]] = {}
    for i, r in enumerate(roots):
        groups.setdefault(int(r), []).append(rid[i])
    cid_of_root = {r: customer_id_for(ids) for r, ids in groups.items()}
    records["customer_id"] = [cid_of_root[int(r)] for r in roots]
    stats["customers"] = len(groups)
    stats["links"] = len(events)
    merges = pd.DataFrame([{
        "run_id": run_id, "customer_id": cid_of_root[int(roots[a])], "record_a": rid[a], "record_b": rid[b],
        "score": sc, "threshold": model["threshold"], "model": model.get("kind", "logreg"),
        "decided_by": "auto",
    } for a, b, sc in events], columns=["run_id", "customer_id", "record_a", "record_b", "score", "threshold",
                                        "model", "decided_by"])
    return EROutput(tables={"customer_records": records, "customers": _golden(records), "merge_history": merges},
                    stats=stats)
