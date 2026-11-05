"""One tool registry shared by the agent (Meta function calling), the MCP server
and the HTTP API, so all three surfaces expose identical behavior.

Each tool has: a name, a description written for a model, a JSON Schema for
its arguments (the OpenAI-style `parameters` object Meta's chat completions
accepts), and a Python function that returns JSON-serializable data.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable

from . import engine
from .data import Dataset


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    parameters: dict
    fn: Callable[..., Any]

    def schema(self) -> dict:
        """OpenAI-compatible `tools[]` entry (used by Meta Model API chat completions)."""
        return {"type": "function", "function": {"name": self.name, "description": self.description,
                                                 "parameters": self.parameters}}


def build_tools(ds: Dataset) -> dict[str, Tool]:
    def search_drugs(query: str) -> dict:
        return {"matches": ds.search(query)}

    def list_payers() -> dict:
        return {"payers": [{"id": p["id"], "name": p["name"], "formula": p["formula"], "notes": p.get("notes", "")}
                           for p in ds.payers.values()]}

    def rank_ndcs(drug_id: str, payer_id: str, quantity: float) -> dict:
        return engine.rank(ds, drug_id, payer_id, float(quantity))

    def rank_ndcs_for_payer_mix(drug_id: str, payer_mix: dict, quantity: float) -> dict:
        return engine.rank_mix(ds, drug_id, {k: float(v) for k, v in payer_mix.items()}, float(quantity))

    def price_ndc(ndc: str, payer_id: str, quantity: float) -> dict:
        return engine.price_fill(ds, ndc, payer_id, float(quantity))

    def audit_payer_rates(payer_id: str) -> dict:
        return engine.rate_audit(ds, payer_id)

    qty = {"type": "number", "exclusiveMinimum": 0,
           "description": "Units dispensed per fill, in the drug's billing unit (EA for tablets/capsules, GM for inhalers, ML for liquids)."}
    tools = [
        Tool("search_drugs",
             "Find drug_ids matching a drug name, strength, form, or an NDC. Always call this first to get a drug_id and its billing unit.",
             {"type": "object", "properties": {"query": {"type": "string", "description": "e.g. 'atorvastatin 40 mg' or '99101-0301-30'"}},
              "required": ["query"], "additionalProperties": False},
             search_drugs),
        Tool("list_payers",
             "List the pharmacy's payer plans and how each one calculates reimbursement.",
             {"type": "object", "properties": {}, "additionalProperties": False},
             list_payers),
        Tool("rank_ndcs",
             "Rank every NDC of one drug by dispensing margin (reimbursement minus acquisition cost) for ONE payer. Returns per-NDC math, data-quality flags and confidence. This is the source of truth for all numbers.",
             {"type": "object", "properties": {
                 "drug_id": {"type": "string"}, "payer_id": {"type": "string"}, "quantity": qty},
              "required": ["drug_id", "payer_id", "quantity"], "additionalProperties": False},
             rank_ndcs),
        Tool("rank_ndcs_for_payer_mix",
             "Rank NDCs by EXPECTED margin across a mix of payers (weights = share of fills). Use when the pharmacy serves several plans for this drug.",
             {"type": "object", "properties": {
                 "drug_id": {"type": "string"},
                 "payer_mix": {"type": "object", "additionalProperties": {"type": "number", "minimum": 0},
                               "description": "payer_id -> share of fills, e.g. {\"medicaid-nadac\": 0.4, \"pbm-mac\": 0.6}"},
                 "quantity": qty},
              "required": ["drug_id", "payer_mix", "quantity"], "additionalProperties": False},
             rank_ndcs_for_payer_mix),
        Tool("price_ndc",
             "Full reimbursement and margin breakdown for one specific NDC and payer (use to double-check or explain one row).",
             {"type": "object", "properties": {
                 "ndc": {"type": "string"}, "payer_id": {"type": "string"}, "quantity": qty},
              "required": ["ndc", "payer_id", "quantity"], "additionalProperties": False},
             price_ndc),
        Tool("audit_payer_rates",
             "Cross-check one MAC-based payer's contracted rates: flags any drug where this payer's MAC is suspiciously "
             "low versus another payer's MAC for the same drug, or below the public NADAC rate, and lists drugs with no "
             "MAC on file. Use when asked why a payer underpays, or whether a payer's rates look fair.",
             {"type": "object", "properties": {"payer_id": {"type": "string"}},
              "required": ["payer_id"], "additionalProperties": False},
             audit_payer_rates),
    ]
    return {t.name: t for t in tools}


def call_tool(tools: dict[str, Tool], name: str, arguments: str | dict | None) -> dict:
    """Run a tool from a model's tool call. Errors come back as data so the model can recover."""
    tool = tools.get(name)
    if tool is None:
        return {"error": f"unknown tool {name!r}", "available": sorted(tools)}
    try:
        args = json.loads(arguments) if isinstance(arguments, str) else (arguments or {})
        if not isinstance(args, dict):
            return {"error": "tool arguments must be a JSON object"}
    except json.JSONDecodeError as e:
        return {"error": f"arguments were not valid JSON: {e}"}
    try:
        return tool.fn(**args)
    except TypeError as e:
        return {"error": f"bad arguments for {name}: {e}"}
    except (KeyError, ValueError) as e:
        return {"error": str(e).strip("'\"")}
