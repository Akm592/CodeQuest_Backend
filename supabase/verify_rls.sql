\set ON_ERROR_STOP off
\pset pager off

-- Seed two users and a session + message each, as the table owner.
insert into auth.users (id, email) values
    ('11111111-1111-1111-1111-111111111111', 'alice@example.com'),
    ('22222222-2222-2222-2222-222222222222', 'bob@example.com')
on conflict do nothing;

-- The owner is subject to FORCE RLS, so seed with RLS temporarily off.
alter table public.chat_sessions no force row level security;
alter table public.messages      no force row level security;

insert into public.chat_sessions (id, user_id, session_name) values
    ('aaaaaaaa-0000-0000-0000-000000000001', '11111111-1111-1111-1111-111111111111', 'Alice chat'),
    ('bbbbbbbb-0000-0000-0000-000000000002', '22222222-2222-2222-2222-222222222222', 'Bob chat')
on conflict do nothing;

insert into public.messages (session_id, sender_type, content) values
    ('aaaaaaaa-0000-0000-0000-000000000001', 'user', 'alice secret'),
    ('bbbbbbbb-0000-0000-0000-000000000002', 'user', 'bob secret')
on conflict do nothing;

alter table public.chat_sessions force row level security;
alter table public.messages      force row level security;

\echo ''
\echo '=== ANON (no JWT) — must see NOTHING ==='
set role anon;
select 'anon can read chat_sessions: ' || count(*) from public.chat_sessions;
select 'anon can read messages: '      || count(*) from public.messages;
reset role;

\echo ''
\echo '=== ALICE — must see only her own row ==='
set role authenticated;
set request.jwt.claim.sub = '11111111-1111-1111-1111-111111111111';
select 'alice sees sessions: ' || count(*) from public.chat_sessions;
select 'alice sees session_name: ' || string_agg(session_name, ',') from public.chat_sessions;
select 'alice sees messages: ' || coalesce(string_agg(content, ','), '(none)') from public.messages;
\echo '-- IDOR attempt: alice queries bob session id directly'
select 'alice reading bob session by id: ' || count(*) from public.chat_sessions
    where id = 'bbbbbbbb-0000-0000-0000-000000000002';
select 'alice reading bob messages by session id: ' || count(*) from public.messages
    where session_id = 'bbbbbbbb-0000-0000-0000-000000000002';
reset role;
reset request.jwt.claim.sub;

\echo ''
\echo '=== BOB — must see only his own row ==='
set role authenticated;
set request.jwt.claim.sub = '22222222-2222-2222-2222-222222222222';
select 'bob sees session_name: ' || string_agg(session_name, ',') from public.chat_sessions;
select 'bob sees messages: ' || coalesce(string_agg(content, ','), '(none)') from public.messages;
reset role;
reset request.jwt.claim.sub;

\echo ''
\echo '=== WRITE ISOLATION ==='
set role authenticated;
set request.jwt.claim.sub = '11111111-1111-1111-1111-111111111111';

\echo '-- alice tries to insert a message into bob session (must FAIL)'
savepoint sp1;
insert into public.messages (session_id, sender_type, content)
    values ('bbbbbbbb-0000-0000-0000-000000000002', 'user', 'alice injected');
rollback to sp1;

\echo '-- alice tries to insert a session owned by bob (must FAIL)'
savepoint sp2;
insert into public.chat_sessions (id, user_id, session_name)
    values ('cccccccc-0000-0000-0000-000000000003', '22222222-2222-2222-2222-222222222222', 'spoof');
rollback to sp2;

\echo '-- alice tries to delete bob session (must affect 0 rows)'
delete from public.chat_sessions where id = 'bbbbbbbb-0000-0000-0000-000000000002';

\echo '-- alice inserts into her OWN session (must SUCCEED)'
insert into public.messages (session_id, sender_type, content)
    values ('aaaaaaaa-0000-0000-0000-000000000001', 'bot', 'legit reply');

reset role;
reset request.jwt.claim.sub;

\echo ''
\echo '=== CASCADE: deleting a session removes its messages ==='
select 'messages before: ' || count(*) from public.messages;
delete from public.chat_sessions where id = 'aaaaaaaa-0000-0000-0000-000000000001';
select 'messages after alice session delete: ' || count(*) from public.messages;
