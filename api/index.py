"""LEGEND invite contest backend (Vercel).

One FastAPI app serves:
  /api/webhook          Telegram updates: join captcha, message counting, leaves
  /api/config           bot username and group link for the site
  /api/leaderboard      public leaderboard
  /api/ref/me           Telegram-login user: membership check, ref link, own stats
  /api/ref/resolve      ref landing page: inviter name and invite link
  /api/admin/*          setup, report, audit (need ADMIN_KEY)

All state lives in Supabase (supabase/contest.sql). The bot is only an admin
in the group; users never have to talk to it.
"""

import csv
import hashlib
import hmac
import html
import io
import logging
import os
import random
import time
from datetime import datetime, timezone
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("legend-contest")

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
GROUP_ID = os.environ.get("TELEGRAM_GROUP_ID", "")
GROUP_URL = os.environ.get("TELEGRAM_GROUP_URL", "https://t.me/the_legend_mm")
WEBHOOK_SECRET = os.environ.get("TELEGRAM_WEBHOOK_SECRET", "")
ADMIN_KEY = os.environ.get("ADMIN_KEY", "")
SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
# Optional ISO times, e.g. 2026-11-15T20:00:00Z (UTC) or 2026-11-15T21:00:00+01:00.
# Only joins between start and end are credited.
CONTEST_STARTS_AT = os.environ.get("CONTEST_STARTS_AT", "")
CONTEST_ENDS_AT = os.environ.get("CONTEST_ENDS_AT", "")
# Prize per place in USD, 1st place first. Ties split the prizes of the places they share.
PRIZES = [float(x) for x in os.environ.get(
    "CONTEST_PRIZES", "60,45,35,25,20,15,15,15,10,10").split(",") if x.strip()]
# The site hides the contest section this many hours after the end.
HIDE_AFTER_HOURS = 72

CAPTCHA_SECONDS = 300
LOGIN_MAX_AGE = 30 * 24 * 3600
SWEEP_EVERY = 20

app = FastAPI(title="LEGEND invite contest")
_last_sweep = 0.0
_bot_username = ""


def now_ts() -> float:
    return time.time()


def parse_time(value: str) -> datetime | None:
    if not value:
        return None
    try:
        t = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        log.error("bad contest time %r", value)
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def contest_open() -> bool:
    now = datetime.now(timezone.utc)
    start, end = parse_time(CONTEST_STARTS_AT), parse_time(CONTEST_ENDS_AT)
    return (start is None or now >= start) and (end is None or now < end)


def assign_prizes(rows: list[dict[str, Any]]) -> list[float]:
    """Prize per leaderboard row. Needs at least 1 verified invite.
    Tied rows share the prizes of the places they cover, e.g. two people
    tied for 3rd split the 3rd and 4th prizes equally."""
    prizes = [0.0] * len(rows)
    i = 0
    while i < len(rows):
        j = i
        while j < len(rows) and rows[j]["rank"] == rows[i]["rank"]:
            j += 1
        if rows[i]["verified"] > 0:
            start = int(rows[i]["rank"]) - 1
            pool = sum(PRIZES[start:start + (j - i)])
            for k in range(i, j):
                prizes[k] = round(pool / (j - i), 2)
        i = j
    return prizes


# --------------------------------------------------
# Supabase (PostgREST RPC) and Telegram helpers
# --------------------------------------------------

async def rpc(fn: str, **args: Any) -> Any:
    headers = {"apikey": SUPABASE_KEY, "Content-Type": "application/json"}
    # Legacy service_role keys are JWTs; new sb_secret_ keys go in apikey only.
    if SUPABASE_KEY.startswith("eyJ"):
        headers["Authorization"] = f"Bearer {SUPABASE_KEY}"
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.post(f"{SUPABASE_URL}/rest/v1/rpc/{fn}", json=args, headers=headers)
    if r.status_code >= 300:
        raise RuntimeError(f"Supabase {fn} failed: {r.status_code} {r.text[:200]}")
    return r.json() if r.content else None


