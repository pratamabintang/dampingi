-- Migration: add profiles.role (ADR-0005)
--
-- Run in the Supabase dashboard SQL editor. There is no API-key route to
-- DDL (PostgREST cannot; the Management API needs a PAT; direct connection
-- needs the DB password).
--
-- Role is authorization data: exactly one per Profile, fixed at
-- registration, stored lowercase, validated by a CHECK constraint.
-- text + CHECK rather than a Postgres ENUM: dropping or renaming an ENUM
-- value needs a type rebuild, while a CHECK is one ALTER.

-- 1. Add the column. The default exists only so NOT NULL holds for any
--    existing row; it is dropped immediately after (step 2) so nothing can
--    be inserted with a silent fallback role.
alter table public.profiles
  add column if not exists role text not null default 'mother'
  check (role in ('mother', 'companion', 'professional'));

-- 2. Drop the default: from here on, the trigger must supply the role
--    explicitly, and a signup without one fails loudly (see step 3).
alter table public.profiles
  alter column role drop default;

-- 3. The trigger writes role from signup metadata, with NO coalesce
--    fallback. A user created without role (e.g. by hand in the dashboard)
--    fails the insert rather than being silently mislabelled — a
--    wrongly-labelled Professional is worse than a failed signup.
create or replace function public.handle_new_user()
returns trigger
language plpgsql
security definer set search_path = ''
as $$
begin
  insert into public.profiles (user_id, name, role)
  values (
    new.id,
    coalesce(new.raw_user_meta_data ->> 'name', ''),
    new.raw_user_meta_data ->> 'role'
  );
  return new;
end;
$$;

-- Verify:
--   select column_name, data_type, is_nullable, column_default
--     from information_schema.columns
--    where table_name = 'profiles' and column_name = 'role';
--   -- expect: role | text | NO | (null)

--   select conname, pg_get_constraintdef(oid)
--     from pg_constraint
--    where conrelid = 'public.profiles'::regclass and contype = 'c';
--   -- expect a check: role = ANY (ARRAY['mother','companion','professional'])
