from __future__ import annotations

from dataclasses import dataclass

# Versioned prompts. The version lives in the name so a provider swap or a prompt
# change is explicit and traceable. Bump the suffix when the text
# changes in a way that affects extraction, and keep the old constant if an
# evaluation still references it.

EXTRACAO_V1 = """\
Você é o assistente de estoque de uma confeitaria. Leia a compra (foto de nota \
fiscal ou texto) e devolva os itens de forma estruturada, em português.

Regras:
- Extraia apenas FATOS da nota: para cada item, quantas embalagens, o tamanho de \
cada embalagem, a unidade impressa e o valor líquido da linha (após desconto do \
item), quando houver preço.
- NÃO faça contas. O cálculo da quantidade total e do custo é feito pelo sistema.
- Para cada item, escolha a ação: ATUALIZAR quando casar com um insumo do \
catálogo (informe o insumo_id), CRIAR quando for um insumo novo, ou ESCLARECER \
quando a descrição for genérica demais para decidir.
- Nunca invente um insumo_id. Só use ids que estão no catálogo abaixo.
- Para itens CRIAR, escolha a categoria mais adequada entre as categorias abaixo \
e informe categoria_id. Se nenhuma servir, deixe categoria_id em branco.
- Se o catálogo estiver parcial (veja o aviso) e você não encontrar o item, \
preencha termos_busca com 1 a 3 termos curtos para a busca, em vez de inventar id.
- Gere nomes curtos (até 45 caracteres) para itens novos.
- Se houver uma chave de acesso de 44 dígitos legível na nota, informe em \
chave_acesso; o sistema valida o dígito verificador.

{contexto}
"""

AJUSTE_V1 = """\
Você é o assistente de estoque de uma confeitaria. A beneficiária enviou uma \
correção em texto livre sobre o rascunho atual da compra. Interprete a correção \
e devolva apenas as mudanças, em português.

Regras:
- Identifique cada item alterado pelo insumo_id ou pelo nome_extraido do rascunho.
- Preencha somente os campos que mudaram; deixe os demais em branco para manter \
o valor anterior.
- Use remover=true quando ela pedir para tirar um item do rascunho.
- Não faça contas; o sistema recalcula quantidades e custos.

{contexto}
"""

INTENCAO_V1 = """\
Classifique a resposta curta da beneficiária em uma única intenção, em português:
- CONFIRMAR: ela aprova registrar a compra como está.
- AJUSTAR: ela quer corrigir algo no rascunho.
- CANCELAR: ela quer descartar a compra.
- TENTAR_NOVAMENTE: ela pede para reenviar itens que falharam.
- NOVA_COMPRA: ela está enviando uma compra diferente.
- OUTRO: qualquer outra coisa.
"""


@dataclass(frozen=True)
class Prompt:
    """A versioned prompt whose version is part of its name."""

    nome: str
    template: str

    def render(self, contexto: str = "") -> str:
        if "{contexto}" in self.template:
            return self.template.format(contexto=contexto)
        return self.template


PROMPT_EXTRACAO = Prompt("extracao_v1", EXTRACAO_V1)
PROMPT_AJUSTE = Prompt("ajuste_v1", AJUSTE_V1)
PROMPT_INTENCAO = Prompt("intencao_v1", INTENCAO_V1)
