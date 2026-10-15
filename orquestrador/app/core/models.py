from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import Enum

from pydantic import BaseModel


class UnidadeMedida(str, Enum):
    KG = "KG"
    G = "G"
    L = "L"
    ML = "ML"
    UNIDADE = "UNIDADE"


class OrigemMovimentacao(str, Enum):
    MANUAL = "MANUAL"
    NOTA_IMAGEM = "NOTA_IMAGEM"
    TEXTO = "TEXTO"


class TipoMovimentacao(str, Enum):
    SALDO_INICIAL = "SALDO_INICIAL"
    ENTRADA_COMPRA = "ENTRADA_COMPRA"
    AJUSTE = "AJUSTE"


class StatusNotaFiscal(str, Enum):
    INEXISTENTE = "INEXISTENTE"
    REGISTRADA = "REGISTRADA"


class StatusEnvioNotificacao(str, Enum):
    PENDENTE = "PENDENTE"
    ENVIADA = "ENVIADA"
    FALHA = "FALHA"
    NAO_ENVIAR = "NAO_ENVIAR"


class CatalogoItem(BaseModel):
    idInsumo: int
    nome: str
    unidade: UnidadeMedida


class CatalogoPagina(BaseModel):
    itens: list[CatalogoItem]
    total: int
    proximoCursor: str | None = None


class ObterOuCriarRequest(BaseModel):
    nome: str
    unidade: UnidadeMedida
    fkCategoriaInsumo: int | None = None


class Insumo(BaseModel):
    idInsumo: int
    nome: str
    unidade: UnidadeMedida | None = None


class ObterOuCriarResultado(BaseModel):
    insumo: Insumo
    criado: bool


class NotaFiscalStatus(BaseModel):
    status: StatusNotaFiscal
    insumoIds: list[int]


class MovimentoRequest(BaseModel):
    insumoId: int
    quantidadeInformada: Decimal
    unidadeInformada: UnidadeMedida
    origem: OrigemMovimentacao
    valorLiquido: Decimal | None = None
    chaveNota: str | None = None
    fluxoId: str | None = None
    ocorridoEm: datetime | None = None


class MovimentoResultado(BaseModel):
    idMovimentacao: int
    insumoId: int
    tipo: TipoMovimentacao
    quantidade: Decimal
    saldoAtual: Decimal
    duplicado: bool
    custoUnitario: Decimal | None = None


class AgendaItem(BaseModel):
    idNotificacao: int
    janelaInicio: datetime
    janelaFim: datetime
    templateKey: str
    parametros: str | None = None


class ConfirmacaoEnvioRequest(BaseModel):
    statusEnvio: StatusEnvioNotificacao
    canalMessageId: str | None = None
