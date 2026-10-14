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

## Build the image

```sh
docker build -t orquestrador .
docker run --rm -p 8000:8000 --env-file .env orquestrador
```

## Configuration

All settings come from environment variables. No value is baked into
the image.

| Variable | Required | Default | Used by |
|---|---|---|---|
| `CHANNEL` | no | `baileys` | channel selection (`baileys` or `cloud_api`) |
| `BENEFICIARIA_TELEFONE` | yes | — | authorized sender, E.164 |
| `ADAPTER_URL` | no (Baileys) | — | Baileys adapter base URL |
| `ADAPTER_SECRET` | no (Baileys) | — | HMAC secret for the internal webhook |
| `GRUPO_JID` | no (adapter) | — | dedicated group JID (adapter variable) |
| `WABA_PHONE_ID` | no (Cloud API) | — | Cloud API phone id |
| `WABA_TOKEN` | no (Cloud API) | — | Cloud API token |
| `WABA_APP_SECRET` | no (Cloud API) | — | Cloud API app secret |
| `WABA_VERIFY_TOKEN` | no (Cloud API) | — | Cloud API webhook verify token |
| `LLM_PROVIDER` | no | — | AI provider id |
| `LLM_MODEL` | no | — | AI model id |
| `LLM_API_KEY` | no | — | AI provider key |
| `LLM_FALLBACKS` | no | — | ordered fallback providers |
| `CORE_BASE_URL` | yes | — | core API base URL |
| `CORE_AUTH_*` | no | — | core credentials (reserved) |
| `REDIS_URL` | yes | — | Redis connection URL |
| `CONV_TTL_MIN` | no | `30` | conversation inactivity TTL (minutes) |
| `MAX_AJUSTES` | no | `2` | allowed free-text adjustments |
| `RETRY_MAX_TENTATIVAS` | no | `6` | retry attempts |
| `RETRY_BASE_S` | no | `2` | retry base backoff (seconds) |
| `RETRY_CAP_S` | no | `300` | retry backoff cap (seconds) |
| `TZ` | no | `America/Sao_Paulo` | time zone |

Only `BENEFICIARIA_TELEFONE`, `CORE_BASE_URL` and `REDIS_URL` are required for the
service to start. Channel and LLM credentials are read lazily by the components
that use them, so `/health` works without them.
