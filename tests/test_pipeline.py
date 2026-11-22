import duckdb
import pytest

from onboard.config import Settings
from onboard.pipeline import MappingNotConfirmed, run


def test_pipeline_refuses_unconfirmed_tables(small_project, tmp_path):
    s = Settings(root=tmp_path)  # empty mapping store
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "raw").symlink_to(small_project / "data" / "raw", target_is_directory=True)
    with pytest.raises(MappingNotConfirmed):
        run(settings=s, manifest=small_project / "sources.yaml", db_path=tmp_path / "x.duckdb", run_er=False)


def test_pipeline_builds_model_and_quarantines(confirmed_project, tmp_path):
    db = tmp_path / "t.duckdb"
    out = run(settings=Settings(root=confirmed_project), db_path=db, run_er=False)
    assert out["rows_out"]["customer_records"] > 400
    with duckdb.connect(str(db), read_only=True) as con:
        checks = {r[0] for r in con.execute("select distinct check_name from quarantine").fetchall()}
        assert checks <= {"missing_required", "duplicate_key", "out_of_range", "invalid", "referential"}
        assert con.execute("select count(*) from quarantine where raw_json is null").fetchone()[0] == 0
        # referential integrity holds in the model
        orphans = con.execute("select count(*) from invoices i left join accounts a using(account_id) "
                              "where a.account_id is null").fetchone()[0]
        assert orphans == 0
        assert con.execute("select min(amount_usd) from invoices").fetchone()[0] > 0
        assert con.execute("select count(*) from tickets where csat_score not between 1 and 5").fetchone()[0] == 0
