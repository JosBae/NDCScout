"""The NDC Scout agent: a tool-calling loop on Meta Model API plus a verifier.

Flow (one run):
  1. System prompt = skills/ndc-reimbursement/SKILL.md + its references
     (the same files a coding agent like Muse Code/Claude Code reads).
  2. The model calls tools (search_drugs, rank_ndcs, ...) until it can answer.
  3. It must answer with one JSON object (schema in the skill).
  4. verify() recomputes every number with engine.py. If anything is off,
     the agent gets ONE repair turn with the exact discrepancies.
  5. The result carries the report, the verification, a full trace and cost.

Guards: max_turns, per-run USD budget, local monthly cap (llm.MetaClient).
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import engine
from .data import Dataset
from .llm import BudgetExceeded, MetaAPIError, MetaClient
from .ndc import NDCError, normalize
from .tools import build_tools, call_tool

from . import resource_root

SKILL_DIR = resource_root() / "skills" / "ndc-reimbursement"
MARGIN_TOLERANCE = 0.011  # dollars
INFO_FLAGS = {"slow_mover", "mac_capped"}  # informational; don't require a human


def system_prompt() -> str:
    parts = [(SKILL_DIR / "SKILL.md").read_text()]
    for ref in sorted((SKILL_DIR / "references").glob("*.md")):
        parts.append(f"\n\n<reference file=\"{ref.name}\">\n{ref.read_text()}\n</reference>")
    parts.append(
        "\n\nYou are running inside the NDC Scout service with the tools listed. "
        "Never invent prices or margins: every number you report must come from a tool result. "
        "When done, reply with ONLY the final JSON object described in the skill (no prose, no code fence)."
    )
    return "".join(parts)


def build_request(drug: str, quantity: float, payer_id: str | None = None,
                  payer_mix: dict | None = None, question: str | None = None) -> str:
    req = {"drug": drug, "quantity": quantity}
    if payer_mix:
        req["payer_mix"] = payer_mix
    else:
        req["payer_id"] = payer_id
    text = question or "Which NDC should the pharmacy stock and dispense to get the best reimbursement margin?"
    return f"{text}\n\nRequest:\n```json\n{json.dumps(req)}\n```"


# ------------------------------------------------------------------ verifier
def parse_report(text: str | None) -> dict | None:
    if not text:
        return None
    t = text.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*\})\s*```", t, re.S)
    if fence:
        t = fence.group(1)
    else:
        start, end = t.find("{"), t.rfind("}")
        if start == -1 or end == -1:
            return None
        t = t[start:end + 1]
    try:
        obj = json.loads(t)
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None


def _same_ndc(a, b) -> bool:
    try:
        return normalize(str(a)) == normalize(str(b))
    except NDCError:
        return False


def verify(ds: Dataset, report: dict | None, drug_id: str | None = None) -> dict:
    """Recompute the answer with the engine. errors = wrong; warnings = incomplete."""
    errors: list[str] = []
    warnings: list[str] = []
    if report is None:
        return {"passed": False, "errors": ["Final answer was not a JSON object."], "warnings": []}
    drug_id = report.get("drug_id") or drug_id
    qty = report.get("quantity")
    mix, payer_id = report.get("payer_mix"), report.get("payer_id")
    for key in ("drug_id", "quantity", "ranking", "summary"):
        if report.get(key) in (None, "", []):
            errors.append(f"Missing field {key!r}.")
    if "recommended_ndc" not in report:  # may be null when nothing is computable
        errors.append("Missing field 'recommended_ndc'.")
    if not (mix or payer_id):
        errors.append("Missing payer_id or payer_mix.")
    if errors:
        return {"passed": False, "errors": errors, "warnings": warnings}
    try:
        truth = engine.rank_mix(ds, drug_id, mix, float(qty)) if mix else engine.rank(ds, drug_id, payer_id, float(qty))
    except (KeyError, ValueError) as e:
        return {"passed": False, "errors": [f"Could not recompute: {e}"], "warnings": []}

    margin_key = "expected_margin" if mix else "margin"
    by_ndc = {c["ndc11"]: c for c in truth["candidates"]}
    best = truth["best_ndc"]
    best_margin = next((c[margin_key] for c in truth["candidates"] if c["computable"]), None)

    rec = report.get("recommended_ndc")
    if best is None:
        if rec:
            errors.append(f"No NDC is computable for this request, but {rec} was recommended.")
    elif not _same_ndc(rec, best):
        rec_row = None
        try:
            rec_row = by_ndc.get(normalize(str(rec)))
        except NDCError:
            pass
        if rec_row is None:
            errors.append(f"Recommended NDC {rec!r} is not a candidate for {drug_id}.")
        elif not rec_row["computable"]:
            errors.append(f"Recommended NDC {rec} cannot be priced (blocking data issue) and must not be recommended.")
        else:
            errors.append(f"Recommended {rec} (margin {rec_row[margin_key]}) but the best is {best} (margin {best_margin}).")
    if best_margin is not None:
        try:
            if abs(float(report.get("expected_margin_per_fill")) - best_margin) > MARGIN_TOLERANCE:
                errors.append(f"expected_margin_per_fill {report.get('expected_margin_per_fill')} != engine {best_margin}.")
        except (TypeError, ValueError):
            errors.append("expected_margin_per_fill is missing or not a number.")

    seen = set()
    for row in report.get("ranking") or []:
        try:
            n = normalize(str(row.get("ndc")))
        except (NDCError, AttributeError):
            errors.append(f"Ranking row has an invalid NDC: {row!r}.")
            continue
        seen.add(n)
        t = by_ndc.get(n)
        if t is None:
            errors.append(f"Ranking lists {row.get('ndc')} which is not a candidate.")
            continue
        claimed = row.get("margin")
        if claimed is not None and t[margin_key] is not None:
            try:
                if abs(float(claimed) - t[margin_key]) > MARGIN_TOLERANCE:
                    errors.append(f"{t['ndc']}: reported margin {claimed} != engine {t[margin_key]}.")
            except (TypeError, ValueError):
                errors.append(f"{t['ndc']}: margin is not a number.")
        if claimed is not None and t[margin_key] is None:
            errors.append(f"{t['ndc']}: reported a margin but it cannot be computed (blocking data issue).")
    missing = [c["ndc"] for c in truth["candidates"] if c["ndc11"] not in seen]
    if missing:
        warnings.append(f"Ranking omits candidates: {', '.join(missing)}.")

    # Anything blocking or warning-level should surface for a human.
    flagged = []
    for c in truth["candidates"]:
        # single-payer rows carry flag dicts; payer-mix rows carry flag codes
        codes = [f["code"] if isinstance(f, dict) else f for f in (c.get("flags") or [])]
        codes = [x for x in codes if x not in INFO_FLAGS]
        if not c["computable"] or codes:
            flagged.append(c["ndc"])
    if flagged and not report.get("needs_human"):
        warnings.append(f"Data issues on {', '.join(flagged)} but needs_human is empty.")

    return {"passed": not errors, "errors": errors, "warnings": warnings,
            "engine_best_ndc": best, "engine_best_margin": best_margin}


# --------------------------------------------------------------------- agent
@dataclass
class AgentResult:
    status: str                    # verified | unverified | budget_exceeded | max_turns | api_error
    report: dict | None
    verification: dict | None
    trace: list[dict] = field(default_factory=list)
    usage: dict = field(default_factory=dict)
    model: str = ""
    mode: str = ""
    duration_s: float = 0.0
    error: dict | None = None

    def as_dict(self) -> dict:
        return self.__dict__.copy()


def run(ds: Dataset, client: MetaClient, *, drug: str, quantity: float, payer_id: str | None = None,
        payer_mix: dict | None = None, question: str | None = None, max_turns: int = 8,
        repair_turns: int = 1) -> AgentResult:
    tools = build_tools(ds)
    schemas = [t.schema() for t in tools.values()]
    messages: list[dict] = [{"role": "system", "content": system_prompt()},
                            {"role": "user", "content": build_request(drug, quantity, payer_id, payer_mix, question)}]
    trace: list[dict] = []
    t0 = time.monotonic()
    report = verification = None
    status = "max_turns"
    error = None
    repairs_left = repair_turns
    drug_hint = None

    def finish(st):
        return AgentResult(st, report, verification, trace, client.usage.as_dict(), client.model, client.mode,
                           round(time.monotonic() - t0, 2), error)

    for turn in range(1, max_turns + 1):
        try:
            data = client.chat(messages, schemas)
        except BudgetExceeded as e:
            error = {"kind": "budget", "message": str(e)}
            trace.append({"turn": turn, "type": "stopped", "reason": str(e)})
            return finish("budget_exceeded")
        except MetaAPIError as e:
            error = e.as_dict()
            trace.append({"turn": turn, "type": "api_error", **error})
            return finish("api_error")
        msg = (data.get("choices") or [{}])[0].get("message") or {}
        tool_calls = msg.get("tool_calls") or []
        assistant = {"role": "assistant", "content": msg.get("content") or ""}
        if tool_calls:
            assistant["tool_calls"] = tool_calls
        messages.append(assistant)
        u = data.get("usage") or {}
        trace.append({"turn": turn, "type": "model", "text": (msg.get("content") or "")[:2000],
                      "tool_calls": [{"name": c["function"]["name"], "arguments": c["function"].get("arguments")} for c in tool_calls],
                      "tokens": {"in": u.get("prompt_tokens") or u.get("input_tokens"), "out": u.get("completion_tokens") or u.get("output_tokens")}})
        if tool_calls:
            for call in tool_calls:
                name = call["function"]["name"]
                result = call_tool(tools, name, call["function"].get("arguments"))
                if name in ("rank_ndcs", "rank_ndcs_for_payer_mix") and isinstance(result, dict) and "drug" in result:
                    drug_hint = result["drug"]["drug_id"]
                trace.append({"turn": turn, "type": "tool", "name": name, "arguments": call["function"].get("arguments"),
                              "result_preview": _preview(result)})
                messages.append({"role": "tool", "tool_call_id": call.get("id"), "name": name,
                                 "content": json.dumps(result)})
            continue

        report = parse_report(msg.get("content"))
        verification = verify(ds, report, drug_hint)
        trace.append({"turn": turn, "type": "verification", **verification})
        if verification["passed"]:
            return finish("verified")
        if repairs_left <= 0:
            return finish("unverified")
        repairs_left -= 1
        messages.append({"role": "user", "content":
                         "Verification failed. The engine recomputed your answer and found:\n- "
                         + "\n- ".join(verification["errors"])
                         + "\nCall tools again if needed, then reply with the corrected JSON object only."})
    return finish(status)


def _preview(result) -> str:
    s = json.dumps(result)
    return s if len(s) <= 600 else s[:600] + "…"
