import pytest
from fastapi.testclient import TestClient

from onboard import api
from onboard.config import Settings
from onboard.service import Workspace


@pytest.fixture()
def client(small_project, tmp_path):
    s = Settings(root=small_project)
    w = Workspace(settings=s)
    api.set_workspace(w)
    yield TestClient(api.app)
    api.set_workspace(None)


def test_tables_and_suggestions(client):
    tables = client.get("/api/tables").json()
    assert {t["key"] for t in tables} >= {"crm.contacts", "support.tickets"}
    doc = client.get("/api/tables/crm.contacts/suggestions").json()
    assert doc["entity"] == "customer"
    s = doc["suggestions"][0]
    assert {"column", "target", "confidence", "method", "status", "rejected"} <= set(s)


def test_confirm_rejects_impossible_then_saves(client):
    doc = client.get("/api/tables/crm.contacts/suggestions").json()
    mapping = [{"column": s["column"], "target": s["target"] or "ignore"} for s in doc["suggestions"]]
    bad = [dict(m) for m in mapping]
    for m in bad:
        if m["column"] == "Created Date":
            m["target"] = "email"
    r = client.post("/api/tables/crm.contacts/confirm", json={"mappings": bad})
    assert r.status_code == 422
    assert r.json()["detail"]["rejected"][0]["column"] == "Created Date"
    r = client.post("/api/tables/crm.contacts/confirm", json={"mappings": mapping, "confirmed_by": "me"})
    assert r.status_code == 200 and r.json()["saved"]
    doc = client.get("/api/tables/crm.contacts/suggestions").json()
    assert doc["confirmed"] is True
    assert all(s["method"] == "store" for s in doc["suggestions"])


def test_mock_support_api_mounted(client):
    assert client.get("/api/health").json() == {"ok": True}
