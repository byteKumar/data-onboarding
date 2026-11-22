"""Train, tune and measure entity resolution against the hidden ground truth.

    uv run python eval/er_eval.py

Protocol (fixed before looking at any number):
* Records are split by TRUE customer (hash of the true id): 60% train, 20% validation, 20% test,
  so no customer's records leak across splits.
* Candidate pairs come from the pipeline's own blocking; a pair is evaluated only if both records
  sit in the same split. A true pair the blocking never proposed counts as a miss (recall).
* Logistic regression on the rapidfuzz features is fit on train pairs only.
* The link threshold is chosen on validation only (max pairwise F1 after clustering, ties -> higher).
* Pairs spanning two splits are excluded from split metrics; they are all non-matches, so the
  number the model would wrongly link is reported separately, plus all-records metrics.
* Test is scored once, at that threshold, and reported as is.
* Metrics are pairwise after clustering: precision = correctly co-clustered pairs / predicted pairs,
  recall = correctly co-clustered pairs / true pairs, within each split's records.

Writes config/er_model.json (read by the pipeline) and eval/results/er.json.
"""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
from datetime import datetime
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
from eval_common import ROOT, save
from sklearn.linear_model import LogisticRegression

from onboard.config import Settings
from onboard.er import FEATURES, candidate_pairs, cluster, features, prepare
from onboard.pipeline import run as run_pipeline

SPLITS = {"train": range(0, 6), "validation": range(6, 8), "test": range(8, 10)}


def split_of(true_id: str) -> str:
    h = int(hashlib.md5(true_id.encode()).hexdigest(), 16) % 10
    return next(name for name, r in SPLITS.items() if h in r)


def pairs_in(labels: np.ndarray) -> int:
    _, counts = np.unique(labels, return_counts=True)
    return int((counts * (counts - 1) // 2).sum())


def cluster_metrics(pred: np.ndarray, true: np.ndarray) -> dict:
    joint = pd.DataFrame({"p": pred, "t": true}).groupby(["p", "t"]).size().to_numpy()
    tp = int((joint * (joint - 1) // 2).sum())
    pp, tt = pairs_in(pred), pairs_in(true)
    precision = tp / pp if pp else 1.0
    recall = tp / tt if tt else 1.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"precision": round(precision, 4), "recall": round(recall, 4), "f1": round(f1, 4),
            "true_pairs": tt, "predicted_pairs": pp, "correct_pairs": tp}


def evaluate_split(n_split: int, a: np.ndarray, b: np.ndarray, s: np.ndarray, true: np.ndarray, t: float) -> dict:
    roots, _ = cluster(n_split, a, b, s, t)
    return cluster_metrics(roots, true)


def main(argv=None) -> int:
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=str(ROOT), help="project root (tests use a temp project)")
    ap.add_argument("--no-save", action="store_true", help="do not write config/ or eval/results/")
    args = ap.parse_args(argv)
    root = Path(args.root)
    s = Settings(root=root)
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "er_eval.duckdb"
        run_pipeline(settings=s, db_path=db, run_er=False)
        with duckdb.connect(str(db), read_only=True) as con:
            records = con.execute("select * from customer_records order by record_id").df()
    truth = pd.read_csv(root / "data" / "ground_truth" / "clusters.csv")
    records = records.merge(truth, on="record_id", how="left")
    assert records["true_customer_id"].notna().all(), "every pipeline record must exist in the ground truth"
    records["split"] = records["true_customer_id"].map(split_of)

    p = prepare(records)
    stats: dict = {}
    pairs = candidate_pairs(p, stats)
    feats = features(p, pairs)
    split_arr = records["split"].to_numpy()
    true_arr = records["true_customer_id"].to_numpy()
    pairs["split_a"], pairs["split_b"] = split_arr[pairs["a"]], split_arr[pairs["b"]]
    pairs["label"] = (true_arr[pairs["a"]] == true_arr[pairs["b"]]).astype(int)

    per_split = {}
    for name in SPLITS:
        idx = np.where(split_arr == name)[0]
        local = {g: i for i, g in enumerate(idx)}
        m = (pairs["split_a"] == name) & (pairs["split_b"] == name)
        sub = pairs[m]
        true_pairs = pairs_in(true_arr[idx])
        per_split[name] = {
            "records": len(idx), "mask": m.to_numpy(),
            "a": sub["a"].map(local).to_numpy(), "b": sub["b"].map(local).to_numpy(),
            "true": true_arr[idx], "true_pairs": true_pairs,
            "blocking_recall": round(int(sub["label"].sum()) / true_pairs, 4) if true_pairs else 1.0,
            "candidate_pairs": int(m.sum()),
        }

    tr = per_split["train"]["mask"]
    clf = LogisticRegression(max_iter=2000, C=1.0)
    clf.fit(feats[tr].to_numpy(), pairs.loc[tr, "label"].to_numpy())
    model = {"kind": "logreg", "features": FEATURES, "coef": clf.coef_[0].round(6).tolist(),
             "intercept": round(float(clf.intercept_[0]), 6)}

    def scores(name):
        m = per_split[name]["mask"]
        z = feats[m].to_numpy() @ np.asarray(model["coef"]) + model["intercept"]
        return 1 / (1 + np.exp(-z))

    v = per_split["validation"]
    sv = scores("validation")
    sweep = []
    for t in np.round(np.arange(0.05, 1.0, 0.01), 2):
        met = evaluate_split(v["records"], v["a"], v["b"], sv, v["true"], float(t))
        sweep.append({"threshold": float(t), **met})
    best = max(sweep, key=lambda r: (r["f1"], r["threshold"]))
    threshold = best["threshold"]

    te = per_split["test"]
    test = evaluate_split(te["records"], te["a"], te["b"], scores("test"), te["true"], threshold)

    # Pairs whose records sit in different splits are skipped above. They are all non-matches by
    # construction, so count how many the model would wrongly link, and report all-records metrics.
    cross = (pairs["split_a"] != pairs["split_b"]).to_numpy()
    z_all = feats.to_numpy() @ np.asarray(model["coef"]) + model["intercept"]
    s_all = 1 / (1 + np.exp(-z_all))
    cross_links = int(((s_all >= threshold) & cross).sum())
    all_records = evaluate_split(len(records), pairs["a"].to_numpy(), pairs["b"].to_numpy(), s_all, true_arr,
                                 threshold)

    model["threshold"] = threshold
    model["trained"] = {"at": datetime.now().isoformat(timespec="seconds"), "train_pairs": int(tr.sum()),
                        "tuned_on": "validation", "selection": "max pairwise F1 after clustering"}

    out = {
        "command": "uv run python eval/er_eval.py",
        "records": len(records), "blocking": stats,
        "splits": {k: {kk: vv for kk, vv in d.items() if kk not in ("mask", "a", "b", "true")}
                   for k, d in per_split.items()},
        "model": model, "threshold": threshold,
        "validation_at_threshold": {k: best[k] for k in best if k != "threshold"},
        "test_at_threshold": test,
        "cross_split_pairs": int(cross.sum()), "cross_split_false_links_at_threshold": cross_links,
        "all_records_at_threshold_includes_train": all_records,
        "validation_sweep": sweep,
    }
    print(json.dumps({k: v for k, v in out.items() if k != "validation_sweep"}, indent=2))
    if not args.no_save:
        (root / "config").mkdir(exist_ok=True)
        (root / "config" / "er_model.json").write_text(json.dumps(model, indent=2))
        print("saved", save("er", out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
