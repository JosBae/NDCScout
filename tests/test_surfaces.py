"""API, MCP, importers, and a true end-to-end run over a network socket."""
import asyncio
import csv
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    monkeypatch.setenv("NDC_SCOUT_HOME", str(tmp_path))
    monkeypatch.delenv("META_API_KEY", raising=False)


# ------------------------------------------------------------------- HTTP API
def test_api_endpoints():
    from ndc_scout.api import app
    c = TestClient(app)
    h = c.get("/api/health").json()
    assert h["synthetic_data"] is True and h["meta_api"]["default_mode"] == "mock"
    assert len(c.get("/api/drugs").json()["drugs"]) == 6
    assert c.get("/api/drugs", params={"q": "lisinopril"}).json()["drugs"][0]["drug_id"] == "lisinopril-10mg-tab"
    r = c.post("/api/rank", json={"drug_id": "atorvastatin-40mg-tab", "payer_id": "pbm-mac", "quantity": 30}).json()
    assert r["best_ndc"] == "99103-0711-05"
    assert c.post("/api/rank", json={"drug_id": "nope", "payer_id": "pbm-mac", "quantity": 30}).status_code == 404
    assert c.post("/api/rank", json={"drug_id": "atorvastatin-40mg-tab", "quantity": 30}).status_code == 422
    a = c.post("/api/ask", json={"drug": "sertraline 50", "payer_id": "pbm-awp-lesser", "quantity": 30, "mode": "auto"}).json()
    assert a["status"] == "verified" and a["mode"] == "mock"
    live = c.post("/api/ask", json={"drug": "sertraline", "payer_id": "pbm-mac", "quantity": 30, "mode": "live"}).json()
    assert live["status"] == "api_error" and live["error"]["kind"] == "no_key"
    assert "<title>NDC Scout</title>" in c.get("/").text
    assert c.get("/api/usage").json()["by_month"]


# ------------------------------------------------------------------- MCP stdio
def test_mcp_stdio_roundtrip():
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    async def go():
        p = StdioServerParameters(command=sys.executable, args=["-m", "ndc_scout.mcp_server"], cwd=str(ROOT))
        async with stdio_client(p) as streams:
            async with ClientSession(*streams[:2]) as s:
                await s.initialize()
                names = sorted(t.name for t in (await s.list_tools()).tools)
                r = await s.call_tool("rank_ndcs_for_payer_mix", {"drug_id": "atorvastatin-40mg-tab",
                                                                  "payer_mix": {"medicaid-nadac": 1}, "quantity": 30})
                res = await s.read_resource("skill://ndc-reimbursement")
                return names, json.loads(r.content[0].text), res.contents[0].text
    names, body, skill = asyncio.run(asyncio.wait_for(go(), 60))
    assert names == ["list_payers", "price_ndc", "rank_ndcs", "rank_ndcs_for_payer_mix", "search_drugs"]
    assert body["best_ndc"] == "99103-0711-05"
    assert "name: ndc-reimbursement" in skill


