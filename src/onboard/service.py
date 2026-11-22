"""Glue used by both the CLI and the API: profile -> suggest -> confirm, with files on disk."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from onboard.config import Settings
from onboard.connectors import TableSpec, load_manifest, load_table
from onboard.mapping.engine import MappingResult, suggest
from onboard.mapping.llm import estimate
from onboard.mapping.store import MappingStore, fingerprint
from onboard.mapping.validators import validate
from onboard.profiling import ColumnProfile, profile_table
from onboard.schema import IGNORE


class Workspace:
    def __init__(self, settings: Settings | None = None, manifest: Path | None = None):
        self.s = settings or Settings()
        self.manifest = Path(manifest) if manifest else self.s.manifest_path
        self.store = MappingStore(self.s.mapping_store_dir)
        self._profiles: dict[str, list[ColumnProfile]] = {}

    def specs(self) -> list[TableSpec]:
        return load_manifest(self.manifest)

    def spec(self, key: str) -> TableSpec:
        for sp in self.specs():
            if sp.key == key:
                return sp
        raise KeyError(key)

    def profiles(self, spec: TableSpec) -> list[ColumnProfile]:
        if spec.key not in self._profiles:
            self._profiles[spec.key] = profile_table(load_table(spec, self.s.root))
        return self._profiles[spec.key]

    def fp(self, spec: TableSpec) -> str:
        return fingerprint(spec.entity, [p.column for p in self.profiles(spec)])

    def suggestions_path(self, spec: TableSpec) -> Path:
        return self.s.suggestions_dir / f"{spec.key}.json"

    # ---------------------------------------------------------------- suggest
    def rules_only(self, spec: TableSpec) -> MappingResult:
        return suggest(spec.entity, self.profiles(spec), llm=None, table_key=spec.key)

    def llm_estimate(self, spec: TableSpec) -> dict:
        if self.store.get(self.fp(spec)):
            return {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0, "note": "stored mapping, no call"}
        res = self.rules_only(spec)
        by_col = {p.column: p for p in self.profiles(spec)}
        pending = [by_col[s.column] for s in res.suggestions if s.target is None]
        taken = {s.target: s.column for s in res.suggestions if s.target and s.target != IGNORE}
        return {**estimate(spec.key, spec.entity, pending, taken), "columns_for_llm": len(pending)}

    def run_suggest(self, spec: TableSpec, llm=None) -> dict:
        fp = self.fp(spec)
        stored = self.store.get_mapping(fp)
        res = suggest(spec.entity, self.profiles(spec), llm=llm, stored=stored, table_key=spec.key)
        doc = self._doc(spec, res, provider=(llm.name if llm and not stored else "none"))
        p = self.suggestions_path(spec)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(doc, indent=2, default=str))
        return doc

    def _doc(self, spec: TableSpec, res: MappingResult, provider: str) -> dict:
        fp = self.fp(spec)
        return {
            "table_key": spec.key, "entity": spec.entity, "fingerprint": fp, "provider": provider,
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "profiles": [p.to_dict() for p in self.profiles(spec)],
            "suggestions": [s.to_dict() for s in res.suggestions],
            "stats": res.stats(), "llm_usage": res.llm_usage,
            "confirmed": self.store.get(fp) is not None,
        }

    def load_suggestions(self, spec: TableSpec) -> dict:
        p = self.suggestions_path(spec)
        if p.exists():
            doc = json.loads(p.read_text())
            if doc["fingerprint"] == self.fp(spec):
                doc["confirmed"] = self.store.get(doc["fingerprint"]) is not None
                return doc
        stored = self.store.get_mapping(self.fp(spec))
        res = suggest(spec.entity, self.profiles(spec), stored=stored, table_key=spec.key)
        return self._doc(spec, res, provider="none")

    # ---------------------------------------------------------------- confirm
    def confirm(self, spec: TableSpec, mapping: dict[str, str], confirmed_by: str) -> dict:
        profiles = {p.column: p for p in self.profiles(spec)}
        missing = sorted(set(profiles) - set(mapping))
        extra = sorted(set(mapping) - set(profiles))
        rejected = []
        for col, tgt in mapping.items():
            if col in profiles and (why := validate(tgt, profiles[col], spec.entity)):
                rejected.append({"column": col, "target": tgt, "reason": why})
        used: dict[str, str] = {}
        for col, tgt in mapping.items():
            if tgt != IGNORE and tgt in used:
                rejected.append({"column": col, "target": tgt, "reason": f"'{tgt}' already mapped from '{used[tgt]}'"})
            used.setdefault(tgt, col)
        if missing or extra or rejected:
            return {"saved": False, "missing": missing, "unknown_columns": extra, "rejected": rejected}
        fp = self.fp(spec)
        self.store.save(fp, spec.key, spec.entity, mapping, confirmed_by)
        return {"saved": True, "fingerprint": fp, "rejected": []}
