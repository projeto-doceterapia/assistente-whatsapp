"""Unit tests for the pure phone-matching helpers."""

from __future__ import annotations

import hashlib
import hmac
import json

import httpx

from app.channels.baileys import BaileysChannel
from app.channels.phone import digits, same_phone
from app.channels.settings import BaileysSettings


def test_digits_keeps_only_digits() -> None:
    assert digits("+55 (11) 99999-9999") == "5511999999999"
    assert digits("5511999999999@s.whatsapp.net") == "5511999999999"
    assert digits("") == ""


def test_plus_sign_vs_none_match() -> None:
    assert same_phone("+5511999999999", "5511999999999")
    assert same_phone("5511999999999", "+5511999999999")


def test_ninth_digit_missing_both_directions() -> None:
    # 13-digit (with ninth) equals the 12-digit form missing that ninth digit.
    assert same_phone("5511999999999", "551199999999")
    assert same_phone("551199999999", "5511999999999")


def test_non_brazilian_numbers_compare_exactly() -> None:
    assert same_phone("+1202555019", "1202555019")
    # A non-BR number differing only by a stray leading 9 must not match.
    assert not same_phone("19202555019", "1202555019")


def test_different_numbers_are_false() -> None:
    assert not same_phone("5511999999999", "5511888888888")


def test_empty_never_matches() -> None:
    assert not same_phone("", "5511999999999")
    assert not same_phone("5511999999999", "")
    assert not same_phone("", "")


def test_different_area_code_is_false() -> None:
    # Same subscriber tail but different area code (11 vs 21) is a different line.
    assert not same_phone("5511999999999", "552199999999")


def test_group_like_id_is_false() -> None:
    # WhatsApp group JIDs normalize to a long digit run (e.g. 18 digits); it is
    # never the beneficiary and must not match a mobile number.
    assert not same_phone("120363021234567890", "5511999999999")


def test_ninth_digit_rule_requires_leading_nine() -> None:
    # A 13-digit number whose fifth digit is not 9 is not the ninth-digit form,
    # so dropping a digit must not make it equal to a 12-digit number.
    assert not same_phone("5511899999999", "551199999999")


_WEBHOOK_SECRET = "webhook-secret"
_BENEFICIARIA = "+5511999999999"


def _parsed_sender(sender_jid: str) -> str:
    """Run a signed adapter event through the channel and return its sender."""
    channel = BaileysChannel(
        BaileysSettings(
            adapter_url="http://adapter",
            adapter_secret="adapter-secret",
            webhook_secret=_WEBHOOK_SECRET,
        ),
        httpx.AsyncClient(base_url="http://adapter"),
    )
    body = json.dumps(
        {
            "event": "message",
            "id": "m1",
            "groupJid": "120363000000000000@g.us",
            "senderJid": sender_jid,
            "fromMe": False,
            "kind": "text",
            "text": "oi",
            "timestamp": 1790000000000,
        }
    ).encode()
    digest = hmac.new(_WEBHOOK_SECRET.encode(), body, hashlib.sha256).hexdigest()
    message = channel.verify_and_parse(body, f"sha256={digest}")
    assert message is not None
    return message.sender


def test_adapter_sender_forms_reach_the_beneficiary_match() -> None:
    # What the adapter can send for a group author, traced through the channel.
    assert same_phone(_parsed_sender("5511999999999@s.whatsapp.net"), _BENEFICIARIA)
    assert same_phone(_parsed_sender("5511999999999:12@s.whatsapp.net"), _BENEFICIARIA)
    assert same_phone(_parsed_sender("551199999999@s.whatsapp.net"), _BENEFICIARIA)


def test_lid_and_group_ids_never_match_the_beneficiary() -> None:
    assert not same_phone(_parsed_sender("227318203441233@lid"), _BENEFICIARIA)
    assert not same_phone(_parsed_sender("120363000000000000@g.us"), _BENEFICIARIA)