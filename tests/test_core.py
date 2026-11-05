import dataclasses
import json

import pytest

from ndc_scout import engine
from ndc_scout.data import load
from ndc_scout.ndc import NDCError, format11, normalize


@pytest.fixture(scope="module")
def ds():
    return load()


# ---------------------------------------------------------------- NDC format
@pytest.mark.parametrize("raw,expected", [
    ("1234-5678-90", "01234567890"),     # 4-4-2 pads labeler
    ("12345-678-90", "12345067890"),     # 5-3-2 pads product
    ("12345-6789-0", "12345678900"),     # 5-4-1 pads package
    ("12345-6789-01", "12345678901"),    # already 5-4-2
    ("12345678901", "12345678901"),
    (" 12345-6789-01 ", "12345678901"),
])
def test_normalize(raw, expected):
    assert normalize(raw) == expected


@pytest.mark.parametrize("raw", ["1234567890", "123-45-6", "abc", "", "123456789012", "12345-67890-1"])
def test_normalize_rejects(raw):
    with pytest.raises(NDCError):
        normalize(raw)


def test_format11():
    assert format11("99101030130") == "99101-0301-30"


# ---------------------------------------------------------------- engine math
def test_price_fill_nadac_math(ds):
    r = engine.price_fill(ds, "99101030130", "medicaid-nadac", 30)
    # NADAC 0.0412*30 = 1.236 + fee 10.50 = 11.736 ; acquisition 2.95/90*30 = 0.98333 ; margin 10.7527
    assert r["reimbursement"] == 11.74
    assert r["acquisition_cost"] == 0.98
    assert r["margin"] == 10.75
    assert r["confidence"] == "high"


def test_lesser_of_uses_mac_when_lower(ds):
    r = engine.price_fill(ds, "99102044190", "pbm-awp-lesser", 30)
    # AWP 1.42*(1-0.84)=0.2272 > MAC 0.19 -> MAC binds
    assert r["basis"]["binding"] == "mac"
    assert r["ingredient_cost"] == round(0.19 * 30, 2)
    assert any(f["code"] == "mac_capped" for f in r["flags"])


def test_best_ndc_depends_on_payer(ds):
    nadac = engine.rank(ds, "atorvastatin-40mg-tab", "medicaid-nadac", 30)["best_ndc"]
    awp = engine.rank(ds, "atorvastatin-40mg-tab", "partd-awp", 30)["best_ndc"]
    assert nadac == "99103-0711-05"   # cheapest to buy per unit
    assert awp == "99102-0441-90"     # highest AWP
    assert nadac != awp


@pytest.mark.parametrize("ndc,payer,code", [
    ("99102056601", "medicaid-nadac", "no_nadac"),
    ("99103043318", "medicaid-nadac", "unit_mismatch"),
    ("99103023301", "pbm-mac", "not_in_catalog"),
])
def test_blocking_flags_make_row_uncomputable(ds, ndc, payer, code):
    r = engine.price_fill(ds, ndc, payer, 30)
    assert not r["computable"] and r["margin"] is None and r["confidence"] == "none"
    assert code in [f["code"] for f in r["flags"]]


def test_warning_flags(ds):
    stale = engine.price_fill(ds, "99104002210", "pbm-mac", 30)
    assert "stale_catalog_price" in [f["code"] for f in stale["flags"]] and stale["confidence"] == "medium"
    disc = engine.price_fill(ds, "99104013301", "pbm-mac", 30)
    assert "discontinuing" in [f["code"] for f in disc["flags"]]


def test_rank_puts_uncomputable_last(ds):
    r = engine.rank(ds, "metformin-500mg-tab", "medicaid-nadac", 60)
    assert r["candidates"][-1]["ndc11"] == "99102056601"
    assert r["candidates"][-1]["rank"] is None
    margins = [c["margin"] for c in r["candidates"] if c["computable"]]
    assert margins == sorted(margins, reverse=True)


def test_rank_mix_is_weighted_average(ds):
    mix = {"medicaid-nadac": 1, "pbm-mac": 3}
    r = engine.rank_mix(ds, "atorvastatin-40mg-tab", mix, 30)
    row = next(c for c in r["candidates"] if c["ndc11"] == "99101030130")
    a = engine.price_fill(ds, "99101030130", "medicaid-nadac", 30)["margin"]
    b = engine.price_fill(ds, "99101030130", "pbm-mac", 30)["margin"]
    assert row["expected_margin"] == round(0.25 * a + 0.75 * b, 2)


def test_rank_mix_blocks_if_any_payer_blocks(ds):
    r = engine.rank_mix(ds, "albuterol-hfa-90mcg", {"medicaid-nadac": 1, "pbm-mac": 1}, 8.5)
    blocked = next(c for c in r["candidates"] if c["ndc11"] == "99103043318")
    assert not blocked["computable"] and blocked["blocked_by"] == {"medicaid-nadac": ["unit_mismatch"]}


def test_errors(ds):
    with pytest.raises(KeyError):
        engine.rank(ds, "nope", "pbm-mac", 30)
    with pytest.raises(KeyError):
        engine.rank(ds, "atorvastatin-40mg-tab", "nope", 30)
    with pytest.raises(ValueError):
        engine.price_fill(ds, "99101030130", "pbm-mac", 0)


# ---------------------------------------------------------------- rate audit
def test_rate_audit_flags_peer_gap(ds):
    r = engine.rate_audit(ds, "pbm-mac")
    assert r["uses_mac"] is True
    f = next(c for c in r["checks"] if c["code"] == "mac_below_peer_payers" and c["drug_id"] == "atorvastatin-40mg-tab")
    assert f["peer_payer_id"] == "pbm-awp-lesser"
    assert f["this_mac"] == 0.052 and f["peer_mac"] == 0.19
    assert f["ratio"] == round(0.052 / 0.19, 3)


def test_rate_audit_non_mac_payer_is_clean(ds):
    r = engine.rate_audit(ds, "medicaid-nadac")
    assert r["uses_mac"] is False
    assert r["checks"] == []


def test_rate_audit_unknown_payer(ds):
    with pytest.raises(KeyError):
        engine.rate_audit(ds, "nope")


def test_rate_audit_nadac_floor(ds):
    table = {**ds.payers["pbm-mac"]["mac_per_unit"], "atorvastatin-40mg-tab": 0.01}
    patched = dataclasses.replace(ds, payers={**ds.payers, "pbm-mac": {**ds.payers["pbm-mac"], "mac_per_unit": table}})
    r = engine.rate_audit(patched, "pbm-mac")
    f = next(c for c in r["checks"] if c["code"] == "mac_below_nadac_floor" and c["drug_id"] == "atorvastatin-40mg-tab")
    assert f["nadac_floor"] == 0.0412


def test_rate_audit_no_mac_on_file(ds):
    table = dict(ds.payers["pbm-mac"]["mac_per_unit"]); del table["sertraline-50mg-tab"]
    patched = dataclasses.replace(ds, payers={**ds.payers, "pbm-mac": {**ds.payers["pbm-mac"], "mac_per_unit": table}})
    r = engine.rate_audit(patched, "pbm-mac")
    assert any(c["code"] == "no_mac_on_file" and c["drug_id"] == "sertraline-50mg-tab" for c in r["checks"])
    assert r["summary"]["drugs_on_file"] == len(table)


def test_search(ds):
    assert ds.search("atorvastatin 40")[0]["drug_id"] == "atorvastatin-40mg-tab"
    assert ds.search("99101-0301-30")[0]["matched_ndc"] == "99101030130"
    assert ds.search("zzz") == []
    json.dumps(ds.search("albuterol"))  # serializable
