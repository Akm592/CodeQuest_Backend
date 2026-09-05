-- Minimal local stand-in for the parts of Supabase the migration relies on.
-- Not part of the repo — verification scaffolding only.

create extension if not exists pgcrypto;

create schema if not exists auth;

create table if not exists auth.users (
    id    uuid primary key,
    email text
);

-- Supabase derives auth.uid() from the request JWT claims GUC.
create or replace function auth.uid()
returns uuid
language sql
stable
as $$
    select nullif(current_setting('request.jwt.claim.sub', true), '')::uuid;
$$;

do $$
begin
    if not exists (select 1 from pg_roles where rolname = 'anon') then
        create role anon nologin;
    end if;
    if not exists (select 1 from pg_roles where rolname = 'authenticated') then
        create role authenticated nologin;
    end if;
    if not exists (select 1 from pg_roles where rolname = 'service_role') then
        create role service_role nologin bypassrls;
    end if;
end
$$;

grant usage on schema public to anon, authenticated, service_role;
grant usage on schema auth   to anon, authenticated, service_role;
grant select on auth.users   to authenticated;
