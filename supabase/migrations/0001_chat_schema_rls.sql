-- CodeQuest — chat schema + Row Level Security
--
-- This is the first checked-in description of the database. Until now the schema
-- existed only in the Supabase dashboard and NO row level security was defined,
-- while the anon key (which is public by design — it ships in the frontend JS
-- bundle) was used by both the browser and the backend. Without RLS that means
-- anyone who opens devtools can read and write every user's chats.
--
-- Safe to run more than once: every statement is guarded.
--
-- HOW TO APPLY
--   Supabase dashboard -> SQL Editor -> paste -> Run.
--   Run it against a branch/staging project first if you have one.
--   See supabase/README.md for the verification steps that prove RLS works.

begin;

-- ---------------------------------------------------------------------------
-- 1. Tables
-- ---------------------------------------------------------------------------

create table if not exists public.chat_sessions (
    id           uuid primary key,
    user_id      uuid,
    session_name text        not null default 'New Chat',
    created_at   timestamptz not null default now(),
    updated_at   timestamptz not null default now()
);

create table if not exists public.messages (
    id                 uuid primary key default gen_random_uuid(),
    session_id         uuid        not null,
    sender_type        text        not null,
    content            text,
    intent             text,
    visualization_data jsonb,
    parent_message_id  uuid,
    metadata           jsonb       not null default '{}'::jsonb,
    created_at         timestamptz not null default now()
);

-- Columns that may be missing on an existing project.
-- `updated_at` in particular is expected by the frontend's ChatSessionInfo type
-- but has never existed in the database.
alter table public.chat_sessions add column if not exists updated_at timestamptz not null default now();
alter table public.messages      add column if not exists metadata   jsonb       not null default '{}'::jsonb;

-- AuthContext.createChatSession inserts only user_id, so session_name must
-- have a default or that insert fails.
alter table public.chat_sessions alter column session_name set default 'New Chat';

-- ---------------------------------------------------------------------------
-- 2. chat_sessions.user_id : text -> uuid, then FK to auth.users
--
--    auth.uid() returns uuid, so a text column can never match it and every
--    RLS policy below would silently deny everything. This is the one step
--    that can lose data, so it backs up before it deletes.
-- ---------------------------------------------------------------------------

do $$
declare
    col_type text;
    bad_rows bigint;
begin
    select data_type into col_type
    from information_schema.columns
    where table_schema = 'public' and table_name = 'chat_sessions' and column_name = 'user_id';

    if col_type is null then
        raise notice 'chat_sessions.user_id not found; skipping conversion.';

    elsif col_type = 'uuid' then
        raise notice 'chat_sessions.user_id is already uuid; skipping conversion.';

    else
        -- The backend used to write the literal string 'user_placeholder' here.
        -- Those rows cannot be cast and would abort the ALTER.
        select count(*) into bad_rows
        from public.chat_sessions
        where user_id !~* '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$';

        if bad_rows > 0 then
            raise notice 'Quarantining % chat_sessions row(s) with a non-uuid user_id.', bad_rows;

            create table if not exists public.chat_sessions_legacy_backup
                (like public.chat_sessions including defaults);

            insert into public.chat_sessions_legacy_backup
            select * from public.chat_sessions
            where user_id !~* '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$';

            delete from public.messages m
            where exists (
                select 1 from public.chat_sessions_legacy_backup b where b.id = m.session_id
            );

            delete from public.chat_sessions
            where user_id !~* '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$';
        end if;

        alter table public.chat_sessions alter column user_id type uuid using user_id::uuid;
    end if;
end
$$;

-- Rows whose uuid is well formed but whose account no longer exists would
-- abort the foreign key.
delete from public.messages m
where exists (
    select 1 from public.chat_sessions s
    where s.id = m.session_id
      and (s.user_id is null or not exists (select 1 from auth.users u where u.id = s.user_id))
);

delete from public.chat_sessions s
where s.user_id is null
   or not exists (select 1 from auth.users u where u.id = s.user_id);

do $$
begin
    if not exists (
        select 1 from pg_constraint where conname = 'chat_sessions_user_id_fkey'
    ) then
        alter table public.chat_sessions
            add constraint chat_sessions_user_id_fkey
            foreign key (user_id) references auth.users (id) on delete cascade;
    end if;
end
$$;

alter table public.chat_sessions alter column user_id set not null;

-- ---------------------------------------------------------------------------
-- 3. messages -> chat_sessions foreign keys
--
--    Without the cascade, deleting a session from the sidebar leaves its
--    messages behind forever.
-- ---------------------------------------------------------------------------

delete from public.messages m
where not exists (select 1 from public.chat_sessions s where s.id = m.session_id);