# ------------------------------------------------------------------- importers
def test_import_fda_and_nadac_and_catalog(tmp_path):
    from ndc_scout import importers
    from ndc_scout.data import load
    fda = {"results": [{
        "product_ndc": "0093-0058", "generic_name": "ATORVASTATIN CALCIUM", "labeler_name": "Example Labs",
        "dosage_form": "TABLET, FILM COATED", "active_ingredients": [{"name": "ATORVASTATIN CALCIUM", "strength": "40 mg/1"}],
        "packaging": [{"package_ndc": "0093-0058-01", "description": "90 TABLET, FILM COATED in 1 BOTTLE (0093-0058-01)"},
                      {"package_ndc": "0093-0058-19", "description": "10 BLISTER PACK in 1 CARTON (0093-0058-19)  > 10 TABLET, FILM COATED in 1 BLISTER PACK"},
                      {"package_ndc": "bad", "description": "x"}]},
        {"product_ndc": "12345-678", "generic_name": "ALBUTEROL SULFATE", "labeler_name": "Inhaler Co",
         "dosage_form": "AEROSOL, METERED", "active_ingredients": [{"name": "ALBUTEROL SULFATE", "strength": "90 ug/1"}],
         "packaging": [{"package_ndc": "12345-678-90", "description": "1 INHALER in 1 CARTON (12345-678-90)  > 200 AEROSOL, METERED in 1 INHALER > 8.5 g in 1 INHALER"}]}]}
    (tmp_path / "fda.json").write_text(json.dumps(fda))
    out = tmp_path / "data"
    assert importers.import_fda_ndc(tmp_path / "fda.json", out) == {"products_written": 3, "packages_skipped": 1}
    rows = list(csv.DictReader(open(out / "products.csv")))
    assert [(r["ndc11"], r["package_size"], r["unit"]) for r in rows] == [
        ("00093005801", "90", "EA"), ("00093005819", "100", "EA"), ("12345067890", "8.5", "GM")]

    (tmp_path / "nadac.csv").write_text(
        "NDC Description,NDC,NADAC Per Unit,Effective Date,Pricing Unit,Pharmacy Type Indicator,OTC,As of Date\n"
        "ATORVASTATIN 40 MG TABLET,93005801,0.04500,09/17/2026,EA,C/I,N,10/21/2026\n"
        "ATORVASTATIN 40 MG TABLET,00093005801,0.04120,10/15/2026,EA,C/I,N,10/21/2026\n")
    assert importers.import_nadac(tmp_path / "nadac.csv", out) == {"nadac_rows": 1}
    nad = list(csv.DictReader(open(out / "nadac.csv")))
    assert nad[0]["nadac_per_unit"] == "0.04120" and nad[0]["effective_date"] == "2026-10-15"

    (tmp_path / "w.csv").write_text("Item NDC,Cost,Size\n0093-0058-01,\"$3.10\",90\nnotanndc,1,1\n")
    assert importers.import_catalog(tmp_path / "w.csv", out, "Item NDC", "Cost", "Size") == {"catalog_rows": 1, "rows_skipped": 1}
    (out / "payers.json").write_text(json.dumps({"payers": [{"id": "m", "name": "M", "formula": "nadac_plus_fee", "dispensing_fee": 10}]}))
    ds = load(out)
    from ndc_scout import engine
    r = engine.rank(ds, rows[0]["drug_id"], "m", 30)
    assert r["best_ndc"] == "00093-0058-01" and r["synthetic_data"] is False


# ------------------------------------------- end to end over a real socket
def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_end_to_end_over_network(tmp_path):
    """Mock Meta API as a real HTTP server; CLI in LIVE mode pointed at it via META_API_BASE.
    This is the exact code path a real key uses. Only the server on the other end differs."""
    port = _free_port()
    srv = subprocess.Popen([sys.executable, "-m", "ndc_scout.cli", "mock-api", "--port", str(port)], cwd=ROOT,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(100):
            try:
                httpx.get(f"http://127.0.0.1:{port}/v1/models", headers={"Authorization": "Bearer x"}, timeout=1)
                break
            except httpx.HTTPError:
                time.sleep(0.1)
        env = dict(os.environ, META_API_BASE=f"http://127.0.0.1:{port}/v1", META_API_KEY="test-key", NDC_SCOUT_HOME=str(tmp_path))
        out = subprocess.run([sys.executable, "-m", "ndc_scout.cli", "ask", "--drug", "amoxicillin 500", "--payer", "pbm-mac",
                              "--qty", "30", "--json"], cwd=ROOT, env=env, capture_output=True, text=True, timeout=60)
        assert out.returncode == 0, out.stderr
        res = json.loads(out.stdout)
        assert res["mode"] == "live" and res["status"] == "verified"
        # 500-count bottle: $27.75/500 = $0.0555/unit beats $6.10/100 = $0.061/unit under a per-drug MAC
        assert res["report"]["recommended_ndc"] == "99101-0222-05"
        assert any("99103-0233-01" in n for n in res["report"]["needs_human"])  # not in catalog -> flagged
        log = [json.loads(l) for l in (tmp_path / "usage.jsonl").read_text().splitlines()]
        assert len(log) == 3 and all(r["host"] == f"127.0.0.1:{port}" and r["cost_usd"] > 0 for r in log)
    finally:
        srv.terminate()
        srv.wait(10)
