"""LLM mapping suggestions behind a small interface.

* FakeLLM       deterministic, offline, used by tests and the free demo (LLM_PROVIDER=fake)
* AnthropicLLM  the real thing (LLM_PROVIDER=anthropic), refuses to run unless the caller
                passed allow_paid=True, which the CLI only does after a dry-run estimate and --yes

Only column names, profile stats and a few sample values are sent. Samples of email and
phone columns are replaced by their shape (e.g. 'aaaa@aaaa.aaa') so no contact data leaves.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Protocol

from rapidfuzz import fuzz

from onboard.config import LLM_PRICE_IN_PER_M, LLM_PRICE_OUT_PER_M, llm_model, llm_provider
from onboard.mapping.rules import normalize
from onboard.profiling import ColumnProfile, _shape
from onboard.schema import FIELDS, IGNORE, fields_for_entity

SYSTEM = (
    "You map columns from a customer's data export onto a fixed unified schema. "
    "For every column you are given, choose exactly one target field from the allowed list, or 'ignore' "
    "if the column has no place in the schema. Use the column name, the profiled pattern and the sample "
    "values. Never invent fields. Give a confidence between 0 and 1 and a one-sentence reason. "
    "Prefer 'ignore' over a guess when unsure."
)
OUTPUT_TOKENS_PER_COLUMN = 60  # for the cost estimate only


class PaidCallNotConfirmed(RuntimeError):
    pass


class LLMError(RuntimeError):
    pass


@dataclass
class Proposal:
    column: str
    target: str
    confidence: float
    reason: str


@dataclass
class LLMResult:
    proposals: list[Proposal]
    usage: dict = field(default_factory=dict)


class MappingLLM(Protocol):
    name: str

    def suggest(self, *, table_key: str, entity: str, columns: list[ColumnProfile],
                already_mapped: dict[str, str]) -> LLMResult: ...


def allowed_targets(entity: str, already_mapped: dict[str, str]) -> list[str]:
    return [f for f in fields_for_entity(entity) if f not in already_mapped] + [IGNORE]


def _safe_samples(p: ColumnProfile) -> list[str]:
    if p.pattern in ("email", "phone"):
        return [_shape(s) for s in p.samples[:3]]
    return [s[:40] for s in p.samples[:3]]


def build_prompt(table_key: str, entity: str, columns: list[ColumnProfile], already_mapped: dict[str, str]) -> str:
    targets = allowed_targets(entity, already_mapped)
    field_lines = [f"- {t}: {FIELDS[t].description}" for t in targets if t != IGNORE]
    field_lines.append("- ignore: the column does not belong in the unified schema")
    cols = [{
        "column": p.column, "pattern": p.pattern, "dtype": p.dtype, "null_rate": p.null_rate,
        "distinct_count": p.distinct_count, "samples": _safe_samples(p),
    } for p in columns]
    return (
        f"Table '{table_key}' holds {entity} records.\n"
        f"Already mapped (do not reuse): {json.dumps(already_mapped) if already_mapped else 'none'}\n\n"
        "Allowed target fields:\n" + "\n".join(field_lines) + "\n\n"
        "Columns to map:\n" + json.dumps(cols, indent=1)
    )


def response_schema(targets: list[str]) -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["mappings"],
        "properties": {
            "mappings": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["column", "target_field", "confidence", "reason"],
                    "properties": {
                        "column": {"type": "string"},
                        "target_field": {"type": "string", "enum": targets},
                        "confidence": {"type": "number"},
                        "reason": {"type": "string"},
                    },
                },
            }
        },
    }


def cost_usd(input_tokens: int, output_tokens: int) -> float:
    return input_tokens * LLM_PRICE_IN_PER_M / 1e6 + output_tokens * LLM_PRICE_OUT_PER_M / 1e6


def estimate(table_key: str, entity: str, columns: list[ColumnProfile], already_mapped: dict[str, str]) -> dict:
    """Rough pre-call estimate (~4 characters per token). No API call is made."""
    if not columns:
        return {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}
    prompt = build_prompt(table_key, entity, columns, already_mapped)
    schema = json.dumps(response_schema(allowed_targets(entity, already_mapped)))
    tin = (len(SYSTEM) + len(prompt) + len(schema)) // 4 + 50
    tout = OUTPUT_TOKENS_PER_COLUMN * len(columns) + 20
    return {"input_tokens": tin, "output_tokens": tout, "cost_usd": round(cost_usd(tin, tout), 6)}


class FakeLLM:
    """Deterministic stand-in. Fuzzy-matches column names to field names/descriptions.

    It is deliberately mediocre so validators have something to reject. `script`
    pins answers for specific columns in tests. Its accuracy says nothing about a real model.
    """

    name = "fake"

    def __init__(self, script: dict[str, tuple[str, float]] | None = None):
        self.script = script or {}
        self.calls = 0

    def suggest(self, *, table_key, entity, columns, already_mapped) -> LLMResult:
        self.calls += 1
        targets = [t for t in allowed_targets(entity, already_mapped) if t != IGNORE]
        props = []
        for p in columns:
            if p.column in self.script:
                t, c = self.script[p.column]
                props.append(Proposal(p.column, t, c, "scripted"))
                continue
            name = normalize(p.column)
            best, score = IGNORE, 0.0
            for t in targets:
                s = max(fuzz.token_set_ratio(name, t.replace("_", " ")),
                        0.8 * fuzz.partial_ratio(name, FIELDS[t].description.lower()))
                if s > score:
                    best, score = t, s
            if score < 55:
                best = IGNORE
            props.append(Proposal(p.column, best, round(score / 100, 2), "fuzzy name match (fake LLM)"))
        return LLMResult(props, {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0, "provider": "fake"})


class AnthropicLLM:
    name = "anthropic"

    def __init__(self, model: str | None = None, allow_paid: bool = False, client=None):
        self.model = model or llm_model()
        self.allow_paid = allow_paid
        self._client = client

    def suggest(self, *, table_key, entity, columns, already_mapped) -> LLMResult:
        if not self.allow_paid:
            raise PaidCallNotConfirmed("paid LLM call refused: run with --dry-run first, then --yes")
        if self._client is None:
            import anthropic

            self._client = anthropic.Anthropic()
        targets = allowed_targets(entity, already_mapped)
        resp = self._client.messages.create(
            model=self.model,
            max_tokens=4000,
            system=SYSTEM,
            messages=[{"role": "user", "content": build_prompt(table_key, entity, columns, already_mapped)}],
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": response_schema(targets)}},
        )
        usage = {
            "input_tokens": resp.usage.input_tokens,
            "output_tokens": resp.usage.output_tokens,
            "cost_usd": round(cost_usd(resp.usage.input_tokens, resp.usage.output_tokens), 6),
            "provider": "anthropic", "model": self.model,
        }
        if resp.stop_reason in ("refusal", "max_tokens"):
            raise LLMError(f"LLM stopped with stop_reason={resp.stop_reason}; usage={usage}")
        text = next(b.text for b in resp.content if b.type == "text")
        data = json.loads(text)
        wanted = {p.column for p in columns}
        props = [Proposal(m["column"], m["target_field"], float(m["confidence"]), m["reason"])
                 for m in data["mappings"] if m["column"] in wanted]
        return LLMResult(props, usage)


def get_llm(provider: str | None = None, allow_paid: bool = False) -> MappingLLM | None:
    provider = provider or llm_provider()
    if provider == "none":
        return None
    if provider == "fake":
        return FakeLLM()
    if provider == "anthropic":
        return AnthropicLLM(allow_paid=allow_paid)
    raise ValueError(f"unknown LLM_PROVIDER '{provider}' (none | fake | anthropic)")
