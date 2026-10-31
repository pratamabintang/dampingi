-- Migration: baseline schema for dampingi
--
-- Complete schema for a fresh local development environment.
-- Includes the profiles table, soft-delete tombstone, role authorization,
-- and the trigger that syncs auth metadata to profiles.

-- profiles table: stores Profile records synced from auth.users.
-- Role is authorization data (ADR-0005): exactly one per Profile,
-- fixed at registration, stored lowercase, validated by CHECK.
-- text + CHECK rather than Postgres ENUM: dropping or renaming an
-- ENUM value needs a type rebuild, while a CHECK is one ALTER.
--
-- Soft-delete tombstone (ADR-0003): NULL = active; set only by the app
-- (never by the DB). "Deleting" a user = Supabase ban + this timestamp.

create table public.profiles (
  user_id uuid primary key references auth.users (id) on delete cascade,
  name text not null,
  role text not null
    check (role in ('mother', 'companion', 'professional')),
  deleted_at timestamptz,
  created_at timestamptz not null default now()
);

-- Trigger: handle_new_user()
-- Runs after insert on auth.users (when a user signs up).
-- Copies signup metadata (name, role) into a profiles row.
-- Role must be present in signup metadata; no fallback.
-- A signup without role fails loudly rather than silently
-- mislabeling the user (wrongly-labelled Professional is worse
-- than a failed signup).

create or replace function public.handle_new_user()
returns trigger
language plpgsql
security definer set search_path = ''
-- NOT "stable": PostgreSQL forbids INSERT in non-volatile functions,
-- and a stable trigger function makes every signup fail.
as $$
begin
  insert into public.profiles (user_id, name, role)
  values (
    new.id,
    coalesce(new.raw_user_meta_data ->> 'name', ''),
    -- no fallback: a signup without a role must fail, not be
    -- silently mislabelled (ADR-0005)
    new.raw_user_meta_data ->> 'role'
  );
  return new;
end;
$$;

create trigger on_user_created
  after insert on auth.users
  for each row execute function public.handle_new_user();

-- Verify:
--   select column_name, data_type, is_nullable, column_default
--     from information_schema.columns
--    where table_name = 'profiles'
--    order by ordinal_position;
--   -- expect columns: user_id, name, role (all NOT NULL), deleted_at, created_at
--
--   select conname, pg_get_constraintdef(oid)
--     from pg_constraint
--    where conrelid = 'public.profiles'::regclass and contype = 'c';
--   -- expect a check: role = ANY (ARRAY['mother','companion','professional'])
--
--   select trigger_name, event_object_table
--     from information_schema.triggers
--    where trigger_name = 'on_user_created';
--   -- expect: on_user_created on auth.users
