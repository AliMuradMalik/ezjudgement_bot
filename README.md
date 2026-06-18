# EzJudgements API

Agentic RAG service over a curated OpenAI vector store of legal judgement PDFs,
exposed as a production FastAPI application with chat-history persistence in
PostgreSQL.

- **Agentic** — the model drives its own multi-step file searches via the OpenAI
  Responses API + `file_search` tool, not a single retrieve-then-answer shot.
  `tool_choice="required"` forces a search every turn.
- **Web-search fallback** — if a judgement isn't in the vector store, the agent
  falls back to the OpenAI `web_search` tool and answers from the web, clearly
  labelled as such. Web sources come back as citations with `url` + `title`.
- **Stateful** — every conversation, message, tool call and citation is
  persisted; conversations can be listed, fetched and deleted over HTTP, scoped
  per device.
- **Streaming** — `POST /chat/stream` emits SSE events (`text.delta`,
  `tool.searching`, `tool.searched`, `done`) for real-time UIs.
- **Production-ready** — connection pool, CORS, request-id middleware,
  structured logging, typed settings, Docker image.

---

## Architecture

```
            ┌────────────┐         ┌───────────────┐         ┌──────────────┐
  client ──►│  FastAPI   ├────────►│  RagService   ├────────►│  OpenAI API  │
            │  (app/)    │         │  (AsyncOpenAI)│         │  file_search │
            └─────┬──────┘         └───────────────┘         └──────────────┘
                  │
                  │ psycopg2 pool
                  ▼
            ┌────────────────────────────┐
            │  PostgreSQL                │
            │  schema: ezjudgements      │
            │  tables: conversations,    │
            │  messages, tool_calls,     │
            │  citations                 │
            └────────────────────────────┘
```

### Project layout

```
EzJudgements bot/
├── run.py                   # uvicorn entry point (dev)
├── requirements.txt
├── .env / .env.example
├── app/
│   ├── main.py              # app factory, lifespan, CORS, error handlers
│   ├── config.py            # pydantic-settings
│   ├── logging_config.py
│   ├── middleware.py        # request id + access log
│   ├── deps.py              # X-User-ID dependency, DI wiring
│   ├── schemas.py           # Pydantic request/response models
│   ├── rag.py               # AsyncOpenAI agent (answer + answer_stream)
│   └── routers/
│       ├── health.py        # /health, /health/db
│       ├── chat.py          # /chat, /chat/stream
│       └── conversations.py # CRUD
├── database/
│   ├── schema.sql           # DDL (isolated in the ezjudgements schema)
│   ├── init_db.py           # applies schema.sql
│   └── db.py                # threaded connection pool + helpers
└── prompts/
    └── ezprompt.py          # system prompt for the agent
```

> A `static/` folder may exist locally with a development chat UI, but it is
> gitignored and is not part of what you deploy. Production deployments serve
> only the API + `/docs`.

---

## Quick start

### 1. Requirements

- Python 3.11+
- A reachable PostgreSQL 13+ database (DigitalOcean managed Postgres is fine)
- An OpenAI API key with access to the Responses API
- An existing OpenAI vector store id containing your PDFs

### 2. Install

```bash
pip install -r requirements.txt
```

### 3. Configure

Copy `.env.example` to `.env` and fill in every value:

```env
OPENAI_API_KEY=sk-...
DATABASE_URL=postgresql://user:pass@host:25060/db?sslmode=require
CORS_ORIGINS=["https://your-frontend.example.com"]
ENVIRONMENT=production
LOG_LEVEL=INFO
VECTOR_STORE_ID=vs_...
MODEL=gpt-4.1
MAX_NUM_RESULTS=10
DB_POOL_MIN=1
DB_POOL_MAX=10
```

> DigitalOcean managed Postgres requires `?sslmode=require` on the connection
> string.

### 4. Apply the database schema

The DDL is idempotent and lives in a dedicated `ezjudgements` schema, so it
cannot collide with existing production tables. It never issues DROP / ALTER.

```bash
python -m database.init_db
```

You should see:

```
Applying ezjudgements schema to ... :
  - ezjudgements.citations
  - ezjudgements.conversations
  - ezjudgements.messages
  - ezjudgements.tool_calls
```

### 5. Run

Development:

```bash
python run.py
```

