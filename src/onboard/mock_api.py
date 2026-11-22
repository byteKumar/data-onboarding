"""A tiny mock of a support-desk JSON API (Zendesk-like), paginated.

Run standalone:  uv run uvicorn onboard.mock_api:app --port 8004
It is also mounted inside the main API at /mock/support.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query

from onboard.config import Settings


@lru_cache(maxsize=4)
def _load(path: str, mtime: float) -> list[dict]:
    return json.loads(Path(path).read_text())


def create_mock_app(data_path: Path | None = None) -> FastAPI:
    app = FastAPI(title="Mock support API")
    path = Path(data_path) if data_path else Settings().raw_dir / "support_tickets.json"

    @app.get("/tickets")
    def tickets(page: int = Query(1, ge=1), page_size: int = Query(1000, ge=1, le=5000)) -> dict:
        if not path.exists():
            raise HTTPException(404, f"no data at {path}; run `uv run onboard generate` first")
        data = _load(str(path), path.stat().st_mtime)
        start = (page - 1) * page_size
        chunk = data[start:start + page_size]
        has_more = start + page_size < len(data)
        return {"data": chunk, "page": page, "next_page": page + 1 if has_more else None, "total": len(data)}

    return app


app = create_mock_app()
