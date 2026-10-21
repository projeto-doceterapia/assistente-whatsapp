from __future__ import annotations

from pathlib import Path

from pydantic import ValidationError

from app.evaluation.manifest import Manifesto, TipoEntrada


class ManifestError(ValueError):
    """Raised when a manifest file is missing, malformed or internally inconsistent."""


def carregar_manifesto(caminho: Path) -> Manifesto:
    """Load and validate an evaluation manifest from a JSON file.

    Validates the pydantic schema and a few cross-field rules the schema alone
    cannot express: unique case ids, a text body for text cases and an image
    path for image cases, and category ids referenced by expected new items
    existing in the fixture.
    """
    if not caminho.is_file():
        raise ManifestError(f"manifest not found: {caminho}")
    try:
        manifesto = Manifesto.model_validate_json(caminho.read_text(encoding="utf-8"))
    except ValidationError as exc:
        raise ManifestError(f"invalid manifest {caminho}: {exc}") from exc

    _validar_casos(manifesto)
    return manifesto


def _validar_casos(manifesto: Manifesto) -> None:
    categorias_validas = {c.nome for c in manifesto.catalogo.categorias}
    insumos_validos = {i.idInsumo for i in manifesto.catalogo.insumos}
    vistos: set[str] = set()

    for caso in manifesto.casos:
        if caso.id in vistos:
            raise ManifestError(f"duplicate case id: {caso.id}")
        vistos.add(caso.id)

        if caso.tipo is TipoEntrada.TEXTO and not caso.texto:
            raise ManifestError(f"case {caso.id}: tipo=texto requires 'texto'")
        if caso.tipo is TipoEntrada.IMAGEM and not caso.imagem:
            raise ManifestError(f"case {caso.id}: tipo=imagem requires 'imagem'")

        for esperado in caso.itens_esperados:
            if esperado.insumo_id is not None and esperado.insumo_id not in insumos_validos:
                raise ManifestError(
                    f"case {caso.id}: expected insumo_id {esperado.insumo_id} not in fixture"
                )
            if (
                esperado.categoria_nome is not None
                and esperado.categoria_nome not in categorias_validas
            ):
                raise ManifestError(
                    f"case {caso.id}: expected category '{esperado.categoria_nome}' "
                    "not in fixture"
                )
