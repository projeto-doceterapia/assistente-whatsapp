"""The parameters the back-end stores for each notification, rendered for real.

`contracts/notificacao-parametros.json` is a copy of the file the back-end test
suite serializes against. A template that stops matching those names would send
an empty section to the owner without raising anything, so it fails here.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.templates import load_default_renderer

_CONTRACT = json.loads(
    (Path(__file__).parent / "contracts" / "notificacao-parametros.json").read_text(
        encoding="utf-8"
    )
)

_EXPECTED_PARTS: dict[str, list[str]] = {
    "resumo_inicio_dia": [
        "Resumo do início do dia (16/11/2026)",
        "Sinais a conferir: Pedido 42 - Cliente Exemplo",
        "Pagamentos finais a conferir: nada pendente",
        "Entregas de hoje: Pedido 40 - Cliente Teste | Pedido 41",
        "Pedidos atrasados: nada pendente",
    ],
    "resumo_fim_dia": [
        "Resumo do fim do dia (16/11/2026)",
        "Pedidos a produzir nos próximos dias: 18/11: Pedido 43 - Cliente Exemplo",
        "Aguardando sinal: 18/11: Pedido 44 - Cliente Teste",
        "Insumos a comprar: Chocolate meio amargo: 0.8 KG (mínimo 2 KG)",
    ],
    "estoque_baixo": ["Estoque baixo: Chocolate meio amargo está em 0.8 (mínimo 2.5)"],
    "dia_lotado": ["Dia lotado: 20/11/2026 atingiu o limite com 6 pedidos"],
}


@pytest.fixture
def renderer():
    return load_default_renderer()


@pytest.mark.parametrize("key", sorted(_CONTRACT))
def test_contract_names_match_template_placeholders(renderer, key: str) -> None:
    assert set(_CONTRACT[key]) == renderer.required_parameters(key)


@pytest.mark.parametrize("key", sorted(_CONTRACT))
def test_contract_payload_renders_readable_parts(renderer, key: str) -> None:
    assert renderer.render_parts(key, _CONTRACT[key]) == _EXPECTED_PARTS[key]


def test_every_back_end_template_is_covered() -> None:
    assert set(_CONTRACT) == set(_EXPECTED_PARTS)
