from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from app.evaluation import __main__ as cli
from app.evaluation.loader import ManifestError, carregar_manifesto
from app.evaluation.manifest import (
    CasoAvaliacao,
    CatalogoFixture,
    ItemEsperado,
    Manifesto,
    TipoEntrada,
)
from app.evaluation.runner import executar
from app.evaluation.scoring import pontuar_caso
from app.core.models import CatalogoItem, UnidadeMedida
from app.llm.schemas import AcaoItem
from app.purchase.rendering import Categoria
from app.purchase.validacao import ItemValidado

SET_JSON = Path(__file__).resolve().parents[1] / "evaluation" / "set.json"
IMAGES = Path(__file__).resolve().parents[1] / "evaluation" / "images"


def _item_validado(**kwargs: object) -> ItemValidado:
    base: dict[str, object] = {
        "acao": AcaoItem.ATUALIZAR,
        "insumo_id": 1,
        "nome": "Farinha de trigo",
        "unidade": UnidadeMedida.KG,
        "quantidade_total": Decimal("10"),
        "calculo": "",
        "custo_unitario": Decimal("3.8900"),
    }
    base.update(kwargs)
    return ItemValidado(**base)


def test_loader_reads_the_real_set() -> None:
    manifesto = carregar_manifesto(SET_JSON)
    assert manifesto.versao == "1"
    assert len(manifesto.casos) == 8
    assert all(c.id in manifesto.respostas_fake for c in manifesto.casos)


def test_loader_rejects_unknown_insumo_id(tmp_path: Path) -> None:
    ruim = tmp_path / "ruim.json"
    ruim.write_text(
        '{"versao":"x","catalogo":{"categorias":[{"idCategoriaInsumo":1,"nome":"A"}],'
        '"insumos":[{"idInsumo":1,"nome":"X","unidade":"KG"}]},'
        '"casos":[{"id":"c1","descricao":"d","tipo":"texto","texto":"t",'
        '"itens_esperados":[{"acao":"ATUALIZAR","insumo_id":99}]}]}',
        encoding="utf-8",
    )
    with pytest.raises(ManifestError, match="insumo_id 99"):
        carregar_manifesto(ruim)


def test_scoring_counts_a_matching_item_as_hit() -> None:
    esperado = ItemEsperado(
        acao=AcaoItem.ATUALIZAR,
        insumo_id=1,
        unidade=UnidadeMedida.KG,
        quantidade_total=Decimal("10"),
        custo_unitario=Decimal("3.8900"),
    )
    pontuacao = pontuar_caso([esperado], [_item_validado()])
    assert pontuacao.acertos == 1
    assert pontuacao.denominador == 1


def test_scoring_misses_when_quantity_outside_tolerance() -> None:
    esperado = ItemEsperado(
        acao=AcaoItem.ATUALIZAR,
        insumo_id=1,
        quantidade_total=Decimal("10"),
    )
    computado = _item_validado(quantidade_total=Decimal("9"))
    pontuacao = pontuar_caso([esperado], [computado])
    assert pontuacao.acertos == 0


def test_scoring_penalizes_extra_computed_item() -> None:
    esperado = ItemEsperado(acao=AcaoItem.ATUALIZAR, insumo_id=1)
    extra = _item_validado(insumo_id=2, nome="Açúcar refinado")
    pontuacao = pontuar_caso([esperado], [_item_validado(), extra])
    # One hit, but the spurious line enlarges the denominator, so accuracy < 1.
    assert pontuacao.acertos == 1
    assert pontuacao.denominador == 2


async def test_runner_passes_on_the_real_text_set() -> None:
    manifesto = carregar_manifesto(SET_JSON)
    relatorio = await executar(
        manifesto, IMAGES, usar_fake=True, provider_nome="fake"
    )
    assert relatorio.acuracia_por_item == 1.0
    assert relatorio.aprovado is True
    assert relatorio.acuracia_categoria == 1.0
    assert relatorio.uso.disponivel is False


def _manifesto_abaixo_do_limite() -> Manifesto:
    # A wrong expected quantity makes the single case miss, driving accuracy to 0.
    catalogo = CatalogoFixture(
        insumos=[CatalogoItem(idInsumo=1, nome="Farinha de trigo", unidade=UnidadeMedida.KG)],
        categorias=[Categoria(idCategoriaInsumo=1, nome="A classificar")],
    )
    caso = CasoAvaliacao(
        id="c1",
        descricao="espera quantidade errada de propósito",
        tipo=TipoEntrada.TEXTO,
        texto="2 sacos de 5kg de farinha de trigo por 38,90",
        itens_esperados=[
            ItemEsperado(
                acao=AcaoItem.ATUALIZAR,
                insumo_id=1,
                quantidade_total=Decimal("999"),
            )
        ],
    )
    recorded = (
        '{"itens":[{"acao":"ATUALIZAR","insumo_id":1,"nome_extraido":"Farinha",'
        '"quantidade_embalagens":2,"tamanho_embalagem":5,"unidade_embalagem":"KG",'
        '"valor_liquido":38.90,"termos_busca":[]}],"chave_acesso":null}'
    )
    return Manifesto(
        versao="test",
        catalogo=catalogo,
        casos=[caso],
        respostas_fake={"c1": recorded},
    )


async def test_runner_fails_below_threshold() -> None:
    manifesto = _manifesto_abaixo_do_limite()
    relatorio = await executar(
        manifesto, IMAGES, usar_fake=True, provider_nome="fake"
    )
    assert relatorio.acuracia_por_item == 0.0
    assert relatorio.aprovado is False


def test_cli_exit_zero_when_set_passes() -> None:
    codigo = cli.main(["--manifest", str(SET_JSON), "--fake"])
    assert codigo == 0


def test_cli_exit_nonzero_below_threshold(tmp_path: Path) -> None:
    manifesto = _manifesto_abaixo_do_limite()
    caminho = tmp_path / "falha.json"
    caminho.write_text(manifesto.model_dump_json(), encoding="utf-8")
    codigo = cli.main(["--manifest", str(caminho), "--fake"])
    assert codigo == 1
