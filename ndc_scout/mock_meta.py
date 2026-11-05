"""A MOCK of Meta Model API's Chat Completions endpoint, for offline demos and tests.

It is NOT a language model. It plays a fixed script, using the same wire
format the real API uses, so everything except the model itself runs for real:
the HTTP client, auth header, tool schemas, tool-call loop, tool execution,
the verifier and the repair turn.

  turn 1  -> calls search_drugs + list_payers (two parallel tool calls)
  turn 2  -> calls rank_ndcs / rank_ndcs_for_payer_mix with the top match
  turn 3  -> answers with JSON built from the rank result
             (with mistake=True it first answers WRONG, picking the runner-up,
              so you can watch the verifier catch it and the repair turn fix it)

Run standalone: `ndc-scout mock-api --port 8787`, then
`META_API_BASE=http://127.0.0.1:8787/v1 META_API_KEY=mock ndc-scout ask ...`.
"""
from __future__ import annotations

import json
import re
import time
import uuid

from fastapi import FastAPI, Header, HTTPException, Request

MOCK_MODELS = ["muse-spark-1.3", "muse-spark-1.1", "muse-spark-1.3-contributor"]


def _tokens(obj) -> int:
    return max(1, len(json.dumps(obj)) // 4)


def _call(name: str, args: dict) -> dict:
    return {"id": f"call_{uuid.uuid4().hex[:12]}", "type": "function",
            "function": {"name": name, "arguments": json.dumps(args)}}


def _request_block(messages: list[dict]) -> dict:
    for m in messages:
        if m.get("role") == "user":
            hit = re.search(r"```json\s*(\{.*?\})\s*```", m.get("content") or "", re.S)
            if hit:
                return json.loads(hit.group(1))
    return {}


def _final_from_rank(req: dict, rank: dict, wrong: bool) -> dict:
    mix = req.get("payer_mix")
    key = "expected_margin" if mix else "margin"
    cands = rank["candidates"]
    computable = [c for c in cands if c["computable"]]
    pick = computable[1] if (wrong and len(computable) > 1) else (computable[0] if computable else None)
    needs = []
    for c in cands:
        if not c["computable"]:
            reasons = c.get("blocked_by") or [f["message"] for f in c.get("flags", []) if f["severity"] == "blocking"]
            needs.append(f"{c['ndc']}: cannot be priced: {json.dumps(reasons) if isinstance(reasons, dict) else '; '.join(reasons)}")
        else:
            for f in c.get("flags", []):
                if isinstance(f, dict) and f["severity"] == "warning":
                    needs.append(f"{c['ndc']}: {f['message']}")
    if rank.get("synthetic_data"):
        needs.append("All prices are SYNTHETIC sample data; load real NADAC, catalog and payer files before acting.")
    best = computable[0] if computable else None
    summary = (f"Stock {pick['ndc']} ({pick['labeler']}, {pick['package']}): about ${pick[key]:.2f} margin per fill of "
               f"{req['quantity']:g} units" if pick else "No NDC could be priced for this request")
    if best and len(computable) > 1:
        summary += f", ${best[key] - computable[-1][key]:.2f} more than the worst option."
    return {
        "drug_id": rank["drug"]["drug_id"],
        "payer_id": None if mix else req.get("payer_id"),
        "payer_mix": mix,
        "quantity": req["quantity"],
        "recommended_ndc": pick["ndc"] if pick else None,
        "expected_margin_per_fill": pick[key] if pick else None,
        "ranking": [{"ndc": c["ndc"], "margin": c[key], "confidence": c.get("confidence", "n/a"),
                     "reason": "computable" if c["computable"] else "blocked"} for c in cands],
        "needs_human": needs,
        "summary": summary + " [MOCK MODEL OUTPUT]",
    }


def create_app(mistake: bool = False) -> FastAPI:
    app = FastAPI(title="MOCK Meta Model API (not a model)")

    @app.get("/v1/models")
    def models(authorization: str | None = Header(default=None)):
        if not authorization:
            raise HTTPException(401, {"error": {"message": "missing Authorization header (mock)"}})
        return {"object": "list", "data": [{"id": m, "object": "model", "owned_by": "mock"} for m in MOCK_MODELS]}

    @app.post("/v1/chat/completions")
    async def chat(request: Request, authorization: str | None = Header(default=None)):
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(401, {"error": {"message": "missing bearer token (mock)"}})
        body = await request.json()
        messages = body.get("messages") or []
        model = body.get("model", "muse-spark-1.3")
        if model not in MOCK_MODELS:
            raise HTTPException(404, {"error": {"message": f"model '{model}' does not exist or you lack access (mock)"}})
        req = _request_block(messages)
        tool_msgs = [m for m in messages if m.get("role") == "tool"]
        repaired = any(m.get("role") == "user" and "Verification failed" in (m.get("content") or "") for m in messages)
        last_rank = next((json.loads(m["content"]) for m in reversed(tool_msgs)
                          if m.get("name") in ("rank_ndcs", "rank_ndcs_for_payer_mix")), None)
        search = next((json.loads(m["content"]) for m in tool_msgs if m.get("name") == "search_drugs"), None)

        if not req:
            message = {"role": "assistant", "content": "MOCK: no ```json request block found; the mock only handles NDC Scout requests."}
        elif search is None:
            message = {"role": "assistant", "content": "", "tool_calls": [
                _call("search_drugs", {"query": req["drug"]}), _call("list_payers", {})]}
        elif last_rank is None:
            matches = search.get("matches") or []
            if not matches:
                message = {"role": "assistant", "content": json.dumps({
                    "drug_id": None, "quantity": req["quantity"], "recommended_ndc": None, "ranking": [],
                    "needs_human": [f"No drug matched {req['drug']!r}"], "summary": "No match. [MOCK]"})}
            elif req.get("payer_mix"):
                message = {"role": "assistant", "content": "", "tool_calls": [_call("rank_ndcs_for_payer_mix", {
                    "drug_id": matches[0]["drug_id"], "payer_mix": req["payer_mix"], "quantity": req["quantity"]})]}
            else:
                message = {"role": "assistant", "content": "", "tool_calls": [_call("rank_ndcs", {
                    "drug_id": matches[0]["drug_id"], "payer_id": req["payer_id"], "quantity": req["quantity"]})]}
        else:
            final = _final_from_rank(req, last_rank, wrong=mistake and not repaired)
            message = {"role": "assistant", "content": json.dumps(final)}

        prompt_tokens = _tokens(messages) + _tokens(body.get("tools") or [])
        return {
            "id": f"mockcmpl-{uuid.uuid4().hex[:16]}", "object": "chat.completion", "created": int(time.time()),
            "model": model, "x_mock": True,
            "choices": [{"index": 0, "message": message,
                         "finish_reason": "tool_calls" if message.get("tool_calls") else "stop"}],
            "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": _tokens(message),
                      "total_tokens": prompt_tokens + _tokens(message)},
        }

    return app