Production (multi-worker):

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 4 --proxy-headers
```

Docs: <http://localhost:8000/docs>

---

## Configuration reference

| Variable          | Required | Default          | Purpose                                   |
| ----------------- | -------- | ---------------- | ----------------------------------------- |
| `OPENAI_API_KEY`  | yes      | —                | OpenAI credentials (server-side only, never returned to clients) |
| `DATABASE_URL`    | yes      | —                | Postgres DSN (include `?sslmode=require`) |
| `VECTOR_STORE_ID` | no       | —                | OpenAI vector store id                    |
| `MODEL`           | no       | `gpt-4.1`        | OpenAI model                              |
| `MAX_NUM_RESULTS` | no       | `10`             | `file_search` results per query           |
| `SOURCES_DIR`     | no       | `sources`        | Folder of original PDFs served as source links (filenames must match the vector store) |
| `CORS_ORIGINS`    | no       | `["*"]`          | JSON list of allowed origins              |
| `ENVIRONMENT`     | no       | `production`     | `development` enables reload              |
| `LOG_LEVEL`       | no       | `INFO`           | Root logger level                         |
| `DB_POOL_MIN`     | no       | `1`              | Min connections held by the pool          |
| `DB_POOL_MAX`     | no       | `10`             | Max connections held by the pool          |

---

## HTTP API

All routes except `/`, `/docs`, `/health*` require the header
`X-User-ID: <id>` (any opaque string 8–256 chars; the dev UI mints a UUID per
device and persists it in `localStorage`). Conversation endpoints enforce
ownership: a client can only read or delete conversations whose
`user_identifier` matches its `X-User-ID`.

> **No real authentication is built in.** The `X-User-ID` header is
> self-asserted and not verified — treat it as a session/scoping label, not a
> security boundary. Put Cloudflare, an API gateway, or `slowapi` rate limits
> in front before exposing this service publicly.

### Health

```http
GET /health        -> 200 {"status":"ok", "environment":"production", "version":"1.0.0"}
GET /health/db     -> 200 {"status":"ok", "latency_ms": 4.2}
```

### Chat (JSON)

```http
POST /chat
X-User-ID: <uuid>
Content-Type: application/json

{
  "message": "Summarise the doctrine of frustration under Indian contract law.",
  "conversation_id": null         // omit to start a new conversation
}
```

Response:

```json
{
  "conversation_id": "8b1d...",
  "message_id": 42,
  "answer": "Under the Indian Contract Act, 1872 ...",
  "tool_calls": [
    { "tool_type": "file_search", "queries": ["doctrine of frustration India"], "result_count": 8 }
  ],
  "citations": [
    { "file_id": "file_abc", "filename": "Satyabrata_Ghose_v_Mugneeram.pdf" }
  ]
}
```

Citations from the corpus carry `filename` / `judgment_url` / `headnote_url`;
citations from the web-search fallback instead carry `url` and `title` (the
`file_id` field holds the URL so persistence stays uniform):

```json
{ "file_id": "https://example.org/case", "url": "https://example.org/case", "title": "Case name — Court" }
```

### Chat (SSE stream)

```http
POST /chat/stream
X-User-ID: <uuid>
Content-Type: application/json
```

Events (one JSON object per event, type in `event:` field):

| Event            | Payload                                                     |
| ---------------- | ----------------------------------------------------------- |
| `tool.searching` | `{"type":"tool.searching"}`                                 |
| `tool.searched`  | `{"type":"tool.searched","queries":[...]}`                  |
| `text.delta`     | `{"type":"text.delta","delta":"..."}` — one chunk of answer |
| `done`           | final answer, tool_calls, citations, response_id            |
| `persisted`      | `{"conversation_id":"...","message_id":N}`                  |
| `error`          | `{"type":"error","message":"upstream model error"}`          |

Minimal JS consumer:

```js
const res = await fetch("/chat/stream", {
  method: "POST",
  headers: { "Content-Type": "application/json", "X-User-ID": userId },
  body: JSON.stringify({ message: "..." }),
});
const reader = res.body.getReader();
// parse SSE frames...
```

### Conversations

```http
POST   /conversations                 # create empty (optional — /chat creates one implicitly)
GET    /conversations?limit=50&offset=0
GET    /conversations/{id}            # full message list
DELETE /conversations/{id}            # cascade deletes messages, tool_calls, citations
```

All four are scoped by `X-User-ID`: you can only see / modify your own.

### Source PDFs

```http
GET /sources/judgment/{filename}   # full judgment PDF, inline (200) or 404
GET /sources/headnote/{filename}   # headnote/summary PDF, inline (200) or 404
```

The PDFs that back the vector store live under `SOURCES_DIR` in this layout:

```
<SOURCES_DIR>/
  CLC/<YEAR>/headnote/CLC<YEAR>K<NN>.pdf
  CLC/<YEAR>/judgment/CLC<YEAR>K<NN>.pdf
  SCMR/<YEAR>/headnote/SCMR<YEAR>S<NNNN>.pdf
  SCMR/<YEAR>/judgment/SCMR<YEAR>S<NNNN>.pdf
