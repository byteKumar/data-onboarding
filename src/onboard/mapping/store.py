"""Confirmed mappings, keyed by a fingerprint of (entity, column names).

When the customer sends next month's export with the same columns, the fingerprint
matches and the confirmed mapping is reused with no rules, no LLM and no review.
A renamed or added column changes the fingerprint, which sends the table back to review.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path


def fingerprint(entity: str, columns: list[str]) -> str:
    body = entity + "\n" + "\n".join(sorted(c.strip() for c in columns))
    return hashlib.sha256(body.encode()).hexdigest()[:16]


class MappingStore:
    def __init__(self, directory: Path):
        self.dir = Path(directory)

    def _path(self, fp: str) -> Path:
        return self.dir / f"{fp}.json"

    def get(self, fp: str) -> dict | None:
        p = self._path(fp)
        return json.loads(p.read_text()) if p.exists() else None

    def get_mapping(self, fp: str) -> dict[str, str] | None:
        rec = self.get(fp)
        return rec["mapping"] if rec else None

    def save(self, fp: str, table_key: str, entity: str, mapping: dict[str, str], confirmed_by: str,
             note: str = "") -> dict:
        self.dir.mkdir(parents=True, exist_ok=True)
        prev = self.get(fp)
        history = (prev or {}).get("history", [])
        if prev:
            history.append({"mapping": prev["mapping"], "confirmed_by": prev["confirmed_by"],
                            "confirmed_at": prev["confirmed_at"]})
        rec = {
            "fingerprint": fp, "table_key": table_key, "entity": entity, "columns": sorted(mapping),
            "mapping": mapping, "confirmed_by": confirmed_by,
            "confirmed_at": datetime.now().isoformat(timespec="seconds"), "note": note, "history": history,
        }
        self._path(fp).write_text(json.dumps(rec, indent=2))
        return rec

    def all(self) -> list[dict]:
        if not self.dir.exists():
            return []
        return [json.loads(p.read_text()) for p in sorted(self.dir.glob("*.json"))]