async def tg(method: str, payload: dict[str, Any], quiet: bool = False) -> Any:
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.post(f"https://api.telegram.org/bot{TOKEN}/{method}", json=payload)
    data = r.json()
    if not data.get("ok"):
        if quiet:
            log.info("Telegram %s ignored: %s", method, data.get("description"))
            return None
        raise RuntimeError(f"Telegram {method} failed: {data.get('description')}")
    return data.get("result")


def in_group(member: dict[str, Any]) -> bool:
    status = member.get("status", "")
    return status in {"creator", "administrator", "member"} or (
        status == "restricted" and bool(member.get("is_member"))
    )


def display(user: dict[str, Any]) -> str:
    return (user.get("first_name") or user.get("username") or "friend")[:40]


async def bot_username() -> str:
    global _bot_username
    if not _bot_username:
        _bot_username = (await tg("getMe", {}))["username"]
    return _bot_username


# --------------------------------------------------
# Captcha
# --------------------------------------------------

def new_question() -> tuple[str, int, list[int]]:
    a, b = random.randint(2, 9), random.randint(2, 9)
    answer = a + b
    options = {answer}
    while len(options) < 4:
        wrong = answer + random.choice([-5, -4, -3, -2, -1, 1, 2, 3, 4, 5])
        if wrong > 0:
            options.add(wrong)
    shuffled = list(options)
    random.shuffle(shuffled)
    return f"{a} + {b}", answer, shuffled


def captcha_markup(uid: int, options: list[int]) -> dict[str, Any]:
    return {"inline_keyboard": [[
        {"text": str(n), "callback_data": f"cap:{uid}:{n}"} for n in options
    ]]}


def captcha_text(user: dict[str, Any], question: str, retry: bool = False) -> str:
    who = f'<a href="tg://user?id={int(user["id"])}">{html.escape(display(user))}</a>'
    head = "Wrong answer, try this one." if retry else f"Welcome to LEGEND, {who}!"
    return (
        f"{head}\n\nTo unlock chat, tap the answer to <b>{question} = ?</b>\n"
        f"You have {CAPTCHA_SECONDS // 60} minutes."
    )


async def mute(uid: int) -> None:
    perms = {k: False for k in (
        "can_send_messages", "can_send_audios", "can_send_documents", "can_send_photos",
        "can_send_videos", "can_send_video_notes", "can_send_voice_notes", "can_send_polls",
        "can_send_other_messages", "can_add_web_page_previews", "can_invite_users",
    )}
    await tg("restrictChatMember", {
        "chat_id": GROUP_ID, "user_id": uid, "permissions": perms,
        "use_independent_chat_permissions": True,
    })


async def unmute(uid: int) -> None:
    # All True lifts the restriction; the group's default permissions then apply.
    perms = {k: True for k in (
        "can_send_messages", "can_send_audios", "can_send_documents", "can_send_photos",
        "can_send_videos", "can_send_video_notes", "can_send_voice_notes", "can_send_polls",
        "can_send_other_messages", "can_add_web_page_previews", "can_change_info",
        "can_invite_users", "can_pin_messages", "can_manage_topics",
    )}
    await tg("restrictChatMember", {"chat_id": GROUP_ID, "user_id": uid, "permissions": perms})


async def remove(uid: int) -> None:
    # Ban then unban so they are out of the group but can join again later.
    await tg("banChatMember", {"chat_id": GROUP_ID, "user_id": uid}, quiet=True)
    await tg("unbanChatMember", {"chat_id": GROUP_ID, "user_id": uid, "only_if_banned": True}, quiet=True)


async def delete_message(msg_id: int | None) -> None:
    if msg_id:
        await tg("deleteMessage", {"chat_id": GROUP_ID, "message_id": int(msg_id)}, quiet=True)


async def sweep_expired(force: bool = False) -> None:
    """Remove users whose captcha timed out. Runs lazily on incoming traffic."""
    global _last_sweep
    if not force and now_ts() - _last_sweep < SWEEP_EVERY:
        return
    _last_sweep = now_ts()
    for row in await rpc("lgd_take_expired_captchas") or []:
        await delete_message(row.get("captcha_msg_id"))
        await remove(int(row["telegram_id"]))


