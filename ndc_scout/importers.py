"""Convert real public/pharmacy files into the app's data files.

Sources (download on your own machine; the build sandbox couldn't reach them):
  * FDA NDC Directory, openFDA JSON:
      https://download.open.fda.gov/drug/ndc/drug-ndc-0001-of-0001.json.zip
  * CMS NADAC weekly CSV: data.medicaid.gov → search "NADAC (National Average Drug Acquisition Cost)"
    → current year → Export CSV.
  * Your wholesaler price file: export from your wholesaler portal as CSV.

Field names follow those sources' published schemas; they were tested against
small hand-made fixtures in that shape (tests/test_importers.py), not a live download.
"""
from __future__ import annotations

import csv
import json
import re
import zipfile
from datetime import datetime
from pathlib import Path

from .ndc import NDCError, normalize

_UNIT_MAP = {"TABLET": "EA", "CAPSULE": "EA", "PATCH": "EA", "SUPPOSITORY": "EA", "TROCHE": "EA", "LOZENGE": "EA",
             "ML": "ML", "G": "GM", "GM": "GM"}
_DOSE_COUNTS = {"AEROSOL", "ACTUATION", "ACTUATIONS", "SPRAY", "SPRAYS", "DOSE", "DOSES", "PUFF", "PUFFS"}
_QTY = re.compile(r"(\d+(?:\.\d+)?)\s+([A-Za-z]+)")


def slug(*parts: str) -> str:
    s = "-".join(p for p in parts if p)
    s = re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")
    return re.sub(r"-+", "-", s)


def package_size(description: str) -> tuple[float, str] | None:
    """'30 TABLET in 1 BOTTLE (…)' -> (30, 'EA'); '1 CANISTER in 1 CARTON > 8.5 g in 1 CANISTER' -> (8.5, 'GM').

    Heuristic: walk the '>'-separated levels; counts of EA units multiply, a
    mL/g quantity at the innermost level wins. Returns None if nothing billable found.
    """
    total, unit = 1.0, None
    for level in description.split(">"):
        m = _QTY.search(re.sub(r"\([^)]*\)", "", level))
        if not m:
            continue
        qty, word = float(m.group(1)), m.group(2).upper()
        if word in _DOSE_COUNTS:
            continue  # "200 AEROSOL, METERED in 1 INHALER" counts doses, not billing units
        mapped = _UNIT_MAP.get(word) or _UNIT_MAP.get(word.rstrip("S"))
        if mapped in ("ML", "GM"):
            return qty * (total if unit is None else 1), mapped
        if mapped == "EA":
            total, unit = total * qty, "EA"
        else:
            total *= qty  # container count (BOTTLE, CARTON, BLISTER…) multiplies the inner count
    return (total, unit) if unit else None


def _iso(d: str | None) -> str:
    d = (d or "").strip()
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%Y%m%d", "%m/%d/%y"):
        try:
            return datetime.strptime(d, fmt).date().isoformat()
        except ValueError:
            continue
    return ""


def import_fda_ndc(src: str | Path, out_dir: str | Path, generic_filter: str | None = None) -> dict:
    """openFDA drug-ndc JSON (or its .zip) -> products.csv. Returns counts."""
    src = Path(src)
    if src.suffix == ".zip":
        with zipfile.ZipFile(src) as z:
            doc = json.loads(z.read(z.namelist()[0]))
    else:
        doc = json.loads(src.read_text())
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    written = skipped = 0
    with open(out / "products.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["ndc11", "drug_id", "labeler_name", "generic_name", "strength", "dosage_form",
                    "package_size", "unit", "package_description", "marketing_end_date"])
        for prod in doc.get("results", []):
            generic = (prod.get("generic_name") or "").strip()
            if generic_filter and generic_filter.lower() not in generic.lower():
                continue
            strength = "; ".join(f"{a.get('strength', '')}".strip() for a in prod.get("active_ingredients", [])) or ""
            form = (prod.get("dosage_form") or "").strip()
            drug_id = slug(generic, strength.replace("/", " per "), form.split(",")[0])
            for pkg in prod.get("packaging", []):
                try:
                    n = normalize(pkg["package_ndc"])
                except (KeyError, NDCError):
                    skipped += 1
                    continue
                size = package_size(pkg.get("description", ""))
                if size is None:
                    skipped += 1
                    continue
                end = _iso(pkg.get("marketing_end_date") or prod.get("marketing_end_date"))
                w.writerow([n, drug_id, prod.get("labeler_name", ""), generic.title(), strength, form,
                            f"{size[0]:g}", size[1], pkg.get("description", ""), end])
                written += 1
    return {"products_written": written, "packages_skipped": skipped}


def import_nadac(src: str | Path, out_dir: str | Path) -> dict:
    """CMS NADAC CSV -> nadac.csv. Keeps the latest effective row per NDC."""
    best: dict[str, dict] = {}
    with open(src, newline="", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            raw = (r.get("NDC") or "").strip()
            if raw.isdigit() and len(raw) < 11:
                raw = raw.zfill(11)  # Excel drops NADAC's leading zeros; NADAC NDCs are always 11 digits
            try:
                n = normalize(raw)
            except NDCError:
                continue
            row = {"ndc11": n, "nadac_per_unit": (r.get("NADAC Per Unit") or r.get("NADAC_Per_Unit") or "").strip(),
                   "pricing_unit": (r.get("Pricing Unit") or r.get("Pricing_Unit") or "").strip().upper(),
                   "effective_date": _iso(r.get("Effective Date") or r.get("Effective_Date")),
                   "as_of_date": _iso(r.get("As of Date") or r.get("As_of_Date"))}
            if not row["nadac_per_unit"]:
                continue
            if n not in best or row["effective_date"] > best[n]["effective_date"]:
                best[n] = row
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    with open(out / "nadac.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["ndc11", "nadac_per_unit", "pricing_unit", "effective_date", "as_of_date"])
        w.writeheader()
        w.writerows(best.values())
    return {"nadac_rows": len(best)}


def import_catalog(src: str | Path, out_dir: str | Path, ndc_col: str = "NDC", price_col: str = "Price",
                   size_col: str = "Pack Size", wholesaler: str = "", as_of: str = "") -> dict:
    """A wholesaler CSV export -> catalog.csv. Column names vary by wholesaler, so they're parameters."""
    rows, bad = [], 0
    with open(src, newline="", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            try:
                n = normalize(r[ndc_col])
                price = float(str(r[price_col]).replace("$", "").replace(",", ""))
                size = float(r[size_col])
            except (KeyError, ValueError, NDCError):
                bad += 1
                continue
            rows.append({"ndc11": n, "pack_price": f"{price:.2f}", "pack_size": f"{size:g}",
                         "wholesaler": wholesaler, "as_of_date": as_of or datetime.now().date().isoformat()})
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    with open(out / "catalog.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["ndc11", "pack_price", "pack_size", "wholesaler", "as_of_date"])
        w.writeheader()
        w.writerows(rows)
    return {"catalog_rows": len(rows), "rows_skipped": bad}
