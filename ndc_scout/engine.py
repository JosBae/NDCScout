"""Deterministic reimbursement math. This is the source of truth.

The model never does arithmetic that ends up in a recommendation: it calls
these functions as tools, and the verifier (agent.verify) recomputes every
number it reports against this module.

Margin for one fill of `quantity` billing units of one NDC under one payer:

    reimbursement = ingredient_cost(payer formula, NDC, quantity) + dispensing_fee
    acquisition   = (wholesaler pack price / pack size) * quantity
    margin        = reimbursement - acquisition

Supported payer formulas (payers.json "formula"):
    nadac_plus_fee                  NADAC/unit * qty + fee              (typical Medicaid)
    mac_plus_fee                    MAC/unit * qty + fee                (MAC is per drug, not per NDC)
    awp_discount_plus_fee           AWP/unit * (1 - disc) * qty + fee
    lesser_of_awp_discount_or_mac   min(AWP*(1-disc), MAC) * qty + fee
"""
from __future__ import annotations

from datetime import date

from .data import Dataset, Product
from .ndc import format11, normalize

STALE_CATALOG_DAYS = 30
DISCONTINUE_WINDOW_DAYS = 180
SLOW_MOVER_FILLS = 10  # package holds more than this many fills -> inventory warning
MAC_PEER_RATIO = 0.6       # flag a payer's MAC if it's <= 60% of the highest peer payer's MAC for the same drug
MAC_NADAC_FLOOR_RATIO = 0.5  # flag a payer's MAC if it's <= 50% of the public NADAC rate for that drug

BLOCKING, WARNING, INFO = "blocking", "warning", "info"


def _flag(code: str, severity: str, message: str) -> dict:
    return {"code": code, "severity": severity, "message": message}


def _days_between(a: str, b: date) -> int | None:
    try:
        return (b - date.fromisoformat(a)).days
    except (TypeError, ValueError):
        return None


def ingredient_cost(ds: Dataset, product: Product, payer: dict, quantity: float) -> tuple[float | None, dict, list[dict]]:
    """Return (ingredient cost or None, basis details, flags) for the payer formula."""
    formula = payer["formula"]
    flags: list[dict] = []
    basis: dict = {"formula": formula}
    n = product.ndc11

    if formula == "nadac_plus_fee":
        nad = ds.nadac.get(n)
        if nad is None:
            flags.append(_flag("no_nadac", BLOCKING, "NDC is not in the NADAC file; this payer's rate cannot be computed (many Medicaid plans fall back to WAC, which is not loaded)."))
            return None, basis, flags
        if nad.pricing_unit != product.unit:
            flags.append(_flag("unit_mismatch", BLOCKING,
                               f"NADAC prices this NDC per {nad.pricing_unit} but the product is billed per {product.unit}; refusing to multiply mismatched units."))
            return None, basis, flags
        basis.update(nadac_per_unit=nad.per_unit, nadac_effective=nad.effective_date)
        return nad.per_unit * quantity, basis, flags

    mac = (payer.get("mac_per_unit") or {}).get(product.drug_id)
    awp = ds.awp.get(n)

    if formula == "mac_plus_fee":
        if mac is None:
            flags.append(_flag("no_mac", BLOCKING, "Payer has no MAC for this drug."))
            return None, basis, flags
        basis.update(mac_per_unit=mac)
        return mac * quantity, basis, flags

    if formula in ("awp_discount_plus_fee", "lesser_of_awp_discount_or_mac"):
        if awp is None:
            flags.append(_flag("no_awp", BLOCKING, "No AWP loaded for this NDC (AWP comes from a licensed compendium)."))
            return None, basis, flags
        disc = float(payer["awp_discount"])
        awp_rate = awp * (1 - disc)
        basis.update(awp_per_unit=awp, awp_discount=disc, awp_rate_per_unit=round(awp_rate, 6))
        if formula == "awp_discount_plus_fee":
            return awp_rate * quantity, basis, flags
        if mac is None:
            flags.append(_flag("no_mac", WARNING, "Payer has no MAC for this drug; using AWP discount alone."))
            return awp_rate * quantity, basis, flags
        rate = min(awp_rate, mac)
        basis.update(mac_per_unit=mac, binding="mac" if mac < awp_rate else "awp")
        if mac < awp_rate:
            flags.append(_flag("mac_capped", INFO, "MAC is lower than AWP-discount, so the MAC sets the price."))
        return rate * quantity, basis, flags

    raise ValueError(f"unknown payer formula {formula!r}")