# --------------------------------------------------
# Telegram update handlers
# --------------------------------------------------

async def on_member_update(event: dict[str, Any]) -> None:
    if str(event.get("chat", {}).get("id")) != GROUP_ID:
        return
    old, new = event.get("old_chat_member", {}), event.get("new_chat_member", {})
    user = new.get("user") or {}
    uid = int(user.get("id", 0))
    if not uid or user.get("is_bot"):
        return

    joined, left = in_group(new) and not in_group(old), in_group(old) and not in_group(new)
    if left:
        await delete_message(await rpc("lgd_on_leave", p_uid=uid))
        return
    if not joined or new.get("status") in {"creator", "administrator"}:
        return

    link = (event.get("invite_link") or {}).get("invite_link")
    referrer = await rpc(
        "lgd_on_join", p_uid=uid, p_username=user.get("username"),
        p_first_name=user.get("first_name"), p_invite_link=link, p_credit=contest_open(),
    )
    log.info("join uid=%s referrer=%s", uid, referrer)

    await mute(uid)
    question, answer, options = new_question()
    sent = await tg("sendMessage", {
        "chat_id": GROUP_ID, "text": captcha_text(user, question), "parse_mode": "HTML",
        "reply_markup": captcha_markup(uid, options),
    })
    await rpc("lgd_set_captcha", p_uid=uid, p_answer=answer,
              p_msg_id=sent["message_id"], p_ttl_seconds=CAPTCHA_SECONDS)


async def on_callback(cb: dict[str, Any]) -> None:
    data = cb.get("data") or ""
    presser = int((cb.get("from") or {}).get("id", 0))
    if not data.startswith("cap:"):
        await tg("answerCallbackQuery", {"callback_query_id": cb["id"]}, quiet=True)
        return
    _, target, choice = data.split(":")
    if int(target) != presser:
        await tg("answerCallbackQuery", {
            "callback_query_id": cb["id"], "text": "This check is for the new member.", "show_alert": True,
        }, quiet=True)
        return

    msg_id = (cb.get("message") or {}).get("message_id")
    result = await rpc("lgd_captcha_answer", p_uid=presser, p_choice=int(choice))

    if result == "ok":
        await unmute(presser)
        await delete_message(msg_id)
        await tg("answerCallbackQuery", {
            "callback_query_id": cb["id"], "text": "Verified. Welcome to LEGEND!",
        }, quiet=True)
    elif result == "wrong":
        question, answer, options = new_question()
        await tg("editMessageText", {
            "chat_id": GROUP_ID, "message_id": msg_id, "parse_mode": "HTML",
            "text": captcha_text(cb["from"], question, retry=True),
            "reply_markup": captcha_markup(presser, options),
        }, quiet=True)
        await rpc("lgd_set_captcha", p_uid=presser, p_answer=answer,
                  p_msg_id=msg_id, p_ttl_seconds=CAPTCHA_SECONDS)
        await tg("answerCallbackQuery", {
            "callback_query_id": cb["id"], "text": "Wrong answer. Try again.",
        }, quiet=True)
    elif result == "kick":
        await tg("answerCallbackQuery", {
            "callback_query_id": cb["id"], "text": "Too many wrong answers. You can join again later.",
            "show_alert": True,
        }, quiet=True)
        await delete_message(msg_id)
        await remove(presser)
    else:
        await tg("answerCallbackQuery", {"callback_query_id": cb["id"]}, quiet=True)


async def on_message(msg: dict[str, Any]) -> None:
    chat = msg.get("chat") or {}
    user = msg.get("from") or {}

    if chat.get("type") == "private":
        await tg("sendMessage", {
            "chat_id": chat["id"],
            "text": f"Hi! Join the LEGEND community here: {GROUP_URL}\n"
                    "Referral links and the leaderboard are on the LEGEND website.",
        }, quiet=True)
        return
    if str(chat.get("id")) != GROUP_ID:
        return

    # Keep the chat clean: drop "X joined" / "X left" service messages.
    if msg.get("new_chat_members") or msg.get("left_chat_member"):
        await delete_message(msg.get("message_id"))
        return
    if not user.get("id") or user.get("is_bot") or msg.get("sender_chat"):
        return

    await rpc("lgd_on_message", p_uid=int(user["id"]),
              p_username=user.get("username"), p_first_name=user.get("first_name"))


