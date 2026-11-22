import json
import sys

import numpy as np
import pandas as pd

from onboard.er import candidate_pairs, cluster, features, norm_name, norm_street, prepare, resolve
from tests.conftest import ROOT


def recs(rows):
    cols = ["record_id", "source_table", "customer_ref", "full_name", "email", "phone", "street", "city", "state",
            "postal_code", "company_name", "segment", "signup_date", "plan"]
    return pd.DataFrame([{c: r.get(c) for c in cols} for r in rows])


def test_normalizers():
    assert norm_name("Bob J. Smith") == ("robert smith", "robert", "smith")
    assert norm_street("12 Main Street Apt 4") == "12 main st"


def test_blocking_and_scoring_separate_match_from_nonmatch():
    df = recs([
        {"record_id": "crm:1", "source_table": "crm.contacts", "full_name": "Robert Smith", "email": "rs@x.com",
         "phone": "6175550101", "street": "12 Main Street", "postal_code": "02139"},
        {"record_id": "bill:9", "source_table": "billing.accounts", "full_name": "Bob Smith",
         "email": "rs@x.com", "phone": "6175550101", "street": "12 Main St", "postal_code": "02139"},
        {"record_id": "crm:2", "source_table": "crm.contacts", "full_name": "Rachel Smithers", "email": "r@y.com",
         "phone": "6175559999", "street": "99 Oak Ave", "postal_code": "02139"},
    ])
    out = resolve(df, None, "test")
    cid = dict(zip(out.tables["customer_records"].record_id, out.tables["customer_records"].customer_id,
                   strict=True))
    assert cid["crm:1"] == cid["bill:9"] != cid["crm:2"]
    mh = out.tables["merge_history"]
    assert len(mh) == 1 and set(mh[["record_a", "record_b"]].iloc[0]) == {"crm:1", "bill:9"}
    p = prepare(df)
    pairs = candidate_pairs(p)
    assert len(features(p, pairs)) == len(pairs)


def test_cluster_is_transitive_and_thresholded():
    roots, events = cluster(4, np.array([0, 1, 2]), np.array([1, 2, 3]), np.array([0.9, 0.8, 0.2]), 0.5)
    assert roots[0] == roots[1] == roots[2] != roots[3]
    assert len(events) == 2


def test_er_eval_protocol_runs(tmp_path, capsys):
    """Mechanics only, on a separate seed and scale from the measured dataset."""
    import shutil

    from onboard.synth import generate

    sys.path.insert(0, str(ROOT / "eval"))
    import er_eval
    from seed_confirmed_mappings import seed

    generate(tmp_path / "data", scale=0.1, seed=3)
    shutil.copy(ROOT / "sources.yaml", tmp_path / "sources.yaml")
    seed(tmp_path, tmp_path / "sources.yaml", tmp_path / "data" / "ground_truth")
    assert er_eval.main(["--root", str(tmp_path), "--no-save"]) == 0
    printed = capsys.readouterr().out
    body = json.loads(printed[: printed.rindex("}") + 1])
    assert 0 <= body["test_at_threshold"]["precision"] <= 1
    assert set(body["splits"]) == {"train", "validation", "test"}


def test_cluster_metrics():
    sys.path.insert(0, str(ROOT / "eval"))
    from er_eval import cluster_metrics

    m = cluster_metrics(np.array([1, 1, 2, 3]), np.array(["a", "a", "a", "b"]))
    assert m["precision"] == 1.0 and m["recall"] == round(1 / 3, 4)
