import hashlib

import pandas as pd

from onboard.synth import generate


def _digest(d):
    h = hashlib.sha256()
    for name in ["raw/crm_contacts.csv", "raw/support_tickets.json", "ground_truth/clusters.csv"]:
        h.update((d / name).read_bytes())
    return h.hexdigest()


def test_same_seed_same_bytes(tmp_path):
    a = generate(tmp_path / "a", scale=0.02, seed=1)
    b = generate(tmp_path / "b", scale=0.02, seed=1)
    assert a == b
    assert _digest(tmp_path / "a") == _digest(tmp_path / "b")


def test_ground_truth_covers_every_record(small_project):
    d = small_project / "data"
    gt = pd.read_csv(d / "ground_truth" / "clusters.csv")
    crm = pd.read_csv(d / "raw" / "crm_contacts.csv", dtype=str)
    assert gt["record_id"].is_unique
    assert set("crm.contacts:" + crm["Contact ID"]) <= set(gt["record_id"])
    # cross-system duplicates exist: some true customers have records in 2+ sources
    per_customer = gt.groupby("true_customer_id")["record_id"].count()
    assert (per_customer > 1).mean() > 0.3


def test_mapping_truth_lists_every_column(small_project):
    d = small_project / "data"
    m = pd.read_csv(d / "ground_truth" / "column_mapping.csv")
    crm = pd.read_csv(d / "raw" / "crm_contacts.csv", nrows=1)
    assert set(m[m.table == "contacts"]["column"]) == set(crm.columns)
