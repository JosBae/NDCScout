# NDC Scout: which NDC actually reimburses best?

> Meta Global AI Developer Hackathon, Indie Developer track, theme "Build for Your World".
> Built on Meta Model API (Muse Spark) with tool calling and a deterministic verifier.

> ⚠️ `sample_data/` is **synthetic**: fictitious labelers, invented prices,
> illustrative payer formulas. See [docs/END-TO-END.md](docs/END-TO-END.md) to
> load real FDA and CMS data.

## The problem

A generic drug is sold as many NDCs: different labelers and package sizes, all
the same medicine. Pharmacies are paid per claim by formulas that treat those
NDCs differently.

- **Medicaid (NADAC + fee) and PBM MAC payers** pay the same whichever NDC you
  dispense, so the winner is the NDC you **buy** cheapest.
- **AWP-based payers** price each NDC separately, so a high-AWP NDC can pay
  far more.

So the best NDC changes from payer to payer. A pharmacy with a mixed book
needs the one with the best **expected** margin, and nobody has time to work
that out for hundreds of drugs. NDC Scout does: it ranks a drug's NDCs by
margin per fill for one payer or a payer mix, and flags anything a human
should check.

## What it does

```
$ ndc-scout ask --drug "atorvastatin 40" --payer partd-awp --qty 30
  [turn 1] model -> search_drugs, list_payers
  [turn 2] model -> rank_ndcs(atorvastatin-40mg-tab, partd-awp, 30)
  [turn 3] model -> final answer
  [turn 3] verifier: PASS
RECOMMEND 99102-0441-90  ($7.90 per fill)
  ! 99104-0022-10: Wholesaler price is 80 days old
```

- **The agent** (Muse Spark through Meta Model API) understands the request,
  picks tools, and explains the result in plain language.
- **The engine** (`ndc_scout/engine.py`) does every dollar of the math.
- **The verifier** recomputes every number the model reports. If anything is
  wrong, the model gets one repair turn with the exact discrepancies, and a
  wrong answer is never shown as verified.
- **Flags:**
  - blocking: missing NADAC, unit mismatch, not in your catalog
  - warning: stale price, discontinuing
  - info: slow mover, MAC-capped

  Every warning or blocking flag is surfaced under "needs a human".
- **Spend guards:** a per-run budget, a local monthly cap that **refuses**
  calls once reached (Meta has no hard cap), and a token and cost log.
- **Compare AWP vs MAC:** a chart ranking every NDC's margin under a MAC-based
  payer against an AWP-discount payer side by side — the two formulas often
  disagree on which NDC is best.
- **Rate audit:** cross-checks one payer's MAC rates against every other
  MAC-based payer for the same drug, and against the public NADAC floor, to
  flag a contract that looks stingier than it should — a possible sign of a
  payer underpaying relative to its peers. Deterministic, same as every other
  number here; no model involved.

## Run it

```bash
pip install -e ".[dev]"
python -m pytest -q                       # 46 tests, no API calls
ndc-scout serve                           # http://127.0.0.1:8000  (mock mode if no key)
ndc-scout ask --drug metformin --mix medicaid-nadac=0.3,pbm-mac=0.7 --qty 60 --mock
ndc-scout ask ... --mock --mistake        # watch the verifier catch a wrong answer
cp .env.example .env && $EDITOR .env      # add META_API_KEY, then just:
ndc-scout check-api && ndc-scout ask --drug lisinopril --payer medicaid-nadac --qty 30
# .env loads automatically (python-dotenv); an already-exported shell var still wins
```

## Surfaces (all share one tool registry)

| Surface | How |
|---|---|
| Web UI | `ndc-scout serve` → `/` (Ranking and Rate audit pages, Material Design 3 UI) |
| HTTP API | `/api/health`, `/api/drugs`, `/api/payers`, `/api/rank`, `/api/ask`, `/api/rate-audit`, `/api/usage` |
| CLI | `ndc-scout rank \| ask \| serve \| mcp \| mock-api \| check-api \| usage \| import` |
| MCP server | `python -m ndc_scout.mcp_server` (see `.mcp.json`) |
| Skills | `skills/ndc-reimbursement/` (also the agent's system prompt), `skills/meta-spend-safety/` |

## How it uses Meta's models

- **Model:** `muse-spark-1.3` through Meta Model API Chat Completions, with
  OpenAI-style function calling, parallel tool calls and an optional
  `reasoning_effort`.
- **Beyond a basic integration:**
  - a multi-turn tool agent
  - a deterministic verifier with a self-repair turn
  - payer-mix optimization
  - a cross-payer MAC rate audit tool (`audit_payer_rates`)
  - budget-enforced runs
  - the same tools exposed over MCP
- **Open-weights path:** set `META_API_BASE` and `META_MODEL` to a partner
  endpoint for `muse-spark-open`. See docs/END-TO-END.md §7.

## Docs

- [docs/HOW-IT-WORKS.md](docs/HOW-IT-WORKS.md): architecture, file by file,
  and what one agent run does step by step.
- [docs/END-TO-END.md](docs/END-TO-END.md): what's verified, what needs your
  key or data, and how to fix each gap.
- [DEMO.md](DEMO.md): demo paths, the video script and the judging checklist.
- [CHANGES.md](CHANGES.md): the change manifest.

## What was built during the hackathon

Everything in this repo, started Oct 26, 2026. It contains no pre-existing
code. Third-party pieces: FastAPI, httpx, the MCP Python SDK, and Uvicorn.
The pricing snapshot is adapted from omenking/meta-costcenter (MIT).

## Not medical advice

NDC Scout only compares packages of the *same* drug, strength and form for
purchasing. It never suggests changing a patient's therapy.

MIT License.