def price_fill(ds: Dataset, ndc: str, payer_id: str, quantity: float) -> dict:
    """Full economics of dispensing `quantity` units of one NDC to one payer."""
    n = normalize(ndc)
    product = ds.products.get(n)
    if product is None:
        raise KeyError(f"NDC {format11(n)} is not in products.csv")
    payer = ds.payers.get(payer_id)
    if payer is None:
        raise KeyError(f"unknown payer_id {payer_id!r}; known: {sorted(ds.payers)}")
    if quantity <= 0:
        raise ValueError("quantity must be positive")

    ingredient, basis, flags = ingredient_cost(ds, product, payer, quantity)
    fee = float(payer.get("dispensing_fee", 0))
    ref = ds.reference_date

    cat = ds.catalog.get(n)
    acquisition = None
    if cat is None:
        flags.append(_flag("not_in_catalog", BLOCKING, "Not in your wholesaler catalog, so acquisition cost is unknown."))
    else:
        if abs(cat.pack_size - product.package_size) > 1e-9:
            flags.append(_flag("pack_size_mismatch", WARNING,
                               f"Catalog pack size {cat.pack_size:g} differs from FDA package size {product.package_size:g}; using catalog."))
        acquisition = cat.per_unit * quantity
        age = _days_between(cat.as_of_date, ref)
        if age is None or age > STALE_CATALOG_DAYS:
            flags.append(_flag("stale_catalog_price", WARNING,
                               f"Wholesaler price is {age if age is not None else 'of unknown'} days old (as of {cat.as_of_date or 'unknown'})."))

    if product.marketing_end_date:
        left = -(_days_between(product.marketing_end_date, ref) or 0)
        if left <= DISCONTINUE_WINDOW_DAYS:
            flags.append(_flag("discontinuing", WARNING, f"FDA marketing end date {product.marketing_end_date} ({left} days away)."))
    if product.package_size > SLOW_MOVER_FILLS * quantity:
        flags.append(_flag("slow_mover", INFO,
                           f"One package ({product.package_size:g} {product.unit}) covers {product.package_size / quantity:.0f} fills; ties up inventory."))

    reimbursement = None if ingredient is None else ingredient + fee
    margin = None if (reimbursement is None or acquisition is None) else reimbursement - acquisition
    severities = {f["severity"] for f in flags}
    confidence = "none" if BLOCKING in severities else ("medium" if WARNING in severities else "high")

    return {
        "ndc11": n,
        "ndc": format11(n),
        "labeler": product.labeler_name,
        "package": f"{product.package_size:g} {product.unit}",
        "package_size": product.package_size,
        "billing_unit": product.unit,
        "payer_id": payer_id,
        "quantity": quantity,
        "ingredient_cost": _r(ingredient),
        "dispensing_fee": round(fee, 2),
        "reimbursement": _r(reimbursement),
        "acquisition_cost": _r(acquisition),
        "acquisition_per_unit": None if cat is None else round(cat.per_unit, 5),
        "margin": _r(margin),
        "basis": basis,
        "flags": flags,
        "confidence": confidence,
        "computable": margin is not None,
    }


def _r(v: float | None) -> float | None:
    return None if v is None else round(v, 2)


def rank(ds: Dataset, drug_id: str, payer_id: str, quantity: float) -> dict:
    """Rank every NDC of a drug by margin for one payer. Uncomputable NDCs go last."""
    cands = ds.candidates(drug_id)
    if not cands:
        raise KeyError(f"unknown drug_id {drug_id!r}")
    rows = [price_fill(ds, p.ndc11, payer_id, quantity) for p in cands]
    # Best margin first; among ties prefer higher confidence, then smaller package (less inventory tied up).
    conf_order = {"high": 0, "medium": 1, "none": 2}
    rows.sort(key=lambda r: (not r["computable"], -(r["margin"] or 0), conf_order[r["confidence"]], r["package_size"]))
    for i, r in enumerate(rows, 1):
        r["rank"] = i if r["computable"] else None
    best = next((r for r in rows if r["computable"]), None)
    spread = None
    computable = [r for r in rows if r["computable"]]
    if len(computable) >= 2:
        spread = round(computable[0]["margin"] - computable[-1]["margin"], 2)
    return {
        "drug": ds.describe_drug(drug_id),
        "payer": {k: ds.payers[payer_id][k] for k in ("id", "name", "formula")},
        "quantity": quantity,
        "best_ndc": best["ndc"] if best else None,
        "margin_spread_per_fill": spread,
        "candidates": rows,
        "synthetic_data": ds.synthetic,
        "data_as_of": ds.reference_date.isoformat(),
    }


