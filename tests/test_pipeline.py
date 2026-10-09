"""Offline tests: every network call is replaced with fixtures."""

import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from tracker import config, extract, main, owners, store
from tracker.sources import ireland, planit

ROOT = Path(__file__).resolve().parent.parent

PLANIT_PAGE = {
    "total": 2,
    "records": [
        {
            "name": "Buckinghamshire/24/0001/FUL", "uid": "24/0001/FUL",
            "area_name": "Buckinghamshire", "start_date": "2026-06-01",
            "app_state": "Undecided", "link": "https://example/1",
            "description": "Erection of two data centre buildings (90MW IT load) with substation",
            "address": "Land at Example Road", "lat": 51.6, "lng": -0.8,
            "other_fields": {"applicant_company": "Example DC Bucks Limited", "agent_company": "Planning Co"},
        },
        {
            "name": "Slough/24/0002/FUL", "uid": "24/0002/FUL", "area_name": "Slough",
            "start_date": "2026-05-01", "app_state": "Permitted", "decided_date": "2026-09-01",
            "description": "New server room within existing office", "address": "1 High St",
            "other_fields": {"applicant_company": "Acme Insurance Ltd"},
        },
    ],
}

IE_META = {"fields": [{"name": n} for n in [
    "OBJECTID", "PlanningAuthority", "ApplicationNumber", "DevelopmentDescription",
    "DevelopmentAddress", "ApplicationStatus", "Decision", "ReceivedDate", "DecisionDate",
    "ApplicantSurname", "ApplicantForename", "LinkAppDetails"]]}
IE_QUERY = {"features": [{
    "attributes": {
        "PlanningAuthority": "South Dublin County Council", "ApplicationNumber": "SD26A/0100",
        "DevelopmentDescription": "10 year permission for a data centre campus with 110kV substation",
        "DevelopmentAddress": "Grange Castle", "ApplicationStatus": "Decided",
        "Decision": "Grant Permission", "ReceivedDate": 1767225600000, "DecisionDate": 1777593600000,
        "ApplicantSurname": "CyrusOne Ireland Limited", "ApplicantForename": None,
        "LinkAppDetails": "https://example/ie"},
    "geometry": {"x": -6.44, "y": 53.32}}]}


@pytest.fixture
def tmp_data(tmp_path, monkeypatch):
    d = tmp_path / "data"
    d.mkdir()
    shutil.copy(ROOT / "data" / "known_owners.csv", d / "known_owners.csv")
    monkeypatch.setattr(config, "DATA_DIR", d)
    monkeypatch.setattr(config, "MASTER_CSV", d / "applications.csv")
    monkeypatch.setattr(config, "CHANGES_DIR", d / "changes")
    monkeypatch.setattr(config, "KNOWN_OWNERS_CSV", d / "known_owners.csv")
    monkeypatch.setattr(config, "SEARCH_PHRASES", ["data centre"])
    return d


def fake_planit(url, params=None, auth=None):
    assert params["page"] >= 1, "PlanIt rejects page < 1"
    return PLANIT_PAGE


def fake_ireland(url, params=None, auth=None):
    return IE_META if url == ireland.LAYER else IE_QUERY


CH = {
    "/search/companies": {"items": [{"company_number": "11111111", "title": "EXAMPLE DC BUCKS LIMITED"}]},
    "/company/11111111/persons-with-significant-control": {"items": [{
        "kind": "corporate-entity-person-with-significant-control", "name": "Example DC Holdco Ltd",
        "identification": {"registration_number": "22222222", "country_registered": "England"}}]},
    "/company/22222222/persons-with-significant-control": {"items": [{
        "kind": "corporate-entity-person-with-significant-control", "name": "Vantage Data Centers EMEA S.a r.l.",
        "identification": {"registration_number": "B123", "country_registered": "Luxembourg"}}]},
}


def fake_ch(url, params=None, auth=None):
    return CH.get(url.replace(owners.CH_API, ""), {})


class FakeClaude:
    def __init__(self):
        self.calls = 0
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kw):
        self.calls += 1
        text = kw["messages"][0]["content"].split("Application:")[-1]
        dc = "server room" not in text
        out = {"is_data_centre": dc, "category": "hyperscale/campus" if dc else "not data centre",
               "stage": "full application", "mw_it": 90 if "90MW" in text else None, "mw_grid": None,
               "floor_area_m2": None, "halls": 2 if "two" in text else None,
               "operator_mentioned": None, "extract_notes": "x"}
        return SimpleNamespace(content=[SimpleNamespace(text="Here: " + json.dumps(out))])


def test_planit_normalise_and_paging(monkeypatch):
    monkeypatch.setattr(planit, "get_json", fake_planit)
    monkeypatch.setattr(config, "SEARCH_PHRASES", ["data centre"])
    rows = planit.fetch(30)
    assert {r["id"] for r in rows} == {"UK:Buckinghamshire/24/0001/FUL", "UK:Slough/24/0002/FUL"}
    r = next(r for r in rows if "Bucks" in r["applicant"])
    assert r["agent"] == "Planning Co" and r["authority"] == "Buckinghamshire"


