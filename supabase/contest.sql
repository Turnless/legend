-- LEGEND invite contest: tables and functions.
-- Run once in the Supabase SQL Editor. Safe to re-run.
-- Does not touch the older participants/referrals tables.
--
-- Verified invite rule (change it in lgd_verified only):
--   joined through a referral link, solved the captcha,
--   still in the group 12 hours after joining,
--   and sent at least 3 counted messages (max 1 counted per minute).

create table if not exists public.lgd_members (
  telegram_id bigint primary key,
  username text,
  first_name text,
  status text not null default 'member' check (status in ('captcha', 'member', 'left')),
  joined_at timestamptz,
  captcha_passed_at timestamptz,
  captcha_answer int,
  captcha_msg_id bigint,
  captcha_expires_at timestamptz,
  captcha_tries int not null default 0,
  msg_count int not null default 0,
  last_counted_at timestamptz,
  referred_by bigint references public.lgd_members(telegram_id) on delete set null,
  ref_slug text unique,
  ref_invite_link text unique,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint lgd_no_self_ref check (referred_by is null or referred_by <> telegram_id)
);

create index if not exists lgd_members_referred_by_idx on public.lgd_members(referred_by);
create index if not exists lgd_members_captcha_idx on public.lgd_members(status, captcha_expires_at);

alter table public.lgd_members enable row level security;
revoke all on public.lgd_members from anon, authenticated;
grant all on public.lgd_members to service_role;

-- ------------------------------------------------------------------
-- Rules
-- ------------------------------------------------------------------

create or replace function public.lgd_verified(m public.lgd_members)
returns boolean language sql stable as $$
  select m.status = 'member'
     and m.captcha_passed_at is not null
     and m.joined_at <= now() - interval '12 hours'
     and m.msg_count >= 3
$$;

-- ------------------------------------------------------------------
-- Group events
-- ------------------------------------------------------------------

-- A user joined the group. Credits the referrer only for people we have
-- never seen before, so existing members can't leave and rejoin through
-- someone's link. Returns the referrer's telegram_id or null.
create or replace function public.lgd_on_join(
  p_uid bigint, p_username text, p_first_name text,
  p_invite_link text, p_credit boolean
) returns bigint language plpgsql as $$
declare
  v_ref bigint;
begin
  if exists (select 1 from public.lgd_members where telegram_id = p_uid) then
    update public.lgd_members set
      username = p_username, first_name = p_first_name,
      status = 'captcha', joined_at = now(), captcha_passed_at = null,
      captcha_answer = null, captcha_msg_id = null, captcha_expires_at = null,
      captcha_tries = 0, msg_count = 0, last_counted_at = null, updated_at = now()
    where telegram_id = p_uid;
    return null;
  end if;

  if p_credit and p_invite_link is not null then
    select telegram_id into v_ref from public.lgd_members
    where ref_invite_link = p_invite_link and telegram_id <> p_uid;
  end if;

  insert into public.lgd_members (telegram_id, username, first_name, status, joined_at, referred_by)
  values (p_uid, p_username, p_first_name, 'captcha', now(), v_ref);
  return v_ref;
end $$;

create or replace function public.lgd_set_captcha(
  p_uid bigint, p_answer int, p_msg_id bigint, p_ttl_seconds int
) returns void language sql as $$
  update public.lgd_members set
    captcha_answer = p_answer, captcha_msg_id = p_msg_id,
    captcha_expires_at = now() + make_interval(secs => p_ttl_seconds),
    updated_at = now()
  where telegram_id = p_uid and status = 'captcha'
$$;

-- Returns 'ok', 'wrong', 'kick' (too many wrong answers) or 'none'.
create or replace function public.lgd_captcha_answer(p_uid bigint, p_choice int)
returns text language plpgsql as $$
declare
  m public.lgd_members;
