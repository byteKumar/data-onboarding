"""Pipeline runtime at 20K and 200K customer records. Each scale is run ONCE and reported as is.

    uv run python scripts/benchmark.py --scales 1 10

For each scale: generate the sources (timed separately, not part of the pipeline number), reuse the
confirmed mappings from data/mapping_store (same column names -> same fingerprint, so no review is
needed: this is the mapping store doing its job), reuse config/er_model.json, then time one
`pipeline.run` (load + normalize + checks + model + entity resolution + DuckDB write).
No ground truth is read here.
"""

from __future__ import annotations

import argparse
import json
import platform
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import duckdb

from onboard.config import Settings
from onboard.pipeline import run
from onboard.synth import generate

ROOT = Path(__file__).resolve().parents[1]


def machine() -> str:
    try:
        cpu = subprocess.run(["sysctl", "-n", "machdep.cpu.brand_string"], capture_output=True, text=True).stdout
        mem = int(subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True).stdout) / 2**30
        return f"{cpu.strip()}, {mem:.0f} GB RAM, macOS {platform.mac_ver()[0]}, Python {platform.python_version()}"
    except Exception:  # noqa: BLE001
        return f"{platform.platform()}, Python {platform.python_version()}"


def bench(scale: float, seed: int) -> dict:
    main = Settings(root=ROOT)
    root = ROOT / "data" / "bench" / f"scale_{scale:g}"
    shutil.rmtree(root, ignore_errors=True)
    (root / "config").mkdir(parents=True)
    t = time.perf_counter()
    summary = generate(root / "data", scale=scale, seed=seed, unseen=False)
    gen_s = time.perf_counter() - t
    shutil.copy(ROOT / "sources.yaml", root / "sources.yaml")
    shutil.copytree(main.mapping_store_dir, root / "data" / "mapping_store")
    if main.er_model_path.exists():
        shutil.copy(main.er_model_path, root / "config" / "er_model.json")
    s = Settings(root=root)
    t = time.perf_counter()
    out = run(settings=s, db_path=root / "data" / "bench.duckdb")
    wall = time.perf_counter() - t
    with duckdb.connect(str(root / "data" / "bench.duckdb"), read_only=True) as con:
        reasons = con.execute("SELECT check_name, detail, COUNT(*) n FROM quarantine GROUP BY ALL "
                              "ORDER BY n DESC").fetchall()
    return {
        "scale": scale, "seed": seed, "customer_records_generated": summary["customer_records"],
        "invoices_generated": summary["invoices"], "tickets_generated": summary["tickets"],
        "generate_s": round(gen_s, 2), "pipeline_wall_s": round(wall, 2), "pipeline": out,
        "quarantine_by_reason": [{"check": c, "detail": d, "rows": n} for c, d, n in reasons],
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scales", type=float, nargs="+", default=[1, 10])
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args(argv)
    if not Settings(root=ROOT).mapping_store_dir.exists():
        print("No confirmed mappings in data/mapping_store. Confirm them first (dashboard or "
              "eval/seed_confirmed_mappings.py).")
        return 2
    results = []
    for sc in args.scales:
        r = bench(sc, args.seed)
        print(f"scale {sc:g}: {r['customer_records_generated']} customer records, "
              f"pipeline {r['pipeline_wall_s']} s (generate {r['generate_s']} s)")
        results.append(r)
    out = {"measured_at": datetime.now().isoformat(timespec="seconds"), "machine": machine(),
           "command": "uv run python " + " ".join(sys.argv), "runs": results}
    p = ROOT / "eval" / "results" / "benchmark.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(out, indent=2, default=str))
    print("saved", p)
    return 0


if __name__ == "__main__":
    sys.exit(main())
