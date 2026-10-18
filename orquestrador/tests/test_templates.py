from __future__ import annotations

import pytest

from app.templates import (
    MissingTemplateParameterError,
    TemplateNotFoundError,
    load_default_renderer,
)

_KEYS = [
    "resumo_inicio_dia",
    "resumo_fim_dia",
    "estoque_baixo",
    "dia_lotado",
    "compra_registrada",
    "alerta_operacional",
]

_FULL_PARAMS: dict[str, dict[str, object]] = {
    "resumo_inicio_dia": {
        "data": "14/06/2026",
        "sinais_pendentes": ["Pedido #10 - Ana", "Pedido #11 - Bia"],
        "pagamentos_finais": ["Pedido #7 - Caio"],
        "entregas_hoje": ["Pedido #12 - Dora"],
        "pedidos_atrasados": ["Pedido #3 - Edu"],
    },
    "resumo_fim_dia": {
        "data": "14/06/2026",
        "pedidos_proximos": ["15/06: Pedido #20", "17/06: Pedido #21"],
        "aguardando_sinal": ["Pedido #22 - Fabi"],
        "insumos_comprar": ["Açúcar: 1 kg (mín. 2 kg)", "Farinha: 0,5 kg (mín. 3 kg)"],
    },
    "estoque_baixo": {"insumo": "Açúcar", "saldo": "1 kg", "minimo": "2 kg"},
    "dia_lotado": {"data": "20/06/2026", "pedidos": 8},
    "compra_registrada": {"itens": ["2 kg de açúcar", "1 L de leite"]},
    "alerta_operacional": {"mensagem": "Sessão do WhatsApp caiu."},
}


@pytest.fixture
def renderer():
    return load_default_renderer()


def test_all_expected_keys_exist(renderer) -> None:
    assert set(renderer.keys()) == set(_KEYS)


@pytest.mark.parametrize("key", _KEYS)
def test_text_mode_renders_multiline(renderer, key: str) -> None:
    text = renderer.render_text(key, _FULL_PARAMS[key])
    assert text.strip()
    if key.startswith("resumo_"):
        assert "\n" in text  # summaries are multi-line in Baileys mode


@pytest.mark.parametrize("key", _KEYS)
def test_parts_mode_has_no_newlines(renderer, key: str) -> None:
    parts = renderer.render_parts(key, _FULL_PARAMS[key])
    assert parts
    for part in parts:
        assert "\n" not in part  # Cloud API template params forbid line breaks


@pytest.mark.parametrize("key", _KEYS)
def test_same_data_in_both_modes(renderer, key: str) -> None:
    # Every list item that appears in the multi-line text must also appear in
    # the joined, newline-free parts, so both channels carry the same data.
    params = _FULL_PARAMS[key]
    text = renderer.render_text(key, params)
    joined = " ".join(renderer.render_parts(key, params))
    for raw in params.values():
        values = raw if isinstance(raw, list) else [raw]
        for value in values:
            assert str(value) in text
            assert str(value) in joined


def test_empty_day_summary_marks_every_section(renderer) -> None:
    empty = {
        "data": "15/06/2026",
        "sinais_pendentes": [],
        "pagamentos_finais": [],
        "entregas_hoje": [],
        "pedidos_atrasados": [],
    }
    text = renderer.render_text("resumo_inicio_dia", empty)
    assert text.count("nada pendente") == 4
    parts = renderer.render_parts("resumo_inicio_dia", empty)
    assert " ".join(parts).count("nada pendente") == 4


def test_missing_section_is_treated_as_empty(renderer) -> None:
    # A section omitted from parametros still renders, as "nada pendente".
    text = renderer.render_text("resumo_fim_dia", {"data": "15/06/2026"})
    assert text.count("nada pendente") == 3


def test_missing_required_parameter_raises_clear_error(renderer) -> None:
    with pytest.raises(MissingTemplateParameterError) as exc:
        renderer.render("estoque_baixo", {"insumo": "Açúcar", "saldo": "1 kg"}, strict=True)
    assert exc.value.template_key == "estoque_baixo"
    assert exc.value.parameter == "minimo"
    assert "minimo" in str(exc.value)


def test_unknown_template_raises(renderer) -> None:
    with pytest.raises(TemplateNotFoundError):
        renderer.render("inexistente", {})


def test_list_items_joined_with_separator(renderer) -> None:
    parts = renderer.render_parts("compra_registrada", {"itens": ["a", "b", "c"]})
    assert "a | b | c" in " ".join(parts)
