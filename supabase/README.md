# Database schema & Row Level Security

Until this directory existed, the CodeQuest schema lived only in the Supabase
dashboard and **no Row Level Security policies were defined**. That matters because
the `anon` key is public by design — it ships inside the frontend JavaScript bundle —
and both the browser and the backend used it. Without RLS, anyone who opened devtools
could read and write every user's chat sessions and messages.

`migrations/0001_chat_schema_rls.sql` is the first checked-in description of the
schema. It creates the tables if they are missing, brings an existing project up to
the same shape, and turns RLS on.

## Applying it

There is no migration tooling wired into this repo yet, so apply it by hand:

1. Supabase dashboard → **SQL Editor** → paste the contents of
   `migrations/0001_chat_schema_rls.sql` → **Run**.
2. Run it against a branch or staging project first if you have one.

The script is idempotent — every statement is guarded, so re-running it is safe.

### Read this before running it on production data

The one destructive step converts `chat_sessions.user_id` from `text` to `uuid` and
adds a foreign key to `auth.users`. This is required: `auth.uid()` returns a `uuid`, so
a `text` column can never match it and every policy would silently deny everything.

Two classes of row cannot survive that conversion, and the script removes them:

- rows whose `user_id` is not a valid UUID — the backend used to write the literal
  string `"user_placeholder"`. These are **copied to `chat_sessions_legacy_backup`
  first**, along with a delete of their messages.
- rows whose `user_id` is a well-formed UUID belonging to an account that no longer
  exists. These are deleted outright, since the foreign key would reject them anyway.

In this codebase's history the backend never actually persisted messages (its
`persist` flag was always false) and the only writer — the frontend — always used a
real `auth.users` UUID, so both sets are expected to be empty. Check the row counts
before you run it if the project has real traffic.

## Verifying that RLS actually works

Do not take "it applied cleanly" as proof. Run `verify_rls.sql` (below) or check by
hand:

**As a signed-out visitor**, with just the anon key:

```js
await supabase.from('chat_sessions').select('*')
```

This must return an error or an empty set — never rows. After this migration `anon`
has no table privileges at all, so it returns a permission error.

**As two different signed-in users**, confirm neither can see the other: sign in as A,
note a session id, then sign in as B and query that id directly. B must get zero rows,
not a permission error and not the row.

`verify_rls.sql` automates exactly this against a local PostgreSQL instance with a
small stand-in for Supabase's `auth` schema. It is a test fixture, not something to run
against your project.

## What the policies say

- `chat_sessions`: a row is yours when `user_id = auth.uid()`. Separate policies for
  select / insert / update / delete, all scoped `to authenticated`.
- `messages`: has no `user_id` of its own, so ownership is derived from its parent
  session via an `EXISTS` subquery against `chat_sessions`.
- Both tables use `force row level security`, so the table owner is bound by the
  policies too.
- `anon` has all privileges revoked. `service_role` holds the `bypassrls` attribute and
  is *not* constrained by any of this — which is precisely why the backend forwards the
  caller's JWT and uses the anon key rather than `service_role`.

`auth.uid()` is written as `(select auth.uid())` in the policies. That is deliberate:
the subquery form lets Postgres hoist the call into an InitPlan and evaluate it once
per query instead of once per row.

## Running `verify_rls.sql` locally

```bash
# any local PostgreSQL 14+ will do
initdb -D /tmp/cqpg/data -U codequest --auth=trust
pg_ctl -D /tmp/cqpg/data -o '-k /tmp/cqpg -p 55432' start

psql -h /tmp/cqpg -p 55432 -U codequest -d postgres -f supabase/_local_auth_stub.sql
psql -h /tmp/cqpg -p 55432 -U codequest -d postgres -f supabase/migrations/0001_chat_schema_rls.sql
psql -h /tmp/cqpg -p 55432 -U codequest -d postgres -f supabase/verify_rls.sql
```

`_local_auth_stub.sql` provides the pieces of Supabase the migration depends on —
the `auth.users` table, an `auth.uid()` that reads the JWT-claim GUC, and the `anon` /
`authenticated` / `service_role` roles. Expected output: `anon` gets *permission
denied* on both tables; each user sees exactly one session and only their own
messages; cross-user reads return `0`; cross-user inserts raise
`new row violates row-level security policy`; and deleting a session cascades to its
messages.
