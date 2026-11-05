# How everything works

Architecture, file by file, and what one agent run actually does.

---

## 1. The question it answers

> "For this generic drug, which NDC (labeler and package) should my pharmacy
> stock and dispense to make the best margin with this payer, or my mix of payers?"

margin per fill = payer reimbursement − what you paid for those units.

The core insight, shown in the sample data:

- **NADAC and MAC payers** pay the same rate whichever NDC you dispense, so
  the winner is the NDC you **buy** cheapest.
- **AWP-based payers** price each NDC separately, so a high-AWP NDC can win
  even though it costs more to buy.

For atorvastatin 40 mg in the sample data, Medicaid's best is `99103-0711-05`
and Part D's best is `99102-0441-90`.

## 2. Architecture

```
web/index.html ─┐
CLI (cli.py) ───┼─> agent.py ──HTTP──> Meta Model API  /v1/chat/completions   (or mock_meta.py)
HTTP API ───────┘     │  tools.py (one registry) ──> engine.py ──> data.py ──> sample_data/ or real_data/
                      └─ verify(): recompute with engine.py; 1 repair turn
MCP server (mcp_server.py) ──> tools.py   (same tools for any MCP-capable client)
skills/ndc-reimbursement/ ──> loaded as the agent's system prompt AND usable as a coding-agent skill
```

| File | What it does |
|---|---|
| `ndc_scout/ndc.py` | NDC parsing. Turns the 10-digit hyphenated forms (4-4-2, 5-3-2, 5-4-1) into 11-digit 5-4-2 and **rejects** bare 10-digit codes, which are ambiguous. |
| `ndc_scout/data.py` | Loads `products.csv` (FDA), `nadac.csv` (CMS), `awp.csv`, `catalog.csv` (your wholesaler) and `payers.json`, and handles search. |
| `ndc_scout/engine.py` | **All money math.** Four payer formulas, flags with severities (blocking, warning, info), confidence, ranking, payer-mix expected margin, and a cross-payer MAC rate audit. |
| `ndc_scout/tools.py` | Six tools, each with a JSON Schema, shared by every surface. Errors are returned as data so the model can recover. |
| `ndc_scout/llm.py` | The Meta client: OpenAI-compatible chat completions, error-to-fix mapping, token and cost accounting, a per-run budget, a local monthly cap, and the `usage.jsonl` log. |
| `ndc_scout/agent.py` | The tool loop, JSON answer parsing, `verify()`, and a repair turn. It returns the status, report, verification, full trace and cost. |
| `ndc_scout/mock_meta.py` | A scripted stand-in for the API using the same wire format. It is **not a model**. |
| `ndc_scout/api.py` | FastAPI with `/api/health`, `/api/drugs`, `/api/payers`, `/api/rank`, `/api/ask`, `/api/rate-audit` and `/api/usage`, and serves the UI. |
| `ndc_scout/mcp_server.py` | An MCP stdio server with the same six tools, plus the skill files as resources. |
| `ndc_scout/importers.py` | Converts real files: openFDA NDC JSON, CMS NADAC CSV, and a wholesaler CSV. |
| `skills/ndc-reimbursement/` | The domain rules and answer schema, used as the system prompt. |
| `skills/meta-spend-safety/` | The day-zero billing/key checklist, applied to this repo. |
| `web/index.html` | The web UI: a Ranking page (Rank / Ask the agent / Compare AWP vs MAC) and a Rate Audit page, built on Material Web Components. |

## 3. One agent run, step by step

1. **System prompt.** It is `SKILL.md` plus `references/*.md`, about 2k tokens.
   The user message contains the question and a fenced JSON `Request` block
   (drug, quantity, payer or mix).
2. **Turn 1.** The model calls `search_drugs` and `list_payers`; parallel tool
   calls are supported. Each tool runs locally, and its JSON result goes back
   as a `role: tool` message.
3. **Turn 2.** The model calls `rank_ndcs` or `rank_ndcs_for_payer_mix`.
4. **Turn 3.** The model answers with the JSON report.
5. **`verify()`.**
   - It recomputes the ranking with the engine and checks the recommended NDC
     (which must be the engine's best computable one), the stated margin
     (within $0.01), every ranking row's margin, and that no uncomputable NDC
     was recommended.
   - Missing `needs_human` items or omitted candidates come back as warnings.
6. **Repair.** If verification fails, the model gets **one** repair message
   listing the exact discrepancies. Running the mock with `--mistake` shows the
   whole sequence: FAIL, then repair, then PASS.
7. **Guards.**
   - The run is limited by `max_turns` (8) and the per-run budget.
   - The monthly cap is checked against `usage.jsonl` before every call.
   - Each call is logged with token counts and estimated cost.

The model handles language and routing; the deterministic engine owns every
number; nothing reaches the user unchecked.

## 4. Design choices worth knowing

| Choice | Why |
|---|---|
| Skills-first domain knowledge, with `references/` | Keeps the prompt editable in one place (`skills/ndc-reimbursement/`) instead of buried in Python strings, and makes it reusable as a coding-agent skill too |
| A per-run budget and a local monthly cap that **refuses** calls | Meta's API has no hard spend cap of its own, only an email alert |
| A dated pricing snapshot, labeled as an estimate | `llm.PRICING` carries its source and date in a comment; unknown models price at the most expensive known rate rather than guessing low |
| An offline, free demo path | `--mock` and the scripted mock server exercise the exact same HTTP + tool-calling path as live mode, so demos never cost money or require a key |
| Usage logged without transcripts | `usage.jsonl` stores token counts only, never prompts, so the cost log can't leak a conversation |
| Errors carry a `kind` and a `fix` | `MetaAPIError(kind, fix)` and `ndc-scout check-api` turn an opaque HTTP error into something actionable |
| Failing closed, not guessing | The engine marks a row uncomputable rather than guessing on a unit mismatch or missing data |