def test_planit_placeholder_names_are_blank():
    rec = {"name": "Slough/SMI/2026/31", "other_fields": {
        "applicant_name": "See source", "agent_name": "See source"}}
    r = planit.normalise(rec)
    assert r["applicant"] == "" and r["agent"] == ""


def test_ireland_field_discovery(monkeypatch):
    monkeypatch.setattr(ireland, "get_json", fake_ireland)
    rows = ireland.fetch(30)
    assert len(rows) == 1
    r = rows[0]
    assert r["id"] == "IE:South Dublin County Council:SD26A/0100"
    assert r["received_date"] == "2026-01-01" and r["applicant"] == "CyrusOne Ireland Limited"


def test_ireland_missing_field_raises(monkeypatch):
    monkeypatch.setattr(ireland, "get_json", lambda *a, **k: {"fields": [{"name": "Foo"}]})
    with pytest.raises(RuntimeError, match="description field not found"):
        ireland.fetch(30)


def test_extract_parses_and_caches(monkeypatch):
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "x")
    claude, cache = FakeClaude(), {}
    rows = [planit.normalise(PLANIT_PAGE["records"][0])]
    extract.extract(rows, cache, client=claude)
    extract.extract([dict(rows[0])], cache, client=claude)
    assert rows[0]["mw_it"] == 90 and claude.calls == 1


def test_owner_chain_via_companies_house(monkeypatch, tmp_data):
    monkeypatch.setattr(owners, "get_json", fake_ch)
    monkeypatch.setattr(config, "COMPANIES_HOUSE_API_KEY", "x")
    row = planit.normalise(PLANIT_PAGE["records"][0])
    owners.resolve([row], owners.load_known(), {})
    assert row["owner_parent"] == "Vantage Data Centers"
    assert row["ownership_chain"].endswith("Vantage Data Centers EMEA S.a r.l.")
    assert row["owner_confidence"] == "high"


def test_known_name_in_ireland():
    row = {"country": "IE", "applicant": "CyrusOne Ireland Limited", "agent": "", "description": ""}
    owners.resolve([row], owners.load_known(), {})
    assert row["owner_parent"] == "CyrusOne"


def test_end_to_end_then_status_change(monkeypatch, tmp_data):
    monkeypatch.setattr(planit, "get_json", fake_planit)
    monkeypatch.setattr(ireland, "get_json", fake_ireland)
    monkeypatch.setattr(owners, "get_json", fake_ch)
    monkeypatch.setattr(config, "COMPANIES_HOUSE_API_KEY", "x")
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "x")
    claude = FakeClaude()
    monkeypatch.setattr(extract, "_client", lambda: claude)

    assert main.run(["uk", "ie"], 30) == []
    log = next((tmp_data / "changes").glob("*.md")).read_text()
    assert "2 new applications" in log  # server room filtered out by Claude
    assert "Vantage Data Centers" in log and "CyrusOne" in log

    PLANIT_PAGE["records"][0]["app_state"] = "Permitted"
    try:
        main.run(["uk"], 30)
    finally:
        PLANIT_PAGE["records"][0]["app_state"] = "Undecided"
    log = next((tmp_data / "changes").glob("*.md")).read_text()
    assert "status: Undecided -> Permitted" in log
    master = store.load_master()
    assert len(master) == 3


def test_one_failing_source_does_not_stop_others(monkeypatch, tmp_data):
    monkeypatch.setattr(planit, "get_json", fake_planit)
    def boom(*a, **k):
        raise ConnectionError("down")
    monkeypatch.setattr(ireland, "get_json", boom)
    assert main.run(["uk", "ie"], 30, do_extract=False) == ["ie"]
    assert len(store.load_master()) == 2


def test_merge_keeps_reviewed_fields():
    old = {"id": "UK:x", "status": "Undecided", "stage": "approved", "mw_it": "90",
           "extract_hash": "manual-review", "project": "P", "owner_parent": "Vantage"}
    fresh = [{"id": "UK:x", "status": "Undecided", "stage": "", "mw_it": "", "owner_parent": ""}]
    master, new_ids, changes = store.merge({"UK:x": dict(old)}, fresh, today="2026-10-10")
    row = master["UK:x"]
    assert row["stage"] == "approved" and row["mw_it"] == "90" and row["project"] == "P"
    assert row["owner_parent"] == "Vantage"
    assert new_ids == [] and changes == []  # a blank re-fetch is not a stage change


def test_merge_manual_rows_beat_new_extraction():
    old = {"id": "UK:x", "stage": "approved", "extract_hash": "manual-review"}
    fresh = [{"id": "UK:x", "stage": "full application", "extract_hash": "abc"}]
    master, _, changes = store.merge({"UK:x": dict(old)}, fresh, today="2026-10-10")
    assert master["UK:x"]["stage"] == "approved" and changes == []
