# Conjunto de avaliação da IA

Conjunto versionado que mede a leitura de compras pela IA.
O runner roda o pipeline real de extração (`app/purchase` através de `LlmPort`),
compara com saídas esperadas escritas à mão e **reprova a troca de provedor** se
a acurácia por item ficar abaixo do limite (padrão 0,90).

Arquivos:

- `set.json` — 8 casos de texto em PT-BR com saída esperada e um catálogo-fixture
  de 25 insumos. Já contém respostas gravadas (`respostas_fake`) para rodar
  offline, sem provedor real.
- `images.json` — placeholder do conjunto de fotos de nota (sem casos ainda).
- `images/` — pasta onde o responsável coloca as 20 a 30 fotos de cupom.

## Como rodar

O serviço tem como alvo Python 3.12; rode dentro do container fixado:

```sh
# Offline, com as respostas gravadas (determinístico, não chama provedor):
docker run --rm -v "$PWD:/app" -w /app python:3.12.7-slim \
  sh -c "pip install -r requirements.txt && python -m app.evaluation --fake"

# Com Gemini (precisa de GEMINI_API_KEY e GEMINI_MODEL no ambiente):
docker run --rm -e GEMINI_API_KEY -e GEMINI_MODEL -v "$PWD:/app" -w /app \
  python:3.12.7-slim \
  sh -c "pip install -r requirements.txt && \
    python -m app.evaluation --provider gemini --report evaluation/report.json"
```

Sem `GEMINI_API_KEY` **nenhuma chamada real é feita**: o runner registra o
provedor como indisponível no relatório e sai com código diferente de zero. A
mesma observação vale para o relatório salvo.

Flags: `--manifest <caminho>` (padrão `evaluation/set.json`), `--images <pasta>`,
`--provider <nome>`, `--fake`, `--threshold <0-1>`, `--report <arquivo.json>`.
O processo sai com código 0 quando a acurácia por item atinge o limite e 1
quando fica abaixo.

O relatório traz: acurácia por item (casamento de item, unidade, quantidade e
custo dentro da tolerância), acurácia de categoria, latência p50/p95 e, quando o
provedor reportar uso de tokens, a estimativa de tokens/custo.

## Formato do manifesto

JSON validado por pydantic (`app/evaluation/manifest.py`). Campos principais:

- `versao`, `threshold` (0-1), `catalogo` (`insumos` e `categorias`) e `casos`.
- Cada caso: `id`, `descricao`, `tipo` (`texto` ou `imagem`), `texto` **ou**
  `imagem` (caminho relativo a `images/`), `chave_acesso_esperada` (opcional) e
  `itens_esperados`.
- Cada item esperado: `acao` (`ATUALIZAR`/`CRIAR`/`ESCLARECER`), `insumo_id`
  (quando `ATUALIZAR`), `nome`, `unidade`, `quantidade_total` (na unidade do
  insumo), `custo_unitario` (líquido, por unidade do insumo), `categoria_nome`
  (quando `CRIAR`) e `tolerancias` (`quantidade_abs`, `custo_unitario_abs`).
  Campos deixados como `null` não são pontuados.

Valores em texto decimal (`"38.90"`) para não perder precisão.

## Checklist: adicionar as fotos de nota (20 a 30)

As fotos são do responsável; este conjunto só traz o formato. Para cada foto:

1. **Anonimizar antes de salvar** (RGPD/LGPD): cubra ou borre CPF/CNPJ, nome do
   consumidor, endereço, telefone e qualquer QR Code/código de barras que leve a
   dados pessoais. Mantenha legíveis apenas itens, quantidades, unidades e
   valores. Não comite fotos com dados pessoais.
2. **Remover metadados**: salve sem EXIF (o pré-processamento do serviço já tira
   EXIF em produção, mas o arquivo versionado não deve conter geolocalização).
3. **Nome do arquivo**: `nota-<NN>.jpg` com `NN` de dois dígitos
   (`nota-01.jpg`, `nota-02.jpg`, ...), em `images/`. Use `.jpg`, `.png` ou
   `.webp`.
4. **Preencher o esperado** em `images.json`: acrescente um caso em `casos` com
   `tipo: "imagem"`, `imagem: "nota-01.jpg"` e a lista `itens_esperados` lida à
   mão da foto (um item por linha da nota; use `ESCLARECER` para linhas que você
   também não decidiria, e `CRIAR` com `categoria_nome` para insumos fora do
   catálogo-fixture). Ajuste o `catalogo` para conter os `insumo_id` que você
   referenciar. Se a nota tiver chave de acesso legível, informe
   `chave_acesso_esperada` (44 dígitos).
5. **Rodar**: `python -m app.evaluation --manifest evaluation/images.json`
   (com o provedor configurado) e confira acurácia, latência e custo no
   relatório. A meta é **>= 90% de acerto por item** no conjunto completo
   (texto + fotos) antes do piloto.

Modelo de um caso de imagem para copiar em `images.json`:

```json
{
  "id": "img-01",
  "descricao": "cupom térmico legível, itens no catálogo",
  "tipo": "imagem",
  "imagem": "nota-01.jpg",
  "chave_acesso_esperada": null,
  "itens_esperados": [
    {
      "acao": "ATUALIZAR",
      "insumo_id": 1,
      "unidade": "KG",
      "quantidade_total": "10",
      "custo_unitario": "3.8900"
    }
  ]
}
```
