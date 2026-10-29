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

import base64
import csv
import hashlib
import hmac
import html
import io
import json
import logging
import math
import os
import random
import time
from datetime import datetime, timezone
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse

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


def assign_prizes(rows: list[dict[str, Any]], score: str = "verified") -> list[float]:
    """Prize per leaderboard row. Needs a score of at least 1.
    Tied rows share the prizes of the places they cover, e.g. two people
    tied for 3rd split the 3rd and 4th prizes equally."""
    prizes = [0.0] * len(rows)
    i = 0
    while i < len(rows):
        j = i
        while j < len(rows) and rows[j]["rank"] == rows[i]["rank"]:
            j += 1
        if rows[i][score] > 0:
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
# Meme contest: entry fee paid in LGD, community votes
# --------------------------------------------------

CHAIN_RPC = "https://rpc.mainnet.chain.robinhood.com"
LGD_TOKEN = "0x165dc537ec67ce2c033bcfcac562e6c311c7c213"
LGD_CURVE = "0x92175de95b1a5f85c719a9dd4fe2e86ed6ce2b6f"
TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
MEME_FEE_WALLET = os.environ.get("MEME_FEE_WALLET", "").strip().lower()
MEME_STARTS_AT = os.environ.get("MEME_STARTS_AT", "")
MEME_ENDS_AT = os.environ.get("MEME_ENDS_AT", "")
MEME_FEE_USD = float(os.environ.get("MEME_FEE_USD", "1"))
QUOTE_SECONDS = 30 * 60
MAX_IMAGE_BYTES = 3 * 1024 * 1024
_price_cache: dict[str, float] = {}


def sig3_up(x: float) -> int:
    """Round up to 3 significant figures, e.g. 643,210 -> 644,000."""
    if x <= 0:
        return 0
    step = 10 ** max(0, int(math.floor(math.log10(x))) - 2)
    return int(math.ceil(x / step) * step)


def sig3(x: float) -> int:
    if x <= 0:
        return 0
    step = 10 ** max(0, int(math.floor(math.log10(x))) - 2)
    return int(round(x / step) * step)


