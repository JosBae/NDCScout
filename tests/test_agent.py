import json

import pytest
from fastapi.testclient import TestClient

from ndc_scout import agent, mock_meta
from ndc_scout.data import load
from ndc_scout.llm import MetaClient, estimate_cost


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("NDC_SCOUT_HOME", str(tmp_path))


@pytest.fixture(scope="module")
def ds():
    return load()


def client(mistake=False, **kw):
    tc = TestClient(mock_meta.create_app(mistake=mistake), base_url="http://mock")
    return MetaClient(api_key="mock", base_url="http://mock/v1", mode=kw.pop("mode", "mock"), http=tc, **kw)


def test_agent_verified_single_payer(ds):
    r = agent.run(ds, client(), drug="atorvastatin 40mg", quantity=30, payer_id="partd-awp")
    assert r.status == "verified"
    assert r.report["recommended_ndc"] == "99102-0441-90"
    assert r.usage["calls"] == 3
    names = [t["name"] for t in r.trace if t["type"] == "tool"]
    assert names == ["search_drugs", "list_payers", "rank_ndcs"]


def test_verifier_catches_wrong_answer_and_repair_fixes_it(ds):
    r = agent.run(ds, client(mistake=True), drug="atorvastatin", quantity=30, payer_id="partd-awp")
    vers = [t for t in r.trace if t["type"] == "verification"]
    assert [v["passed"] for v in vers] == [False, True]
    assert "best is 99102-0441-90" in vers[0]["errors"][0]
    assert r.status == "verified"


def test_no_repair_turn_leaves_unverified(ds):
    r = agent.run(ds, client(mistake=True), drug="atorvastatin", quantity=30, payer_id="partd-awp", repair_turns=0)
    assert r.status == "unverified"


def test_agent_payer_mix(ds):
    mix = {"medicaid-nadac": 0.3, "pbm-mac": 0.5, "partd-awp": 0.2}
    r = agent.run(ds, client(), drug="metformin 500", quantity=60, payer_mix=mix)
    assert r.status == "verified"
    assert any("99102-0566-01" in n for n in r.report["needs_human"])


def test_budget_guard_stops_run(ds):
    c = client(mode="live", max_budget_usd=0.000001)  # mock server, but billed as if live
    r = agent.run(ds, c, drug="atorvastatin", quantity=30, payer_id="pbm-mac")
    assert r.status == "budget_exceeded"
    assert r.usage["calls"] == 1  # first call allowed, second refused


def test_monthly_cap(ds, tmp_path):
    (tmp_path / "usage.jsonl").write_text(json.dumps({"ts": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
                                                     "mode": "live", "cost_usd": 5.0}) + "\n")
    r = agent.run(ds, client(mode="live", monthly_cap_usd=4.0), drug="atorvastatin", quantity=30, payer_id="pbm-mac")
    assert r.status == "budget_exceeded" and r.usage["calls"] == 0


def test_api_error_has_fix(ds):
    r = agent.run(ds, client(model="muse-nonexistent"), drug="atorvastatin", quantity=30, payer_id="pbm-mac")
    assert r.status == "api_error" and r.error["status"] == 404 and r.error["fix"]


def test_no_key(ds):
    r = agent.run(ds, MetaClient(api_key=None), drug="atorvastatin", quantity=30, payer_id="pbm-mac")
    assert r.status == "api_error" and r.error["kind"] == "no_key"


def test_usage_logged(ds, tmp_path):
    agent.run(ds, client(), drug="atorvastatin", quantity=30, payer_id="pbm-mac")
    lines = (tmp_path / "usage.jsonl").read_text().splitlines()
    assert len(lines) == 3 and all(json.loads(l)["mode"] == "mock" for l in lines)


def test_verify_rejects_fabricated_numbers(ds):
    good = agent.run(ds, client(), drug="atorvastatin", quantity=30, payer_id="medicaid-nadac").report
    bad = dict(good, expected_margin_per_fill=99.0)
    bad["ranking"] = [dict(good["ranking"][0], margin=42.0)] + good["ranking"][1:]
    v = agent.verify(ds, bad)
    assert not v["passed"] and len(v["errors"]) == 2
    assert agent.verify(ds, None)["passed"] is False
    blocked = dict(good, recommended_ndc="99103-0433-18")
    assert not agent.verify(ds, blocked)["passed"]


def test_parse_report_handles_fences():
    assert agent.parse_report('Here:\n```json\n{"a": 1}\n```') == {"a": 1}
    assert agent.parse_report('{"a": 2}') == {"a": 2}
    assert agent.parse_report("no json") is None


def test_cost_estimate():
    assert round(estimate_cost("muse-spark-1.3", 1_000_000, 1_000_000), 2) == 5.50
    assert estimate_cost("unknown-model", 1_000_000, 0) == 4.0  # priced at the most expensive known rate
