"""Connectors: CSV, Excel and a paginated JSON API. All return raw DataFrames (object dtype).

Raw means raw: no type coercion here, so profiling sees exactly what the customer sent
(ZIPs as ints from Excel, dates as a mix of strings and Excel date cells, and so on).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import httpx
import pandas as pd
import yaml


@dataclass
class TableSpec:
    source: str
    name: str
    kind: str  # csv | excel | json_api
    entity: str
    path: str | None = None
    sheet: str | None = None
    url: str | None = None
    endpoint: str | None = None

    @property
    def key(self) -> str:
        return f"{self.source}.{self.name}"


def load_manifest(path: Path) -> list[TableSpec]:
    doc = yaml.safe_load(Path(path).read_text())
    specs = []
    for src in doc["sources"]:
        for t in src["tables"]:
            specs.append(TableSpec(source=src["name"], kind=src["kind"], name=t["name"], entity=t["entity"],
                                   path=t.get("path"), sheet=t.get("sheet"), url=t.get("url"),
                                   endpoint=t.get("endpoint")))
    return specs


def read_csv(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, dtype=object, keep_default_na=False, na_values=[""])
    return df.astype(object).where(df.notna(), None)


def read_excel(path: Path, sheet: str | int = 0) -> pd.DataFrame:
    df = pd.read_excel(path, sheet_name=sheet, engine="calamine", dtype=object)
    return df.astype(object).where(df.notna(), None)


def _flatten(records: list[dict]) -> pd.DataFrame:
    df = pd.json_normalize(records, sep=".")
    for c in df.columns:
        if df[c].map(lambda v: isinstance(v, list)).any():
            df[c] = df[c].map(lambda v: ",".join(map(str, v)) if isinstance(v, list) else v)
    df = df.astype(object)
    return df.where(df.notna(), None)


def fetch_json_api(base_url: str, endpoint: str, data_path: Path | None = None, page_size: int = 2000) -> pd.DataFrame:
    """Page through {base_url}{endpoint}?page=N. base_url='inproc' serves the mock API in-process."""
    if base_url == "inproc":
        from fastapi.testclient import TestClient

        from onboard.mock_api import create_mock_app

        client: httpx.Client = TestClient(create_mock_app(data_path))
        prefix = ""
    else:
        client = httpx.Client(timeout=30)
        prefix = base_url.rstrip("/")
    records: list[dict] = []
    page: int | None = 1
    with client:
        while page:
            r = client.get(f"{prefix}{endpoint}", params={"page": page, "page_size": page_size})
            r.raise_for_status()
            body = r.json()
            records.extend(body["data"])
            page = body.get("next_page")
    return _flatten(records)


def load_table(spec: TableSpec, root: Path) -> pd.DataFrame:
    if spec.kind == "csv":
        return read_csv(root / spec.path)
    if spec.kind == "excel":
        return read_excel(root / spec.path, spec.sheet or 0)
    if spec.kind == "json_api":
        data_path = root / spec.path if spec.path else None
        return fetch_json_api(spec.url or "inproc", spec.endpoint or "/", data_path)
    raise ValueError(f"unknown source kind {spec.kind}")
