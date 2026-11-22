"""Helpers shared by eval scripts. eval/ is the ONLY place allowed to read data/ground_truth*."""

from __future__ import annotations

import json
import platform
import subprocess
from datetime import datetime
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "eval" / "results"


def truth_mapping(gt_dir: Path) -> dict[tuple[str, str], str]:
    df = pd.read_csv(gt_dir / "column_mapping.csv")
    return {(f"{r.source}.{r.table}", r.column): r.target_field for r in df.itertuples()}


def machine() -> str:
    try:
        cpu = subprocess.run(["sysctl", "-n", "machdep.cpu.brand_string"], capture_output=True, text=True).stdout
        mem = int(subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True).stdout) / 2**30
        osv = f"{platform.system()} {platform.release()}"
        return f"{cpu.strip()}, {mem:.0f} GB RAM, {osv}, Python {platform.python_version()}"
    except Exception:  # noqa: BLE001 - non-mac CI
        return f"{platform.platform()}, Python {platform.python_version()}"


def save(name: str, payload: dict) -> Path:
    RESULTS.mkdir(parents=True, exist_ok=True)
    payload = {"measured_at": datetime.now().isoformat(timespec="seconds"), "machine": machine(), **payload}
    p = RESULTS / f"{name}.json"
    p.write_text(json.dumps(payload, indent=2, default=str))
    return p
