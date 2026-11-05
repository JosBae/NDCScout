"""NDC (National Drug Code) parsing and normalization.

An NDC identifies a drug *package*: labeler (who markets it), product (drug,
strength, form) and package (size/type). FDA publishes NDCs as 10 digits in one
of three hyphenated layouts:

    4-4-2   e.g. 1234-5678-90
    5-3-2   e.g. 12345-678-90
    5-4-1   e.g. 12345-6789-0

Pharmacy claims (NCPDP) and CMS files (NADAC) use the 11-digit "HIPAA" form,
always 5-4-2, made by left-padding whichever segment is short:

    4-4-2 -> 0 + labeler        1234-5678-90  -> 01234-5678-90
    5-3-2 -> 0 + product        12345-678-90  -> 12345-0678-90
    5-4-1 -> 0 + package        12345-6789-0  -> 12345-6789-00

A bare 10-digit string without hyphens is ambiguous (you cannot tell which
segment to pad), so it is rejected rather than guessed. Joining files on the
wrong NDC form is the most common reason pharmacy price lookups silently miss.
"""
from __future__ import annotations

import re

_HYPHENATED = re.compile(r"^(\d{4,5})-(\d{3,4})-(\d{1,2})$")


class NDCError(ValueError):
    """The input is not a usable NDC; the message says why."""


def normalize(raw: str) -> str:
    """Return the 11-digit NDC (digits only) for any accepted input form.

    Accepts: 11 digits, 11 digits hyphenated 5-4-2, or a hyphenated 10-digit
    NDC in 4-4-2 / 5-3-2 / 5-4-1 layout. Rejects un-hyphenated 10-digit codes.
    """
    if raw is None:
        raise NDCError("NDC is empty")
    s = str(raw).strip().replace(" ", "")
    if not s:
        raise NDCError("NDC is empty")
    m = _HYPHENATED.match(s)
    if m:
        lab, prod, pkg = m.groups()
        shape = (len(lab), len(prod), len(pkg))
        if shape == (5, 4, 2):
            return lab + prod + pkg
        if shape == (4, 4, 2):
            return "0" + lab + prod + pkg
        if shape == (5, 3, 2):
            return lab + "0" + prod + pkg
        if shape == (5, 4, 1):
            return lab + prod + "0" + pkg
        raise NDCError(f"{raw!r} has segment lengths {shape}; expected 4-4-2, 5-3-2, 5-4-1 or 5-4-2")
    digits = re.sub(r"\D", "", s)
    if s != digits:
        raise NDCError(f"{raw!r} contains characters other than digits and hyphens in an unknown layout")
    if len(digits) == 11:
        return digits
    if len(digits) == 10:
        raise NDCError(
            f"{raw!r} is a 10-digit NDC without hyphens, which is ambiguous "
            "(4-4-2, 5-3-2 and 5-4-1 pad differently). Pass it with hyphens or as 11 digits."
        )
    raise NDCError(f"{raw!r} has {len(digits)} digits; an NDC has 10 (hyphenated) or 11")


def format11(ndc11: str) -> str:
    """11-digit NDC -> '12345-6789-01' for display."""
    n = normalize(ndc11)
    return f"{n[:5]}-{n[5:9]}-{n[9:]}"


def product_code(ndc11: str) -> str:
    """The labeler+product part (first 9 digits): same drug/strength/form, any package."""
    return normalize(ndc11)[:9]


def is_valid(raw: str) -> bool:
    try:
        normalize(raw)
        return True
    except NDCError:
        return False
