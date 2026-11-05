# Change manifest: v0.1.0 (2026-10-26, initial build)

All files are new.

| File | Purpose |
|---|---|
| `ndc_scout/ndc.py` | NDC parse/normalize (4-4-2, 5-3-2, 5-4-1 → 5-4-2); rejects ambiguous bare 10-digit codes |
| `ndc_scout/data.py` | Loads products, NADAC, AWP, catalog and payers; drug search, including NDC lookup |
| `ndc_scout/engine.py` | Payer formulas, margin per fill, flags and confidence, ranking, payer-mix expected margin |
| `ndc_scout/tools.py` | Tool definitions with JSON Schemas, shared by agent, MCP and API (see additions below) |
| `ndc_scout/llm.py` | Meta Model API client: chat completions, error→fix mapping, cost estimate, run budget, monthly cap, usage log |
| `ndc_scout/agent.py` | Tool-calling loop, JSON answer parsing, `verify()`, repair turn, trace |
| `ndc_scout/mock_meta.py` | Scripted, OpenAI-compatible stand-in for offline demos and tests (not a model) |
| `ndc_scout/api.py` | FastAPI endpoints and UI hosting |
| `ndc_scout/mcp_server.py` | MCP stdio server (tools plus skill resources) |
| `ndc_scout/importers.py` | openFDA NDC JSON, CMS NADAC CSV and wholesaler CSV → app data files |
| `ndc_scout/cli.py` | `ndc-scout` command: rank, ask, serve, mcp, mock-api, check-api, usage, import |
| `web/index.html` | Single-file UI (light/dark, mobile) |
| `skills/ndc-reimbursement/**` | Domain skill; also the agent's system prompt |
| `skills/meta-spend-safety/SKILL.md` | Day-zero billing and key checklist, applied to this repo |
| `scripts/make_sample_data.py`, `sample_data/*` | Reproducible SYNTHETIC dataset (20 NDCs, 6 drugs, 4 payers) |
| `tests/test_core.py`, `test_agent.py`, `test_surfaces.py` | 46 tests, including MCP over stdio and end-to-end over a real socket |
| `AGENTS.md`, `.mcp.json`, `.env.example`, `.gitignore`, `pyproject.toml`, `LICENSE` | Agent instructions, MCP config, env template, packaging |
| `docs/HOW-IT-WORKS.md`, `docs/END-TO-END.md`, `README.md`, `DEMO.md` | Documentation |
| `demo/*` | Screenshots, walkthrough video, CLI transcript |

## Added after the initial build (2026-11-04)

| Change | Where |
|---|---|
| `engine.rate_audit()`: cross-checks one payer's MAC rates against every other MAC-based payer for the same drug, and against the public NADAC floor. Deterministic, no model involved. | `ndc_scout/engine.py` |
| `audit_payer_rates` tool (now six tools total, shared by agent/MCP/API) | `ndc_scout/tools.py` |
| `GET /api/rate-audit?payer_id=` (free, no model call, same pattern as `/api/rank`) | `ndc_scout/api.py` |
| `.env` auto-loads via `python-dotenv` on import (`override=False`, so an exported shell var still wins) | `ndc_scout/__init__.py`, `pyproject.toml` |
| `GET /api/health` now also reports `reasoning_effort` | `ndc_scout/api.py` |
| Web UI rebuilt on Material Web Components (MD3), with a two-page nav (Ranking / Rate audit) using the View Transitions API, a new "Compare AWP vs MAC" dumbbell chart, and a restructured turn-by-turn agent trace | `web/index.html` |
| 5 new tests covering `rate_audit()` (46 tests total) | `tests/test_core.py` |

## Fixed during the build

- **Packaging:** a regular `pip install .` couldn't find `sample_data/`,
  `skills/` or `web/`, which sit next to the package. They are now resolved
  through `ndc_scout.resource_root()`: first `$NDC_SCOUT_ROOT`, then the
  source checkout, then the current directory. This was verified in a fresh
  virtual environment.
- **Importer:** "200 AEROSOL, METERED in 1 INHALER > 8.5 g" was being read as
  1700 g. Dose counts no longer multiply billing units. Caught by a test.
- **UI:** on phones, the results column grew to fit the table, causing
  sideways scrolling (676 px on a 390 px screen). Grid items now shrink, and
  the table scrolls inside its card.
