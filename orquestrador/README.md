# orquestrador

Orquestrador do assistente de estoque do WhatsApp. Serviço Python 3.12 (FastAPI) que
conduz a conversa de compra, fala com a API central e mantém o estado da conversa no
Redis. É um consumidor da central; nunca acessa o banco de dados diretamente.

## Executar

```sh
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Verificação de saúde (healthcheck): `GET /health` retorna `{"status":"ok","version":"<v>"}`.

## Testar

O serviço tem como alvo o Python 3.12. Execute a suíte em um contêiner fixado:

```sh
docker run --rm -v "$PWD:/app" -w /app python:3.12.7-slim \
  sh -c "pip install -r requirements-dev.txt && python -m pytest -q"
```

`pytest-asyncio` é necessário porque o cliente da central e a camada de estado no Redis
são assíncronos (o serviço executa seus workers como tasks asyncio).

Os testes de estado no Redis precisam de um Redis real. Aponte `REDIS_TEST_URL` para um;
eles são ignorados de forma limpa quando não definido:

```sh
docker run -d --name redis-test -p 127.0.0.1::6379 redis:7
PORT=$(docker port redis-test 6379 | cut -d: -f2)
docker run --rm -e REDIS_TEST_URL="redis://host.docker.internal:$PORT/0" \
  --add-host=host.docker.internal:host-gateway -v "$PWD:/app" -w /app \
  python:3.12.7-slim sh -c "pip install -r requirements-dev.txt && python -m pytest -q"
