# Data-quality flags returned by the tools

| code | severity | meaning | what to tell the pharmacist |
|---|---|---|---|
| `no_nadac` | blocking | NDC missing from NADAC; NADAC payer can't be priced | Medicaid may pay WAC instead; check the plan |
| `unit_mismatch` | blocking | price unit ≠ billing unit (e.g. per EA vs per GM) | billing in the wrong unit is a common audit finding |
| `not_in_catalog` | blocking | no wholesaler price, so acquisition cost is unknown | ask the wholesaler, or upload a newer catalog |
| `no_awp` / `no_mac` | blocking / warning | missing benchmark for that payer formula | load AWP or the plan's MAC list |
| `stale_catalog_price` | warning | wholesaler price older than 30 days | re-check the price before ordering |
| `discontinuing` | warning | FDA marketing end date within 180 days | don't stock up |
| `pack_size_mismatch` | warning | catalog pack ≠ FDA package size | confirm the item |
| `slow_mover` | info | one package covers more than 10 fills | ties up cash in inventory |
| `mac_capped` | info | MAC is lower than AWP-discount, so MAC sets the price | none needed |

NDC format: FDA uses 10 digits with hyphens (4-4-2, 5-3-2 or 5-4-1). Claims
and NADAC use 11 digits (5-4-2), padding the short segment with a leading
zero. A 10-digit code without hyphens is ambiguous and is rejected.
