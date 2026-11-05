# How everything works

Part A explains the four reference repos and the hackathon rules, based on
their actual files rather than their READMEs. Part B explains NDC Scout and
maps each idea it borrowed back to where it came from.

---

## Part A: the reference material

### A1. meta-ai-field-notes: the "Day Zero Billing and Safety Checklist"

Repo layout: `docs/day-zero-billing-and-safety-checklist/{guide,0_understand,1_basic_safety,2_muse_cost_monitor,3_muse_keyguard}.md`,
plus experiment folders (`q001…q003`) and a list of feature requests in the README.

**What problem it describes (0_understand.md)**
- *Billing:* a card is required even when you have free credits, and Meta bills
  pay-as-you-go rather than prepaid. There is **no hard spend cap**, only
  **one** monthly email alert per account. That alert can't be set per key or
  as a percentage of a cap.
- *Keys:* keys **never expire** and can't be deactivated, only deleted. You
  can't delete your **last** key, and there are no official key-management
  endpoints.

**What it tells you to do (1_basic_safety.md):** turn on 2FA at dev.meta.ai.
Set the monthly spend notification and test it by setting it to $0.01 and
making a few calls. Review the Usage tab (filter by key or model, export CSV).
Then optionally install the two community tools below. Both only see the
machine they run on.

**Q001 (`run_benchmark.py`): the most useful technical finding.**
- Muse Code has a headless mode:
  `muse exec --json --provider meta --model <m> --prompt-file … --workspace … --session-id <uuid> --disable-approval`.
- The `--json` stream only carries lifecycle events, so the script reads token
  usage from the durable session log at
  `$XDG_DATA_HOME/muse/sessions/**/<session-id>/session.jsonl`. It sums every
  `payload.event` whose `kind == "model_completed"` (`input_tokens`,
  `output_tokens`, `reasoning_tokens`, `cached_tokens`, …).
- Billing has two modes. **api** uses `META_API_KEY`. **subscription** removes
  `META_API_KEY` from the child process, so Muse falls back to the stored
  `muse login`.
- Muse Wonder Full is subscription-only. An API key returns "model … does not
  exist or you lack access", but it works through `muse exec` while logged in.

### A2. meta-costcenter (`meta-cost`): read-only spend visibility

Go binary with SQLite and an embedded React dashboard on `127.0.0.1:8765`.
Key files: `internal/costcenter/collector.go`, `billing.go`, `alerts.go`,
`keyguard.go`, `pricing.json`, and `plans/PLAN.md`.

1. **Collection (`collector.go`).** It scans Muse's retained session JSONL
   files incrementally, recognizing a file by `device:inode` plus a byte
   offset. That way appended or replaced files are neither re-counted nor
   missed. Only an allowlist of usage fields reaches SQLite
   (`input_tokens`, `output_tokens`, and `cached_tokens`, `cached_input_tokens`
   or `cache_read_tokens`). **Transcripts are never stored.** Malformed
   records are counted but never fatal, and cached tokens greater than input
   is treated as corrupt.
2. **Billing mode (`billing.go`).** `meta-cost hook install` registers a Muse
   **SessionStart hook** (`meta-cost hook session-start`). On every session
   start it reads Muse's payload and environment and decides whether the
   credential was a key, which means pay-as-you-go (`env_key`, `stdin_key` or
   `stored_key`), or an account login, which means subscription. Reports can
   then split real pay-as-you-go spend from "subscription-equivalent" usage.
3. **Pricing (`pricing.json`).** A dated snapshot of USD per million tokens,
   for example `muse-spark-1.3`: $1.25 input, $0.15 cached, $4.25 output, and
   `muse-wonder-1.0`: $4 / $0.20 / $20. Costs are **estimates**. Unpriced
   models are left out of totals rather than guessed.
4. **Alerts (`alerts.go`).**
   - An alert has a period (day, week starting Monday, or month), an optional
     project, an optional "payg only" scope, a limit of at least $0.01, and
     warning levels such as 50, 80 and 100%.
   - It can also have `revoke_keys` with up to 20 `key_match` patterns
     (letters, digits, `. _ - * ?`).
   - Alerts only **warn**, through the dashboard banner and a desktop
     notification. They never stop Muse.
5. **Keyguard link (`keyguard.go`).**
   - Cost Center **never touches keys**. It runs `meta-keyguard status --json`,
     which contains no secrets, to show whether revoke alerts are actually
     enforced.
   - The answer is cached for 10 s, or 60 s when Keyguard is missing, because
     the dashboard polls every 2 s.

### A3. meta-keyguard: expiring keys bolted onto a platform without them

The problem: dev.meta.ai has no key API. So Keyguard drives **the dashboard's
own internal endpoints, from inside your signed-in Chrome**.