def rank_mix(ds: Dataset, drug_id: str, payer_mix: dict[str, float], quantity: float) -> dict:
    """Expected margin per fill across a payer mix (weights are shares of fills, normalized)."""
    if not payer_mix:
        raise ValueError("payer_mix is empty")
    total = sum(payer_mix.values())
    if total <= 0:
        raise ValueError("payer_mix weights must sum to a positive number")
    weights = {k: v / total for k, v in payer_mix.items()}
    per_payer = {pid: {r["ndc11"]: r for r in rank(ds, drug_id, pid, quantity)["candidates"]} for pid in weights}
    out = []
    for p in ds.candidates(drug_id):
        n = p.ndc11
        parts = {pid: per_payer[pid][n] for pid in weights}
        if all(r["computable"] for r in parts.values()):
            expected = sum(weights[pid] * parts[pid]["margin"] for pid in weights)
            out.append({"ndc": format11(n), "ndc11": n, "labeler": p.labeler_name, "package": f"{p.package_size:g} {p.unit}",
                        "expected_margin": round(expected, 2), "computable": True,
                        "by_payer": {pid: parts[pid]["margin"] for pid in weights},
                        "flags": sorted({f["code"] for r in parts.values() for f in r["flags"]})})
        else:
            blocked = {pid: [f["code"] for f in r["flags"] if f["severity"] == BLOCKING] for pid, r in parts.items() if not r["computable"]}
            out.append({"ndc": format11(n), "ndc11": n, "labeler": p.labeler_name, "package": f"{p.package_size:g} {p.unit}",
                        "expected_margin": None, "computable": False, "blocked_by": blocked})
    out.sort(key=lambda r: (not r["computable"], -(r["expected_margin"] or 0)))
    return {"drug": ds.describe_drug(drug_id), "payer_mix": {k: round(v, 4) for k, v in weights.items()},
            "quantity": quantity, "best_ndc": out[0]["ndc"] if out and out[0]["computable"] else None,
            "candidates": out, "synthetic_data": ds.synthetic}


def rate_audit(ds: Dataset, payer_id: str) -> dict:
    """Cross-check one payer's contracted MAC rates against other payers' MAC for the
    same drug, and against the public NADAC rate, so a suspiciously low (or just
    stale/mistyped) MAC surfaces before it quietly erodes margin on every fill.

    Deterministic, same as every other number in this module -- no model involved.
    Severities reuse BLOCKING/WARNING/INFO, but nothing here blocks a ranking; it is
    a standing data-quality and fairness check a human reviews on its own.
    """
    payer = ds.payers.get(payer_id)
    if payer is None:
        raise KeyError(f"unknown payer_id {payer_id!r}; known: {sorted(ds.payers)}")
    mac_table = payer.get("mac_per_unit") or {}
    uses_mac = payer["formula"] in ("mac_plus_fee", "lesser_of_awp_discount_or_mac")
    checks: list[dict] = []

    if uses_mac:
        peer_tables = [(pid, p["id"], p["name"], p.get("mac_per_unit") or {})
                       for pid, p in ds.payers.items() if pid != payer_id and p.get("mac_per_unit")]
        for drug_id, mac in mac_table.items():
            peers = [(pid, name, t[drug_id]) for pid, _, name, t in peer_tables if drug_id in t]
            if peers:
                peer_id_best, peer_name, peer_mac = max(peers, key=lambda x: x[2])
                if peer_mac > 0 and mac <= MAC_PEER_RATIO * peer_mac:
                    ratio = mac / peer_mac
                    checks.append({"drug_id": drug_id, "code": "mac_below_peer_payers", "severity": WARNING,
                        "this_mac": mac, "peer_payer_id": peer_id_best, "peer_payer_name": peer_name, "peer_mac": peer_mac,
                        "ratio": round(ratio, 3),
                        "message": f"{drug_id}: MAC is {mac:g}/unit, only {ratio:.0%} of {peer_name}'s MAC ({peer_mac:g}/unit) for the same drug."})

            cands = ds.candidates(drug_id)
            nadac_rates = [ds.nadac[p.ndc11].per_unit for p in cands
                           if p.ndc11 in ds.nadac and ds.nadac[p.ndc11].pricing_unit == p.unit]
            if nadac_rates:
                floor = min(nadac_rates)
                if floor > 0 and mac <= MAC_NADAC_FLOOR_RATIO * floor:
                    checks.append({"drug_id": drug_id, "code": "mac_below_nadac_floor", "severity": WARNING,
                        "this_mac": mac, "nadac_floor": floor, "ratio": round(mac / floor, 3),
                        "message": f"{drug_id}: MAC {mac:g}/unit is below {MAC_NADAC_FLOOR_RATIO:.0%} of the public NADAC rate ({floor:g}/unit) -- confirm this contract term."})

        for drug_id in sorted(ds.drugs()):
            if drug_id not in mac_table:
                checks.append({"drug_id": drug_id, "code": "no_mac_on_file", "severity": INFO,
                    "message": f"{drug_id}: no MAC on file for this payer; pricing falls back to this payer's other formula terms, or is blocked."})

    order = {BLOCKING: 0, WARNING: 1, INFO: 2}
    checks.sort(key=lambda c: (order[c["severity"]], c["drug_id"]))
    return {"payer": {k: payer[k] for k in ("id", "name", "formula")}, "uses_mac": uses_mac, "checks": checks,
            "summary": {"drugs_on_file": len(mac_table), "flagged": sum(1 for c in checks if c["severity"] != INFO)}}
