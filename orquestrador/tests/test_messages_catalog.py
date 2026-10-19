"""Snapshot tests for the PT-BR message catalog.

Each rendered message is compared against a plain-text file under
tests/snapshots/. Set WRITE_SNAPSHOTS=1 to (re)generate them after an
intentional wording change, then review the diff before committing.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from app.messages import (
    ConfirmationItem,
    FailedItem,
    ItemMarker,
    RecordedItem,
    assistant_unavailable,
    extraction_failed,
    final_failure,
    final_partial,
    final_success,
    help_text,
    invoice_already_registered,
    invoice_partial,
    new_purchase_while_pending,
    nothing_pending,
    reading_ack,
    recording_wait,
    render_confirmation,
    retry_window_expired,
    third_adjustment,
    unsupported_type,
)

_SNAPSHOT_DIR = Path(__file__).parent / "snapshots"

_THREE_ITEMS = [
    ConfirmationItem(nome="Arroz", marker=ItemMarker.CONFIRM, calculo="2 × 5 kg = 10 kg"),
    ConfirmationItem(nome="Feijão", marker=ItemMarker.CONFIRM, calculo="3 kg"),
    ConfirmationItem(
        nome="Farinha de mandioca",
        marker=ItemMarker.NEW,
        calculo="1 kg",
        categoria="Farinhas",
    ),
]

_CLARIFY_ITEMS = [
    ConfirmationItem(nome="Açúcar", marker=ItemMarker.CONFIRM, calculo="2 kg"),
    ConfirmationItem(
        nome="Produto genérico",
        marker=ItemMarker.CLARIFY,
        duvida="você comprou 2 kg de quê? Preciso saber o nome do produto",
    ),
]

_PARTIAL_INVOICE_ITEMS = [
    ConfirmationItem(nome="Leite condensado", marker=ItemMarker.CONFIRM, calculo="6 × 395 g = 2.370 g"),
]

_RECORDED = [
    RecordedItem(nome="Arroz", calculo="10 kg"),
    RecordedItem(nome="Feijão", calculo="3 kg"),
    RecordedItem(nome="Açúcar", calculo="5 kg"),
]

_FAILED_RETRYABLE = [FailedItem(nome="Óleo", motivo="o sistema estava fora do ar")]
_FAILED_NOT_RETRYABLE = [
    FailedItem(nome="Item inválido", motivo="não passou na validação", retryable=False),
]

# (name, rendered text)
_CASES = [
    ("reading_ack", reading_ack()),
    ("confirmation_three_items", render_confirmation(_THREE_ITEMS)),
    ("confirmation_clarify", render_confirmation(_CLARIFY_ITEMS)),
    ("invoice_already_registered", invoice_already_registered()),
    ("invoice_partial", invoice_partial(_PARTIAL_INVOICE_ITEMS)),
    ("recording_wait", recording_wait()),
    ("final_success", final_success(_RECORDED)),
    ("final_partial_retryable", final_partial(_RECORDED, _FAILED_RETRYABLE)),
    ("final_partial_not_retryable", final_partial(_RECORDED, _FAILED_NOT_RETRYABLE)),
    ("final_failure", final_failure()),
    ("third_adjustment", third_adjustment()),
    ("unsupported_type", unsupported_type()),
    ("extraction_failed", extraction_failed()),
    ("assistant_unavailable", assistant_unavailable()),
    ("nothing_pending", nothing_pending()),
    ("new_purchase_while_pending", new_purchase_while_pending()),
    ("retry_window_expired", retry_window_expired()),
    ("help_text", help_text()),
]


@pytest.mark.parametrize("name, rendered", _CASES, ids=[c[0] for c in _CASES])
def test_catalog_snapshots(name: str, rendered: str):
    path = _SNAPSHOT_DIR / f"{name}.txt"

    if os.environ.get("WRITE_SNAPSHOTS") == "1":
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(rendered, encoding="utf-8", newline="\n")

    # Messages never contain a carriage return, so normalize the stored file's
    # line endings: with core.autocrlf a Windows checkout turns the LF
    # snapshots into CRLF, which must still match the LF-rendered string.
    expected = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    assert rendered == expected


def test_summary_comes_before_the_question():
    text = render_confirmation(_THREE_ITEMS)
    resumo_idx = text.index("Entendi esta compra:")
    pergunta_idx = text.index("?")
    assert resumo_idx < pergunta_idx


def test_partial_offers_retry_only_when_retryable():
    com_retry = final_partial(_RECORDED, _FAILED_RETRYABLE)
    sem_retry = final_partial(_RECORDED, _FAILED_NOT_RETRYABLE)

    assert "tenta de novo" in com_retry
    assert "tenta de novo" not in sem_retry
