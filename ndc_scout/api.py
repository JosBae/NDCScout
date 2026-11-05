"""HTTP API + web UI (FastAPI).

  GET  /api/health          config, data source, whether a live key is configured
  GET  /api/drugs?q=        search drugs (empty q = list all)
  GET  /api/payers          payer plans
  GET  /api/rate-audit      cross-check one payer's MAC vs peer payers and NADAC (no model call, free)
  POST /api/rank            deterministic ranking (no model call, free)
  POST /api/ask             agent run: mode "live" (Meta Model API), "mock" (scripted stand-in), or "auto"
  GET  /api/usage           this app's logged model spend (estimates)
  GET  /                    web UI (web/index.html)

Run: `ndc-scout serve --port 8000`
"""
from __future__ import annotations

import json
import os
from collections import defaultdict
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field

from . import agent, data, engine, mock_meta
from .llm import MetaClient, home

from . import resource_root

WEB = resource_root() / "web"
app = FastAPI(title="NDC Scout", version="0.1.0")


def ds() -> data.Dataset:
    return data.cached(os.environ.get("NDC_SCOUT_DATA_DIR"))


class RankIn(BaseModel):
    drug_id: str
    quantity: float = Field(gt=0)
    payer_id: str | None = None
    payer_mix: dict[str, float] | None = None


class AskIn(BaseModel):
    drug: str
    quantity: float = Field(gt=0)
    payer_id: str | None = None
    payer_mix: dict[str, float] | None = None
    question: str | None = None
    mode: str = "auto"          # auto | live | mock
    mock_mistake: bool = False  # mock only: answer wrong first to demonstrate the verifier


def make_client(mode: str, mock_mistake: bool = False) -> MetaClient:
    """live -> real Meta Model API from env; mock -> in-process scripted stand-in over the same HTTP interface."""
    if mode == "auto":
        mode = "live" if os.environ.get("META_API_KEY") else "mock"
    if mode == "mock":
        tc = TestClient(mock_meta.create_app(mistake=mock_mistake), base_url="http://mock-meta")
        return MetaClient(api_key="mock", base_url="http://mock-meta/v1", model="muse-spark-1.3", mode="mock", http=tc)
    if mode == "live":
        return MetaClient.from_env()
    raise HTTPException(400, f"mode must be auto, live or mock, not {mode!r}")


@app.get("/api/health")
def health():
    d = ds()
    live = bool(os.environ.get("META_API_KEY"))
    c = MetaClient.from_env()
    return {"ok": True, "data_dir": d.source_dir, "synthetic_data": d.synthetic, "data_as_of": d.reference_date.isoformat(),
            "ndc_count": len(d.products), "payer_count": len(d.payers),
            "meta_api": {"key_configured": live, "base_url": c.base_url, "model": c.model,
                         "default_mode": "live" if live else "mock", "reasoning_effort": c.reasoning_effort,
                         "max_run_usd": c.max_budget_usd, "monthly_cap_usd": c.monthly_cap_usd}}


@app.get("/api/drugs")
def drugs(q: str = ""):
    d = ds()
    if q.strip():
        return {"drugs": d.search(q)}
    return {"drugs": [d.describe_drug(x) for x in sorted(d.drugs())]}


@app.get("/api/payers")
def payers():
    return {"payers": list(ds().payers.values())}


@app.get("/api/rate-audit")
def rate_audit(payer_id: str):
    try:
        return engine.rate_audit(ds(), payer_id)
    except KeyError as e:
        raise HTTPException(404, str(e).strip("'\""))


@app.post("/api/rank")
def rank(body: RankIn):
    try:
        if body.payer_mix:
            return engine.rank_mix(ds(), body.drug_id, body.payer_mix, body.quantity)
        if not body.payer_id:
            raise HTTPException(422, "payer_id or payer_mix is required")
        return engine.rank(ds(), body.drug_id, body.payer_id, body.quantity)
    except (KeyError, ValueError) as e:
        raise HTTPException(404 if isinstance(e, KeyError) else 422, str(e).strip("'\""))


@app.post("/api/ask")
def ask(body: AskIn):
    if not (body.payer_id or body.payer_mix):
        raise HTTPException(422, "payer_id or payer_mix is required")
    client = make_client(body.mode, body.mock_mistake)
    result = agent.run(ds(), client, drug=body.drug, quantity=body.quantity, payer_id=body.payer_id,
                       payer_mix=body.payer_mix, question=body.question)
    return result.as_dict()


@app.get("/api/usage")
def usage():
    log = home() / "usage.jsonl"
    months: dict = defaultdict(lambda: defaultdict(lambda: {"calls": 0, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}))
    if log.exists():
        for line in log.read_text().splitlines():
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            m = months[str(r.get("ts", ""))[:7]][r.get("mode", "?")]
            m["calls"] += 1
            m["input_tokens"] += r.get("input_tokens", 0)
            m["output_tokens"] += r.get("output_tokens", 0)
            m["cost_usd"] = round(m["cost_usd"] + float(r.get("cost_usd") or 0), 6)
    return {"log": str(log), "by_month": months,
            "note": "Estimates from a pricing snapshot, for calls made by this app only. Meta's Billing page is the source of truth."}


@app.get("/")
def index():
    return FileResponse(WEB / "index.html")