begin
  select * into m from public.lgd_members where telegram_id = p_uid for update;
  if not found or m.status <> 'captcha' or m.captcha_answer is null then
    return 'none';
  end if;
  if p_choice = m.captcha_answer then
    update public.lgd_members set
      status = 'member', captcha_passed_at = now(), captcha_answer = null,
      captcha_msg_id = null, captcha_expires_at = null, updated_at = now()
    where telegram_id = p_uid;
    return 'ok';
  end if;
  if m.captcha_tries + 1 >= 3 then
    update public.lgd_members set
      status = 'left', captcha_answer = null, captcha_msg_id = null,
      captcha_expires_at = null, captcha_tries = m.captcha_tries + 1, updated_at = now()
    where telegram_id = p_uid;
    return 'kick';
  end if;
  update public.lgd_members set captcha_tries = m.captcha_tries + 1, updated_at = now()
  where telegram_id = p_uid;
  return 'wrong';
end $$;

-- Captchas past their deadline: marks them left and returns who to remove.
create or replace function public.lgd_take_expired_captchas()
returns table (telegram_id bigint, captcha_msg_id bigint) language sql as $$
  with expired as (
    select m.telegram_id, m.captcha_msg_id from public.lgd_members m
    where m.status = 'captcha' and m.captcha_expires_at < now()
    for update skip locked
  )
  update public.lgd_members m set
    status = 'left', captcha_answer = null, captcha_msg_id = null,
    captcha_expires_at = null, updated_at = now()
  from expired e
  where m.telegram_id = e.telegram_id
  returning e.telegram_id, e.captcha_msg_id
$$;

-- A user left or was removed. Returns their pending captcha message id, if any.
create or replace function public.lgd_on_leave(p_uid bigint)
returns bigint language sql as $$
  with old as (
    select captcha_msg_id from public.lgd_members where telegram_id = p_uid
  )
  update public.lgd_members set
    status = 'left', captcha_answer = null, captcha_msg_id = null,
    captcha_expires_at = null, updated_at = now()
  where telegram_id = p_uid
  returning (select captcha_msg_id from old)
$$;

-- A user sent a message in the group. Counts at most one message per minute.
-- People who were in the group before the bot are added as members.
create or replace function public.lgd_on_message(
  p_uid bigint, p_username text, p_first_name text
) returns void language plpgsql as $$
begin
  insert into public.lgd_members (telegram_id, username, first_name, status, joined_at, captcha_passed_at)
  values (p_uid, p_username, p_first_name, 'member', now(), now())
  on conflict (telegram_id) do update set
    username = excluded.username,
    first_name = excluded.first_name,
    -- A message from someone we marked as left means we missed a rejoin.
    status = case when lgd_members.status = 'left' then 'member' else lgd_members.status end,
    joined_at = case when lgd_members.status = 'left' then now() else lgd_members.joined_at end,
    captcha_passed_at = coalesce(lgd_members.captcha_passed_at, now()),
    msg_count = case
      when lgd_members.status = 'left' then 1
      when lgd_members.status = 'member'
       and (lgd_members.last_counted_at is null or lgd_members.last_counted_at <= now() - interval '60 seconds')
      then lgd_members.msg_count + 1
      else lgd_members.msg_count end,
    last_counted_at = case
      when lgd_members.status = 'left'
        or (lgd_members.status = 'member'
            and (lgd_members.last_counted_at is null or lgd_members.last_counted_at <= now() - interval '60 seconds'))
      then now() else lgd_members.last_counted_at end,
    updated_at = now()
  where lgd_members.status <> 'captcha';
end $$;

-- ------------------------------------------------------------------
-- Referral links
-- ------------------------------------------------------------------

-- Makes sure a confirmed group member has a row and a link slug.
-- The slug is fixed once created, even if the username changes later.
create or replace function public.lgd_ensure_ref(
  p_uid bigint, p_username text, p_first_name text
) returns table (ref_slug text, ref_invite_link text) language plpgsql as $$
declare
  v_slug text;
  v_base text;
