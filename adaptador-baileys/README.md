# adaptador-baileys

WhatsApp adapter (Node 22 + TypeScript) that keeps the Baileys session, filters
inbound messages to a single dedicated group, forwards normalized events to the
orchestrator over an HMAC-signed webhook, and exposes a small HTTP API.

## Layout

- `src/config/` — configuration and logger.
- `src/http/` — Fastify server and HTTP contract.
- `src/media/` — opaque media reference store.
- `src/webhook/` — outbound forwarder and HMAC signing.
- `src/whatsapp/` — Baileys socket, allowlist, reconnect, shared types.
- `test/` mirrors `src/`; shared test doubles live in `test/support/`.

## Run

```sh
npm ci
npm run build
npm start        # or: node dist/main.js
npm test         # node:test
```

Docker:

```sh
docker build -t adaptador-baileys .
docker run --rm -e BAILEYS_ENABLED=false -p 8080:8080 adaptador-baileys
```

Set `BAILEYS_ENABLED=false` to start the HTTP layer without a WhatsApp
connection (health reports `degraded`); this is used for image verification.

## Environment variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `GRUPO_JID` | when enabled | — | JID of the dedicated group; only its messages are processed |
| `ADAPTER_SECRET` | when enabled | — | Shared secret required on inbound HTTP endpoints |
| `WEBHOOK_URL` | when enabled | — | Orchestrator endpoint events are forwarded to |
| `WEBHOOK_SECRET` | when enabled | — | HMAC-SHA256 key used to sign forwarded events |
| `SESSION_DIR` | no | `./session` (`/data/session` in image) | Directory for Baileys session credentials |
| `BAILEYS_ENABLED` | no | `true` | Set `false` to run without a WhatsApp connection |
| `MEDIA_TTL_MS` | no | `120000` | Lifetime of opaque media references |
| `MEDIA_MAX_BYTES` | no | `8388608` | Max media size (bytes); larger downloads are refused with `413` |
| `PORT` | no | `8080` | HTTP port |
| `HOST` | no | `0.0.0.0` | HTTP bind address |
| `LOG_LEVEL` | no | `info` | pino log level |

Message content is never logged. Only `image/jpeg`, `image/png` and
`image/webp` media is downloaded; any other type is refused with `415`.
Downloads are capped at `MEDIA_MAX_BYTES` and aborted with `413` once exceeded.
While the session is unpaired the QR string is logged so the operator can
render it; keep adapter logs private, since they identify the linked chat.

## HTTP contract

All endpoints except `GET /health` require the header
`X-Adapter-Secret: <ADAPTER_SECRET>`.

| Method | Path | Body / Params | Response |
|---|---|---|---|
| `GET` | `/health` | — | `200 {"status":"connected\|degraded\|down"}` |
| `POST` | `/messages` | `{ "text": string }` | `202 {"status":"queued"}` (sent to the dedicated group) |
| `GET` | `/media/{ref}` | opaque `ref` | `200` media stream; `404` if unknown/expired, `413` if over the cap, `415` if not an allowed image |

Inbound events are sent to `WEBHOOK_URL` as JSON, signed with HMAC-SHA256 in the
`X-Signature-256: sha256=<hex>` header using `WEBHOOK_SECRET`. Each event is
either a normalized `message` (id, group/sender JID, kind, text, media ref,
timestamp) or a `session` event (`connected`, `disconnected`, `logged_out`).
