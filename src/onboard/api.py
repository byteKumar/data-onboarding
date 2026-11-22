"""FastAPI backend for the dashboard: mapping review/confirm + analytics.

    uv run uvicorn onboard.api:app --port 8003
"""

from __future__ import annotations

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from onboard.mock_api import create_mock_app
from onboard.schema import FIELDS, IGNORE
from onboard.service import Workspace

app = FastAPI(title="data-onboarding API")
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
                   allow_methods=["*"], allow_headers=["*"])
app.mount("/mock/support", create_mock_app())

_ws: Workspace | None = None


def ws() -> Workspace:
    global _ws
    if _ws is None:
        _ws = Workspace()
    return _ws


def set_workspace(w: Workspace) -> None:
    """Used by tests to point the API at a temporary project."""
    global _ws
    _ws = w


def _spec(key: str):
    try:
        return ws().spec(key)
    except KeyError as e:
        raise HTTPException(404, f"unknown table {key}") from e


class ColumnChoice(BaseModel):
    column: str
    target: str


class ConfirmBody(BaseModel):
    mappings: list[ColumnChoice]
    confirmed_by: str = "dashboard"


@app.get("/api/health")
def health() -> dict:
    return {"ok": True}


@app.get("/api/fields")
def fields() -> list[dict]:
    out = [{"name": f.name, "description": f.description, "entities": list(f.entities)} for f in FIELDS.values()]
    return out + [{"name": IGNORE, "description": "Drop this column", "entities": ["*"]}]


@app.get("/api/tables")
def tables() -> list[dict]:
    w = ws()
    out = []
    for sp in w.specs():
        doc = w.load_suggestions(sp)
        out.append({"key": sp.key, "entity": sp.entity, "kind": sp.kind, "fingerprint": doc["fingerprint"],
                    "confirmed": doc["confirmed"], "provider": doc["provider"], "stats": doc["stats"]})
    return out


@app.get("/api/tables/{key}/suggestions")
def table_suggestions(key: str) -> dict:
    return ws().load_suggestions(_spec(key))


@app.post("/api/tables/{key}/confirm")
def confirm(key: str, body: ConfirmBody) -> dict:
    res = ws().confirm(_spec(key), {c.column: c.target for c in body.mappings}, body.confirmed_by)
    if not res["saved"]:
        raise HTTPException(422, res)
    return res
