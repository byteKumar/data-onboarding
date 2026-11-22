"""Command line entry point: `uv run onboard <command>`."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from onboard.config import Settings


def cmd_generate(args) -> int:
    from onboard.synth import generate

    out = Path(args.out) if args.out else Settings().data_dir
    summary = generate(out, scale=args.scale, seed=args.seed)
    print(json.dumps(summary, indent=2))
    return 0


def cmd_profile(args) -> int:
    from onboard.connectors import load_manifest, load_table
    from onboard.profiling import profile_table

    s = Settings()
    for spec in load_manifest(Path(args.manifest) if args.manifest else s.manifest_path):
        if args.table and spec.key != args.table:
            continue
        df = load_table(spec, s.root)
        print(f"\n== {spec.key} ({spec.entity}) rows={len(df)}")
        for p in profile_table(df):
            print(f"  {p.column:28} {p.pattern:9} {p.dtype:22} null={p.null_rate:<6} "
                  f"distinct={p.distinct_count:<6} e.g. {p.samples[:2]}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="onboard")
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("generate", help="write synthetic sources + hidden ground truth")
    g.add_argument("--scale", type=float, default=1.0, help="1.0 ~ 20K customer records, 10 ~ 200K")
    g.add_argument("--seed", type=int, default=42)
    g.add_argument("--out", default=None, help="output data dir (default ./data)")
    g.set_defaults(fn=cmd_generate)
    p = sub.add_parser("profile", help="profile every column of every source table")
    p.add_argument("--manifest", default=None)
    p.add_argument("--table", default=None, help="e.g. crm.contacts")
    p.set_defaults(fn=cmd_profile)
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
