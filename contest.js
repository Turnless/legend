// ======================================
// LEGEND INVITE CONTEST
// Talks to the same-site /api backend. No secrets here.
// ======================================

(function () {
  const AUTH_KEY = "lgd_tg_auth";
  const $ = id => document.getElementById(id);
  const status = $("lgd-ref-status");
  let myName = null;

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
        td.colSpan = 4;
        tr.appendChild(td);
        body.appendChild(tr);
      }
      data.entries.forEach(entry => {
        const tr = el("tr");
        if (myName && entry.name === myName) tr.className = "lgd-me-row";
        tr.append(el("td", entry.rank), el("td", entry.name), el("td", entry.invites), el("td", entry.verified));
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
    $("lgd-ref-login").hidden = true;
    $("lgd-ref-me").hidden = false;
  }

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
  });

  async function init() {
    try {
      const res = await fetch("/api/config");
      const config = await res.json();
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