```
CLI (WSL) --unix socket--> bridge (native messaging host) --stdin/stdout--> Chrome extension --fetch, your cookie--> dev.meta.ai
```

- **`plans/RECON.md`.** The endpoints were found in dev.meta.ai's public
  Next.js JavaScript bundles:
  - `GET /api/portal/teams`
  - `GET` and `POST /api/portal/teams/{teamId}/api-keys` (create body:
    `{name, project_id}`)
  - `DELETE /api/portal/api-keys/{id}`

  The create response's secret is `access_token`, 48 characters. These
  endpoints are undocumented, so Meta can change them at any time.
- **`extension/adapter.js`** is the only file that knows those URLs. It turns
  HTTP results into error codes you can act on: `not_logged_in` (401 or a
  login redirect), `reauth_required` (a 403 with `action_url`),
  `create_blocked` (`payment_method_required` or `age_restricted`),
  `rate_limited`, and `endpoint_changed`. It also redacts responses with
  `shapeOf()`, which replaces every string with its length, so you can debug
  without leaking a key.
- **`manifest.json`** is a Manifest V3 extension. Its permissions are
  `nativeMessaging`, `alarms`, `storage`, `scripting` and `notifications`, and
  its host permission is limited to `https://dev.meta.ai/*`.
- **`nativemsg.go`.** Chrome's native messaging framing is a 4-byte
  native-endian length followed by UTF-8 JSON, with host-to-extension messages
  capped at 1 MB. Chrome starts the bridge through `wsl.exe`.
- **`bridge.go`** relays CLI requests over a Unix socket. Socket paths are
  limited to 108 bytes, so it falls back to `$XDG_RUNTIME_DIR`. It also runs
  the **reaper every 60 s**.
- **`ops.go`.**
  - `Reap()` deletes expired keys, finishes rotations, and applies Cost Center
    limits. The rotation lead is `min(rotate_before, ttl/4)`.
  - Because the last key can't be deleted, it creates a **sentinel** key named
    `keyguard-sentinel` and discards its secret in the browser.
  - `max_ttl` caps lifetimes at a 7-day maximum and a 12-hour default.
  - `kill` deletes every key except the sentinel.
- **`costcenter.go`.**
  - Reads `meta-cost alert status --json`. If an alert with `revoke_keys` has
    crossed its limit, matching keys are revoked, using `path.Match`, which
    behaves like fnmatch here.
  - If Cost Center can't be checked, **key creation is blocked**: it fails
    closed.
- **Limits.**
  - Expiry only happens while Chrome is running with the extension loaded.
  - Secrets are stored in plain text, protected only by file permissions.
  - Spend can overshoot before revocation, so this isn't an account-wide hard
    cap.

### A4. cc-crossbeam: the patterns behind a winning hackathon repo

- **Skills first.** It has 16 folders under `.claude/skills/`. Each `SKILL.md`
  has frontmatter (`name`, `description`, which says *when* to use it), a
  phased workflow, and a `references/` folder. For example,
  `adu-corrections-interpreter` goes through four phases: read the letter,
  categorize into six buckets, research codes with about three parallel
  subagents, and compile the breakdown.
- **Agent SDK flows.** `agents-crossbeam/src/flows/*.ts` call `query()` with:
  - `preset: 'claude_code'` for both tools and system prompt
  - `settingSources: ['project']`, which `docs/learnings-agents-sdk.md` calls
    critical: without it, skills aren't discovered
  - `permissionMode: 'bypassPermissions'`, without which the agent hangs
  - `maxTurns: 80` and `maxBudgetUsd: 15`
- **A human checkpoint.** The analysis flow writes eight JSON files and **stops
  after `contractor_questions.json`**. Generating deliverables is a separate
  flow that runs once a human has answered.
- **An offline demo path.** `DEMO.md` plus the `demo-*` skills run the same
  skills with plain Claude Code on local files. A shortcut runs only phase 2
  with mock session data, so the demo never depends on the production stack.
- **Docs as evidence.** It has `docs/plans/*` (23 plan files), `docs/testing/*`,
  `docs/learnings-*`, and `progress.md` (voice logs).

### A5. The hackathon rules that constrain the design

These come from the Cerebral Valley participant guide.

- **Requirements:** the repo must be public, the project must be new work
  built during the event (and clearly marked), and you need a 3-minute demo
  video. Submit by **Nov 4, 9:00 PM ET**.
- **Banned:** medical advice, basic RAG, simple chatbots and similar projects,
  and any data or code you don't have rights to.
- **Judging:**
  - Stage 1 is pass/fail: the project fits a theme and genuinely uses Meta
    Model API or Meta open-weights models.
  - Stage 2 weights **Impact 30%, Use of Meta AI tooling 25%, Demo 25%,
    Technical depth 20%**.
  - The top 5 in each track go to a live final.