```

Every case has two PDFs with the same filename — a `judgment/` (full text) and
a `headnote/` (summary). Each citation returned by `/chat` therefore carries up
to two links:

```json
{
  "file_id": "file_abc",
  "filename": "CLC2013K219.pdf",
  "judgment_url": "/sources/judgment/CLC2013K219.pdf",
  "headnote_url": "/sources/headnote/CLC2013K219.pdf"
}
```

The folder is derived straight from the filename: `CLC2013K219.pdf` →
`CLC/2013/<kind>/CLC2013K219.pdf` (series + 4-digit year). A trailing `.pdf` is
optional. If the derived path misses, a cached recursive scan looks the file up
by name. A link is `null` when that variant isn't on disk.

> **Filenames must match the vector store.** A citation maps to a PDF by the
> exact filename that was uploaded to OpenAI. Path traversal is blocked — only
> files inside `SOURCES_DIR` are served. The endpoint is unauthenticated; the
> corpus is the same content the bot already answers from.

---

## Database schema

Everything lives under the `ezjudgements` schema so it is fully isolated from
any pre-existing tables in the target database.

| Table           | Purpose                                                           |
| --------------- | ----------------------------------------------------------------- |
| `conversations` | One row per chat session (`user_identifier`, `title`, `metadata`) |
| `messages`      | User / assistant / system turns with `openai_response_id`         |
| `tool_calls`    | Every `file_search` the agent issued, queries stored as JSONB     |
| `citations`     | OpenAI `file_id` + `filename` for every judgement cited           |

Foreign keys are `ON DELETE CASCADE`, so deleting a conversation removes all
dependent rows.

Re-running `python -m database.init_db` is safe — all DDL is
`CREATE ... IF NOT EXISTS`.

---

## Production notes

- **Never commit `.env`.** Set `OPENAI_API_KEY` and `DATABASE_URL` via your
  platform's secret store. The OpenAI key is read once at startup and never
  echoed back to clients; client-facing errors are sanitised.
- Tune `DB_POOL_MAX` to `workers * expected concurrent requests` — each uvicorn
  worker has its own pool.
- Point the load balancer liveness probe at `/health`, readiness at `/health/db`.
- Every response carries an `X-Request-ID` header; correlate it with the
  `rid=` field in the structured access log.
- **Add abuse protection before going live.** `/chat` has no auth — anyone can
  burn OpenAI credits. Put Cloudflare in front, or add `slowapi` per-IP limits.
- Not included out of the box — add if needed: rate limiting (`slowapi`),
  real user auth, Alembic migrations, OpenTelemetry / Sentry tracing.

---

## Troubleshooting

| Symptom                                                | Likely cause                                                                |
| ------------------------------------------------------ | --------------------------------------------------------------------------- |
| `UndefinedTable: relation "ezjudgements.conversations"` | Schema not applied — run `python -m database.init_db`                       |
| `connection ... SSL required`                          | Missing `?sslmode=require` on `DATABASE_URL`                                |
| `400 missing or invalid X-User-ID`                     | Client did not send the `X-User-ID` header, or sent a value < 8 chars       |
| `404 conversation not found` on a real id              | Your `X-User-ID` does not own that conversation (ownership check)           |
| `502 upstream model error`                             | OpenAI call failed — check server logs for the full stack; client gets only the generic message |
| Bot says "couldn't find any reference"                  | The corpus genuinely lacks the doc, **or** previous "not found" replies are biasing this conversation — click "New chat" |
| CORS blocked in browser                                | Add the frontend origin to `CORS_ORIGINS` in `.env`                         |
