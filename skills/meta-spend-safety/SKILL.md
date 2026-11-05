---
name: meta-spend-safety
description: Guardrails for building on Meta Model API without a surprise bill or a leaked key. Use before running live agent calls, load tests or demos, or when asked about API spend, keys or credits for this project.
---

# Meta Model API spend & key safety (for this repo)

Meta's API is pay-as-you-go. It has no hard spend cap, keys never expire,
and a card is required even with credits. These gaps come from
omenking/meta-ai-field-notes, "Day Zero Billing and Safety Checklist".

## Before any live call
1. On dev.meta.ai, turn on 2FA and set a monthly spend notification on the
   Billing page. The hackathon credits are $150, so set the alert below that.
2. Set local guards (enforced by `ndc_scout/llm.py`, which refuses to call
   once they're hit):
   - `NDC_SCOUT_MAX_RUN_USD=0.50`: budget per agent run
   - `NDC_SCOUT_MONTHLY_CAP_USD=100`: cap summed from `~/.ndc-scout/usage.jsonl`
3. Keep `META_API_KEY` only in `.env`, which is gitignored. Never paste it into
   code, the README or demo videos. Run `git log -p | grep -i meta_api_key`
   before making the repo public, since the hackathon requires a public repo.
4. Prefer `ndc-scout ask --mock` for UI work and tests. It costs nothing.

## Checking spend
- `ndc-scout usage` totals this project's logged calls by month.
- These are estimates from a pricing snapshot. Meta's Billing page is the
  source of truth.
- This guard only sees calls made by this app. Usage from Muse Code is
  tracked by meta-costcenter, and key expiry or revocation by meta-keyguard.