- **Relevant bonuses:**
  - Best Agentic Use-Case ($5K)
  - Best Build on Open Weight Models ($10K, Muse Glimmer or Muse Spark Open;
    on Baseten use `meta-models/muse-spark-open-1026`)
  - Community Vote ($10K)
  - Most Viral Project ($20K)

---

## Part B: NDC Scout

### B1. The question it answers

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

### B2. Architecture

```
web/index.html ─┐
CLI (cli.py) ───┼─> agent.py ──HTTP──> Meta Model API  /v1/chat/completions   (or mock_meta.py)
HTTP API ───────┘     │  tools.py (one registry) ──> engine.py ──> data.py ──> sample_data/ or real_data/
                      └─ verify(): recompute with engine.py; 1 repair turn
MCP server (mcp_server.py) ──> tools.py   (same tools for Claude Code / Cursor / Muse Code if it supports MCP)
skills/ndc-reimbursement/ ──> loaded as the agent's system prompt AND usable as a coding-agent skill
```

| File | What it does |
|---|---|
| `ndc_scout/ndc.py` | NDC parsing. Turns the 10-digit hyphenated forms (4-4-2, 5-3-2, 5-4-1) into 11-digit 5-4-2 and **rejects** bare 10-digit codes, which are ambiguous. |
| `ndc_scout/data.py` | Loads `products.csv` (FDA), `nadac.csv` (CMS), `awp.csv`, `catalog.csv` (your wholesaler) and `payers.json`, and handles search. |
| `ndc_scout/engine.py` | **All money math.** It covers four payer formulas, plus flags with severities (blocking, warning, info), confidence, ranking, and payer-mix expected margin. |
| `ndc_scout/tools.py` | Five tools, each with a JSON Schema, shared by every surface. Errors are returned as data so the model can recover. |
| `ndc_scout/llm.py` | The Meta client: OpenAI-compatible chat completions, error-to-fix mapping, token and cost accounting, a per-run budget, a local monthly cap, and the `usage.jsonl` log. |
| `ndc_scout/agent.py` | The tool loop, JSON answer parsing, `verify()`, and a repair turn. It returns the status, report, verification, full trace and cost. |
| `ndc_scout/mock_meta.py` | A scripted stand-in for the API using the same wire format. It is **not a model**. |
| `ndc_scout/api.py` | FastAPI with `/api/health`, `/api/drugs`, `/api/payers`, `/api/rank`, `/api/ask` and `/api/usage`, and serves the UI. |
| `ndc_scout/mcp_server.py` | An MCP stdio server with the same five tools, plus the skill files as resources. |
| `ndc_scout/importers.py` | Converts real files: openFDA NDC JSON, CMS NADAC CSV, and a wholesaler CSV. |
| `skills/ndc-reimbursement/` | The domain rules and answer schema, used as the system prompt. |
| `skills/meta-spend-safety/` | The day-zero checklist, applied to this repo. |

### B3. One agent run, step by step

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

This is the "verifiable agents" pattern from the Oct 28 workshop. The model
handles language and routing, the deterministic engine owns every number, and
nothing reaches the user unchecked.

### B4. What each borrowed idea maps to

| Idea | Source | Where it lives here |
|---|---|---|
| Skills-first domain knowledge with `references/` | CrossBeam `.claude/skills/*` | `skills/ndc-reimbursement/`, read at runtime by `agent.system_prompt()` |
| `maxBudgetUsd`, `maxTurns` | CrossBeam flows | `NDC_SCOUT_MAX_RUN_USD` and `max_turns` in `llm.py` and `agent.py` |
| A human checkpoint | CrossBeam "stop after contractor_questions" | `needs_human` in every answer; the verifier warns when it's missing |
| An offline demo path | CrossBeam `DEMO.md` | `--mock`, the mock server, and synthetic sample data |
| A dated pricing snapshot; estimates labeled as estimates | meta-costcenter `pricing.json` | `llm.PRICING`, with source and date in a comment |
| Local alerts because Meta has no hard cap | Field notes and Cost Center alerts | `NDC_SCOUT_MONTHLY_CAP_USD`, which **refuses** calls (stricter than Cost Center, which only warns) |
| Usage logged without transcripts | Cost Center collector allowlist | `usage.jsonl` stores token counts only, never prompts |
| Failing loudly with a code you can act on | Keyguard `adapter.js` error codes | `MetaAPIError(kind, fix)` and `ndc-scout check-api` |
| Failing closed | Keyguard blocks create when Cost Center is unknown | The engine marks a row uncomputable rather than guessing on a unit mismatch or missing data |
