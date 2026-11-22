"""Validators reject impossible mappings, whoever proposed them (rule, LLM or human).

They only look at the column profile, so they are cheap and deterministic.
"""

from __future__ import annotations

from onboard.profiling import ColumnProfile
from onboard.schema import FIELDS, IGNORE


def validate(target: str, profile: ColumnProfile, entity: str) -> str | None:
    """Return None if the mapping is plausible, else a human-readable rejection reason."""
    if target == IGNORE:
        return None
    f = FIELDS.get(target)
    if f is None:
        return f"unknown target field '{target}'"
    if entity not in f.entities:
        return f"'{target}' does not belong in a {entity} table"
    if profile.pattern == "empty":
        return None
    if profile.pattern not in f.patterns:
        return (f"pattern mismatch: values look like {profile.pattern} "
                f"({profile.pattern_share:.0%}), '{target}' expects {'/'.join(f.patterns)}")
    if f.max_distinct is not None and profile.distinct_count > f.max_distinct:
        return f"{profile.distinct_count} distinct values is too many for category field '{target}'"
    if f.min_distinct_ratio is not None and profile.distinct_ratio < f.min_distinct_ratio:
        return f"only {profile.distinct_ratio:.0%} distinct, '{target}' must be a unique key"
    return None
