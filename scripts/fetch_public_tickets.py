"""Fetch a public customer-support ticket dataset for a realism check. Chaman runs this himself.

The pipeline never needs it: it profiles and maps the file like any other export, so you can see
how rules + LLM cope with a dataset nobody designed for this tool.

Default source: the Hugging Face dataset `Tobi-Bueck/customer-support-tickets`, read page by page
through the public datasets-server rows API (no account needed). LICENCE: not verified by the
author of this script. Read the dataset card first and only use the data as its licence allows:
    https://huggingface.co/datasets/Tobi-Bueck/customer-support-tickets
Output goes to data/public/ (gitignored, never committed or redistributed).

    uv run python scripts/fetch_public_tickets.py --limit 2000
    uv run python scripts/fetch_public_tickets.py --dataset other/name --config default --split train
    uv run onboard profile --manifest eval/public_source.yaml
    uv run onboard suggest --manifest eval/public_source.yaml          # rules only, free
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import httpx
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
ROWS_API = "https://datasets-server.huggingface.co/rows"


def fetch(dataset: str, config: str, split: str, limit: int) -> pd.DataFrame:
    rows: list[dict] = []
    with httpx.Client(timeout=60, headers={"User-Agent": "data-onboarding-demo"}) as client:
        while len(rows) < limit:
            length = min(100, limit - len(rows))
            r = client.get(ROWS_API, params={"dataset": dataset, "config": config, "split": split,
                                             "offset": len(rows), "length": length})
            r.raise_for_status()
            batch = [x["row"] for x in r.json().get("rows", [])]
            if not batch:
                break
            rows.extend(batch)
    return pd.json_normalize(rows, sep=".")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", default="Tobi-Bueck/customer-support-tickets")
    ap.add_argument("--config", default="default")
    ap.add_argument("--split", default="train")
    ap.add_argument("--limit", type=int, default=2000)
    args = ap.parse_args(argv)
    print(f"Check the licence first: https://huggingface.co/datasets/{args.dataset}")
    df = fetch(args.dataset, args.config, args.split, args.limit)
    out = ROOT / "data" / "public" / "support_tickets_public.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out, index=False)
    print(f"wrote {len(df)} rows x {len(df.columns)} columns to {out}")
    print("columns:", list(df.columns))
    return 0


if __name__ == "__main__":
    sys.exit(main())
