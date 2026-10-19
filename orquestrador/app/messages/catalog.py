"""PT-BR owner-facing message catalog.

Every function returns a ready-to-send string. Text is PT-BR with correct
accents, short and conversational, never menu-like. The confirmation and the
final result put the purchase summary first so it reads at a glance.
"""

from __future__ import annotations

from collections.abc import Sequence

from app.messages.types import (
    ConfirmationItem,
    FailedItem,
    ItemMarker,
    RecordedItem,
)

_MARK_CONFIRM = "✔"
_MARK_NEW = "⚠"
_MARK_CLARIFY = "❓"


def reading_ack() -> str:
    """Início da leitura."""
    return "Recebi! Estou lendo, um momento…"


def render_confirmation(itens: Sequence[ConfirmationItem]) -> str:
    """Pedido de confirmação: summary first, then one short question.

    Each line is marked: ✔ known item, ⚠ new supply (with its category), ❓
    item that needs clarifying. When any item is new or needs clarifying the
    closing question changes to ask about it.
    """
    linhas = [_confirmation_line(item) for item in itens]
    resumo = "\n".join(linhas)

    tem_novo = any(item.marker is ItemMarker.NEW for item in itens)
    tem_duvida = any(item.marker is ItemMarker.CLARIFY for item in itens)

    if tem_duvida:
        pergunta = "Me conta o que falta pra eu entender direito?"
    elif tem_novo:
        pergunta = "Posso incluir o item novo e registrar? Responda 'sim' ou me diga o que corrigir."
    else:
        pergunta = "Está tudo certo? Responda 'sim' ou me diga o que corrigir."

    return f"Entendi esta compra:\n{resumo}\n\n{pergunta}"


def _confirmation_line(item: ConfirmationItem) -> str:
    if item.marker is ItemMarker.NEW:
        categoria = item.categoria or "A classificar"
        corpo = f"{item.nome} ({_quantidade(item.calculo)}) — não está no seu cadastro, incluir em {categoria}"
        return f"{_MARK_NEW} {corpo}"
    if item.marker is ItemMarker.CLARIFY:
        duvida = item.duvida or "preciso saber o nome do produto"
        return f"{_MARK_CLARIFY} {item.nome}: {duvida}"
    return f"{_MARK_CONFIRM} {item.nome}: {_quantidade(item.calculo)}"


def _quantidade(calculo: str | None) -> str:
    return calculo if calculo else "quantidade a confirmar"


def invoice_already_registered() -> str:
    """Nota já registrada."""
    return "Essa nota já foi registrada antes. Não fiz nenhuma alteração."


def invoice_partial(itens: Sequence[ConfirmationItem]) -> str:
    """Nota parcial: register only the pending items."""
    resumo = "\n".join(_confirmation_line(item) for item in itens)
    return (
        "Parte dessa nota já estava registrada. Vou registrar só o que falta:\n"
        f"{resumo}\n\n"
        "Pode confirmar? Responda 'sim' ou me diga o que corrigir."
    )


def recording_wait() -> str:
    """Aguardando (durante a gravação)."""
    return "Estou registrando, só um instante."


def final_success(itens: Sequence[RecordedItem]) -> str:
    """Sucesso: summary first."""
    resumo = "\n".join(f"{_MARK_CONFIRM} {_recorded_line(item)}" for item in itens)
    return f"Pronto! Atualizei seu estoque:\n{resumo}"


def final_partial(
    recorded: Sequence[RecordedItem], failed: Sequence[FailedItem]
) -> str:
    """Sucesso parcial: recorded summary first, then what failed with a retry
    offer when any failed item can be retried."""
    registrados = "\n".join(f"{_MARK_CONFIRM} {_recorded_line(item)}" for item in recorded)
    falhas = "\n".join(f"{_MARK_NEW} {item.nome}: {item.motivo}" for item in failed)
    corpo = f"Registrei estes itens:\n{registrados}\n\nNão consegui registrar:\n{falhas}"

    if any(item.retryable for item in failed):
        return f"{corpo}\n\nQuer que eu tente de novo? É só responder 'tenta de novo'."
    return (
        f"{corpo}\n\n"
        "Esses itens não dá pra repetir automaticamente. Pode me enviar de novo como uma nova compra."
    )


def final_failure() -> str:
    """Falha total."""
    return (
        "Não consegui registrar agora. Seus dados não foram alterados. "
        "Tente de novo mais tarde."
    )


def final_failure_retryable(failed: Sequence[FailedItem]) -> str:
    """Falha total com itens que ainda dá pra repetir: list them and offer the
    on-request retry."""
    falhas = "\n".join(f"{_MARK_NEW} {item.nome}: {item.motivo}" for item in failed)
    return (
        "Não consegui registrar agora. Seus dados não foram alterados.\n"
        f"{falhas}\n\n"
        "Quer que eu tente de novo? É só responder 'tenta de novo'."
    )


def _recorded_line(item: RecordedItem) -> str:
    if item.calculo:
        return f"{item.nome}: {item.calculo}"
    return item.nome


def third_adjustment() -> str:
    """Após o 3º ajuste."""
    return (
        "Não consegui entender direito. Pode enviar uma foto mais nítida da nota, "
        "ou escrever a compra com mais detalhes?"
    )


def unsupported_type() -> str:
    """Tipo não suportado."""
    return "Só consigo ler foto da nota fiscal ou texto da compra. Pode me enviar assim?"


def extraction_failed() -> str:
    """Leitura sem itens: pede uma foto mais nítida ou texto.

    Separate from the provider-unavailable message: here the assistant worked,
    it just could not read the purchase, so it asks for clearer input and raises
    no alert."""
    return (
        "Não consegui entender essa compra. Pode me mandar uma foto mais nítida "
        "da nota, ou escrever o que você comprou?"
    )


def assistant_unavailable() -> str:
    """Falha do provedor de IA após as tentativas.

    The assistant itself is unavailable; nothing was recorded and the owner is
    asked to try again later. This path feeds the AI-error alert."""
    return (
        "Opa, não consegui processar agora — tive um problema aqui do meu lado. "
        "Não registrei nada. Pode tentar de novo daqui a pouco?"
    )


def nothing_pending() -> str:
    """Sem fluxo pendente."""
    return (
        "Não tem nada pendente por aqui. Se quiser registrar uma compra, "
        "é só enviar a foto da nota ou escrever o que comprou."
    )


def new_purchase_while_pending() -> str:
    """Compra nova com fluxo pendente."""
    return (
        "Antes de começar outra, vamos terminar a compra anterior. "
        "Você quer confirmar ou cancelar?"
    )


def retry_window_expired() -> str:
    """Janela de novo envio expirada: handled as a new interaction."""
    return (
        "Os itens pendentes já expiraram. Se ainda quiser registrar, "
        "me envie a foto da nota ou escreva a compra de novo."
    )


def retry_started() -> str:
    """Pedido de 'tentar de novo' aceito: a nova tentativa começou."""
    return "Beleza! Vou tentar registrar de novo os itens que faltaram, um instante…"


def help_text() -> str:
    """Ajuda: what the assistant can do."""
    return (
        "Eu registro suas compras no estoque. Me envie a foto da nota fiscal "
        "ou escreva o que comprou, que eu leio e confirmo com você antes de salvar."
    )