@app.post("/api/webhook")
async def webhook(request: Request):
    if not WEBHOOK_SECRET or request.headers.get("x-telegram-bot-api-secret-token") != WEBHOOK_SECRET:
        raise HTTPException(403, "Forbidden")
    update = await request.json()
    try:
        if "chat_member" in update:
            await on_member_update(update["chat_member"])
        elif "callback_query" in update:
            await on_callback(update["callback_query"])
        elif "message" in update:
            await on_message(update["message"])
        await sweep_expired()
    except Exception:
        log.exception("update %s failed", update.get("update_id"))
        # 500 makes Telegram retry; handlers are safe to repeat.
        raise HTTPException(500, "Update failed")
    return {"ok": True}


# --------------------------------------------------
# Site API
# --------------------------------------------------

def check_login(auth: dict[str, Any]) -> dict[str, Any]:
    """Verify a Telegram Login Widget payload (core.telegram.org/widgets/login)."""
    if not isinstance(auth, dict) or "hash" not in auth or "id" not in auth:
        raise HTTPException(401, "Log in with Telegram first.")
    fields = {k: v for k, v in auth.items() if k != "hash" and v is not None}
    check = "\n".join(f"{k}={fields[k]}" for k in sorted(fields))
    secret = hashlib.sha256(TOKEN.encode()).digest()
    expected = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, str(auth["hash"])):
        raise HTTPException(401, "Telegram login could not be verified. Log in again.")
    if now_ts() - int(auth.get("auth_date", 0)) > LOGIN_MAX_AGE:
        raise HTTPException(401, "Login expired. Log in again.")
    return fields


@app.get("/api/config")
async def config():
    return JSONResponse(
        {"bot_username": await bot_username(), "group_url": GROUP_URL,
         "starts_at": (t := parse_time(CONTEST_STARTS_AT)) and t.isoformat(),
         "ends_at": (t := parse_time(CONTEST_ENDS_AT)) and t.isoformat(),
         "hide_after_hours": HIDE_AFTER_HOURS,
         "server_time": datetime.now(timezone.utc).isoformat(),
         "prizes": PRIZES,
         "rules": {"hours": 12, "messages": 3}},
        headers={"Cache-Control": "no-store"},
    )


@app.get("/api/leaderboard")
async def leaderboard():
    try:
        await sweep_expired()
    except Exception:
        log.exception("sweep failed")
    rows = await rpc("lgd_leaderboard", p_limit=100) or []
    prizes = assign_prizes(rows)
    return JSONResponse(
        {"updated_at": datetime.now(timezone.utc).isoformat(),
         "entries": [{**{k: r[k] for k in ("rank", "name", "invites", "verified")}, "prize": prize}
                     for r, prize in zip(rows, prizes)]},
        headers={"Cache-Control": "public, s-maxage=30"},
    )


@app.post("/api/ref/me")
async def ref_me(request: Request):
    user = check_login(await request.json())
    uid = int(user["id"])

    member = await tg("getChatMember", {"chat_id": GROUP_ID, "user_id": uid}, quiet=True)
    if not member or not in_group(member):
        return JSONResponse(
            {"error": "not_member", "message": "Join the LEGEND Telegram group first, then try again.",
             "group_url": GROUP_URL},
            status_code=403,
        )

    rows = await rpc("lgd_ensure_ref", p_uid=uid, p_username=user.get("username"),
                     p_first_name=user.get("first_name"))
    slug, link = rows[0]["ref_slug"], rows[0]["ref_invite_link"]
    if not link:
        created = await tg("createChatInviteLink", {"chat_id": GROUP_ID, "name": f"ref {slug}"[:32]})
        await rpc("lgd_save_ref_link", p_uid=uid, p_link=created["invite_link"])

    stats = (await rpc("lgd_my_stats", p_uid=uid) or [{}])[0]
    invites, verified = stats.get("invites", 0), stats.get("verified", 0)
    top = await rpc("lgd_leaderboard", p_limit=100) or []
    prize = next((p for r, p in zip(top, assign_prizes(top)) if int(r["telegram_id"]) == uid), 0.0)
    return {
        "slug": slug,
        "name": f"@{user['username']}" if user.get("username") else user.get("first_name"),
        "rank": stats.get("rank"),
        "invites": invites,
        "verified": verified,
        "pending": invites - verified,
        "prize": prize,
    }