begin
  insert into public.lgd_members (telegram_id, username, first_name, status, joined_at, captcha_passed_at)
  values (p_uid, p_username, p_first_name, 'member', now(), now())
  on conflict (telegram_id) do update set
    username = excluded.username, first_name = excluded.first_name, updated_at = now();

  select m.ref_slug into v_slug from public.lgd_members m where m.telegram_id = p_uid;
  if v_slug is null then
    v_base := left(lower(regexp_replace(coalesce(p_username, p_first_name, ''), '[^a-zA-Z0-9_]', '', 'g')), 24);
    if length(v_base) < 3 then
      v_base := 'legend' || v_base;
    end if;
    v_slug := v_base;
    while exists (select 1 from public.lgd_members m where m.ref_slug = v_slug) loop
      v_slug := v_base || (1000 + floor(random() * 9000))::int;
    end loop;
    update public.lgd_members m set ref_slug = v_slug, updated_at = now() where m.telegram_id = p_uid;
  end if;

  return query select m.ref_slug, m.ref_invite_link from public.lgd_members m where m.telegram_id = p_uid;
end $$;

create or replace function public.lgd_save_ref_link(p_uid bigint, p_link text)
returns void language sql as $$
  update public.lgd_members set ref_invite_link = p_link, updated_at = now()
  where telegram_id = p_uid and ref_invite_link is null
$$;

create or replace function public.lgd_resolve(p_slug text)
returns table (name text, invite_link text) language sql stable as $$
  select coalesce('@' || m.username, m.first_name, 'A LEGEND member'), m.ref_invite_link
  from public.lgd_members m
  where m.ref_slug = lower(p_slug) and m.ref_invite_link is not null
$$;

-- ------------------------------------------------------------------
-- Leaderboard and reports
-- ------------------------------------------------------------------

create or replace function public.lgd_leaderboard(p_limit int default 100)
returns table (rank bigint, telegram_id bigint, name text, invites bigint, verified bigint)
language sql stable as $$
  with s as (
    select r.telegram_id,
           coalesce('@' || r.username, r.first_name, 'LEGEND member') as name,
           count(*) filter (where i.status in ('captcha', 'member')) as invites,
           count(*) filter (where public.lgd_verified(i)) as verified
    from public.lgd_members r
    join public.lgd_members i on i.referred_by = r.telegram_id
    group by r.telegram_id
  )
  select rank() over (order by s.verified desc, s.invites desc), s.telegram_id, s.name, s.invites, s.verified
  from s
  where s.invites > 0 or s.verified > 0
  order by 1, s.name
  limit p_limit
$$;

create or replace function public.lgd_my_stats(p_uid bigint)
returns table (rank bigint, invites bigint, verified bigint)
language sql stable as $$
  select l.rank, l.invites, l.verified
  from public.lgd_leaderboard(1000000) l
  where l.telegram_id = p_uid
$$;

-- Everyone who joined through a link, for reviewing the winners by hand.
create or replace function public.lgd_report()
returns table (
  referrer_id bigint, referrer text, invitee_id bigint, invitee_username text,
  invitee_name text, status text, joined_at timestamptz, captcha_passed_at timestamptz,
  msg_count int, verified boolean
) language sql stable as $$
  select r.telegram_id, coalesce('@' || r.username, r.first_name),
         i.telegram_id, i.username, i.first_name, i.status, i.joined_at,
         i.captcha_passed_at, i.msg_count, public.lgd_verified(i)
  from public.lgd_members i
  join public.lgd_members r on r.telegram_id = i.referred_by
  order by r.telegram_id, i.joined_at
$$;

create or replace function public.lgd_verified_ids()
returns table (telegram_id bigint) language sql stable as $$
  select m.telegram_id from public.lgd_members m
  where m.referred_by is not null and public.lgd_verified(m)
$$;

-- Only the server (service role) may call these.
do $$
declare f record;
begin
  for f in
    select p.oid::regprocedure as sig from pg_proc p
    join pg_namespace n on n.oid = p.pronamespace
    where n.nspname = 'public' and p.proname like 'lgd\_%'
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
