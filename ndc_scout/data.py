"""Load the five data files into memory and answer lookups.

Files (see sample_data/README.md and docs/END-TO-END.md):
  products.csv  one row per package NDC (from the FDA NDC Directory)
  nadac.csv     CMS NADAC per-unit benchmark
  awp.csv       AWP per unit (proprietary in real life; optional)
  catalog.csv   your wholesaler pack prices (what you pay)
  payers.json   your plans' reimbursement formulas
"""
from __future__ import annotations

import csv
import json
import os
import re
from dataclasses import dataclass, field
from datetime import date
from functools import lru_cache
from pathlib import Path

from .ndc import NDCError, normalize

def default_data_dir() -> Path:
    from . import resource_root
    return resource_root() / "sample_data"


@dataclass(frozen=True)
class Product:
    ndc11: str
    drug_id: str
    labeler_name: str
    generic_name: str
    strength: str
    dosage_form: str
    package_size: float
    unit: str
    package_description: str
    marketing_end_date: str | None


@dataclass(frozen=True)
class Nadac:
    per_unit: float
    pricing_unit: str
    effective_date: str
    as_of_date: str


@dataclass(frozen=True)
class CatalogPrice:
    pack_price: float
    pack_size: float
    wholesaler: str
    as_of_date: str

    @property
    def per_unit(self) -> float:
        return self.pack_price / self.pack_size


@dataclass
class Dataset:
    products: dict[str, Product]
    nadac: dict[str, Nadac]
    awp: dict[str, float]
    catalog: dict[str, CatalogPrice]
    payers: dict[str, dict]
    meta: dict = field(default_factory=dict)
    source_dir: str = ""

    # ---- lookups -------------------------------------------------------
    @property
    def reference_date(self) -> date:
        ref = self.meta.get("reference_date")
        return date.fromisoformat(ref) if ref else date.today()

    @property
    def synthetic(self) -> bool:
        return bool(self.meta.get("synthetic"))

    def drugs(self) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        for p in self.products.values():
            out.setdefault(p.drug_id, []).append(p.ndc11)
        return out

    def candidates(self, drug_id: str) -> list[Product]:
        return sorted((p for p in self.products.values() if p.drug_id == drug_id), key=lambda p: p.ndc11)

    def describe_drug(self, drug_id: str) -> dict:
        cands = self.candidates(drug_id)
        if not cands:
            raise KeyError(f"unknown drug_id {drug_id!r}")
        p = cands[0]
        return {"drug_id": drug_id, "generic_name": p.generic_name, "strength": p.strength,
                "dosage_form": p.dosage_form, "billing_unit": p.unit, "ndc_count": len(cands)}

    def search(self, query: str, limit: int = 10) -> list[dict]:
        """Match a free-text drug name or an NDC to drug_ids."""
        q = (query or "").strip()
        if not q:
            return []
        try:  # an NDC resolves to its drug
            n = normalize(q)
            if n in self.products:
                return [self.describe_drug(self.products[n].drug_id) | {"matched_ndc": n}]
        except NDCError:
            pass
        tokens = [t for t in re.split(r"[^a-z0-9.]+", q.lower()) if t]
        scored = []
        for drug_id in self.drugs():
            info = self.describe_drug(drug_id)
            hay = " ".join([drug_id, info["generic_name"], info["strength"], info["dosage_form"]]).lower()
            hay_compact = hay.replace(" ", "")
            hits = sum(1 for t in tokens if t in hay or t in hay_compact)
            if hits:
                scored.append((hits, drug_id, info))
        scored.sort(key=lambda x: (-x[0], x[1]))
        return [info | {"match_score": hits / len(tokens)} for hits, _d, info in scored[:limit]]


def _f(v: str | None) -> float | None:
    v = (v or "").strip()
    return float(v) if v else None


def load(data_dir: str | os.PathLike | None = None) -> Dataset:
    d = Path(data_dir or os.environ.get("NDC_SCOUT_DATA_DIR") or default_data_dir())
    if not (d / "products.csv").exists():
        raise FileNotFoundError(f"{d}/products.csv not found (set NDC_SCOUT_DATA_DIR or run scripts/make_sample_data.py)")

    def rows(name: str):
        path = d / name
        if not path.exists():
            return []
        with open(path, newline="") as f:
            return list(csv.DictReader(f))

    products = {}
    for r in rows("products.csv"):
        n = normalize(r["ndc11"])
        products[n] = Product(n, r["drug_id"], r["labeler_name"], r["generic_name"], r["strength"],
                              r["dosage_form"], float(r["package_size"]), r["unit"].upper(),
                              r.get("package_description", ""), (r.get("marketing_end_date") or None))
    nadac = {normalize(r["ndc11"]): Nadac(float(r["nadac_per_unit"]), r["pricing_unit"].upper(),
                                          r["effective_date"], r["as_of_date"])
             for r in rows("nadac.csv")}
    awp = {normalize(r["ndc11"]): float(r["awp_per_unit"]) for r in rows("awp.csv")}
    catalog = {normalize(r["ndc11"]): CatalogPrice(float(r["pack_price"]), float(r["pack_size"]),
                                                   r.get("wholesaler", ""), r.get("as_of_date", ""))
               for r in rows("catalog.csv")}
    payers_doc = json.loads((d / "payers.json").read_text()) if (d / "payers.json").exists() else {"payers": []}
    payers = {p["id"]: p for p in payers_doc.get("payers", [])}
    return Dataset(products, nadac, awp, catalog, payers, payers_doc.get("meta", {}), str(d))


@lru_cache(maxsize=4)
def cached(data_dir: str | None = None) -> Dataset:
    return load(data_dir)
