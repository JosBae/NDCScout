# How payers price a generic claim (and why the NDC matters)

reimbursement = ingredient cost + dispensing fee. margin = reimbursement − acquisition cost.

| Formula (payers.json) | Ingredient cost | Does the NDC change reimbursement? |
|---|---|---|
| `nadac_plus_fee` (many state Medicaid plans) | NADAC/unit × qty | Rarely: NADAC is usually the same for all generic NDCs of a drug, so the winner is the NDC you buy cheapest. |
| `mac_plus_fee` (PBM generic MAC) | MAC/unit × qty | No: MAC is per drug, so the winner is the NDC you buy cheapest. |
| `awp_discount_plus_fee` | AWP/unit × (1 − discount) × qty | Yes: AWP is set per NDC, so a high-AWP NDC can pay much more. |
| `lesser_of_awp_discount_or_mac` | min(AWP×(1−disc), MAC) × qty | Yes, until the MAC cap binds. |

Key terms:
- **NADAC**: CMS's weekly survey of what pharmacies pay. Public.
- **AWP**: list-price benchmark from a licensed drug compendium (Medi-Span, First Databank). Not public.
- **MAC**: a payer's private per-drug ceiling for generics.
- **Acquisition cost**: your wholesaler's pack price ÷ pack size.

So the best NDC is often different for each payer. A pharmacy with a mixed
book should rank by expected margin across its payer mix.
