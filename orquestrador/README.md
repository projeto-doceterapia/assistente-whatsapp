# orquestrador

WhatsApp stock assistant orchestrator. Python 3.12 service (FastAPI) that drives
the purchase conversation, talks to the core API and keeps conversation state in
Redis. It is one consumer of the core; it never touches the database directly.

## Run

```sh
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Health check: `GET /health` returns `{"status":"ok","version":"<v>"}`.

## Test

The service targets Python 3.12. Run the suite in a pinned container:

```sh
docker run --rm -v "$PWD:/app" -w /app python:3.12.7-slim \
  sh -c "pip install -r requirements-dev.txt && python -m pytest -q"
```

`pytest-asyncio` is required because the core client and the Redis state layer
are async (the service runs its workers as asyncio tasks).

The Redis state tests need a real Redis. Point `REDIS_TEST_URL` at one; they
skip cleanly when it is unset:

```sh
docker run -d --name redis-test -p 127.0.0.1::6379 redis:7
PORT=$(docker port redis-test 6379 | cut -d: -f2)
docker run --rm -e REDIS_TEST_URL="redis://host.docker.internal:$PORT/0" \
  --add-host=host.docker.internal:host-gateway -v "$PWD:/app" -w /app \
  python:3.12.7-slim sh -c "pip install -r requirements-dev.txt && python -m pytest -q"
```

## Build the image

```sh
docker build -t orquestrador .
docker run --rm -p 8000:8000 --env-file .env orquestrador
```

## Configuration

All settings come from environment variables. No value is baked into
the image. Every name below is read by the code (`app/config.py`, `app/channels/settings.py`
and `app/llm/port.py`); the `Secret` column flags credentials that must never be
logged or committed. Only `BENEFICIARIA_TELEFONE`, `CORE_BASE_URL` and
`REDIS_URL` are required for the service to start; channel and LLM settings are
read lazily by the components that use them, so `/health` works without them.

| Variable | Required | Default | Secret | Used by |
|---|---|---|---|---|
| `CHANNEL` | no | `baileys` | no | channel selection (`baileys` or `cloud_api`) |
| `BENEFICIARIA_TELEFONE` | yes | — | no | authorized sender, E.164 |
| `CORE_BASE_URL` | yes | — | no | core API base URL |
| `REDIS_URL` | yes | — | no | Redis connection URL |
| `CONV_TTL_MIN` | no | `30` | no | conversation inactivity TTL (minutes) |
| `MAX_AJUSTES` | no | `2` | no | allowed free-text adjustments |
| `RETRY_MAX_TENTATIVAS` | no | `6` | no | retry attempts |
| `RETRY_BASE_S` | no | `2` | no | retry base backoff (seconds) |
| `RETRY_CAP_S` | no | `300` | no | retry backoff cap (seconds) |
| `TZ` | no | `America/Sao_Paulo` | no | time zone |
| `ADAPTER_URL` | Baileys | — | no | Baileys adapter base URL |
| `ADAPTER_SECRET` | Baileys | — | yes | shared secret sent as `X-Adapter-Secret` to the adapter |
| `WEBHOOK_SECRET` | Baileys | — | yes | HMAC-SHA256 key verifying inbound adapter events (`X-Signature-256`) |
| `CLOUD_API_PHONE_ID` | Cloud API | — | no | Cloud API phone number id |
| `CLOUD_API_TOKEN` | Cloud API | — | yes | Cloud API access token (Bearer) |
| `CLOUD_API_APP_SECRET` | Cloud API | — | yes | app secret that verifies `X-Hub-Signature-256` |
| `CLOUD_API_VERIFY_TOKEN` | Cloud API | — | yes | webhook verification handshake token |
| `CLOUD_API_GRAPH_VERSION` | no | `v23.0` | no | Graph API version segment |
| `CLOUD_API_MEDIA_HOSTS` | no | `.fbcdn.net,.facebook.com,.whatsapp.net` | no | comma-separated host suffixes the Cloud API may download media from (HTTPS only); the CDN download drops the bearer token |
| `MEDIA_MAX_BYTES` | no | `8388608` | no | hard cap on a downloaded media body (bytes); the body is streamed and aborted above this size |
| `LLM_PROVIDER` | no | `gemini` | no | LLM provider selection |
| `GEMINI_API_KEY` | Gemini | — | yes | Gemini API key |
| `GEMINI_MODEL` | Gemini | — | no | Gemini model id (e.g. `gemini-2.0-flash`) |

## Channels and notification templates

Two interchangeable channels implement the same `ChannelPort`
(`app/channel/port.py`): `BaileysChannel` (unofficial linked device, via the
`adaptador-baileys` HTTP API) and `CloudApiChannel` (Meta WhatsApp Cloud API).
`CHANNEL` selects which one runs. Both pass the same contract suite
(`tests/channel_contract.py`). Their settings are read lazily, only when a
channel is built, in `app/channels/settings.py`.

Inbound media is downloaded by streaming the response and aborting above
`MEDIA_MAX_BYTES`, so an oversized body is never fully buffered. For the Cloud
API two-step flow, the CDN download runs on a separate client that carries no
`Authorization` header and only accepts HTTPS URLs whose host matches
`CLOUD_API_MEDIA_HOSTS`, so the Graph bearer token is never sent to the host the
first step returned.

Proactive notifications are rendered from a single source,
`app/templates/templates.yaml`. Baileys sends the multi-line rendered text;
Cloud API always sends the approved template with the same data as body
parameters. Cloud API forbids line breaks inside template parameters, so
list-shaped sections are joined inline with ` | `. Empty sections render
`nada pendente` and are never omitted.

Graph API version and payload shapes were verified against Meta's official
documentation on 2026-10-20: the latest versions are v24.0/v25.0/v26.0
(https://developers.facebook.com/docs/graph-api/changelog/versions); v23.0 is
the pinned default (supported until 2027-10-08). Message, template, webhook and
media payload shapes follow
https://developers.facebook.com/docs/whatsapp/cloud-api/reference/messages and
https://developers.facebook.com/docs/whatsapp/cloud-api/webhooks/payload-examples.

## LLM provider (purchase extraction)

The purchase flow extracts items with an LLM behind `LlmPort`. The
first provider is Gemini, using the official `google-genai` SDK. It is selected
and configured through `LLM_PROVIDER`, `GEMINI_API_KEY` and `GEMINI_MODEL`
(`app/llm/port.py`); tests use a fake provider that replays recorded responses
and never call the network.

The catalog search loop is capped by two settings passed to `ExtracaoService`:
`rodadas-busca` (default 1) and the per-search candidate limit
(default 200).
