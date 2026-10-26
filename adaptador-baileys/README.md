# adaptador-baileys

Adaptador do WhatsApp (Node 22 + TypeScript) que mantém a sessão Baileys, filtra
as mensagens recebidas para um único grupo dedicado, encaminha eventos
normalizados ao orquestrador por um webhook assinado com HMAC e expõe uma
pequena API HTTP.

## Layout

- `src/config/` — configuração e logger.
- `src/http/` — servidor Fastify e contrato HTTP.
- `src/media/` — armazenamento de referências opacas de mídia.
- `src/webhook/` — encaminhador de saída e assinatura HMAC.
- `src/whatsapp/` — socket Baileys, allowlist, reconexão, tipos compartilhados.
- `test/` espelha `src/`; os dublês de teste compartilhados ficam em `test/support/`.

## Execução

```sh
npm ci
npm run build
npm start        # ou: node dist/main.js
npm test         # node:test
```

Docker:

```sh
docker build -t adaptador-baileys .
docker run --rm -e BAILEYS_ENABLED=false -p 8080:8080 adaptador-baileys
```

Defina `BAILEYS_ENABLED=false` para iniciar a camada HTTP sem uma conexão com o
WhatsApp (a verificação de saúde reporta `degraded`); isso é usado para verificar
a imagem.

## Variáveis de ambiente

| Variável | Obrigatória | Padrão | Descrição |
|---|---|---|---|
| `GRUPO_JID` | quando habilitado | — | JID do grupo dedicado; apenas suas mensagens são processadas |
| `ADAPTER_SECRET` | quando habilitado | — | Segredo compartilhado exigido nos endpoints HTTP de entrada |
| `WEBHOOK_URL` | quando habilitado | — | Endpoint do orquestrador para onde os eventos são encaminhados |
| `WEBHOOK_SECRET` | quando habilitado | — | Chave HMAC-SHA256 usada para assinar os eventos encaminhados |
| `SESSION_DIR` | não | `./session` (`/data/session` na imagem) | Diretório das credenciais da sessão Baileys |
| `BAILEYS_ENABLED` | não | `true` | Defina `false` para executar sem uma conexão com o WhatsApp |
| `MEDIA_TTL_MS` | não | `120000` | Tempo de vida das referências opacas de mídia |
| `MEDIA_MAX_BYTES` | não | `8388608` | Tamanho máximo de mídia (bytes); downloads maiores são recusados com `413` |
| `DISCOVER_GROUPS` | não | `false` | Defina `true` para registrar o id e o nome de cada grupo da conta vinculada uma vez por execução, para descobrir o valor de `GRUPO_JID` |
| `ACCEPT_OWN_MESSAGES` | não | `false` | Defina `true` para processar as mensagens enviadas pela própria conta vinculada no grupo dedicado; use apenas para teste com um único número |
| `PORT` | não | `8080` | Porta HTTP |
| `HOST` | não | `0.0.0.0` | Endereço HTTP de bind |
| `LOG_LEVEL` | não | `info` | Nível de log do pino |

O conteúdo das mensagens nunca é registrado. Apenas mídias `image/jpeg`,
`image/png` e `image/webp` são baixadas; qualquer outro tipo é recusado com
`415`. Os downloads são limitados a `MEDIA_MAX_BYTES` e abortados com `413`
quando esse limite é excedido. Enquanto a sessão não está pareada, a string do
QR é registrada para que o operador possa renderizá-la; mantenha os logs do
adaptador privados, pois eles identificam o chat vinculado.

## Como descobrir o id do grupo

`GRUPO_JID` deve ser o id do grupo dedicado (termina com `@g.us`). Para lê-lo,
pareie a sessão e então inicie o adaptador uma vez com `DISCOVER_GROUPS=true`.
Depois que a conexão abre, ele registra uma linha com a contagem de grupos e, em
seguida, uma linha por grupo da conta vinculada:

```
{"msg":"group discovery: group","jid":"1203...@g.us","subject":"Group name"}
```

Copie o `jid` do grupo dedicado para `GRUPO_JID`, depois remova
`DISCOVER_GROUPS` (ou defina `false`) e reinicie. Apenas o id e o assunto são
registrados, nunca mensagens ou participantes. A lista é requisitada uma vez por
processo, porque o WhatsApp pode aplicar rate limit a chamadas repetidas; deixe a
configuração desligada em operação normal e mantenha os logs privados.

## Teste com um único número

Por padrão, o adaptador descarta todas as mensagens enviadas pela conta
vinculada (`fromMe`), para que o assistente nunca processe as próprias
respostas. Isso impede um cenário comum de teste: um desenvolvedor pareia o
próprio número e escreve sozinho em um grupo dedicado que contém apenas ele;
como todas as mensagens são `fromMe`, nada é processado.

Defina `ACCEPT_OWN_MESSAGES=true` para habilitar o modo de teste. Com ele
ligado, qualquer mensagem digitada pela conta vinculada no grupo dedicado passa
a ser processada normalmente. Para evitar que o assistente responda a si mesmo
em loop, o adaptador registra o id de cada mensagem que ele próprio envia e
continua descartando esses ecos: apenas o que você digita é processado.

Este modo é apenas para teste com um único número; mantenha-o desligado em
operação normal.

## Contrato HTTP

Todos os endpoints, exceto `GET /health`, exigem o cabeçalho
`X-Adapter-Secret: <ADAPTER_SECRET>`.

| Método | Caminho | Corpo / Parâmetros | Resposta |
|---|---|---|---|
| `GET` | `/health` | — | `200 {"status":"connected\|degraded\|down"}` |
| `POST` | `/messages` | `{ "text": string }` | `202 {"status":"queued"}` (enviada ao grupo dedicado) |
| `GET` | `/media/{ref}` | `ref` opaco | stream de mídia `200`; `404` se desconhecido/expirado, `413` se acima do limite, `415` se não for uma imagem permitida |

Os eventos de entrada são enviados para `WEBHOOK_URL` como JSON, assinados com
HMAC-SHA256 no cabeçalho `X-Signature-256: sha256=<hex>` usando
`WEBHOOK_SECRET`. Cada evento é ou uma `message` normalizada (id, JID do
grupo/remetente, kind, texto, referência de mídia, timestamp) ou um evento
`session` (`connected`, `disconnected`, `logged_out`).
