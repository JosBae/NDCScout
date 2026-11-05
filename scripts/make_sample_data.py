"""Generate the SYNTHETIC sample dataset in sample_data/.

Everything this script writes is made up for demos and tests:
  * labeler codes 99101-99106 and labeler names are fictitious,
  * every price (NADAC-style, AWP-style, MAC, wholesaler) is invented,
  * payer formulas are simplified illustrations, not any real plan's contract.

The *shapes* mirror real sources so the same code runs on real data:
  products.csv  ~ FDA NDC Directory (one row per package NDC)
  nadac.csv     ~ CMS NADAC weekly file (per-unit acquisition benchmark)
  awp.csv       ~ a compendia AWP feed (Medi-Span / First Databank; proprietary in real life)
  catalog.csv   ~ a pharmacy's wholesaler price file (what YOU pay per pack)
  payers.json   ~ reimbursement formulas you'd copy from your PBM/Medicaid contracts

Run:  python scripts/make_sample_data.py
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "sample_data"
REFERENCE_DATE = "2026-10-21"  # the "as of" date the sample pretends to be

LABELERS = {
    "99101": "Northwind Generics (sample)",
    "99102": "Bluebird Pharma (sample)",
    "99103": "Cardinal Point Labs (sample)",
    "99104": "Harbor Rx (sample)",
    "99105": "Summit Therapeutics (sample)",
    "99106": "Lowcountry Pharma (sample)",
}

DRUGS = {
    "atorvastatin-40mg-tab": dict(generic_name="Atorvastatin Calcium", strength="40 mg", dosage_form="TABLET, FILM COATED", unit="EA"),
    "metformin-500mg-tab": dict(generic_name="Metformin Hydrochloride", strength="500 mg", dosage_form="TABLET", unit="EA"),
    "lisinopril-10mg-tab": dict(generic_name="Lisinopril", strength="10 mg", dosage_form="TABLET", unit="EA"),
    "amoxicillin-500mg-cap": dict(generic_name="Amoxicillin", strength="500 mg", dosage_form="CAPSULE", unit="EA"),
    "sertraline-50mg-tab": dict(generic_name="Sertraline Hydrochloride", strength="50 mg", dosage_form="TABLET", unit="EA"),
    "albuterol-hfa-90mcg": dict(generic_name="Albuterol Sulfate", strength="90 mcg/actuation", dosage_form="AEROSOL, METERED", unit="GM"),
}

# ndc11, drug_id, package_size, nadac/unit (None = not in NADAC), nadac pricing unit,
# awp/unit, wholesaler pack price (None = not in your catalog), catalog as-of, marketing end
ROWS = [
    # atorvastatin 40 mg
    ("99101030130", "atorvastatin-40mg-tab", 90,   0.0412, "EA", 0.9850, 2.95,  "2026-10-19", None),
    ("99102044190", "atorvastatin-40mg-tab", 90,   0.0412, "EA", 1.4200, 4.10,  "2026-10-19", None),
    ("99103071105", "atorvastatin-40mg-tab", 500,  0.0412, "EA", 0.7400, 13.50, "2026-10-19", None),
    ("99104002210", "atorvastatin-40mg-tab", 1000, 0.0412, "EA", 0.6900, 31.00, "2026-08-02", None),  # stale price
    # metformin 500 mg
    ("99101010101", "metformin-500mg-tab", 100,  0.0148, "EA", 0.3300, 1.20,  "2026-10-19", None),
    ("99105020205", "metformin-500mg-tab", 500,  0.0148, "EA", 0.2100, 5.85,  "2026-10-19", None),
    ("99106033310", "metformin-500mg-tab", 1000, 0.0148, "EA", 0.2600, 9.40,  "2026-10-19", None),
    ("99102056601", "metformin-500mg-tab", 100,  None,   "EA", 0.4100, 1.05,  "2026-10-19", None),  # no NADAC
    # lisinopril 10 mg
    ("99103012290", "lisinopril-10mg-tab", 90,   0.0221, "EA", 0.5100, 1.75,  "2026-10-19", None),
    ("99104013301", "lisinopril-10mg-tab", 100,  0.0221, "EA", 0.6800, 2.40,  "2026-10-19", "2026-12-31"),  # discontinuing
    ("99105014410", "lisinopril-10mg-tab", 1000, 0.0221, "EA", 0.4400, 16.90, "2026-10-19", None),
    # amoxicillin 500 mg
    ("99106021101", "amoxicillin-500mg-cap", 100, 0.0734, "EA", 0.9200, 6.10,  "2026-10-19", None),
    ("99101022205", "amoxicillin-500mg-cap", 500, 0.0734, "EA", 0.8700, 27.75, "2026-10-19", None),
    ("99103023301", "amoxicillin-500mg-cap", 100, 0.0734, "EA", 1.1500, None,  None,         None),  # not in catalog
    # sertraline 50 mg
    ("99102031130", "sertraline-50mg-tab", 30,   0.0389, "EA", 1.6600, 1.40,  "2026-10-19", None),
    ("99104032290", "sertraline-50mg-tab", 90,   0.0389, "EA", 1.2100, 3.05,  "2026-10-19", None),
    ("99106034405", "sertraline-50mg-tab", 500,  0.0389, "EA", 0.9900, 16.25, "2026-10-19", None),
    # albuterol HFA (billed per gram)
    ("99105041167", "albuterol-hfa-90mcg", 6.7,  3.1150, "GM", 9.4000, 19.90, "2026-10-19", None),
    ("99101042285", "albuterol-hfa-90mcg", 8.5,  2.8820, "GM", 8.1000, 23.40, "2026-10-19", None),
    ("99103043318", "albuterol-hfa-90mcg", 18,   2.6400, "EA", 6.9000, 52.80, "2026-10-19", None),  # NADAC unit mismatch
]

PAYERS = [
    {
        "id": "medicaid-nadac",
        "name": "State Medicaid (sample NADAC + fee model)",
        "formula": "nadac_plus_fee",
        "dispensing_fee": 10.50,
        "notes": "Many state Medicaid programs pay NADAC (or WAC if no NADAC) plus a professional dispensing fee. Fee here is illustrative; look up your state's actual fee.",
    },
    {
        "id": "pbm-mac",
        "name": "Commercial PBM A (sample generic MAC)",
        "formula": "mac_plus_fee",
        "dispensing_fee": 0.50,
        "mac_per_unit": {
            "atorvastatin-40mg-tab": 0.0520, "metformin-500mg-tab": 0.0180, "lisinopril-10mg-tab": 0.0260,
            "amoxicillin-500mg-cap": 0.0810, "sertraline-50mg-tab": 0.0450, "albuterol-hfa-90mcg": 2.9500,
        },
        "notes": "Pays the plan's Maximum Allowable Cost per unit regardless of NDC, so NDC choice only changes your acquisition cost.",
    },
    {
        "id": "pbm-awp-lesser",
        "name": "Commercial PBM B (sample lesser of AWP-84% or MAC)",
        "formula": "lesser_of_awp_discount_or_mac",
        "awp_discount": 0.84,
        "dispensing_fee": 1.00,
        "mac_per_unit": {
            "atorvastatin-40mg-tab": 0.1900, "metformin-500mg-tab": 0.0450, "lisinopril-10mg-tab": 0.0900,
            "amoxicillin-500mg-cap": 0.1600, "sertraline-50mg-tab": 0.2100, "albuterol-hfa-90mcg": 1.4000,
        },
        "notes": "Reimbursement depends on the specific NDC's AWP until the MAC cap binds, so high-AWP NDCs can reimburse more.",
    },
    {
        "id": "partd-awp",
        "name": "Medicare Part D plan (sample AWP-80% + fee)",
        "formula": "awp_discount_plus_fee",
        "awp_discount": 0.80,
        "dispensing_fee": 0.75,
        "notes": "Pure AWP-discount pricing; NDC-specific AWP drives reimbursement.",
    },
]


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    with open(OUT / "products.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["ndc11", "drug_id", "labeler_name", "generic_name", "strength", "dosage_form",
                    "package_size", "unit", "package_description", "marketing_end_date"])
        for ndc, drug, size, *_rest, mkt_end in ROWS:
            d = DRUGS[drug]
            desc = (f"1 INHALER in 1 CARTON / {size} g" if d["unit"] == "GM"
                    else f"{size} {d['dosage_form'].split(',')[0]} in 1 BOTTLE")
            w.writerow([ndc, drug, LABELERS[ndc[:5]], d["generic_name"], d["strength"], d["dosage_form"],
                        size, d["unit"], desc, mkt_end or ""])
    with open(OUT / "nadac.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["ndc11", "nadac_per_unit", "pricing_unit", "effective_date", "as_of_date"])
        for ndc, _d, _s, nadac, unit, *_ in ROWS:
            if nadac is not None:
                w.writerow([ndc, f"{nadac:.5f}", unit, "2026-10-15", REFERENCE_DATE])
    with open(OUT / "awp.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["ndc11", "awp_per_unit", "effective_date"])
        for ndc, _d, _s, _n, _u, awp, *_ in ROWS:
            w.writerow([ndc, f"{awp:.4f}", "2026-10-01"])
    with open(OUT / "catalog.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["ndc11", "pack_price", "pack_size", "wholesaler", "as_of_date"])
        for ndc, _d, size, _n, _u, _a, price, as_of, _m in ROWS:
            if price is not None:
                w.writerow([ndc, f"{price:.2f}", size, "Sample Wholesaler", as_of])
    meta = {
        "synthetic": True,
        "reference_date": REFERENCE_DATE,
        "warning": "SYNTHETIC SAMPLE DATA. Fictitious labelers, invented prices, illustrative payer formulas. Do not use for real purchasing.",
    }
    (OUT / "payers.json").write_text(json.dumps({"meta": meta, "payers": PAYERS}, indent=2))
    (OUT / "README.md").write_text(
        "# Sample data (SYNTHETIC)\n\n" + meta["warning"] + "\n\n"
        "Generated by `scripts/make_sample_data.py`. Replace with real files using `ndc-scout import` "
        "(see docs/END-TO-END.md).\n"
    )
    print(f"wrote {len(ROWS)} NDCs, {len(PAYERS)} payers to {OUT}")


if __name__ == "__main__":
    main()
