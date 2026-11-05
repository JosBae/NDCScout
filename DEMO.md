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

Recorded output (screenshots, a walkthrough video, a CLI transcript) will be
added to `demo/` once re-recorded against steps 6–7 above; the folder is
empty for now rather than shipping assets that predate the current app.

## Path B: live with Meta Model API

```bash
cp .env.example .env    # add META_API_KEY, keep the budget caps
set -a; . ./.env; set +a
ndc-scout check-api     # confirms URL, auth, model and tool calling; prints the cost
ndc-scout serve         # the UI defaults to "Meta (live)" when a key is set
```

## 3-minute video script

| Time | On-screen | Narration |
|---|---|---|
| 0:00–0:12 | Title card / blank browser, no app yet | "Maria owns an independent pharmacy. Every week she fills prescriptions for a dozen generic drugs — and every week, she has no real idea what she'll actually get paid for them." |
| 0:12–0:35 | Still no app | "PBMs set a 'maximum allowable cost' for every generic — but they don't have to explain how, and it can change without warning. Three companies process eighty percent of the prescription claims in this country. If Maria pushes back on a bad rate, she doesn't get a better deal — she risks losing a huge slice of her patients overnight." |
| 0:35–0:55 | Still no app | "And it's not even consistent. One PBM can pay fifty percent less than another for the identical drug. There's no way to catch that by hand, across hundreds of drugs and every payer in her book. That's the problem NDC Scout solves." |
| 0:55–1:10 | Ranking page, "atorvastatin 40 mg", Medicaid NADAC, click "Rank only" | "It ranks every NDC of a drug by real margin — reimbursement minus acquisition cost — computed by a deterministic pricing engine, not a guess." |
| 1:10–1:45 | Switch to "Meta (live)", ask a plain-English question, let the trace expand, show verifier PASS | "Maria doesn't want to fill out a form — she wants to ask. This is Meta's model doing the work: real tool-calling against the Meta Model API, picking the right lookups, writing the answer. But it never touches the math itself — every number gets independently recomputed and verified before she ever sees it. Right here, turn by turn." |
| 1:45–2:10 | Compare AWP vs MAC, dumbbell chart | "Different payers calculate reimbursement completely differently. Same drug, MAC-based payer versus AWP-based payer — and the best NDC actually flips. A real, non-obvious call she can act on before she buys." |
| 2:10–2:40 | Rate Audit page, run audit, show the flagged row | "And remember that problem — one PBM quietly paying less than another for the same drug? This is the receipt. One click cross-checks a payer's MAC against every peer payer and the public NADAC rate. Right here: fifty-one percent of what a peer pays for the identical drug — found in seconds, not buried in a spreadsheet." |
| 2:40–3:00 | Back to home page | "Meta's model does the talking. Deterministic math does the deciding. And Maria gets her evening back. NDC Scout." |
