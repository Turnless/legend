-- LEGEND invite contest: linked wallets for the holder rule.
-- Run once in the Supabase SQL Editor (after contest.sql). Safe to re-run.

alter table public.lgd_members add column if not exists wallet text;
create unique index if not exists lgd_members_wallet_idx on public.lgd_members(wallet);

-- Links a wallet to a member. Returns 'ok' or 'taken' (wallet already linked to someone else).
create or replace function public.lgd_link_wallet(p_uid bigint, p_wallet text)
returns text language plpgsql as $$
begin
  if exists (select 1 from public.lgd_members where wallet = lower(p_wallet) and telegram_id <> p_uid) then
    return 'taken';
  end if;
  update public.lgd_members set wallet = lower(p_wallet), updated_at = now() where telegram_id = p_uid;
  return 'ok';
end $$;

create or replace function public.lgd_get_wallet(p_uid bigint)
returns text language sql stable as $$
  select wallet from public.lgd_members where telegram_id = p_uid
$$;

-- Top of the leaderboard with linked wallets, for the payout check.
create or replace function public.lgd_winners(p_limit int default 20)
returns table (rank bigint, telegram_id bigint, name text, invites bigint, verified bigint, wallet text)
language sql stable as $$
  select l.rank, l.telegram_id, l.name, l.invites, l.verified, m.wallet
  from public.lgd_leaderboard(p_limit) l
  join public.lgd_members m on m.telegram_id = l.telegram_id
  order by l.rank, l.name
$$;

do $$
declare f record;
begin
  for f in
    select p.oid::regprocedure as sig from pg_proc p
    join pg_namespace n on n.oid = p.pronamespace
    where n.nspname = 'public' and p.proname in ('lgd_link_wallet', 'lgd_get_wallet', 'lgd_winners')
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
