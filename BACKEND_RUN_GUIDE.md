# Running the backend

## Prerequisites

- Python 3.11 (3.10 works, but CI runs 3.11)
- A Supabase project
- A Google Gemini API key

## Setup

```bash
git clone https://github.com/Akm592/CodeQuest_Backend.git
cd CodeQuest_Backend

python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate

pip install -r requirements.txt        # runtime only
pip install -r requirements-dev.txt    # plus pytest and ruff
```

## Configuration

```bash
cp .env.example .env
```

Then fill it in. The required variables are:

| Variable | Notes |
|---|---|
| `GEMINI_API_KEY` | From Google AI Studio |
| `SUPABASE_URL` | Supabase → Settings → API → Project URL |
| `SUPABASE_ANON_KEY` | Supabase → Settings → API → Project API keys → `anon` / `public` |

> Earlier versions of this guide said to set `SUPABASE_KEY`. The application has
> always read **`SUPABASE_ANON_KEY`**, so following those instructions produced a
> `ValueError` on startup.

The anon key is public by design — the frontend ships it in its JavaScript
bundle. Row Level Security is what protects the data, so **apply
`supabase/migrations/0001_chat_schema_rls.sql` before running anything against a
real project.** See [`supabase/README.md`](supabase/README.md).

`.env.example` documents the optional variables too: `GEMINI_MODEL`,
`CORS_ORIGINS`, `GUEST_RATE_LIMIT`, `RATE_LIMIT_RULES`, `TRUSTED_PROXY_HOPS`,
`LOG_LEVEL` and `APP_LOG_FILE`.

## Running

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

`python-dotenv` loads `.env` automatically, so `--env-file` is not needed.

- Swagger UI: <http://localhost:8000/docs>
- ReDoc: <http://localhost:8000/redoc>
- Health check: <http://localhost:8000/health>

Configuration is validated on startup, so a missing variable fails immediately
with a message naming it rather than midway through the first request.

## Tests and linting

```bash
pytest -q
ruff check .
```

`tests/conftest.py` supplies dummy environment variables, so the suite runs
without a `.env`.

## How authentication works

- **No `Authorization` header** → guest. Sessions are ephemeral, nothing is
  written to the database, and requests are rate limited.
- **A valid Supabase JWT** → the token is verified with Supabase, and every
  database query runs as that user so RLS applies.
- **A present but invalid token** → `401`. It is deliberately *not* downgraded to
  guest, because silently treating a signed-in user as a guest would stop
  persisting their messages with nothing surfacing.

## Deployment notes (Render free tier)

- `TRUSTED_PROXY_HOPS=1` matches Render's single proxy. Without it the rate
  limiter keys on the proxy's address and every user shares one bucket.
- Set `CORS_ORIGINS` to your deployed frontend origin, without a trailing slash.
- The instance spins down when idle, which clears all in-process state. The chat
  flow is built to tolerate that: pending LeetCode context round-trips through
  the client, and a signed-in user's history is rebuilt from the database. Guests
  lose conversational context across a spindown.
- Because counters are in memory, the guest limit is a per-instance speed bump
  rather than a true daily quota.
- Keep `LOG_LEVEL=INFO`. `DEBUG` logs prompts and message content.

## Regenerating the OpenAPI schema

```bash
python generate_openapi.py    # writes openapi.json (gitignored)
```
