"""Builds the final result message from per-item recording outcomes.

Maps the aggregate `RecordingResult` onto the PT-BR catalog: full success,
partial (recorded first, then failures, with the retry offer only when a failed
item is retryable), or total failure. The retry-offer wording itself lives in
the catalog.
"""

from __future__ import annotations

from app.messages import (
    FailedItem,
    RecordedItem,
    final_failure,
    final_partial,
    final_success,
)
from app.recording.models import RecordingResult


def final_message(result: RecordingResult) -> str:
    if result.all_ok:
        return final_success(_recorded(result))
    if result.none_ok:
        return final_failure()
    return final_partial(_recorded(result), _failed(result))


def _recorded(result: RecordingResult) -> list[RecordedItem]:
    return [
        RecordedItem(nome=item.nome, calculo=item.calculo or None)
        for item in result.recorded
    ]


def _failed(result: RecordingResult) -> list[FailedItem]:
    return [
        FailedItem(
            nome=item.nome,
            motivo=item.motivo or "não foi possível registrar",
            retryable=item.retryable,
        )
        for item in result.failed
    ]
