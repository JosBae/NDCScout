"""`ndc-scout` command line.

  ndc-scout rank   --drug atorvastatin --payer medicaid-nadac --qty 30
  ndc-scout rank   --drug metformin --mix medicaid-nadac=0.3,pbm-mac=0.7 --qty 60
  ndc-scout ask    --drug "atorvastatin 40" --payer partd-awp --qty 30 [--mock [--mistake]]
  ndc-scout serve  [--port 8000]
  ndc-scout mcp                       (MCP server on stdio)
  ndc-scout mock-api [--port 8787]    (stand-in Meta API on HTTP)
  ndc-scout check-api                 (diagnose your real Meta key/base URL/model)
  ndc-scout usage
  ndc-scout import fda|nadac|catalog FILE --out DIR [...]
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from . import agent, data, engine, importers
from .llm import MetaAPIError, MetaClient


def _mix(s: str | None) -> dict | None:
    if not s:
        return None
    out = {}
    for part in s.split(","):
        k, _, v = part.partition("=")
        out[k.strip()] = float(v)
    return out


def _resolve_drug(ds, text: str) -> str:
    if text in ds.drugs():
        return text
    hits = ds.search(text)
    if not hits:
        sys.exit(f"no drug matches {text!r}; try one of: {', '.join(sorted(ds.drugs()))}")
    return hits[0]["drug_id"]


def _table(result: dict, mix: bool) -> str:
    key = "expected_margin" if mix else "margin"
    lines = [f"{result['drug']['generic_name']} {result['drug']['strength']} · qty {result['quantity']:g} · "
             + (f"mix {result['payer_mix']}" if mix else result["payer"]["name"]),
             f"{'#':>2}  {'NDC':<14} {'labeler':<30} {'package':<9} {'margin':>8}  flags"]
    for i, c in enumerate(result["candidates"], 1):
        flags = c.get("flags") or []
        codes = [f["code"] if isinstance(f, dict) else f for f in flags] or list((c.get("blocked_by") or {}).items())
        m = "—" if c[key] is None else f"${c[key]:.2f}"
        lines.append(f"{i:>2}  {c['ndc']:<14} {c['labeler'][:30]:<30} {c['package']:<9} {m:>8}  {', '.join(map(str, codes))}")
    lines.append(f"best: {result['best_ndc']}" + ("   [SYNTHETIC SAMPLE DATA]" if result.get("synthetic_data") else ""))
    return "\n".join(lines)


def cmd_rank(a):
    ds = data.load(a.data)
    drug_id = _resolve_drug(ds, a.drug)
    mix = _mix(a.mix)
    r = engine.rank_mix(ds, drug_id, mix, a.qty) if mix else engine.rank(ds, drug_id, a.payer, a.qty)
    print(json.dumps(r, indent=2) if a.json else _table(r, bool(mix)))


def cmd_ask(a):
    ds = data.load(a.data)
    if a.mock:
        from fastapi.testclient import TestClient
        from . import mock_meta
        client = MetaClient(api_key="mock", base_url="http://mock-meta/v1", mode="mock",
                            http=TestClient(mock_meta.create_app(mistake=a.mistake), base_url="http://mock-meta"))
    else:
        client = MetaClient.from_env(model=a.model)
    res = agent.run(ds, client, drug=a.drug, quantity=a.qty, payer_id=a.payer, payer_mix=_mix(a.mix), question=a.question)
    if a.json:
        print(json.dumps(res.as_dict(), indent=2))
        return
    print(f"mode={res.mode} model={res.model} status={res.status} turns={sum(1 for t in res.trace if t['type']=='model')} "
          f"tokens={res.usage.get('input_tokens')}/{res.usage.get('output_tokens')} est_cost=${res.usage.get('cost_usd', 0):.4f} "
          f"time={res.duration_s}s")
    for t in res.trace:
        if t["type"] == "model":
            calls = ", ".join(f"{c['name']}({c['arguments']})" for c in t["tool_calls"])
            print(f"  [turn {t['turn']}] model -> " + (calls or "final answer"))
        elif t["type"] == "verification":
            print(f"  [turn {t['turn']}] verifier: {'PASS' if t['passed'] else 'FAIL'} " + "; ".join(t["errors"]))
        elif t["type"] in ("api_error", "stopped"):
            print(f"  [turn {t['turn']}] {t['type']}: {t.get('message') or t.get('reason')}\n     fix: {t.get('fix', '')}")
    if res.report:
        r = res.report
        print(f"\nRECOMMEND {r.get('recommended_ndc')}  (${r.get('expected_margin_per_fill')} per fill)\n{r.get('summary')}")
        for n in r.get("needs_human") or []:
            print(f"  ! {n}")
    sys.exit(0 if res.status == "verified" else 1)


def cmd_check_api(a):
    """Probe the real API step by step so a failure points at one fix."""
    import httpx
    c = MetaClient.from_env(model=a.model)
    print(f"base_url={c.base_url} model={c.model} key={'set (' + str(len(c.api_key)) + ' chars)' if c.api_key else 'NOT SET'}")
    if not c.api_key:
        sys.exit("1) Set META_API_KEY (dev.meta.ai → API keys; add a payment method first).")
    h = {"Authorization": f"Bearer {c.api_key}"}
    try:
        r = httpx.get(f"{c.base_url}/models", headers=h, timeout=30)
        print(f"GET /models -> {r.status_code}")
        if r.status_code == 200:
            ids = [m.get("id") for m in r.json().get("data", [])]
            print("  models:", ", ".join(ids[:20]))
            if c.model not in ids:
                print(f"  ! {c.model} not listed; set META_MODEL to one of the above")
        elif r.status_code == 401:
            print("  ! 401: key invalid, or this API wants a different auth header than 'Authorization: Bearer'.")
    except httpx.HTTPError as e:
        sys.exit(f"cannot reach {c.base_url}: {e}  → check META_API_BASE / network")
    try:
        d = c.chat([{"role": "user", "content": "Reply with the word ok."}])
        print("chat -> ok:", (d["choices"][0]["message"].get("content") or "")[:60].replace("\n", " "), "| usage:", d.get("usage"))
        tool = {"type": "function", "function": {"name": "ping", "description": "Return pong.",
                                                 "parameters": {"type": "object", "properties": {}, "additionalProperties": False}}}
        d = c.chat([{"role": "user", "content": "Call the ping tool."}], [tool])
        calls = d["choices"][0]["message"].get("tool_calls")
        print("tool calling ->", "ok" if calls else f"model answered without a tool call: {d['choices'][0]['message']}")
        print(f"estimated cost of this check: ${c.usage.cost_usd:.5f}")
    except MetaAPIError as e:
        sys.exit(f"chat failed [{e.kind}] {e}\n  fix: {e.fix}")


def cmd_usage(a):
    from .api import usage
    print(json.dumps(usage(), indent=2))


def cmd_import(a):
    fn = {"fda": lambda: importers.import_fda_ndc(a.file, a.out, a.generic),
          "nadac": lambda: importers.import_nadac(a.file, a.out),
          "catalog": lambda: importers.import_catalog(a.file, a.out, a.ndc_col, a.price_col, a.size_col, a.wholesaler)}[a.kind]
    print(json.dumps(fn()))


def main(argv=None):
    p = argparse.ArgumentParser(prog="ndc-scout")
    p.add_argument("--data", help="data directory (default: sample_data or $NDC_SCOUT_DATA_DIR)")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("rank")
    r.add_argument("--drug", required=True); r.add_argument("--qty", type=float, required=True)
    g = r.add_mutually_exclusive_group(required=True); g.add_argument("--payer"); g.add_argument("--mix")
    r.add_argument("--json", action="store_true"); r.set_defaults(fn=cmd_rank)

    k = sub.add_parser("ask")
    k.add_argument("--drug", required=True); k.add_argument("--qty", type=float, required=True)
    g = k.add_mutually_exclusive_group(required=True); g.add_argument("--payer"); g.add_argument("--mix")
    k.add_argument("--question"); k.add_argument("--model")
    k.add_argument("--mock", action="store_true", help="use the scripted stand-in instead of Meta Model API")
    k.add_argument("--mistake", action="store_true", help="mock answers wrong first (shows the verifier)")
    k.add_argument("--json", action="store_true"); k.set_defaults(fn=cmd_ask)

    s = sub.add_parser("serve"); s.add_argument("--port", type=int, default=8000); s.add_argument("--host", default="127.0.0.1")
    s.set_defaults(fn=lambda a: __import__("uvicorn").run("ndc_scout.api:app", host=a.host, port=a.port))
    m = sub.add_parser("mcp"); m.set_defaults(fn=lambda a: __import__("ndc_scout.mcp_server", fromlist=["main"]).main())
    mk = sub.add_parser("mock-api"); mk.add_argument("--port", type=int, default=8787); mk.add_argument("--mistake", action="store_true")
    mk.set_defaults(fn=lambda a: __import__("uvicorn").run(__import__("ndc_scout.mock_meta", fromlist=["x"]).create_app(a.mistake), port=a.port))
    c = sub.add_parser("check-api"); c.add_argument("--model"); c.set_defaults(fn=cmd_check_api)
    u = sub.add_parser("usage"); u.set_defaults(fn=cmd_usage)
    i = sub.add_parser("import"); i.add_argument("kind", choices=["fda", "nadac", "catalog"]); i.add_argument("file")
    i.add_argument("--out", required=True); i.add_argument("--generic", help="fda: only products whose generic name contains this")
    i.add_argument("--ndc-col", default="NDC"); i.add_argument("--price-col", default="Price"); i.add_argument("--size-col", default="Pack Size")
    i.add_argument("--wholesaler", default=""); i.set_defaults(fn=cmd_import)

    a = p.parse_args(argv)
    if a.data:
        os.environ["NDC_SCOUT_DATA_DIR"] = a.data
    a.fn(a)


if __name__ == "__main__":
    main()
