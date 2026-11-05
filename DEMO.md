# Demo guide

## Path A: offline (no key, about 1 minute)

```bash
pip install -e . && ndc-scout serve        # open http://127.0.0.1:8000
```

1. **Rank only:** pick *Atorvastatin 40 mg*, *State Medicaid*, quantity 30.
   The best NDC is the cheapest to buy.
2. Switch to *Medicare Part D*. The best NDC **changes** to the high-AWP one.
   This is the core insight.
3. **Ask the agent** in Mock mode with "answer wrong first" ticked. The trace
   shows `verifier → FAIL`, then the repair, then `PASS`.
4. Choose **Payer mix** with *Metformin 500*, quantity 60. The result shows
   the expected margin across plans, and an NDC with no NADAC goes to "Needs
   a human".
5. Rank *Albuterol*, quantity 8.5, under Medicaid. A `unit_mismatch` block
   appears: NADAC per EA against billing per GM. Refusing to multiply
   mismatched units is the point.
6. **Compare AWP vs MAC:** pick *Atorvastatin 40 mg*, click "Compare AWP vs
   MAC". The dumbbell chart shows the same NDC reimbursing differently under
   each formula, and the best NDC isn't the same one both times.
7. Switch to the **Rate audit** page, pick *Commercial PBM A*, run the audit.
   Several drugs flag `mac_below_peer_payers` — this payer's MAC is as low as
   51% of what another sample payer pays for the identical drug.

Recorded output is in `demo/`: screenshots `01`–`07`, `walkthrough.mp4`, and
`terminal-transcript.txt`. (Predates steps 6–7 above; re-record before
submitting if there's time — see the note in README/CHANGES.)

## Path B: live with Meta Model API

```bash
cp .env.example .env    # add META_API_KEY, keep the budget caps
set -a; . ./.env; set +a
ndc-scout check-api     # confirms URL, auth, model and tool calling; prints the cost
ndc-scout serve         # the UI defaults to "Meta (live)" when a key is set
```

## 3-minute video script

| Time | Beat |
|---|---|
| 0:00 | Introduce yourself and the pharmacy problem: same drug, many NDCs, and payers pay differently |
| 0:25 | Rank under Medicaid, then Part D. The best NDC flips |
| 0:55 | Ask the live agent. Narrate the tool calls in the trace |
| 1:35 | The verifier: show a FAIL, the repair and the PASS, so every number is checked |
| 2:05 | Payer mix and the "needs a human" flags (unit mismatch, stale price) |
| 2:30 | Spend guards, MCP and skills; what's real data and what's synthetic; the roadmap |

## Judging checklist (official rubric)

- [ ] Impact (30%): a named user (independent pharmacy buyer) and the dollar spread per fill
- [ ] Meta tooling (25%): a live Muse Spark run on screen, with tool calls, the verifier and repair; say exactly what you used
- [ ] Demo (25%): the video is the main artifact, and the hosted product works
- [ ] Technical depth (20%): 41 tests, the verifier, fail-closed flags
- [ ] Public repo; `.env` never committed
- [ ] New work only, clearly stated (README section)
- [ ] Synthetic data labeled everywhere it appears
- [ ] Submitted on the Cerebral Valley platform by **Nov 4, 9:00 PM ET**; credits claimed by the same time
