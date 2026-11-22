import json
from types import SimpleNamespace

import pandas as pd
import pytest

from onboard.mapping.engine import suggest
from onboard.mapping.llm import (
    AnthropicLLM,
    FakeLLM,
    LLMError,
    PaidCallNotConfirmed,
    build_prompt,
    estimate,
    get_llm,
    response_schema,
)
from onboard.mapping.store import MappingStore, fingerprint
from onboard.profiling import profile_column


def prof(name, values):
    return profile_column(name, pd.Series(values, dtype=object))


EMAILS = [f"user{i}@corp.com" for i in range(30)]
DATES = [f"0{1 + i % 9}/1{i % 9}/2023" for i in range(30)]
TIERS = ["Enterprise", "SMB", "Mid-Market"] * 10


def test_default_provider_is_none_in_tests():
    assert get_llm() is None


def test_llm_only_sees_unresolved_columns_and_validators_reject_bad_ones():
    profiles = [prof("E-mail", EMAILS), prof("Tier", TIERS), prof("Weird", DATES)]
    fake = FakeLLM(script={"Tier": ("segment", 0.8), "Weird": ("email", 0.9)})  # 2nd is impossible
    res = suggest("customer", profiles, llm=fake)
    by = {s.column: s for s in res.suggestions}
    assert by["E-mail"].method.startswith("rule")
    assert by["Tier"].target == "segment" and by["Tier"].method == "llm"
    assert by["Weird"].target is None
    assert res.stats()["llm_proposals_rejected"] == 1


def test_llm_cannot_steal_a_field_rules_already_mapped():
    profiles = [prof("email", EMAILS), prof("alt", EMAILS[::-1])]
    res = suggest("customer", profiles, llm=FakeLLM(script={"alt": ("email", 0.99)}))
    alt = next(s for s in res.suggestions if s.column == "alt")
    assert alt.target is None and "conflict" in alt.rejected[0]["reason"]


def test_prompt_never_contains_raw_emails():
    p = build_prompt("t", "customer", [prof("x", EMAILS)], {})
    assert "@corp.com" not in p
    assert estimate("t", "customer", [prof("x", EMAILS)], {})["cost_usd"] > 0


def test_schema_is_strict():
    s = response_schema(["email", "ignore"])
    item = s["properties"]["mappings"]["items"]
    assert s["additionalProperties"] is False and item["additionalProperties"] is False
    assert set(item["required"]) == {"column", "target_field", "confidence", "reason"}


def test_anthropic_refuses_without_confirmation():
    with pytest.raises(PaidCallNotConfirmed):
        AnthropicLLM(allow_paid=False).suggest(table_key="t", entity="customer", columns=[prof("x", EMAILS)],
                                               already_mapped={})


class StubMessages:
    """Stands in for client.messages; records the call, returns a canned response. No network."""

    def __init__(self, stop_reason="end_turn", payload=None):
        self.stop_reason, self.payload, self.kwargs = stop_reason, payload, None

    def create(self, **kwargs):
        self.kwargs = kwargs
        text = json.dumps(self.payload or {"mappings": [
            {"column": "Tier", "target_field": "segment", "confidence": 0.9, "reason": "tier values"}]})
        return SimpleNamespace(stop_reason=self.stop_reason, content=[SimpleNamespace(type="text", text=text)],
                               usage=SimpleNamespace(input_tokens=1000, output_tokens=200))


def test_anthropic_parse_and_cost_with_stub_client():
    stub = StubMessages()
    llm = AnthropicLLM(model="test-model", allow_paid=True, client=SimpleNamespace(messages=stub))
    res = llm.suggest(table_key="t", entity="customer", columns=[prof("Tier", TIERS)], already_mapped={})
    assert res.proposals[0].target == "segment"
    assert res.usage["cost_usd"] == pytest.approx(1000 * 0.10 / 1e6 + 200 * 0.50 / 1e6)
    assert stub.kwargs["output_config"]["format"]["type"] == "json_schema"
    assert "temperature" not in stub.kwargs and "thinking" not in stub.kwargs


@pytest.mark.parametrize("reason", ["refusal", "max_tokens"])
def test_anthropic_stop_reasons_raise(reason):
    llm = AnthropicLLM(allow_paid=True, client=SimpleNamespace(messages=StubMessages(stop_reason=reason)))
    with pytest.raises(LLMError):
        llm.suggest(table_key="t", entity="customer", columns=[prof("Tier", TIERS)], already_mapped={})


def test_store_reuse_skips_rules_and_llm(tmp_path):
    store = MappingStore(tmp_path)
    fp = fingerprint("customer", ["Tier", "E-mail"])
    assert fp == fingerprint("customer", ["E-mail", "Tier"])
    store.save(fp, "crm.contacts", "customer", {"Tier": "segment", "E-mail": "email"}, "tester")
    fake = FakeLLM()
    res = suggest("customer", [prof("E-mail", EMAILS), prof("Tier", TIERS)], llm=fake,
                  stored=store.get_mapping(fp))
    assert all(s.method == "store" for s in res.suggestions)
    assert fake.calls == 0
    store.save(fp, "crm.contacts", "customer", {"Tier": "ignore", "E-mail": "email"}, "tester2")
    assert len(store.get(fp)["history"]) == 1