do $$
begin
    if not exists (select 1 from pg_constraint where conname = 'messages_session_id_fkey') then
        alter table public.messages
            add constraint messages_session_id_fkey
            foreign key (session_id) references public.chat_sessions (id) on delete cascade;
    end if;

    if not exists (select 1 from pg_constraint where conname = 'messages_parent_message_id_fkey') then
        alter table public.messages
            add constraint messages_parent_message_id_fkey
            foreign key (parent_message_id) references public.messages (id) on delete set null;
    end if;

    if not exists (select 1 from pg_constraint where conname = 'messages_sender_type_check') then
        alter table public.messages
            add constraint messages_sender_type_check check (sender_type in ('user', 'bot'));
    end if;
end
$$;

-- ---------------------------------------------------------------------------
-- 4. Indexes matching the queries the app actually runs
--    (get_chat_sessions_for_user, get_messages_by_session_id)
-- ---------------------------------------------------------------------------

create index if not exists idx_chat_sessions_user_created on public.chat_sessions (user_id, created_at desc);
create index if not exists idx_messages_session_created   on public.messages (session_id, created_at);
create index if not exists idx_messages_parent            on public.messages (parent_message_id);

-- ---------------------------------------------------------------------------
-- 5. Row Level Security
--
--    `force` also applies policies to the table owner. Note that service_role
--    holds the bypassrls attribute and is unaffected by any of this — which is
--    exactly why the backend forwards the caller's JWT and uses the anon key
--    rather than service_role.
-- ---------------------------------------------------------------------------

alter table public.chat_sessions enable row level security;
alter table public.chat_sessions force  row level security;
alter table public.messages      enable row level security;
alter table public.messages      force  row level security;

-- `(select auth.uid())` rather than a bare `auth.uid()`: the subquery form lets
-- Postgres hoist it into an InitPlan and evaluate it once per query instead of
-- once per row.

drop policy if exists chat_sessions_select_own on public.chat_sessions;
create policy chat_sessions_select_own on public.chat_sessions
    for select to authenticated
    using (user_id = (select auth.uid()));

drop policy if exists chat_sessions_insert_own on public.chat_sessions;
create policy chat_sessions_insert_own on public.chat_sessions
    for insert to authenticated
    with check (user_id = (select auth.uid()));

drop policy if exists chat_sessions_update_own on public.chat_sessions;
create policy chat_sessions_update_own on public.chat_sessions
    for update to authenticated
    using (user_id = (select auth.uid()))
    with check (user_id = (select auth.uid()));

drop policy if exists chat_sessions_delete_own on public.chat_sessions;
create policy chat_sessions_delete_own on public.chat_sessions
    for delete to authenticated
    using (user_id = (select auth.uid()));

-- messages has no user_id of its own, so ownership is derived from its parent
-- session. The subquery re-enters chat_sessions' own policies, which can only
-- narrow the result further — correct, just evaluated twice.

drop policy if exists messages_select_own on public.messages;
create policy messages_select_own on public.messages
    for select to authenticated
    using (exists (
        select 1 from public.chat_sessions s
        where s.id = messages.session_id and s.user_id = (select auth.uid())
    ));

drop policy if exists messages_insert_own on public.messages;
create policy messages_insert_own on public.messages
    for insert to authenticated
    with check (exists (
        select 1 from public.chat_sessions s
        where s.id = messages.session_id and s.user_id = (select auth.uid())
    ));

drop policy if exists messages_update_own on public.messages;
create policy messages_update_own on public.messages
    for update to authenticated
    using (exists (
        select 1 from public.chat_sessions s
        where s.id = messages.session_id and s.user_id = (select auth.uid())
    ))
    with check (exists (
        select 1 from public.chat_sessions s
        where s.id = messages.session_id and s.user_id = (select auth.uid())
    ));

drop policy if exists messages_delete_own on public.messages;
create policy messages_delete_own on public.messages
    for delete to authenticated
    using (exists (
        select 1 from public.chat_sessions s
        where s.id = messages.session_id and s.user_id = (select auth.uid())
    ));

-- ---------------------------------------------------------------------------
-- 6. Grants
--
--    RLS already denies anon (there is no policy granting it anything), but
--    revoking the table privileges outright means a future policy added without
--    a `to authenticated` clause cannot accidentally expose these tables.
-- ---------------------------------------------------------------------------

grant select, insert, update, delete on public.chat_sessions to authenticated;
grant select, insert, update, delete on public.messages      to authenticated;

revoke all on public.chat_sessions from anon;
revoke all on public.messages      from anon;

-- Keep updated_at honest.
create or replace function public.touch_updated_at()
returns trigger
language plpgsql
as $$
begin
    new.updated_at = now();
    return new;
end;
$$;

drop trigger if exists trg_chat_sessions_touch on public.chat_sessions;
create trigger trg_chat_sessions_touch
    before update on public.chat_sessions
    for each row execute function public.touch_updated_at();

commit;
