-- LEGEND meme contest: entries, votes, image storage.
-- Run once in the Supabase SQL Editor (after contest.sql). Safe to re-run.

create table if not exists public.lgd_meme_entries (
  id bigserial primary key,
  telegram_id bigint not null unique,          -- 1 entry per Telegram account
  name text not null,
  meme_no int not null check (meme_no between 1 and 20),
  image_path text not null,
  wallet text not null unique,                 -- 1 entry per wallet (lowercase)
  tx_hash text not null unique,                -- each payment counts once
  fee_lgd numeric not null,
  status text not null default 'pending' check (status in ('pending', 'approved', 'rejected')),
  created_at timestamptz not null default now()
);

create table if not exists public.lgd_meme_votes (
  entry_id bigint not null references public.lgd_meme_entries(id) on delete cascade,
  voter_id bigint not null,
  created_at timestamptz not null default now(),
  primary key (entry_id, voter_id)
);

create index if not exists lgd_meme_votes_voter_idx on public.lgd_meme_votes(voter_id);

alter table public.lgd_meme_entries enable row level security;
alter table public.lgd_meme_votes enable row level security;
revoke all on public.lgd_meme_entries, public.lgd_meme_votes from anon, authenticated;
grant all on public.lgd_meme_entries, public.lgd_meme_votes to service_role;
grant usage, select on sequence public.lgd_meme_entries_id_seq to service_role;

-- Public bucket for approved and pending images (only the server can upload).
do $$
begin
  if exists (select 1 from pg_namespace where nspname = 'storage') then
    insert into storage.buckets (id, name, public) values ('memes', 'memes', true)
    on conflict (id) do nothing;
  end if;
end $$;

-- Returns 'ok', 'telegram_used', 'wallet_used' or 'tx_used'.
create or replace function public.lgd_meme_submit(
  p_uid bigint, p_name text, p_meme_no int, p_image_path text,
  p_wallet text, p_tx_hash text, p_fee_lgd numeric
) returns text language plpgsql as $$
begin
  if exists (select 1 from public.lgd_meme_entries where telegram_id = p_uid) then return 'telegram_used'; end if;
  if exists (select 1 from public.lgd_meme_entries where wallet = lower(p_wallet)) then return 'wallet_used'; end if;
  if exists (select 1 from public.lgd_meme_entries where tx_hash = lower(p_tx_hash)) then return 'tx_used'; end if;
  insert into public.lgd_meme_entries (telegram_id, name, meme_no, image_path, wallet, tx_hash, fee_lgd)
  values (p_uid, p_name, p_meme_no, p_image_path, lower(p_wallet), lower(p_tx_hash), p_fee_lgd);
  return 'ok';
exception when unique_violation then
  return 'telegram_used';
end $$;

-- Read-only checks before paying, so nobody pays for an entry that can't be saved.
create or replace function public.lgd_meme_precheck(p_uid bigint, p_tx_hash text)
returns text language sql stable as $$
  select case
    when exists (select 1 from public.lgd_meme_entries where telegram_id = p_uid) then 'telegram_used'
    when p_tx_hash is not null and exists (select 1 from public.lgd_meme_entries where tx_hash = lower(p_tx_hash)) then 'tx_used'
    else 'ok' end
$$;

-- Approved entries ranked by votes (earlier entry wins a tie on the gallery order;
-- tied ranks still share prizes).
create or replace function public.lgd_meme_board()
returns table (id bigint, telegram_id bigint, name text, meme_no int, image_path text,
               votes bigint, rank bigint, created_at timestamptz)
language sql stable as $$
  with v as (
    select e.id, e.telegram_id, e.name, e.meme_no, e.image_path, e.created_at,
           count(mv.voter_id) as votes
    from public.lgd_meme_entries e
    left join public.lgd_meme_votes mv on mv.entry_id = e.id
    where e.status = 'approved'
    group by e.id
  )
  select v.id, v.telegram_id, v.name, v.meme_no, v.image_path, v.votes,
         rank() over (order by v.votes desc), v.created_at
  from v order by v.votes desc, v.created_at
$$;

-- Returns 'ok', 'already', 'own', 'missing'.
create or replace function public.lgd_meme_vote(p_entry bigint, p_voter bigint)
returns text language plpgsql as $$
declare
  v_owner bigint;
begin
  select telegram_id into v_owner from public.lgd_meme_entries where id = p_entry and status = 'approved';
  if not found then return 'missing'; end if;
  if v_owner = p_voter then return 'own'; end if;
  insert into public.lgd_meme_votes (entry_id, voter_id) values (p_entry, p_voter)
  on conflict do nothing;
  return case when found then 'ok' else 'already' end;
end $$;

create or replace function public.lgd_meme_my(p_uid bigint)
returns table (entry_id bigint, status text, voted bigint[])
language sql stable as $$
  select (select e.id from public.lgd_meme_entries e where e.telegram_id = p_uid),
         (select e.status from public.lgd_meme_entries e where e.telegram_id = p_uid),
         coalesce((select array_agg(mv.entry_id) from public.lgd_meme_votes mv where mv.voter_id = p_uid), '{}')
$$;

-- Admin: every entry with its votes.
create or replace function public.lgd_meme_admin_list()
returns table (id bigint, telegram_id bigint, name text, meme_no int, image_path text, wallet text,
               tx_hash text, fee_lgd numeric, status text, created_at timestamptz, votes bigint)
language sql stable as $$
  select e.id, e.telegram_id, e.name, e.meme_no, e.image_path, e.wallet, e.tx_hash, e.fee_lgd,
         e.status, e.created_at, (select count(*) from public.lgd_meme_votes mv where mv.entry_id = e.id)
  from public.lgd_meme_entries e
  order by (e.status = 'pending') desc, e.created_at desc
$$;

create or replace function public.lgd_meme_set_status(p_id bigint, p_status text)
returns void language sql as $$
  update public.lgd_meme_entries set status = p_status where id = p_id
$$;

-- Only the server (service role) may call these.
do $$
declare f record;
begin
  for f in
    select p.oid::regprocedure as sig from pg_proc p
    join pg_namespace n on n.oid = p.pronamespace
    where n.nspname = 'public' and p.proname like 'lgd\_meme\_%'
  loop
    execute format('revoke all on function %s from public', f.sig);
    if exists (select 1 from pg_roles where rolname = 'anon') then
      execute format('revoke all on function %s from anon, authenticated', f.sig);
    end if;
    if exists (select 1 from pg_roles where rolname = 'service_role') then
      execute format('grant execute on function %s to service_role', f.sig);
    end if;
  end loop;
end $$;
