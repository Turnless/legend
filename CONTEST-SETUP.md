# LEGEND invite contest: setup

The site, the API and the Telegram bot all run in this one Vercel project.
Data lives in Supabase. Users never chat with the bot.

## How it works

1. A user opens the site, taps **Log in with Telegram**.
2. The backend checks they are in the group, then gives them `https://YOUR-SITE/ref?username`.
3. That page sends visitors to a personal Telegram invite link the bot created for the referrer, so every join is credited exactly.
4. New members are muted and must tap the right answer to a sum within 5 minutes. 3 wrong answers or no answer = removed (they can rejoin).
5. An invite becomes **verified** when the person is still in the group 12 hours after joining and has sent 3 messages (at most 1 counted per minute). Leaving cancels it.
6. Only first-time joiners count. Old members who leave and rejoin through a link are not credited.
7. **Timer**: before the start the site shows a countdown to the start (links can already be made, but joins don't count yet). During the contest it counts down to the end. After the end it shows "Contest ended", and 72 hours later the contest section and its menu link disappear. Start, end and duration are shown to everyone in their own time zone.
8. **Prizes**: top 10 by verified invites (then total invites) get the prizes in `CONTEST_PRIZES`. You need at least 1 verified invite to win. Tied people share the prizes of the places they cover (two tied for 3rd split 3rd + 4th).

Changing a time or the prizes in Vercel needs a **Redeploy** to take effect.

## Files

| File | What it is |
|---|---|
| `api/index.py` | Backend: Telegram webhook + site API |
| `requirements.txt`, `vercel.json` | Vercel Python setup and routes (`/api/*`, `/ref`) |
| `supabase/contest.sql` | Tables and functions (new `lgd_` names; old tables untouched) |
| `index.html` | Adds the contest section + nav link |
| `contest.js`, `contest.css` | Contest section logic and style |
| `ref.html` | Invite landing page |

## Setup steps

1. **Supabase**: SQL Editor → paste all of `supabase/contest.sql` → Run.
2. **GitHub**: upload the files above to this repo.
3. **Vercel** (the project that hosts this site) → Settings → Environment Variables:
   - `TELEGRAM_BOT_TOKEN`
   - `TELEGRAM_GROUP_ID` (starts with `-100`)
   - `TELEGRAM_GROUP_URL` (e.g. `https://t.me/the_legend_mm`)
   - `TELEGRAM_WEBHOOK_SECRET` (random letters/numbers, 32+ chars)
   - `SUPABASE_URL`
   - `SUPABASE_SERVICE_ROLE_KEY`
   - `ADMIN_KEY` (another random string, for the admin links below)
   - `CONTEST_STARTS_AT` and `CONTEST_ENDS_AT`: contest start and end. Only joins between them are credited.
     Write them like `2026-11-01T20:00:00+01:00` (20:00 Nigeria time) or `2026-11-01T19:00:00Z` (UTC).
   - optional `CONTEST_PRIZES`: prize per place, 1st first, separated by commas.
     Default `60,45,35,25,20,15,15,15,10,10` (= $250 for the top 10).

   Then Deployments → Redeploy.
4. **BotFather**: `/setdomain` → pick the bot → send your site domain (e.g. `getthelegend.vercel.app`). The login button only works on that domain.
5. **Group**: make the bot an admin with **Ban users**, **Delete messages** and **Invite users via link**. Turn off any other captcha bot (Rose, Combot, etc.).
6. Open `https://YOUR-SITE/api/health` → should show `"ok": true`.
7. Open `https://YOUR-SITE/api/admin/setup?key=YOUR_ADMIN_KEY` → connects the bot. Check `bot_is_admin: true` and `missing_permissions: []`. This also replaces the old legend-work webhook; stop the old Render bot.
8. Test with a second Telegram account: get a link, join through it, solve the captcha, check the leaderboard.

## Before paying prizes

- `https://YOUR-SITE/api/admin/audit?key=YOUR_ADMIN_KEY` re-checks that every verified invite is still in the group.
- `https://YOUR-SITE/api/admin/report?key=YOUR_ADMIN_KEY` downloads a CSV of every invite (IDs, usernames, join time, messages). Check the top entries for fake accounts: lots of invitees with no username, very high Telegram IDs (new accounts), or messages that are all the same.

# Meme contest: setup

Entrants pay a fee in LGD (worth $1 at the current price, shown only as an LGD amount), upload their remake, and members vote. Top 10 by votes win the `CONTEST_PRIZES` split, shown and paid in LGD at the current price.

1. **Supabase**: SQL Editor → paste all of `supabase/memes.sql` → Run (tables + public `memes` image bucket).
2. **Vercel** environment variables, then Redeploy:
   - `MEME_FEE_WALLET`: the wallet that receives entry fees. Entries stay "Opening soon" until it is set.
   - `MEME_STARTS_AT`, `MEME_ENDS_AT`: same format as the invite contest.
   - optional `MEME_FEE_USD` (default `1`).
3. **Review entries** at `https://YOUR-SITE/api/admin/memes?key=YOUR_ADMIN_KEY`. New entries are hidden until you tap Approve. The page shows each payer wallet and payment link.
4. Rules enforced by the server: payment checked onchain (LGD, to the fee wallet, full amount, each transaction once), 1 entry per Telegram account and per wallet, group members only, no voting for your own entry, voting only while the contest is open. The contest card hides 72 hours after the end; the meme files stay.

# Invite contest: holder rule

Winners must hold LGD (worth `INVITE_HOLD_USD`, default $2, shown only as an LGD amount) in a wallet they link on the site by signing a free message.

1. **Supabase**: run `supabase/holders.sql` once.
2. Optional Vercel variable `INVITE_HOLD_USD` (default `2`).
3. At the end, open `https://YOUR-SITE/api/admin/winners?key=YOUR_ADMIN_KEY`: every prize place with its linked wallet, live LGD balance and `eligible` true/false. Run `/api/admin/audit` first.
