"""Mapping engine: stored mapping -> rules -> (optional) LLM, with validators on every proposal.

Order matters and is the main design decision:
1. A mapping a human already confirmed for the same column set is reused as is.
2. Deterministic rules go next: free, explainable, and they never hallucinate.
3. Only the columns rules could not resolve are sent to the LLM (names + profiles only).
4. Every proposal, from any source, passes the same validators. One column per field.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

from onboard.mapping.rules import rule_candidates
from onboard.mapping.validators import validate
from onboard.profiling import ColumnProfile
from onboard.schema import IGNORE


@dataclass
class Suggestion:
    column: str
    target: str | None  # None = unresolved (dropped unless a human maps it)
    confidence: float = 0.0
    method: str = "none"  # store | rule:synonym | rule:token | rule:pattern | llm | none
    status: str = "unresolved"  # ok | unresolved
    reason: str = ""
    rejected: list[dict] = field(default_factory=list)  # proposals validators or conflicts refused

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class MappingResult:
    entity: str
    suggestions: list[Suggestion]
    llm_usage: dict = field(default_factory=dict)

    def mapping(self) -> dict[str, str]:
        """Final column -> field. Unresolved columns are ignored (dropped)."""
        return {s.column: (s.target or IGNORE) for s in self.suggestions}

    def stats(self) -> dict:
        def n(pred) -> int:
            return sum(1 for s in self.suggestions if pred(s))

        rejected = [r for s in self.suggestions for r in s.rejected]
        return {
            "columns": len(self.suggestions),
            "resolved_by_store": n(lambda s: s.method == "store"),
            "resolved_by_rules": n(lambda s: s.method.startswith("rule") and s.target),
            "resolved_by_llm": n(lambda s: s.method == "llm" and s.target),
            "unresolved": n(lambda s: s.target is None),
            "rule_proposals_rejected": sum(1 for r in rejected if r["method"].startswith("rule")),
            "llm_proposals_rejected": sum(1 for r in rejected if r["method"] == "llm"),
        }


def _assign(s: Suggestion, target: str, conf: float, method: str, reason: str) -> None:
    s.target, s.confidence, s.method, s.status, s.reason = target, conf, method, "ok", reason


def suggest(entity: str, profiles: list[ColumnProfile], llm=None, stored: dict[str, str] | None = None,
            table_key: str = "") -> MappingResult:
    by_col = {p.column: p for p in profiles}
    out = {p.column: Suggestion(p.column, None) for p in profiles}

    if stored and set(stored) == set(by_col):
        for col, tgt in stored.items():
            _assign(out[col], tgt, 1.0, "store", "previously confirmed by a human")
        return MappingResult(entity, list(out.values()))

    # 1. rules: first candidate that passes validation
    for p in profiles:
        s = out[p.column]
        for hit in rule_candidates(p.column, p, entity):
            why_not = validate(hit.target, p, entity)
            if why_not:
                s.rejected.append({"target": hit.target, "method": hit.method, "reason": why_not})
                continue
            _assign(s, hit.target, hit.confidence, hit.method, hit.why)
            break

    _resolve_conflicts(out)

    # 2. LLM for what is left
    usage: dict = {}
    pending = [by_col[c] for c, s in out.items() if s.target is None]
    if llm is not None and pending:
        taken = {s.target: s.column for s in out.values() if s.target and s.target != IGNORE}
        result = llm.suggest(table_key=table_key, entity=entity, columns=pending, already_mapped=taken)
        usage = result.usage
        for prop in result.proposals:
            s = out.get(prop.column)
            if s is None or s.target is not None:
                continue
            why_not = validate(prop.target, by_col[prop.column], entity)
            if not why_not and prop.target != IGNORE and prop.target in taken:
                why_not = f"conflict: '{prop.target}' already mapped from '{taken[prop.target]}'"
            if why_not:
                s.rejected.append({"target": prop.target, "method": "llm", "reason": why_not,
                                   "confidence": prop.confidence})
                continue
            _assign(s, prop.target, prop.confidence, "llm", prop.reason)
            if prop.target != IGNORE:
                taken[prop.target] = prop.column

    for s in out.values():
        if s.target is None and not s.reason:
            s.reason = "no rule matched" if not s.rejected else "all proposals were rejected"
    return MappingResult(entity, list(out.values()), usage)


def _resolve_conflicts(out: dict[str, Suggestion]) -> None:
    """One column per target field: keep the most confident, un-map the rest."""
    by_target: dict[str, list[Suggestion]] = {}
    for s in out.values():
        if s.target and s.target != IGNORE:
            by_target.setdefault(s.target, []).append(s)
    for target, group in by_target.items():
        if len(group) < 2:
            continue
        group.sort(key=lambda s: -s.confidence)
        winner = group[0]
        for loser in group[1:]:
            loser.rejected.append({"target": target, "method": loser.method,
                                   "reason": f"conflict: '{winner.column}' is a stronger match"})
            loser.target, loser.confidence, loser.method, loser.status = None, 0.0, "none", "unresolved"
            loser.reason = f"lost '{target}' to '{winner.column}'"
