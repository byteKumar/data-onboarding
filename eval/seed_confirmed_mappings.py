"""Simulate the human confirm step by writing the ground-truth mapping into the mapping store.

Why this exists: benchmarks and the demo need confirmed mappings for every table, and the
runtime numbers should not depend on someone clicking through the confirm screen first.
Records are stamped confirmed_by='simulated-human (ground truth)' so nobody mistakes them
for a real review. It lives in eval/ because it reads the ground truth.

    uv run python eval/seed_confirmed_mappings.py              # data/ground_truth -> data/mapping_store
    uv run python eval/seed_confirmed_mappings.py --root /tmp/x
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from eval_common import ROOT, truth_mapping

from onboard.config import Settings
from onboard.connectors import load_manifest, load_table
from onboard.mapping.store import MappingStore, fingerprint


def seed(root: Path, manifest: Path, gt_dir: Path) -> list[str]:
    s = Settings(root=root)
    truth = truth_mapping(gt_dir)
    store = MappingStore(s.mapping_store_dir)
    done = []
    for spec in load_manifest(manifest):
        cols = [str(c) for c in load_table(spec, root).columns]
        mapping = {c: truth[(spec.key, c)] for c in cols}
        fp = fingerprint(spec.entity, cols)
        store.save(fp, spec.key, spec.entity, mapping, "simulated-human (ground truth)",
                   note="written by eval/seed_confirmed_mappings.py, not a real review")
        done.append(f"{spec.key} -> {fp}")
    return done


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=str(ROOT))
    ap.add_argument("--manifest", default=None)
    ap.add_argument("--gt", default=None, help="ground truth dir (default <root>/data/ground_truth)")
    a = ap.parse_args(argv)
    root = Path(a.root)
    for line in seed(root, Path(a.manifest) if a.manifest else root / "sources.yaml",
                     Path(a.gt) if a.gt else root / "data" / "ground_truth"):
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
