"""End-to-end run: load -> apply confirmed mappings -> normalize -> checks -> model -> ER -> DuckDB.

The pipeline only uses mappings a human confirmed (the mapping store). A table whose
column set has no confirmed mapping stops the run with a clear message, on purpose:
silently guessing a mapping is how bad data gets into a customer's dashboard.
"""

from __future__ import annotations

import json
import time
import uuid
from datetime import datetime
from pathlib import Path

import duckdb
import pandas as pd

from onboard.config import Settings
from onboard.connectors import load_manifest, load_table
from onboard.mapping.store import MappingStore, fingerprint
from onboard.model import SourceFrame, build
from onboard.quality import QualityLog
from onboard.transform import normalize_table


class MappingNotConfirmed(RuntimeError):
    def __init__(self, table_key: str, fp: str):
        super().__init__(f"{table_key}: no confirmed mapping for fingerprint {fp}. "
                         f"Run `uv run onboard suggest --table {table_key}` and confirm it in the dashboard.")
        self.table_key, self.fp = table_key, fp


def _write(con: duckdb.DuckDBPyConnection, name: str, df: pd.DataFrame, append: bool = False) -> None:
    df = df[[c for c in df.columns if not c.startswith("_")]]
    con.register("_df", df)
    if append:
        con.execute(f"CREATE TABLE IF NOT EXISTS {name} AS SELECT * FROM _df WHERE false")
        con.execute(f"INSERT INTO {name} SELECT * FROM _df")
    else:
        con.execute(f"CREATE OR REPLACE TABLE {name} AS SELECT * FROM _df")
    con.unregister("_df")


def run(settings: Settings | None = None, manifest: Path | None = None, db_path: Path | None = None,
        run_er: bool = True) -> dict:
    s = settings or Settings()
    run_id = datetime.now().strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:6]
    timings: dict[str, float] = {}
    t_all = time.perf_counter()
    store = MappingStore(s.mapping_store_dir)

    frames = []
    for spec in load_manifest(Path(manifest) if manifest else s.manifest_path):
        t = time.perf_counter()
        raw = load_table(spec, s.root)
        timings[f"load:{spec.key}"] = time.perf_counter() - t
        fp = fingerprint(spec.entity, [str(c) for c in raw.columns])
        mapping = store.get_mapping(fp)
        if mapping is None:
            raise MappingNotConfirmed(spec.key, fp)
        t = time.perf_counter()
        frames.append(SourceFrame(spec.key, spec.entity, raw, normalize_table(raw, mapping)))
        timings[f"normalize:{spec.key}"] = time.perf_counter() - t

    t = time.perf_counter()
    log = QualityLog()
    tables = build(frames, log)
    timings["checks+model"] = time.perf_counter() - t

    er_stats: dict = {}
    if run_er:
        from onboard.er import resolve

        t = time.perf_counter()
        er_out = resolve(tables["customer_records"], s.er_model_path, run_id)
        tables.update(er_out.tables)
        er_stats = er_out.stats
        timings["entity_resolution"] = time.perf_counter() - t

    t = time.perf_counter()
    quarantine, dq = log.frames(run_id)
    db = Path(db_path) if db_path else s.db_path
    db.parent.mkdir(parents=True, exist_ok=True)
    with duckdb.connect(str(db)) as con:
        for name, df in tables.items():
            if name == "merge_history":
                _write(con, name, df, append=True)
            else:
                _write(con, name, df)
        _write(con, "quarantine", quarantine)
        _write(con, "dq_summary", dq)
        if run_er:
            from onboard.analytics import create_views

            create_views(con)
    timings["write_duckdb"] = time.perf_counter() - t
    timings["total"] = time.perf_counter() - t_all

    summary = {
        "run_id": run_id, "db": str(db),
        "rows_in": {f.key: len(f.raw) for f in frames},
        "rows_out": {k: len(v) for k, v in tables.items()},
        "quarantined": len(quarantine),
        "quarantined_by_check": quarantine.groupby("check_name").size().to_dict() if len(quarantine) else {},
        "er": er_stats,
        "timings_s": {k: round(v, 3) for k, v in timings.items()},
    }
    with duckdb.connect(str(db)) as con:
        _write(con, "pipeline_runs", pd.DataFrame([{
            "run_id": run_id, "finished_at": datetime.now(), "total_s": timings["total"],
            "summary_json": json.dumps(summary, default=str)}]), append=True)
    return summary
