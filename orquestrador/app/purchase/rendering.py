from __future__ import annotations

from pydantic import BaseModel

from app.core.models import CatalogoItem

# Default category name for supplies the AI cannot classify. Kept as a
# constant so validation and rendering agree.
CATEGORIA_PADRAO = "A classificar"


class Categoria(BaseModel):
    """A supply category as read from the core (`GET /categorias-insumo`)."""

    idCategoriaInsumo: int
    nome: str


# The unit conversion table, rendered once into the prompt so the AI knows which
# units are compatible. Conversion itself is done in code.
_TABELA_UNIDADES = (
    "Unidades e conversões:\n"
    "- KG <-> G (1 kg = 1000 g)\n"
    "- L <-> ML (1 l = 1000 ml)\n"
    "- UNIDADE só com UNIDADE"
)


def render_catalogo(itens: list[CatalogoItem]) -> str:
    """Render catalog items as compact `id|nome|unidade` lines."""
    return "\n".join(f"{item.idInsumo}|{item.nome}|{item.unidade.value}" for item in itens)


def render_categorias(categorias: list[Categoria]) -> str:
    """Render categories as compact `id|nome` lines."""
    return "\n".join(f"{c.idCategoriaInsumo}|{c.nome}" for c in categorias)


def render_contexto(
    itens: list[CatalogoItem],
    categorias: list[Categoria],
    total: int,
    parcial: bool,
) -> str:
    """Build the full catalog context block injected into the prompt.

    Includes the compact catalog, categories, the unit table once, and, when the
    catalog is partial, a notice with the total so the AI knows to return search
    terms instead of inventing ids.
    """
    blocos = []
    if parcial:
        blocos.append(
            f"CATÁLOGO PARCIAL: mostrando {len(itens)} de {total} insumos. "
            "Para itens que não encontrar aqui, preencha termos_busca."
        )
    else:
        blocos.append(f"CATÁLOGO COMPLETO: {total} insumos.")
    blocos.append("Insumos (id|nome|unidade):\n" + render_catalogo(itens))
    blocos.append("Categorias (id|nome):\n" + render_categorias(categorias))
    blocos.append(_TABELA_UNIDADES)
    return "\n\n".join(blocos)