```

## Construir a imagem

```sh
docker build -t orquestrador .
docker run --rm -p 8000:8000 --env-file .env orquestrador
```

## Configuração

Todas as configurações vêm de variáveis de ambiente. Nenhum valor é embutido na
imagem. Todos os nomes abaixo são lidos pelo código (`app/config.py`, `app/channels/settings.py`
e `app/llm/port.py`); a coluna `Segredo` marca as credenciais que nunca devem ser
registradas em log nem versionadas. Apenas `BENEFICIARIA_TELEFONE`, `CORE_BASE_URL` e
`REDIS_URL` são obrigatórias para o serviço iniciar; as configurações de canal e de LLM são
lidas de forma preguiçosa pelos componentes que as usam, então `/health` funciona sem elas.

| Variável | Obrigatória | Padrão | Segredo | Usado por |
|---|---|---|---|---|
| `CHANNEL` | não | `baileys` | não | seleção de canal (`baileys` ou `cloud_api`) |
| `BENEFICIARIA_TELEFONE` | sim | — | não | remetente autorizado, E.164 |
| `CORE_BASE_URL` | sim | — | não | URL base da API central |
| `REDIS_URL` | sim | — | não | URL de conexão do Redis |
| `CONV_TTL_MIN` | não | `30` | não | TTL de inatividade da conversa (minutos) |
| `MAX_AJUSTES` | não | `2` | não | ajustes em texto livre permitidos |
| `RETRY_MAX_TENTATIVAS` | não | `6` | não | tentativas de retry |
| `RETRY_BASE_S` | não | `2` | não | backoff base do retry (segundos) |
| `RETRY_CAP_S` | não | `300` | não | teto do backoff do retry (segundos) |
| `TZ` | não | `America/Sao_Paulo` | não | fuso horário |
| `ADAPTER_URL` | Baileys | — | não | URL base do adaptador Baileys |
| `ADAPTER_SECRET` | Baileys | — | sim | segredo compartilhado enviado como `X-Adapter-Secret` ao adaptador |
| `WEBHOOK_SECRET` | Baileys | — | sim | chave HMAC-SHA256 que verifica eventos de entrada do adaptador (`X-Signature-256`) |
| `CLOUD_API_PHONE_ID` | Cloud API | — | não | id do número de telefone da Cloud API |
| `CLOUD_API_TOKEN` | Cloud API | — | sim | token de acesso da Cloud API (Bearer) |
| `CLOUD_API_APP_SECRET` | Cloud API | — | sim | segredo do app que verifica `X-Hub-Signature-256` |
| `CLOUD_API_VERIFY_TOKEN` | Cloud API | — | sim | token de handshake de verificação do webhook |
| `CLOUD_API_GRAPH_VERSION` | não | `v23.0` | não | segmento de versão da Graph API |
| `CLOUD_API_MEDIA_HOSTS` | não | `.fbcdn.net,.facebook.com,.whatsapp.net` | não | sufixos de host separados por vírgula de onde a Cloud API pode baixar mídia (somente HTTPS); o download no CDN descarta o token bearer |
| `MEDIA_MAX_BYTES` | não | `8388608` | não | limite rígido do corpo de uma mídia baixada (bytes); o corpo é transmitido em stream e abortado acima desse tamanho |
| `LLM_PROVIDER` | não | `gemini` | não | seleção do provedor de LLM |
| `GEMINI_API_KEY` | Gemini | — | sim | chave da API do Gemini |
| `GEMINI_MODEL` | Gemini | — | não | id do modelo Gemini (ex.: `gemini-2.0-flash`) |

## Canais e templates de notificação

Dois canais intercambiáveis implementam a mesma `ChannelPort`
(`app/channel/port.py`): `BaileysChannel` (dispositivo vinculado não oficial, via a
API HTTP do `adaptador-baileys`) e `CloudApiChannel` (Meta WhatsApp Cloud API).
`CHANNEL` seleciona qual deles executa. Ambos passam na mesma suíte de contrato
(`tests/channel_contract.py`). Suas configurações são lidas de forma preguiçosa, apenas quando um
canal é construído, em `app/channels/settings.py`.

A mídia de entrada é baixada transmitindo a resposta em stream e abortando acima de
`MEDIA_MAX_BYTES`, de modo que um corpo grande demais nunca é totalmente armazenado em buffer. No fluxo
de duas etapas da Cloud API, o download no CDN roda em um cliente separado que não carrega nenhum
cabeçalho `Authorization` e só aceita URLs HTTPS cujo host corresponda a
`CLOUD_API_MEDIA_HOSTS`, de modo que o token bearer da Graph nunca é enviado ao host que a
primeira etapa retornou.

As notificações proativas são renderizadas a partir de uma única fonte,
`app/templates/templates.yaml`. O Baileys envia o texto renderizado em múltiplas linhas; a
Cloud API sempre envia o template aprovado com os mesmos dados como parâmetros do corpo.
A Cloud API proíbe quebras de linha dentro dos parâmetros do template, então as
seções em formato de lista são unidas em linha com ` | `. Seções vazias renderizam
`nada pendente` e nunca são omitidas.

A versão da Graph API e os formatos de payload foram verificados contra a documentação
oficial da Meta em 2026-10-20: as versões mais recentes são v24.0/v25.0/v26.0
(https://developers.facebook.com/docs/graph-api/changelog/versions); v23.0 é
o padrão fixado (suportado até 2027-10-08). Os formatos de payload de mensagem, template, webhook e
mídia seguem
https://developers.facebook.com/docs/whatsapp/cloud-api/reference/messages e
https://developers.facebook.com/docs/whatsapp/cloud-api/webhooks/payload-examples.

## Provedor de LLM (extração de compra)

O fluxo de compra extrai itens com um LLM por trás de `LlmPort`. O
primeiro provedor é o Gemini, usando o SDK oficial `google-genai`. Ele é selecionado
e configurado por `LLM_PROVIDER`, `GEMINI_API_KEY` e `GEMINI_MODEL`
(`app/llm/port.py`); os testes usam um provedor fake que reproduz respostas gravadas
e nunca chamam a rede.

O loop de busca no catálogo é limitado por duas configurações passadas ao `ExtracaoService`:
`rodadas-busca` (padrão 1) e o limite de candidatos por busca
(padrão 200).

Uma falha transitória do provedor (timeout, rede, 429, 5xx) durante a extração é
repetida brevemente com backoff exponencial antes de a dona ser avisada de que o assistente
está indisponível; uma leitura ilegível e um erro não transitório (400/401/403,
cota permanente) não são repetidos. Os limites vêm do ambiente:

| Variável | Obrigatória | Padrão | Segredo | Usado por |
|---|---|---|---|---|
| `LLM_MAX_TENTATIVAS` | não | `3` | não | total de tentativas de extração em erro transitório do provedor (1 chamada + retries) |
| `LLM_BACKOFF_BASE_S` | não | `1` | não | primeiro backoff antes de um retry transitório (segundos), dobrando a cada tentativa |

## Entrega de notificações

O pacote `app/delivery` entrega as notificações pendentes da central à
dona no WhatsApp. Um
único worker em segundo plano consulta `GET /notificacoes/agenda`, escolhe um horário de envio aleatório
dentro da janela de cada notificação na primeira vez em que ela é vista (persistido no
Redis, de modo que um restart o mantém), envia pela `ChannelPort` exatamente uma vez
(protegido por NX em `notif:{id}:sent`, de modo que duas instâncias nunca enviem em duplicidade) e
confirma o resultado em `POST /notificacoes/{id}/confirmacao-envio` com
backoff exponencial. Se a central estiver indisponível durante a confirmação, o estado enviado
é mantido para que a próxima consulta confirme sem reenviar. Quando uma janela fecha
antes de a notificação ser enviada, ela é reportada como `FALHA` e um alerta
operacional é disparado. Alertas imediatos usam uma janela curta, então com a consulta padrão de 30 s
eles chegam ao WhatsApp em cerca de um minuto.

A trava de envio é marcada com o horário da reivindicação (claim) e, quando o canal retorna um
id de mensagem, esse id é armazenado sob a claim com um compare-and-set, de modo que a
claim e o id permaneçam consistentes. Se o serviço quebrar entre reivindicar e
registrar o id, um worker novo trata a claim como 'send outcome unknown' assim que
ela for mais antiga que `DELIVERY_CLAIM_TTL_S`: ele reporta `FALHA` e dispara um
alerta operacional em vez de arriscar uma entrega duplicada com um reenvio. Dentro
desse TTL um envio ainda pode estar em andamento, então o worker aguarda e reverifica em uma
consulta posterior. A entrega é, portanto, no máximo uma vez.

O `main` conecta o worker por `create_delivery_worker(...)` (dependências
explícitas) e conduz seu ciclo de vida com `await worker.start()` /
`await worker.stop()`. Um `Clock` e um `RandomSource` são injetados, de modo que os testes sejam
determinísticos. Os alertas operacionais passam por uma porta `AlertRaiser` injetada;
quando nenhuma está conectada, o alerta é desabilitado.

| Variável | Obrigatória | Padrão | Segredo | Usado por |
|---|---|---|---|---|
| `DELIVERY_POLL_INTERVAL_S` | não | `30` | não | intervalo de consulta da agenda (segundos) |
| `DELIVERY_AGENDA_HORIZON_MIN` | não | `60` | não | quão à frente de agora o intervalo da agenda se estende (minutos) |
| `DELIVERY_AGENDA_LOOKBACK_MIN` | não | `60` | não | quão para trás o intervalo da agenda alcança, para que janelas abertas enquanto fora do ar ainda sejam entregues (minutos) |
| `DELIVERY_CONFIRM_BASE_S` | não | `2` | não | backoff base do retry de confirmação (segundos) |
| `DELIVERY_CONFIRM_CAP_S` | não | `60` | não | teto do backoff do retry de confirmação (segundos) |
| `DELIVERY_CONFIRM_MAX_TENTATIVAS` | não | `6` | não | tentativas de retry de confirmação por consulta |
| `DELIVERY_CLAIM_TTL_S` | não | `120` | não | por quanto tempo uma claim de envio sem id de mensagem do canal é tolerada antes de a recuperação tratá-la como 'send outcome unknown' (segundos) |

## Alertas operacionais e registro de compra

O pacote `app/ops` expõe problemas operacionais e registra compras.
Todo problema operacional vira uma notificação in-app
`ALERTA_OPERACIONAL` via `POST /notificacoes`, de modo que a dona a veja
no app web mesmo quando o WhatsApp é a parte que falha; quando o canal
reporta saúde, ela também é enviada no WhatsApp, e quando o canal está fora apenas
o alerta in-app é criado. Uma chave de cool-down por condição no Redis evita
tempestades de alerta (padrão 30 min), e os textos dos alertas informam apenas a
condição, nunca dados de recebimento nem segredos.

Um `HealthMonitor` em segundo plano consulta a sessão do WhatsApp (`ChannelPort.health()`),
o Redis (`ping`) e a central, contando falhas consecutivas da central para que um único soluço
não alerte. O worker de retry chama `OperationalAlerts.raise_dlq_item(item)`
quando move um item para a DLQ, e `HealthMonitor.record_ai_error()` rastreia
falhas repetidas da IA. O `main` inicia e para o monitor no lifespan do FastAPI
junto ao worker de entrega.

Após cada compra registrada total ou parcialmente, `PurchaseNotifier
.on_purchase_recorded(summary)` cria uma notificação in-app `COMPRA_REGISTRADA`
(apenas in-app; a central a define por padrão como `NAO_ENVIAR`). Ela é conectada
passando-a logo após o retorno de `Recorder.record(draft)` em
`app/flow/handler.py` (`_record`, após a linha 189), como o listener de compra do recorder;
é um best effort, então um erro da central ali nunca desfaz uma compra registrada.

O provedor de categorias de insumo do fluxo (`FlowDeps.categorias_provider`) é o
`CategoriasProvider` em cache: ele lê `GET /categorias-insumo` com um cache de TTL curto
e recorre à única categoria padrão `A classificar` quando a
central está fora e nada está em cache, de modo que a extração ainda prossiga.

| Variável | Obrigatória | Padrão | Segredo | Usado por |
|---|---|---|---|---|
| `OPS_ALERT_COOLDOWN_S` | não | `1800` | não | janela de dedup/cool-down de alerta por condição (segundos) |
| `OPS_CORE_FAILURE_THRESHOLD` | não | `3` | não | falhas consecutivas da central antes de um alerta de central-fora |
| `OPS_AI_FAILURE_THRESHOLD` | não | `3` | não | falhas consecutivas da IA antes de um alerta de erros-repetidos-da-IA |
| `OPS_CATEGORIAS_TTL_S` | não | `300` | não | TTL das categorias de insumo em cache (segundos) |
| `OPS_HEALTH_POLL_INTERVAL_S` | não | `60` | não | intervalo de consulta do monitor de saúde (segundos) |

## Retry de registro de item

O pacote `app/retry` repete itens de compra que falharam de forma transitória e conduz
a mensagem de resultado final. Após a confirmação, cada item é registrado por conta própria; uma falha
transitória (timeout, 5xx) ou um insumo ausente/inativo (404) é enfileirado no
sorted set do Redis `retry:queue`, pontuado pelo seu horário de próxima tentativa. Um único
`RetryWorker` em segundo plano consulta o conjunto e reproduz cada item devido **com a mesma
chave de idempotência** (`nota:{chave}:{insumoId}` ou `fluxo:{fluxoId}:{insumoId}`), de modo que
uma reprodução nunca duplique. O backoff é exponencial com **full jitter**
(`base * factor ** (attempt-1)`, limitado), e o processamento é single-flight por
item (uma claim NX em `retry:claim:{member}`), de modo que dois workers nunca reenviem o
mesmo item. Tudo vive no Redis, então um kill e restart do processo retoma a
agenda e registra os itens pendentes exatamente uma vez.

Quando as tentativas de um item se esgotam, ele vai para a DLQ (`dlq:items`), um
alerta operacional é disparado, e o item é marcado como elegível para um retry sob
demanda. Quando o último item retentável de uma compra se resolve, o worker envia uma
mensagem de resultado final (sucesso / parcial / falha, resumo primeiro) e limpa a
conversa. O contador de pendências por compra é decrementado em um único passo
no servidor, de modo que uma conclusão quebrada ou duplicada seja um no-op e a mensagem de
resultado final nunca possa ser enviada duas vezes.

Retry sob demanda: após um resultado final com falhas retentáveis, o bot
oferece "tenta de novo". Por uma janela configurável (padrão 24 h) uma intent
`TENTAR_NOVAMENTE` reproduz **apenas** os itens elegíveis — aqueles cujos
retries automáticos se esgotaram, ou que falharam porque o insumo estava ausente
ou inativo — com as mesmas chaves, e reporta um resultado novo. Uma nova compra ou
a expiração da janela descarta a oferta.

O `main` conecta o worker por `create_retry_worker(...)` / um `RetryCoordinator`
(o sink de retry do recorder) e conduz seu ciclo de vida com `await worker.start()`
/ `await worker.stop()`. Um `Clock` e um `RandomSource` são injetados, de modo que a
agenda de backoff seja determinística nos testes.

| Variável | Obrigatória | Padrão | Segredo | Usado por |
|---|---|---|---|---|
| `RETRY_POLL_INTERVAL_S` | não | `5` | não | intervalo de consulta da fila de retry (segundos) |
| `RETRY_BASE_S` | não | `2` | não | base do backoff (segundos) |
| `RETRY_FACTOR` | não | `2` | não | fator de crescimento do backoff |
| `RETRY_CAP_S` | não | `300` | não | teto do backoff (segundos) |
| `RETRY_MAX_TENTATIVAS` | não | `6` | não | tentativas por item antes da DLQ |
| `RETRY_REQUEST_WINDOW_H` | não | `24` | não | janela de 'tenta de novo' sob demanda (horas) |

## Provedor de LLM scripted (testes offline)

Para execuções de integração e ponta a ponta sem uma chave real nem a rede, defina
`LLM_PROVIDER=scripted`. O provedor então reproduz respostas JSON gravadas
a partir do arquivo nomeado por `FAKE_LLM_FIXTURES` (indexado por nome de schema e texto
da requisição; veja `app/llm/scripted.py`). Ele nunca é usado pelo caminho de produção
do Gemini.

| Variável | Obrigatória | Padrão | Segredo | Usado por |
|---|---|---|---|---|
| `LLM_PROVIDER` | não | `gemini` | não | defina como `scripted` para reproduzir respostas gravadas |
| `FAKE_LLM_FIXTURES` | scripted | — | não | caminho para o arquivo JSON de fixtures scripted |