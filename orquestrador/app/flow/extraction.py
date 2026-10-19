"""Extraction step: a raw purchase message becomes a validated draft.

For an image it preprocesses the photo, decodes the invoice access key from the
NFC-e QR / DANFE barcode, asks the AI to read the purchase, then validates and
consolidates the lines in code. For text it skips the
media and key steps. The step performs I/O (channel, core, LLM) but holds no
conversation state; the handler owns that.
"""

from __future__ import annotations

import io

from PIL import Image

from app.channel.port import InboundMessage, MessageKind
from app.core.models import CatalogoItem, CatalogoPagina, OrigemMovimentacao
from app.flow.models import Draft, DraftItem, FlowDeps
from app.invoice import decode_image, resolve_access_key
from app.invoice.models import DecodedCode
from app.llm.port import ImageInput
from app.llm.retry import retry_transient
from app.media.preprocess import MediaError, preprocess_image
from app.purchase.rendering import Categoria
from app.purchase.validacao import consolidar, validar_item


class UnsupportedPurchaseError(RuntimeError):
    """The message is neither a usable photo nor text (audio, video, sticker)."""


class ExtractionStep:
    """Turns an inbound purchase into a consolidated, validated draft."""

    def __init__(self, deps: FlowDeps) -> None:
        self._deps = deps

    async def extract(
        self, message: InboundMessage, categorias: list[Categoria], fluxo_id: str
    ) -> Draft:
        image, chave = await self._read_media(message)
        text = message.text if message.kind is MessageKind.TEXT else None
        if image is None and not text:
            raise UnsupportedPurchaseError("no usable purchase content")

        pagina = await self._deps.core.listar_catalogo()
        resultado = await retry_transient(
            lambda: self._deps.extracao.extrair(
                pagina, categorias, text=text, image=image
            ),
            self._deps.llm_retry,
            self._deps.llm_retry_sleep,
        )

        if chave is None:
            # A key read by the AI is accepted only through modulo-11.
            chave = resolve_access_key([], resultado.chave_acesso).key

        itens = validar_draft(resultado.itens, pagina, categorias)
        origem = (
            OrigemMovimentacao.NOTA_IMAGEM
            if image is not None
            else OrigemMovimentacao.TEXTO
        )
        return Draft(
            fluxo_id=fluxo_id,
            chave_acesso=chave,
            origem=origem,
            itens=itens,
            raw_itens=list(resultado.itens),
        )

    async def _read_media(
        self, message: InboundMessage
    ) -> tuple[ImageInput | None, str | None]:
        if message.kind is not MessageKind.IMAGE or not message.media_ref:
            if message.kind is MessageKind.UNSUPPORTED:
                raise UnsupportedPurchaseError("unsupported message type")
            return None, None
        raw = await self._deps.channel.fetch_media(message.media_ref)
        try:
            limpa = preprocess_image(raw, self._deps.preprocess_config)
        except MediaError as exc:
            raise UnsupportedPurchaseError(str(exc)) from exc
        chave = _decode_key(limpa.data)
        return self._deps.image_input(limpa.data, limpa.mime), chave


def validar_draft(
    itens_ai: list,
    pagina: CatalogoPagina,
    categorias: list[Categoria],
) -> list[DraftItem]:
    """Validate and consolidate AI lines into indexed draft items.

    Shared by extraction and by the adjustment step, which re-validates the
    merged raw lines against the same catalog and categories.
    """
    catalogo: dict[int, CatalogoItem] = {item.idInsumo: item for item in pagina.itens}
    categorias_por_id = {c.idCategoriaInsumo: c for c in categorias}
    validados = [validar_item(item, catalogo, categorias_por_id) for item in itens_ai]
    consolidados = consolidar(validados)
    return [DraftItem.from_validado(i, item) for i, item in enumerate(consolidados)]


def _decode_key(data: bytes) -> str | None:
    """Decode the invoice access key from the cleaned image bytes.

    A decoded QR/barcode wins over the AI key, so this runs before extraction's
    fallback. Decoding failures leave the key to the AI path.
    """
    try:
        with Image.open(io.BytesIO(data)) as image:
            image.load()
            codes: list[DecodedCode] = decode_image(image).codes
    except (OSError, ValueError):
        return None
    return resolve_access_key(codes).key
