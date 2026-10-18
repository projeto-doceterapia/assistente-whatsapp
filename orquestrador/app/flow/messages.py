"""Flow-local PT-BR texts not covered by the shared message catalog.

The shared catalog (`app.messages`) owns every owner-facing message. The
cancellation acknowledgement is only produced by the flow, so it lives here to
keep the catalog untouched. Text is PT-BR with correct accents, short and
conversational, matching the catalog's tone.
"""

from __future__ import annotations


def cancel_confirmed() -> str:
    """Confirma o cancelamento e descarta o rascunho."""
    return "Cancelei essa compra. Quando quiser, é só me enviar outra."
