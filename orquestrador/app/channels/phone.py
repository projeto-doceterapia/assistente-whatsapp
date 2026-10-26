"""Phone-number matching helpers, pure and side-effect free.

The Baileys channel yields a sender as digits only (the JID stripped of its
domain and device suffix), while the configured beneficiary number may be an
E.164 string with a plus sign. Comparing the raw strings would miss a
legitimate beneficiary, so matching is done on the digit strings.

WhatsApp also registers some older Brazilian mobile accounts without the ninth
digit that people type in their number, so the same person can appear as
"55DD9XXXXXXXX" or "55DDXXXXXXXX". These two Brazilian mobile forms are treated
as the same person; everything else must match exactly.
"""

from __future__ import annotations

_BR_COUNTRY_CODE = "55"
_BR_MOBILE_LEN_WITH_NINTH = 13
_BR_MOBILE_LEN_WITHOUT_NINTH = 12
_BR_NINTH_INDEX = 4


def digits(value: str) -> str:
    """Return only the decimal digits of ``value`` (drops '+', spaces, '@')."""
    return "".join(ch for ch in value if ch.isdigit())


def same_phone(a: str, b: str) -> bool:
    """Whether two phone references identify the same person.

    Compares the digit strings. Brazilian mobile numbers are additionally
    treated as equal with or without the ninth digit. Empty references never
    match.
    """
    da = digits(a)
    db = digits(b)
    if not da or not db:
        return False
    if da == db:
        return True
    return _br_mobile_equivalent(da, db) or _br_mobile_equivalent(db, da)


def _br_mobile_equivalent(with_ninth: str, without_ninth: str) -> bool:
    """Whether a 13-digit BR mobile equals a 12-digit one missing the ninth.

    The 13-digit form is "55" + two area-code digits + "9" + the subscriber
    number; dropping that leading "9" must yield the 12-digit form.
    """
    if len(with_ninth) != _BR_MOBILE_LEN_WITH_NINTH:
        return False
    if len(without_ninth) != _BR_MOBILE_LEN_WITHOUT_NINTH:
        return False
    if not with_ninth.startswith(_BR_COUNTRY_CODE):
        return False
    if with_ninth[_BR_NINTH_INDEX] != "9":
        return False
    dropped = with_ninth[:_BR_NINTH_INDEX] + with_ninth[_BR_NINTH_INDEX + 1 :]
    return dropped == without_ninth
