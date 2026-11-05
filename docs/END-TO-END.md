# What works end to end, what doesn't yet, and how to fix each gap

## Works today (verified by 41 tests and the recorded demo)

| Path | Status |
|---|---|
| Engine math, ranking, payer mix, flags | ✅ unit-tested with hand-checked arithmetic |
| Agent loop: tools, final JSON, verifier, repair turn | ✅ against the mock API |
| **The live HTTP code path** (`META_API_BASE` + Bearer key, real socket) | ✅ `test_end_to_end_over_network`: the CLI in live mode against the mock served on a port |
| Budget guard, monthly cap, usage log | ✅ tested |
| HTTP API and web UI (desktop and mobile) | ✅ tested, with screenshots and video in `demo/` |
| MCP server over stdio (tools and skill resources) | ✅ tested with a real MCP client |
| Importers for FDA NDC JSON, NADAC CSV and wholesaler CSV | ✅ against fixtures in the published schemas |

## Not yet end to end, and how to fix it

### 1. A real Meta model has never answered (needs your key)

**Why it's needed:** live mode calls Meta Model API, and only you can create a
key. **Fix:**
1. At dev.meta.ai: turn on 2FA, add a payment method (required even for
   credits; expect a temporary pre-authorization charge), and set a monthly
   spend notification on the Billing page.
2. Claim the $150 hackathon credits through the form in the participant guide
   **before Nov 4, 9 PM ET**.
3. Create a key, then `cp .env.example .env`, fill in `META_API_KEY`, and
   `set -a; . ./.env; set +a`.
4. Run `ndc-scout check-api`. It walks through four checks: list models,
   plain chat, tool calling, and cost. Each failure prints the specific fix.

### 2. Two API details are inferred, not confirmed from Meta's own docs

I couldn't open dev.meta.ai/docs from the build environment. The base URL
`https://api.meta.ai/v1`, `/chat/completions`, `META_API_KEY`, OpenAI-style
`tools`, and `max_completion_tokens` come from **LiteLLM's and promptfoo's
Meta provider pages**, which agree. `Authorization: Bearer` is **assumed**.

| If `check-api` shows… | Change |
|---|---|
| 401 with a valid key | the auth header in `llm.py` (`MetaClient.chat`) and `cli.cmd_check_api` |
| 404 on the URL | `META_API_BASE` |
| model not listed | `META_MODEL`, using one from the printed list |
| 400 naming a parameter | remove or rename it in `MetaClient.chat` (`body = …`) |
| tool calling → "answered without a tool call" | try another model, or `META_REASONING_EFFORT=low` |

### 3. The mock is a script, not a model

The mock always calls the right tools and writes a correct JSON answer, so it
proves the *plumbing*, not the *intelligence*. A real model may wrap its
answer in prose, skip candidates, or pick the wrong tool. Defenses already in
place:

- `parse_report` extracts JSON from fenced or prose-wrapped answers.
- `verify()` catches wrong numbers.
- The repair turn sends back the exact errors.

If live runs still come back `unverified`:

- Tighten `SKILL.md`, the single place to edit the prompt.
- Add `response_format` with a JSON schema in `MetaClient.chat`. promptfoo
  lists it as supported, but I haven't verified it.
- Raise `repair_turns`.

Run 10–20 live queries and track how often the status is `verified`. That
number is a strong "Technical depth" point for judges.

### 4. The data is synthetic (the biggest real-world gap)

The margin math needs both sides of the trade, and most of the inputs aren't
public:

| Input | Public? | How to get it | Importer |
|---|---|---|---|
| NDC → product, package size, labeler | ✅ FDA NDC Directory | download `drug-ndc-0001-of-0001.json.zip` from open.fda.gov | `ndc-scout import fda FILE --out real_data --generic atorvastatin` |
| NADAC per unit | ✅ CMS, weekly | data.medicaid.gov → NADAC → export CSV | `ndc-scout import nadac FILE --out real_data` |
| **What you pay** (acquisition) | ❌ your wholesaler contract | export a price file from your wholesaler portal | `ndc-scout import catalog FILE --out real_data --ndc-col … --price-col … --size-col …` |
| **AWP** | ❌ licensed (Medi-Span / First Databank) | license or trial; otherwise skip AWP payers | write `awp.csv` (`ndc11,awp_per_unit,effective_date`) |
| **Payer formulas, MAC lists, fees** | ❌ PBM contracts | the pharmacy has them; state Medicaid dispensing fees are public | edit `payers.json` |

Then run `NDC_SCOUT_DATA_DIR=real_data ndc-scout serve`.

What's realistic for the hackathon:
- Real FDA and NADAC data, plus Medicaid (`nadac_plus_fee`) with your state's
  published fee, is fully public. That is legitimate real data.
- Acquisition cost, AWP and MAC stay synthetic unless a pharmacy shares them
  *with permission*. The rules ban data you don't have rights to.
- Label anything synthetic, which the UI already does.
- With only public data, you can't use NADAC as your acquisition cost: the
  Medicaid margin would just equal the dispensing fee for every NDC. You need
  real catalog prices for the ranking to mean anything.

I couldn't reach data.medicaid.gov or open.fda.gov from my environment, so the
importers were tested on hand-made fixtures in the documented formats. Run them
once on the real downloads. If a column name differs, the error points at
`importers.py`.

### 5. Muse Code skills and MCP support are unconfirmed

Nothing in the reference repos shows whether Muse Code reads `SKILL.md`
folders or MCP servers. I ship three things:
- `AGENTS.md`, a convention many coding agents read
- `skills/`, plain markdown that's useful to any agent and is also the runtime
  prompt
- `.mcp.json`, the standard MCP server config

To check, ask Muse Code to list its tools with this repo open, or look at
`muse --help` for `mcp` or `skills`. If it supports MCP, register
`python -m ndc_scout.mcp_server`. If it doesn't, the HTTP API works with any
client.

### 6. "Live product" hosting for judges

The rubric gives 25% to the demo, which judges score by whether it holds up
live. To host it:
1. Deploy the FastAPI app on any Python host: `pip install .` and
   `ndc-scout serve --host 0.0.0.0 --port $PORT`.
2. Set `META_API_KEY` as a server secret. It's only read server-side and never
   sent to the browser.
3. Keep `NDC_SCOUT_MAX_RUN_USD` and `NDC_SCOUT_MONTHLY_CAP_USD` low. Anyone
   with the URL can spend your credits, so add rate limiting or a shared
   passcode before posting it publicly.
4. Without a key, `/api/ask` defaults to mock mode, so a public demo never
   breaks. It does label itself MOCK.

### 7. Optional: the $10K open-weights bonus

Because the client is OpenAI-compatible, point `META_API_BASE` at Baseten's
OpenAI-compatible endpoint (see their quickstart in the participant guide) and
set `META_MODEL=meta-models/muse-spark-open-1026`. Add a pricing row in
`llm.PRICING`, since unknown models are priced at the most expensive rate.
Run `check-api` first; tool-calling support on that deployment is unverified.

### 8. Rules to handle yourself

- **New work only.** The building period is Oct 27 – Nov 4. Commit from day 1
  so the history shows it, and in the README and video say what was built
  during the event.
- **"Use of Meta AI tooling" is 25%.** This code was written with Claude, not
  Muse Code. Check the official rules on AI-assisted code, and consider doing
  real development in Muse Code (and saying so). Use Meta's models beyond one
  tool loop, for example Muse Spark reading a photographed wholesaler invoice
  or price sheet through vision to build `catalog.csv`, or the open-weights
  route.
- **The public repo must not contain `.env`.** Run `git log -p | grep -i meta_api`
  before making it public.