async def chain(method: str, params: list[Any]) -> Any:
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.post(CHAIN_RPC, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
    data = r.json()
    if "error" in data:
        raise RuntimeError(f"chain {method}: {data['error']}")
    return data.get("result")


async def lgd_price_usd() -> float:
    """USD price of 1 LGD from the Pons curve and Coinbase ETH price, cached 30 s."""
    if now_ts() - _price_cache.get("ts", 0) < 30:
        return _price_cache["usd"]
    raw = await chain("eth_call", [{"to": LGD_CURVE, "data": "0x0902f1ac"}, "latest"])
    eth, tokens = int(raw[2:66], 16) / 1e18, int(raw[66:130], 16) / 1e18
    async with httpx.AsyncClient(timeout=10) as client:
        eth_usd = float((await client.get("https://api.coinbase.com/v2/prices/ETH-USD/spot")).json()["data"]["amount"])
    _price_cache.update(ts=now_ts(), usd=eth / tokens * eth_usd)
    return _price_cache["usd"]


def meme_phase() -> str:
    now = datetime.now(timezone.utc)
    start, end = parse_time(MEME_STARTS_AT), parse_time(MEME_ENDS_AT)
    if not MEME_FEE_WALLET:
        return "soon"
    if start and now < start:
        return "upcoming"
    if end and now >= end:
        return "ended"
    return "live"


def sign_quote(data: dict[str, Any]) -> str:
    body = base64.urlsafe_b64encode(json.dumps(data, separators=(",", ":")).encode()).decode()
    mac = hmac.new(f"quote:{ADMIN_KEY}:{TOKEN}".encode(), body.encode(), hashlib.sha256).hexdigest()
    return f"{body}.{mac}"


def read_quote(quote: str) -> dict[str, Any]:
    try:
        body, mac = quote.split(".")
        good = hmac.new(f"quote:{ADMIN_KEY}:{TOKEN}".encode(), body.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(mac, good):
            raise ValueError
        return json.loads(base64.urlsafe_b64decode(body))
    except Exception:
        raise HTTPException(400, "The fee quote is invalid. Start again.")


async def require_member(auth: Any) -> dict[str, Any]:
    user = check_login(auth)
    member = await tg("getChatMember", {"chat_id": GROUP_ID, "user_id": int(user["id"])}, quiet=True)
    if not member or not in_group(member):
        raise HTTPException(403, "Join the LEGEND Telegram group first.")
    return user


async def verify_payment(tx_hash: str) -> tuple[str, int, int]:
    """Returns (payer, LGD paid in wei, block timestamp) for LGD sent to the fee wallet."""
    receipt = await chain("eth_getTransactionReceipt", [tx_hash])
    if not receipt:
        raise HTTPException(409, "That transaction isn't confirmed yet. Wait a few seconds and try again.")
    if receipt.get("status") != "0x1":
        raise HTTPException(400, "That transaction failed onchain.")
    paid, payer = 0, None
    for log_ in receipt.get("logs", []):
        topics = log_.get("topics", [])
        if (log_.get("address", "").lower() == LGD_TOKEN and len(topics) == 3 and topics[0] == TRANSFER_TOPIC
                and "0x" + topics[2][-40:].lower() == MEME_FEE_WALLET):
            paid += int(log_["data"], 16)
            payer = "0x" + topics[1][-40:].lower()
    if not payer:
        raise HTTPException(400, "No LGD payment to the contest wallet was found in that transaction.")
    block = await chain("eth_getBlockByNumber", [receipt["blockNumber"], False])
    return payer, paid, int(block["timestamp"], 16)


def image_url(path: str) -> str:
    return f"{SUPABASE_URL}/storage/v1/object/public/memes/{path}"


async def storage(method: str, path: str, content: bytes = b"", content_type: str = "image/jpeg") -> None:
    headers = {"apikey": SUPABASE_KEY, "Content-Type": content_type}
    if SUPABASE_KEY.startswith("eyJ"):
        headers["Authorization"] = f"Bearer {SUPABASE_KEY}"
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.request(method, f"{SUPABASE_URL}/storage/v1/object/memes/{path}",
                                 content=content, headers=headers)
    if r.status_code >= 300:
        raise RuntimeError(f"Storage {method} failed: {r.status_code} {r.text[:200]}")


def image_type(data: bytes) -> str | None:
    if data[:3] == b"\xff\xd8\xff":
        return "jpg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None


@app.get("/api/meme/config")
async def meme_config():
    try:
        price = await lgd_price_usd()
    except Exception:
        log.exception("price failed")
        price = 0.0
    return JSONResponse({
        "phase": meme_phase(),
        "starts_at": (t := parse_time(MEME_STARTS_AT)) and t.isoformat(),
        "ends_at": (t := parse_time(MEME_ENDS_AT)) and t.isoformat(),
        "hide_after_hours": HIDE_AFTER_HOURS,
        "server_time": datetime.now(timezone.utc).isoformat(),
        "fee_lgd": sig3_up(MEME_FEE_USD / price) if price else None,
        "prizes_lgd": [sig3(p / price) for p in PRIZES] if price else None,
        "token": LGD_TOKEN,
        "fee_wallet": MEME_FEE_WALLET or None,
    }, headers={"Cache-Control": "no-store"})


@app.post("/api/meme/quote")
async def meme_quote(request: Request):
    user = await require_member(await request.json())
    if meme_phase() != "live":
        raise HTTPException(403, "Meme contest entries are not open right now.")
    if await rpc("lgd_meme_precheck", p_uid=int(user["id"]), p_tx_hash=None) != "ok":
        raise HTTPException(409, "You already entered the meme contest.")
    amount = sig3_up(MEME_FEE_USD / await lgd_price_usd())
    quote = sign_quote({"uid": int(user["id"]), "lgd": amount, "ts": int(now_ts())})
    return {"amount_lgd": amount, "amount_wei": str(amount * 10**18), "fee_wallet": MEME_FEE_WALLET,
            "token": LGD_TOKEN, "quote": quote, "expires_at": int(now_ts()) + QUOTE_SECONDS}


@app.post("/api/meme/submit")
async def meme_submit(request: Request):
    body = await request.json()
    user = await require_member(body.get("auth"))
    uid = int(user["id"])
    if meme_phase() != "live":
        raise HTTPException(403, "Meme contest entries are not open right now.")
    quote = read_quote(str(body.get("quote", "")))
    if quote.get("uid") != uid:
        raise HTTPException(400, "The fee quote belongs to another account. Start again.")
    try:
        meme_no = int(body.get("meme_no"))
        assert 1 <= meme_no <= 20
    except Exception:
        raise HTTPException(400, "Pick which of the 20 memes you remade.")
    tx_hash = str(body.get("tx_hash", "")).strip().lower()
    if "/tx/" in tx_hash:
        tx_hash = tx_hash.split("/tx/")[1].split("?")[0].split("#")[0]
    if len(tx_hash) != 66 or not tx_hash.startswith("0x"):
        raise HTTPException(400, "That doesn't look like a transaction hash.")
    try:
        image = base64.b64decode(str(body.get("image", "")).split(",")[-1], validate=True)
    except Exception:
        raise HTTPException(400, "The image could not be read.")
    kind = image_type(image)
    if not kind or len(image) > MAX_IMAGE_BYTES:
        raise HTTPException(400, "Upload a JPG, PNG or WEBP image under 3 MB.")

    pre = await rpc("lgd_meme_precheck", p_uid=uid, p_tx_hash=tx_hash)
    if pre != "ok":
        raise HTTPException(409, "You already entered." if pre == "telegram_used" else "That payment was already used for an entry.")

    payer, paid_wei, paid_at = await verify_payment(tx_hash)
    if paid_at < quote["ts"] - 120:
        raise HTTPException(400, "That payment was made before this entry was started. Use a new payment.")
    needed = quote["lgd"] if now_ts() - quote["ts"] <= QUOTE_SECONDS else min(
        quote["lgd"], sig3_up(MEME_FEE_USD / await lgd_price_usd()))
    if paid_wei < needed * 10**18:
        raise HTTPException(400, f"The payment was {paid_wei / 1e18:,.0f} LGD but the fee is {needed:,} LGD.")

    path = f"{uid}-{int(now_ts())}.{kind}"
    await storage("POST", path, image, {"jpg": "image/jpeg", "png": "image/png", "webp": "image/webp"}[kind])
    name = f"@{user['username']}" if user.get("username") else (user.get("first_name") or "LEGEND member")
    result = await rpc("lgd_meme_submit", p_uid=uid, p_name=name[:60], p_meme_no=meme_no, p_image_path=path,
                       p_wallet=payer, p_tx_hash=tx_hash, p_fee_lgd=paid_wei / 1e18)
    if result != "ok":
        try:
            await storage("DELETE", path)
        except Exception:
            log.exception("could not delete %s", path)
        raise HTTPException(409, {
            "telegram_used": "You already entered.",
            "wallet_used": "That wallet was already used for an entry.",
            "tx_used": "That payment was already used for an entry.",
        }.get(result, "Entry could not be saved."))
    log.info("meme entry uid=%s wallet=%s paid=%s", uid, payer, paid_wei / 1e18)
    return {"ok": True, "status": "pending"}


@app.get("/api/meme/entries")
async def meme_entries():
    rows = await rpc("lgd_meme_board") or []
    try:
        price = await lgd_price_usd()
    except Exception:
        price = 0.0
    prizes = assign_prizes(rows, score="votes")
    return JSONResponse({"entries": [{
        "id": r["id"], "name": r["name"], "meme_no": r["meme_no"], "image": image_url(r["image_path"]),
        "votes": r["votes"], "rank": r["rank"], "prize_lgd": sig3(p / price) if p and price else 0,
    } for r, p in zip(rows, prizes)]}, headers={"Cache-Control": "public, s-maxage=15"})


@app.post("/api/meme/me")
async def meme_me(request: Request):
    user = check_login(await request.json())
    row = (await rpc("lgd_meme_my", p_uid=int(user["id"])) or [{}])[0]
    return {"entry_id": row.get("entry_id"), "status": row.get("status"), "voted": row.get("voted") or []}


@app.post("/api/meme/vote")
async def meme_vote(request: Request):
    body = await request.json()
    user = await require_member(body.get("auth"))
    if meme_phase() != "live":
        raise HTTPException(403, "Voting is closed.")
    result = await rpc("lgd_meme_vote", p_entry=int(body.get("entry_id", 0)), p_voter=int(user["id"]))
    if result == "own":
        raise HTTPException(400, "You can't vote for your own entry.")
    if result == "missing":
        raise HTTPException(404, "Entry not found.")
    return {"ok": True, "already": result == "already"}


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


@app.get("/api/admin/memes")
async def admin_memes(key: str = ""):
    """Review page: approve or reject meme entries."""
    require_admin(key)
    rows = await rpc("lgd_meme_admin_list") or []
    cards = []
    for r in rows:
        e = {k: html.escape(str(r.get(k, ""))) for k in r}
        cards.append(f"""<div class="c {e['status']}"><a href="{html.escape(image_url(r['image_path']))}" target="_blank">
<img src="{html.escape(image_url(r['image_path']))}"></a><b>#{e['id']} {e['name']}</b> · remake of meme {e['meme_no']} · {e['votes']} votes
<small>status: <b>{e['status']}</b> · paid {float(r['fee_lgd']):,.0f} LGD · {e['created_at'][:16]}</small>
<small>wallet <a href="https://robinhoodchain.blockscout.com/address/{e['wallet']}" target="_blank">{e['wallet'][:10]}...</a> ·
<a href="https://robinhoodchain.blockscout.com/tx/{e['tx_hash']}" target="_blank">payment</a> · Telegram ID {e['telegram_id']}</small>
<div><button onclick="setStatus({r['id']},'approved')">Approve</button> <button onclick="setStatus({r['id']},'rejected')">Reject</button></div></div>""")
    page = f"""<!doctype html><meta name="viewport" content="width=device-width,initial-scale=1"><title>Meme entries</title>
<style>body{{font:15px system-ui;margin:16px;background:#f7f7f0;color:#18251f}}.g{{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:14px}}
.c{{background:#fff;border:1px solid #dfe5dc;border-radius:14px;padding:12px;display:grid;gap:6px}}.c img{{width:100%;border-radius:10px}}
.pending{{border:2px solid #d6a84f}}.rejected{{opacity:.5}}small{{color:#64736b}}button{{padding:8px 14px;border-radius:99px;border:1px solid #cbd7ce;background:#fff;font-weight:700}}</style>
<h2>Meme entries ({len(rows)})</h2><p>Pending entries first. Approved entries appear in the public gallery.</p><div class="g">{''.join(cards) or 'No entries yet.'}</div>
<script>async function setStatus(id,status){{const r=await fetch('/api/admin/memes/status?key='+encodeURIComponent(new URLSearchParams(location.search).get('key')),{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{id,status}})}});if(r.ok)location.reload();else alert('Failed: '+r.status)}}</script>"""
    return HTMLResponse(page, headers={"Cache-Control": "no-store", "X-Robots-Tag": "noindex"})


@app.post("/api/admin/memes/status")
async def admin_meme_status(request: Request, key: str = ""):
    require_admin(key)
    body = await request.json()
    if body.get("status") not in {"approved", "rejected", "pending"}:
        raise HTTPException(400, "Bad status")
    await rpc("lgd_meme_set_status", p_id=int(body["id"]), p_status=body["status"])
    return {"ok": True}


@app.get("/api/health")
async def health():
    missing = [k for k, v in {
        "TELEGRAM_BOT_TOKEN": TOKEN, "TELEGRAM_GROUP_ID": GROUP_ID,
        "TELEGRAM_WEBHOOK_SECRET": WEBHOOK_SECRET, "SUPABASE_URL": SUPABASE_URL,
        "SUPABASE_SERVICE_ROLE_KEY": SUPABASE_KEY, "ADMIN_KEY": ADMIN_KEY,
    }.items() if not v]
    return {"ok": not missing, "missing_env": missing}
