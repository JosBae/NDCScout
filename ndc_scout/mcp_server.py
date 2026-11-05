"""MCP server exposing NDC Scout's tools over stdio.

Any MCP client can call the same deterministic tools the agent uses: Claude
Code, Cursor, or Muse Code if it supports MCP (unconfirmed; see
docs/END-TO-END.md). Register it with:

    {"mcpServers": {"ndc-scout": {"command": "python", "args": ["-m", "ndc_scout.mcp_server"]}}}

Tools mirror ndc_scout/tools.py exactly. The skill files are also exposed as
resources, so a client can load the domain guidance.
"""
from __future__ import annotations

import json
import os

from mcp.server.mcpserver import MCPServer

from . import data
from .agent import SKILL_DIR
from .tools import build_tools, call_tool

server = MCPServer(
    name="ndc-scout",
    instructions=("Rank NDCs of a generic drug by pharmacy reimbursement margin. Call search_drugs first, "
                  "then rank_ndcs (one payer) or rank_ndcs_for_payer_mix. All numbers come from these tools; "
                  "read resource skill://ndc-reimbursement for the rules."),
)


def _ds():
    return data.cached(os.environ.get("NDC_SCOUT_DATA_DIR"))


def _run(name: str, **kwargs) -> str:
    return json.dumps(call_tool(build_tools(_ds()), name, kwargs))


@server.tool(description="Find drug_ids matching a drug name, strength, form, or an NDC. Call first.")
def search_drugs(query: str) -> str:
    return _run("search_drugs", query=query)


@server.tool(description="List payer plans and how each calculates reimbursement.")
def list_payers() -> str:
    return _run("list_payers")


@server.tool(description="Rank every NDC of a drug by margin per fill for ONE payer, with flags and confidence.")
def rank_ndcs(drug_id: str, payer_id: str, quantity: float) -> str:
    return _run("rank_ndcs", drug_id=drug_id, payer_id=payer_id, quantity=quantity)


@server.tool(description="Rank NDCs by expected margin across a payer mix (payer_id -> share of fills).")
def rank_ndcs_for_payer_mix(drug_id: str, payer_mix: dict[str, float], quantity: float) -> str:
    return _run("rank_ndcs_for_payer_mix", drug_id=drug_id, payer_mix=payer_mix, quantity=quantity)


@server.tool(description="Reimbursement and margin breakdown for one NDC and payer.")
def price_ndc(ndc: str, payer_id: str, quantity: float) -> str:
    return _run("price_ndc", ndc=ndc, payer_id=payer_id, quantity=quantity)


@server.resource("skill://ndc-reimbursement", description="Domain rules and answer format (SKILL.md)")
def skill_doc() -> str:
    return (SKILL_DIR / "SKILL.md").read_text()


@server.resource("skill://ndc-reimbursement/formulas", description="How payer formulas work")
def skill_formulas() -> str:
    return (SKILL_DIR / "references" / "reimbursement-formulas.md").read_text()


def main() -> None:
    server.run("stdio")


if __name__ == "__main__":
    main()