@app.get("/api/ref/resolve")
async def ref_resolve(slug: str = ""):
    rows = await rpc("lgd_resolve", p_slug=slug.strip().lstrip("@")[:40]) if slug else []
    if not rows:
        return {"found": False, "invite_link": GROUP_URL}
    return {"found": True, "name": rows[0]["name"], "invite_link": rows[0]["invite_link"]}


# --------------------------------------------------
# Admin (open in a browser with ?key=ADMIN_KEY)
# --------------------------------------------------

def require_admin(key: str) -> None:
    if not ADMIN_KEY or not hmac.compare_digest(key, ADMIN_KEY):
        raise HTTPException(403, "Forbidden")


@app.get("/api/admin/setup")
async def admin_setup(request: Request, key: str = ""):
    """Points the bot's webhook here and checks the bot's group permissions."""
    require_admin(key)
    url = f"{request.base_url}".rstrip("/").replace("http://", "https://") + "/api/webhook"
    await tg("setWebhook", {
        "url": url, "secret_token": WEBHOOK_SECRET,
        "allowed_updates": ["message", "callback_query", "chat_member"],
    })
    me = await tg("getMe", {})
    bot = await tg("getChatMember", {"chat_id": GROUP_ID, "user_id": me["id"]}, quiet=True) or {}
    needed = ["can_restrict_members", "can_delete_messages", "can_invite_users"]
    return {
        "webhook": url,
        "bot": f"@{me['username']}",
        "bot_is_admin": bot.get("status") == "administrator",
        "missing_permissions": [p for p in needed if not bot.get(p)],
        "webhook_info": await tg("getWebhookInfo", {}),
    }


@app.get("/api/admin/report")
async def admin_report(key: str = ""):
    """CSV of every referred member, to check the top entries by hand."""
    require_admin(key)
    rows = await rpc("lgd_report") or []
    out = io.StringIO()
    cols = ["referrer_id", "referrer", "invitee_id", "invitee_username", "invitee_name",
            "status", "joined_at", "captcha_passed_at", "msg_count", "verified"]
    writer = csv.DictWriter(out, fieldnames=cols)
    writer.writeheader()
    for r in rows:
        writer.writerow({c: r.get(c) for c in cols})
    return PlainTextResponse(out.getvalue(), media_type="text/csv")


@app.get("/api/admin/audit")
async def admin_audit(key: str = ""):
    """Re-checks every verified invitee is still in the group (run before paying)."""
    require_admin(key)
    removed = []
    for row in await rpc("lgd_verified_ids") or []:
        uid = int(row["telegram_id"])
        member = await tg("getChatMember", {"chat_id": GROUP_ID, "user_id": uid}, quiet=True)
        if member is not None and not in_group(member):
            await rpc("lgd_on_leave", p_uid=uid)
            removed.append(uid)
    return {"marked_left": removed}


@app.get("/api/health")
async def health():
    missing = [k for k, v in {
        "TELEGRAM_BOT_TOKEN": TOKEN, "TELEGRAM_GROUP_ID": GROUP_ID,
        "TELEGRAM_WEBHOOK_SECRET": WEBHOOK_SECRET, "SUPABASE_URL": SUPABASE_URL,
        "SUPABASE_SERVICE_ROLE_KEY": SUPABASE_KEY, "ADMIN_KEY": ADMIN_KEY,
    }.items() if not v]
    return {"ok": not missing, "missing_env": missing}
