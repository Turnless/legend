// ======================================
// LEGEND INVITE CONTEST
// Talks to the same-site /api backend. No secrets here.
// ======================================

(function () {
  const AUTH_KEY = "lgd_tg_auth";
  const $ = id => document.getElementById(id);
  const status = $("lgd-ref-status");
  let myName = null;
  let clockOffset = 0; // server time minus this device's time

  function money(value) {
    return "$" + (Number.isInteger(value) ? value : value.toFixed(2));
  }
  function ordinal(n) {
    const s = ["th", "st", "nd", "rd"], v = n % 100;
    return n + (s[(v - 20) % 10] || s[v] || s[0]);
  }

  function readAuth() {
    try { return JSON.parse(localStorage.getItem(AUTH_KEY) || "null"); } catch { return null; }
  }
  function saveAuth(auth) {
    try { auth ? localStorage.setItem(AUTH_KEY, JSON.stringify(auth)) : localStorage.removeItem(AUTH_KEY); } catch {}
  }
  function setStatus(text, isError) {
    status.textContent = text || "";
    status.classList.toggle("lgd-error", Boolean(isError));
  }
  function el(tag, text, cls) {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (cls) node.className = cls;
    return node;
  }

  // ---------- Leaderboard ----------

  async function loadBoard() {
    const body = $("lgd-board-rows");
    try {
      const res = await fetch("/api/leaderboard", { cache: "no-store" });
      if (!res.ok) throw new Error(res.status);
      const data = await res.json();
      body.replaceChildren();
      if (!data.entries.length) {
        const tr = el("tr");
        const td = el("td", "No invites yet. Be the first legend on the board.", "lgd-empty");
        td.colSpan = 5;
        tr.appendChild(td);
        body.appendChild(tr);
      }
      data.entries.forEach(entry => {
        const tr = el("tr");
        if (myName && entry.name === myName) tr.className = "lgd-me-row";
        tr.append(el("td", entry.rank), el("td", entry.name), el("td", entry.invites), el("td", entry.verified),
          el("td", entry.prize ? money(entry.prize) : "—", entry.prize ? "lgd-prize-cell" : ""));
        body.appendChild(tr);
      });
      $("lgd-board-updated").textContent = "Updated " + new Date(data.updated_at).toLocaleTimeString();
    } catch (error) {
      console.error("Leaderboard error:", error);
      $("lgd-board-updated").textContent = "Leaderboard unavailable, retrying...";
    }
  }

  // ---------- Your link ----------

  function showMe(me) {
    myName = me.name;
    const link = `${location.origin}/ref?${me.slug}`;
    const text = "Join me in the LEGEND ($LGD) community 👑";
    $("lgd-me-name").textContent = me.name;
    $("lgd-me-link").textContent = link.replace(/^https?:\/\//, "");
    $("lgd-copy-link").dataset.link = link;
    $("lgd-share-tg").href = `https://t.me/share/url?url=${encodeURIComponent(link)}&text=${encodeURIComponent(text)}`;
    $("lgd-share-x").href = `https://x.com/intent/post?text=${encodeURIComponent(text + " " + link)}`;
    $("lgd-me-rank").textContent = me.rank ? `#${me.rank}` : "—";
    $("lgd-me-invites").textContent = me.invites;
    $("lgd-me-verified").textContent = me.verified;
    $("lgd-me-pending").textContent = me.pending;
    $("lgd-me-prize").hidden = !me.prize;
    $("lgd-me-prize").textContent = me.prize ? `You're in the prize zone: ${money(me.prize)} right now.` : "";
    showHolder(me);
    $("lgd-ref-login").hidden = true;
    $("lgd-ref-me").hidden = false;
  }

  // ---------- Holder rule ----------

  const lgdAmount = n => Number(n).toLocaleString("en-US", { maximumFractionDigits: 0 });

  function showHolder(h) {
    $("lgd-hold-need").textContent = h.hold_lgd ? lgdAmount(h.hold_lgd) : "—";
    const status = $("lgd-hold-status");
    status.classList.toggle("lgd-ok", Boolean(h.holder));
    if (!h.wallet) {
      status.textContent = "No wallet linked yet.";
      $("lgd-link-wallet").textContent = "Link wallet";
      return;
    }
    const w = h.wallet.slice(0, 6) + "…" + h.wallet.slice(-4);
    const bal = h.balance_lgd == null ? "?" : lgdAmount(h.balance_lgd);
    status.textContent = h.holder
      ? `✓ ${w} holds ${bal} LGD. You're eligible.`
      : `${w} holds ${bal} LGD. ${h.hold_lgd ? `Buy ${lgdAmount(Math.max(0, h.hold_lgd - (h.balance_lgd || 0)))} more LGD to be eligible.` : ""}`;
    $("lgd-link-wallet").textContent = "Change wallet";
  }

  $("lgd-link-wallet").addEventListener("click", async () => {
    const eth = window.ethereum;
    const auth = readAuth();
    if (!eth) {
      setStatus("No wallet found in this browser. Open this site in your wallet app's browser to link a wallet.", true);
      return;
    }
    try {
      const [address] = await eth.request({ method: "eth_requestAccounts" });
      const ts = Math.floor(Date.now() / 1000);
      const message = `LEGEND invite contest\nLink wallet ${address.toLowerCase()} to Telegram account ${auth.id}\nTime: ${ts}`;
      setStatus("Sign the message in your wallet. It's free and sends nothing.");
      const signature = await eth.request({ method: "personal_sign", params: [message, address] });
      const res = await fetch("/api/ref/wallet", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ auth, address, ts, signature })
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || "Could not link the wallet.");
      showHolder(data);
      setStatus("Wallet linked.");
    } catch (error) {
      setStatus(error.code === 4001 ? "Signing cancelled." : error.message, true);
    }
  });

  function showLogin() {
    myName = null;
    $("lgd-ref-login").hidden = false;
    $("lgd-ref-me").hidden = true;
  }

  async function loadMe(auth) {
    setStatus("Checking your group membership...");
    try {
      const res = await fetch("/api/ref/me", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(auth)
      });
      const data = await res.json().catch(() => ({}));
      if (res.status === 403 && data.error === "not_member") {
        showLogin();
        setStatus("You're not in the LEGEND group yet. Join it, then log in again.", true);
        return;
      }
      if (res.status === 401) {
        saveAuth(null);
        showLogin();
        setStatus(data.detail || "Please log in again.", true);
        return;
      }
      if (!res.ok) throw new Error(res.status);
      showMe(data);
      setStatus("");
      loadBoard();
    } catch (error) {
      console.error("Referral error:", error);
      setStatus("Could not load your link. Please try again in a moment.", true);
    }
  }

  // Called by the Telegram Login Widget.
  window.lgdOnTelegramAuth = function (user) {
    saveAuth(user);
    loadMe(user);
    window.dispatchEvent(new CustomEvent("lgd-auth", { detail: user }));
  };

  $("lgd-copy-link").addEventListener("click", async event => {
    const button = event.currentTarget;
    try {
      await navigator.clipboard.writeText(button.dataset.link);
      button.textContent = "Copied!";
    } catch {
      button.textContent = "Copy failed";
    }
    setTimeout(() => { button.textContent = "Copy"; }, 1600);
  });

  $("lgd-logout").addEventListener("click", () => {
    saveAuth(null);
    showLogin();
    setStatus("");
    loadBoard();
    window.dispatchEvent(new CustomEvent("lgd-auth", { detail: null }));
  });

  // ---------- Timer and prizes ----------

  function renderPrizes(prizes) {
    const box = $("lgd-prizes");
    box.replaceChildren();
    prizes.forEach((amount, i) => {
      const chip = el("div", undefined, "lgd-prize-chip" + (i < 3 ? " lgd-prize-top" : ""));
      chip.append(el("span", ordinal(i + 1)), el("strong", money(amount)));
      box.appendChild(chip);
    });
    const pool = prizes.reduce((a, b) => a + b, 0);
    $("lgd-prize-pool").textContent = Number.isInteger(pool) ? pool : pool.toFixed(2);
    $("lgd-prize-count").textContent = prizes.length;
  }

  function formatDate(date) {
    return new Intl.DateTimeFormat(undefined, {
      weekday: "short", month: "short", day: "numeric",
      hour: "numeric", minute: "2-digit", timeZoneName: "short"
    }).format(date);
  }

  function formatLength(ms) {
    const hours = Math.round(ms / 3600000);
    const days = Math.floor(hours / 24), rest = hours % 24;
    const parts = [];
    if (days) parts.push(days + (days === 1 ? " day" : " days"));
    if (rest) parts.push(rest + (rest === 1 ? " hour" : " hours"));
    return parts.join(" ") || "Under 1 hour";
  }

  function startTimer(config) {
    const start = config.starts_at ? new Date(config.starts_at) : null;
    const end = config.ends_at ? new Date(config.ends_at) : null;
    if (!start && !end) return;

    const hideAt = end ? end.getTime() + config.hide_after_hours * 3600000 : null;
    const note = el("p", "", "lgd-phase-note");
    $("lgd-ref-login").before(note);
    $("lgd-timer").hidden = false;
    $("lgd-date-start").textContent = start ? formatDate(start) : "Now";
    $("lgd-date-end").textContent = end ? formatDate(end) : "Not set";
    $("lgd-date-length").textContent = start && end ? formatLength(end - start) : "—";

    const pad = n => String(n).padStart(2, "0");
    let timer;

    function tick() {
      const now = Date.now() + clockOffset;
      let phase, target;
      if (start && now < start) { phase = "upcoming"; target = start; }
      else if (end && now < end) { phase = "live"; target = end; }
      else if (end && now < hideAt) { phase = "ended"; }
      else if (end) { phase = "gone"; }
      else { phase = "live"; }

      if (phase === "gone") {
        $("contest").style.display = "none";
        $("lgd-nav-contest").style.display = "none";
        clearInterval(timer);
        return;
      }

      $("lgd-timer").dataset.phase = phase;
      $("lgd-timer-label").textContent =
        phase === "upcoming" ? "CONTEST STARTS IN" : phase === "live" ? "CONTEST ENDS IN" : "CONTEST ENDED";
      $("lgd-timer-clock").hidden = !target;
      $("lgd-timer-ended").hidden = phase !== "ended";
      note.textContent =
        phase === "upcoming" ? "The contest hasn't started yet. You can get your link now; invites count from the start." :
        phase === "ended" ? "The contest has ended. New invites no longer count." : "";
      note.hidden = !note.textContent;

      if (target) {
        const left = Math.max(0, Math.floor((target - now) / 1000));
        $("lgd-t-d").textContent = pad(Math.floor(left / 86400));
        $("lgd-t-h").textContent = pad(Math.floor(left / 3600) % 24);
        $("lgd-t-m").textContent = pad(Math.floor(left / 60) % 60);
        $("lgd-t-s").textContent = pad(left % 60);
      }
    }

    tick();
    timer = setInterval(tick, 1000);
  }

  async function init() {
    try {
      const res = await fetch("/api/config");
      const config = await res.json();
      if (config.server_time) clockOffset = new Date(config.server_time) - Date.now();
      if (config.prizes) renderPrizes(config.prizes);
      startTimer(config);
      $("lgd-join-group").href = config.group_url;
      $("lgd-rule-hours").textContent = config.rules.hours;
      $("lgd-rule-msgs").textContent = config.rules.messages;

      const widget = document.createElement("script");
      widget.async = true;
      widget.src = "https://telegram.org/js/telegram-widget.js?22";
      widget.setAttribute("data-telegram-login", config.bot_username);
      widget.setAttribute("data-size", "large");
      widget.setAttribute("data-radius", "99");
      widget.setAttribute("data-onauth", "lgdOnTelegramAuth(user)");
      $("lgd-tg-widget").appendChild(widget);
    } catch (error) {
      console.error("Contest config error:", error);
      setStatus("The invite contest is temporarily unavailable.", true);
    }

    const auth = readAuth();
    if (auth) loadMe(auth); else loadBoard();
    setInterval(loadBoard, 60000);
  }

  init();
})();
