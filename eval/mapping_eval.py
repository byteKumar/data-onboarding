"""Mapping accuracy against the hidden ground truth.

    uv run python eval/mapping_eval.py --mode rules              # free, deterministic
    uv run python eval/mapping_eval.py --mode rules --unseen     # the 4th source, vs generator truth
    LLM_PROVIDER=anthropic uv run python eval/mapping_eval.py --mode llm            # prints estimate only
    LLM_PROVIDER=anthropic uv run python eval/mapping_eval.py --mode llm --yes      # PAID: rules + LLM

A column counts as correct when the final target (unresolved = 'ignore') equals the truth.
"""

from __future__ import annotations

import argparse
import sys

from eval_common import ROOT, save, truth_mapping

from onboard.connectors import load_manifest, load_table
from onboard.mapping.engine import suggest
from onboard.profiling import profile_table


def run(mode: str, unseen: bool, llm=None) -> dict:
    manifest = ROOT / ("eval/unseen_source.yaml" if unseen else "sources.yaml")
    gt_dir = ROOT / "data" / ("ground_truth_unseen" if unseen else "ground_truth")
    truth = truth_mapping(gt_dir)
    rows, usage_total = [], {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}
    stats_total: dict[str, int] = {}
    for spec in load_manifest(manifest):
        df = load_table(spec, ROOT)
        res = suggest(spec.entity, profile_table(df), llm=llm if mode == "llm" else None, table_key=spec.key)
        for k, v in res.stats().items():
            stats_total[k] = stats_total.get(k, 0) + v
        for k in usage_total:
            usage_total[k] += res.llm_usage.get(k, 0)
        for s in res.suggestions:
            want = truth[(spec.key, s.column)]
            got = s.target or "ignore"
            rows.append({"table": spec.key, "column": s.column, "truth": want, "predicted": got,
                         "unresolved": s.target is None, "method": s.method, "correct": got == want,
                         "rejected": s.rejected})
    n = len(rows)
    correct = sum(r["correct"] for r in rows)
    resolved = [r for r in rows if not r["unresolved"]]
    summary = {
        "mode": mode, "unseen": unseen, "columns": n, "correct": correct,
        "accuracy": round(correct / n, 4),
        "resolved": len(resolved), "correct_when_resolved": sum(r["correct"] for r in resolved),
        "unresolved": n - len(resolved),
        "unresolved_but_truth_ignore": sum(1 for r in rows if r["unresolved"] and r["truth"] == "ignore"),
        **stats_total, "llm_usage": usage_total,
    }
    return {"summary": summary, "columns": rows}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["rules", "llm"], default="rules")
    ap.add_argument("--unseen", action="store_true")
    ap.add_argument("--yes", action="store_true", help="allow the paid LLM call")
    args = ap.parse_args(argv)
    llm = None
    suffix = ""
    if args.mode == "llm":
        from onboard.config import llm_provider
        from onboard.mapping.llm import get_llm
        from onboard.service import Workspace

        provider = llm_provider()
        if provider == "none":
            print("Set LLM_PROVIDER=anthropic (paid) or fake (offline, NOT a real measurement).")
            return 2
        if provider == "anthropic":
            manifest = ROOT / ("eval/unseen_source.yaml" if args.unseen else "sources.yaml")
            w = Workspace(manifest=manifest)
            est = sum(w.llm_estimate(sp)["cost_usd"] for sp in w.specs())
            print(f"ESTIMATE: ~${est:.5f} for this eval run")
            if not args.yes:
                print("No paid call made. Re-run with --yes.")
                return 3
        else:
            suffix = "_fake"  # never report fake numbers as LLM accuracy
        llm = get_llm(provider, allow_paid=args.yes)
    out = run(args.mode, args.unseen, llm=llm)
    for r in out["columns"]:
        flag = "OK " if r["correct"] else "BAD"
        print(f"{flag} {r['table']:18} {r['column']:28} truth={r['truth']:18} got={r['predicted']:18} {r['method']}")
    print(out["summary"])
    name = f"mapping_{args.mode}{suffix}{'_unseen' if args.unseen else ''}"
    print("saved", save(name, {"command": "uv run python " + " ".join(sys.argv), **out}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
