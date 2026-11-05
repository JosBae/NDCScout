---
name: ndc-reimbursement
description: Decide which NDC (package of a generic drug) a retail pharmacy should stock and dispense to maximize reimbursement margin for a payer or payer mix. Use when asked which NDC/labeler/package reimburses best, why two NDCs of the same drug pay differently, or to explain a claim's margin.
---

# NDC Reimbursement Scout

## Goal
For one drug, strength and form, rank every NDC by **margin per fill** =
payer reimbursement − pharmacy acquisition cost, and recommend one NDC a
pharmacist can act on. This is a purchasing/financial decision. Never give
clinical advice and never suggest switching a patient's drug, strength or form.

## Workflow
1. `search_drugs` with the drug text (or NDC) → pick the `drug_id`. Note its
   `billing_unit`; quantity is in that unit (EA tablets, GM inhalers, ML liquids).
   If several drug_ids match and the request does not disambiguate, choose the
   closest strength/form and say so in `needs_human`.
2. If the payer is unclear, call `list_payers`.
3. One payer → `rank_ndcs`. Several payers or a mix → `rank_ndcs_for_payer_mix`.
4. Optionally `price_ndc` to explain one row.
5. Answer with the JSON below. Copy numbers exactly from tool results.

## Rules
- Every number comes from a tool result. Do not compute margins yourself.
- Never recommend an NDC whose `computable` is false (blocking data issue).
- Recommend the engine's top-ranked computable NDC. If a warning (stale price,
  discontinuing) makes that risky, still recommend it, and explain the risk and
  the runner-up in `needs_human`.
- Put every blocking or warning flag into `needs_human` in plain language.
- Mention when the data is synthetic (`synthetic_data: true`).

## Final answer (JSON only)
```json
{
  "drug_id": "atorvastatin-40mg-tab",
  "payer_id": "medicaid-nadac",
  "payer_mix": null,
  "quantity": 30,
  "recommended_ndc": "99103-0711-05",
  "expected_margin_per_fill": 10.93,
  "ranking": [
    {"ndc": "99103-0711-05", "margin": 10.93, "confidence": "high", "reason": "lowest acquisition cost per unit"}
  ],
  "needs_human": ["99104-0022-10: wholesaler price is 80 days old; re-check before ordering"],
  "summary": "2-3 plain sentences a pharmacist can act on."
}
```
Use `payer_id` for one payer and `payer_mix` (same object you passed to the
tool) for a mix; set the other to null. For a mix, `margin` and
`expected_margin_per_fill` are the tool's `expected_margin`. Include every
candidate in `ranking`; use `"margin": null` for uncomputable ones.
