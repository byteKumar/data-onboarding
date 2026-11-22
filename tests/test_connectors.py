from onboard.config import Settings
from onboard.connectors import fetch_json_api, load_manifest, load_table


def test_all_tables_load(small_project):
    specs = load_manifest(small_project / "sources.yaml")
    assert {s.key for s in specs} == {"crm.contacts", "billing.accounts", "billing.invoices", "support.tickets"}
    for s in specs:
        df = load_table(s, small_project)
        assert len(df) > 0


def test_json_api_paginates_and_flattens(small_project):
    path = small_project / "data" / "raw" / "support_tickets.json"
    a = fetch_json_api("inproc", "/tickets", path, page_size=7)
    b = fetch_json_api("inproc", "/tickets", path, page_size=5000)
    assert len(a) == len(b)
    assert "requester.email" in a.columns
    assert isinstance(Settings().root.name, str)
