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


def _workspace(args):
    from onboard.service import Workspace

    return Workspace(manifest=Path(args.manifest) if getattr(args, "manifest", None) else None)


def _selected(w, args):
    return [sp for sp in w.specs() if not args.table or sp.key == args.table]


def cmd_suggest(args) -> int:
    """Rules first; with --llm, unresolved columns go to the LLM. Paid calls need --yes."""
    from onboard.config import llm_model, llm_provider
    from onboard.mapping.llm import get_llm

    w = _workspace(args)
    specs = _selected(w, args)
    llm = None
    if args.llm or args.dry_run:
        provider = llm_provider()
        if provider == "none":
            print("LLM_PROVIDER is 'none'. Set LLM_PROVIDER=fake (offline) or anthropic (paid).")
            return 2
        if provider == "anthropic":
            total = {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}
            for sp in specs:
                est = w.llm_estimate(sp)
                print(f"  {sp.key:20} columns_for_llm={est.get('columns_for_llm', 0):<3} "
                      f"~{est['input_tokens']} in / ~{est['output_tokens']} out tokens  ~${est['cost_usd']:.5f}")
                for k in total:
                    total[k] += est[k]
            print(f"ESTIMATE model={llm_model()}: ~{total['input_tokens']} input + ~{total['output_tokens']} output "
                  f"tokens = ~${total['cost_usd']:.5f}")
            if args.dry_run or not args.yes:
                print("No paid call made. Re-run with --llm --yes to spend it.")
                return 0 if args.dry_run else 3
        llm = get_llm(provider, allow_paid=bool(args.yes))
    spent = 0.0
    for sp in specs:
        doc = w.run_suggest(sp, llm=llm)
        st = doc["stats"]
        spent += doc["llm_usage"].get("cost_usd", 0.0)
        print(f"{sp.key:20} rules={st['resolved_by_rules']} llm={st['resolved_by_llm']} "
              f"store={st['resolved_by_store']} unresolved={st['unresolved']} "
              f"llm_rejected={st['llm_proposals_rejected']} confirmed={doc['confirmed']}")
    if llm is not None:
        print(f"LLM spend this run (from response.usage): ${spent:.5f}")
    print(f"Suggestions written to {w.s.suggestions_dir}. Review them in the dashboard (Mapping tab).")
    return 0


def cmd_accept(args) -> int:
    """Accept the current suggestions for a table as-is (the CLI equivalent of 'Accept all')."""
    w = _workspace(args)
    for sp in _selected(w, args):
        doc = w.load_suggestions(sp)
        mapping = {s["column"]: (s["target"] or "ignore") for s in doc["suggestions"]}
        res = w.confirm(sp, mapping, confirmed_by=args.by)
        print(sp.key, "saved" if res["saved"] else f"NOT saved: {res}")
    return 0


def cmd_mappings(args) -> int:
    from onboard.config import Settings
    from onboard.mapping.store import MappingStore

    for rec in MappingStore(Settings().mapping_store_dir).all():
        print(f"{rec['fingerprint']}  {rec['table_key']:20} by={rec['confirmed_by']:12} at={rec['confirmed_at']}")
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
    sg = sub.add_parser("suggest", help="propose mappings (rules, then LLM with --llm)")
    sg.add_argument("--manifest", default=None)
    sg.add_argument("--table", default=None)
    sg.add_argument("--llm", action="store_true", help="send unresolved columns to the LLM provider")
    sg.add_argument("--dry-run", action="store_true", help="print the cost estimate, make no call")
    sg.add_argument("--yes", action="store_true", help="confirm a paid LLM call")
    sg.set_defaults(fn=cmd_suggest)
    ac = sub.add_parser("accept", help="accept current suggestions into the mapping store")
    ac.add_argument("--manifest", default=None)
    ac.add_argument("--table", default=None)
    ac.add_argument("--by", default="cli")
    ac.set_defaults(fn=cmd_accept)
    mp = sub.add_parser("mappings", help="list confirmed mappings")
    mp.set_defaults(fn=cmd_mappings)
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
