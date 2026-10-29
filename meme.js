// ======================================
// LEGEND MEME CONTEST
// Entry fee is paid in LGD; members vote; top 10 win LGD.
// ======================================

(function () {
  const AUTH_KEY = "lgd_tg_auth";
  const CHAIN_ID = "0x1237"; // 4663
  const $ = id => document.getElementById(id);
  const status = $("lgd-meme-status");
  let config = null;
  let quote = null;
  let imageData = null;
  let mine = { entry_id: null, status: null, voted: [] };
  let clockOffset = 0;

  const lgd = n => Number(n).toLocaleString("en-US");
  const short = n => new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 2 }).format(n);
  function auth() {
    try { return JSON.parse(localStorage.getItem(AUTH_KEY) || "null"); } catch { return null; }
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
  async function post(url, body) {
    const res = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      const error = new Error(typeof data.detail === "string" ? data.detail : "Something went wrong. Try again.");
      error.status = res.status;
      throw error;
    }
    return data;
  }
  function ordinal(n) {
    const s = ["th", "st", "nd", "rd"], v = n % 100;
    return n + (s[(v - 20) % 10] || s[v] || s[0]);
  }

  // ---------- Config, fee, prizes, timer ----------

  async function loadConfig() {
    const res = await fetch("/api/meme/config", { cache: "no-store" });
    config = await res.json();
    clockOffset = new Date(config.server_time) - Date.now();
    $("lgd-meme-fee").textContent = config.fee_lgd ? lgd(config.fee_lgd) : "—";
    const box = $("lgd-meme-prizes");
    box.replaceChildren();
    (config.prizes_lgd || []).forEach((amount, i) => {
      const chip = el("div", undefined, "lgd-prize-chip" + (i < 3 ? " lgd-prize-top" : ""));
      chip.append(el("span", ordinal(i + 1)), el("strong", short(amount)));
      box.appendChild(chip);
    });
    if (config.prizes_lgd) box.appendChild(el("p", "Prizes in LGD, updated live with the LGD price.", "lgd-meme-prize-note"));
  }

  function phaseNow() {
    if (!config || config.phase === "soon") return "soon";
    const now = Date.now() + clockOffset;
    const start = config.starts_at && new Date(config.starts_at).getTime();
    const end = config.ends_at && new Date(config.ends_at).getTime();
    if (start && now < start) return "upcoming";
    if (end && now >= end + config.hide_after_hours * 3600000) return "gone";
    if (end && now >= end) return "ended";
    return "live";
  }

  function countdown(ms) {
    const s = Math.max(0, Math.floor(ms / 1000));
    const d = Math.floor(s / 86400), h = Math.floor(s / 3600) % 24, m = Math.floor(s / 60) % 60;
    return (d ? d + "d " : "") + String(h).padStart(2, "0") + "h " + String(m).padStart(2, "0") + "m " + String(s % 60).padStart(2, "0") + "s";
  }

  let lastPhase = null;
  function tick() {
    const phase = phaseNow();
    const now = Date.now() + clockOffset;
    const timer = $("lgd-meme-timer");
    if (phase === "gone") {
      ["lgd-meme-callout", "lgd-meme-entry", "lgd-meme-gallery"].forEach(id => { $(id).hidden = true; });
      return;
    }
    if (phase === "upcoming") {
      timer.hidden = false;
      $("lgd-meme-timer-label").textContent = "ENTRIES OPEN IN";
      $("lgd-meme-timer-clock").textContent = countdown(new Date(config.starts_at) - now);
    } else if (phase === "live" && config.ends_at) {
      timer.hidden = false;
      $("lgd-meme-timer-label").textContent = "ENDS IN";
      $("lgd-meme-timer-clock").textContent = countdown(new Date(config.ends_at) - now);
    } else if (phase === "ended") {
      timer.hidden = false;
      $("lgd-meme-timer-label").textContent = "CONTEST ENDED";
      $("lgd-meme-timer-clock").textContent = "Winners are being checked";
    } else if (phase === "soon") {
      timer.hidden = false;
      $("lgd-meme-timer-label").textContent = "ENTRIES";
      $("lgd-meme-timer-clock").textContent = "Opening soon";
    } else {
      timer.hidden = true;
    }
    $("lgd-meme-enter-btn").hidden = phase !== "live";
    $("lgd-meme-entry").hidden = phase !== "live";
    if (phase !== lastPhase) {
      lastPhase = phase;
      loadEntries();
    }
  }

  // ---------- Login and my state ----------

  async function refreshMe() {
    const a = auth();
    $("lgd-meme-login").hidden = Boolean(a);
    $("lgd-meme-form").hidden = !a;
    if (!a) return;
    try {
      mine = await post("/api/meme/me", a);
    } catch (error) {
      if (error.status === 401) { $("lgd-meme-login").hidden = false; $("lgd-meme-form").hidden = true; }
      return;
    }
    if (mine.entry_id) {
      $("lgd-meme-form").hidden = true;
      setStatus(mine.status === "approved" ? "Your entry is live in the gallery. Share it and get votes!" :
        mine.status === "rejected" ? "Your entry was not approved. Contact the team in Telegram." :
        "Your entry is in. It will appear in the gallery once it's reviewed.");
    }
    loadEntries();
  }

  // ---------- Form ----------

  for (let i = 1; i <= 20; i++) {
    const option = el("option", `Meme ${String(i).padStart(2, "0")}`);
    option.value = i;
    $("lgd-meme-no").appendChild(option);
  }

  function showPreview() {
    const n = $("lgd-meme-no").value;
    $("lgd-meme-preview").hidden = !(imageData && n);
    if (n) $("lgd-meme-preview-orig").src = `assets/memes/legend-meme-${String(n).padStart(2, "0")}.jpg`;
    if (imageData) $("lgd-meme-preview-mine").src = imageData;
  }

  // Shrink the image in the browser so uploads stay small and fast.
  function resize(file) {
    return new Promise((resolve, reject) => {
      const img = new Image();
      img.onload = () => {
        const scale = Math.min(1, 1600 / Math.max(img.width, img.height));
        const canvas = document.createElement("canvas");
        canvas.width = Math.round(img.width * scale);
        canvas.height = Math.round(img.height * scale);
        const ctx = canvas.getContext("2d");
        ctx.fillStyle = "#fff";
        ctx.fillRect(0, 0, canvas.width, canvas.height);
        ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
        URL.revokeObjectURL(img.src);
        resolve(canvas.toDataURL("image/jpeg", 0.85));
      };
      img.onerror = () => reject(new Error("That file isn't an image we can read."));
      img.src = URL.createObjectURL(file);
    });
  }

  $("lgd-meme-no").addEventListener("change", showPreview);
  $("lgd-meme-file").addEventListener("change", async event => {
    const file = event.target.files[0];
    imageData = null;
    if (!file) return showPreview();
    try {
      imageData = await resize(file);
      setStatus("");
    } catch (error) {
      setStatus(error.message, true);
    }
    showPreview();
  });

  async function getQuote() {
    if (quote && Date.now() / 1000 < quote.expires_at - 60) return quote;
    quote = await post("/api/meme/quote", auth());
    $("lgd-pay-amount").textContent = lgd(quote.amount_lgd);
    $("lgd-pay-amount2").textContent = lgd(quote.amount_lgd);
    $("lgd-pay-wallet-addr").textContent = quote.fee_wallet;
    return quote;
  }

  function ready() {
    if (!$("lgd-meme-no").value) { setStatus("Choose which meme you remade.", true); return false; }
    if (!imageData) { setStatus("Add your image first.", true); return false; }
    return true;
  }

  async function submit(txHash) {
    setStatus("Checking your payment onchain...");
    for (let attempt = 0; attempt < 30; attempt++) {
      try {
        await post("/api/meme/submit", {
          auth: auth(), quote: quote.quote, tx_hash: txHash,
          meme_no: Number($("lgd-meme-no").value), image: imageData
        });
        quote = null;
        await refreshMe();
        return;
      } catch (error) {
        // 409 "not confirmed yet": wait and retry.
        if (error.status === 409 && /confirmed/.test(error.message)) {
          await new Promise(r => setTimeout(r, 2000));
          continue;
        }
        setStatus(error.message, true);
        return;
      }
    }
    setStatus("The payment is taking long to confirm. Paste the transaction hash under 'Pay another way' and submit again.", true);
  }

  async function switchChain(eth) {
    try {
      await eth.request({ method: "wallet_switchEthereumChain", params: [{ chainId: CHAIN_ID }] });
    } catch (error) {
      if (error.code !== 4902) throw error;
      await eth.request({ method: "wallet_addEthereumChain", params: [{
        chainId: CHAIN_ID, chainName: "Robinhood Chain",
        nativeCurrency: { name: "Ether", symbol: "ETH", decimals: 18 },
        rpcUrls: ["https://rpc.mainnet.chain.robinhood.com"],
        blockExplorerUrls: ["https://robinhoodchain.blockscout.com"]
      }] });
    }
  }

  $("lgd-pay-wallet").addEventListener("click", async () => {
    if (!ready()) return;
    const eth = window.ethereum;
    if (!eth) {
      setStatus("No wallet found in this browser. Open this site in your wallet app's browser, or use 'Pay another way'.", true);
      $("lgd-pay").querySelector("details").open = true;
      return;
    }
    try {
      setStatus("Getting the entry fee...");
      const q = await getQuote();
      const [from] = await eth.request({ method: "eth_requestAccounts" });
      await switchChain(eth);
      const data = "0xa9059cbb" + q.fee_wallet.slice(2).toLowerCase().padStart(64, "0") +
        BigInt(q.amount_wei).toString(16).padStart(64, "0");
      setStatus(`Confirm the payment of ${lgd(q.amount_lgd)} LGD in your wallet...`);
      const txHash = await eth.request({ method: "eth_sendTransaction", params: [{ from, to: q.token, data }] });
      await submit(txHash);
    } catch (error) {
      setStatus(error.code === 4001 ? "Payment cancelled." : (error.message || "Payment failed."), true);
    }
  });

  $("lgd-meme-form").addEventListener("submit", async event => {
    event.preventDefault();
    if (!ready()) return;
    const tx = $("lgd-pay-tx").value.trim();
    if (!tx) { setStatus("Paste your transaction hash first.", true); return; }
    try {
      await getQuote();
      await submit(tx);
    } catch (error) {
      setStatus(error.message, true);
    }
  });

  // Show the fee as soon as the manual option is opened.
  $("lgd-pay").querySelector("details").addEventListener("toggle", async event => {
    if (!event.target.open) return;
    try { await getQuote(); } catch (error) { setStatus(error.message, true); }
  });

  $("lgd-pay-copy").addEventListener("click", async event => {
    try {
      await navigator.clipboard.writeText($("lgd-pay-wallet-addr").textContent);
      event.target.textContent = "Copied!";
    } catch {
      event.target.textContent = "Copy failed";
    }
    setTimeout(() => { event.target.textContent = "Copy"; }, 1600);
  });

  // ---------- Gallery and voting ----------

  async function loadEntries() {
    const phase = phaseNow();
    if (phase === "soon" || phase === "upcoming" || phase === "gone") { $("lgd-meme-gallery").hidden = true; return; }
    try {
      const data = await (await fetch("/api/meme/entries", { cache: "no-store" })).json();
      const grid = $("lgd-entry-grid");
      grid.replaceChildren();
      $("lgd-meme-gallery").hidden = false;
      $("lgd-meme-gallery-note").textContent = phase === "ended" ? "Final votes" : "Vote for your favourites";
      if (!data.entries.length) {
        grid.appendChild(el("p", "No entries yet. Be the first to remake a legend.", "lgd-empty"));
        return;
      }
      data.entries.forEach(entry => {
        const card = el("article", undefined, "lgd-entry");
        const img = el("img");
        img.src = entry.image;
        img.alt = `Remake of meme ${entry.meme_no} by ${entry.name}`;
        img.loading = "lazy";
        const info = el("div", undefined, "lgd-entry-info");
        const who = el("div");
        who.append(el("strong", entry.name), el("small", `Remake of meme ${String(entry.meme_no).padStart(2, "0")}`));
        const vote = el("button", `▲ ${entry.votes}`, "lgd-vote");
        vote.type = "button";
        const voted = mine.voted.includes(entry.id), own = mine.entry_id === entry.id;
        vote.disabled = phase !== "live" || voted || own;
        vote.title = own ? "Your entry" : voted ? "You voted" : "Vote";
        if (voted) vote.classList.add("lgd-voted");
        vote.addEventListener("click", () => castVote(entry.id, vote));
        info.append(who, vote);
        card.append(el("span", "#" + entry.rank, "lgd-entry-rank"), img, info);
        if (entry.prize_lgd) card.appendChild(el("div", `${short(entry.prize_lgd)} LGD`, "lgd-entry-prize"));
        grid.appendChild(card);
      });
    } catch (error) {
      console.error("Meme entries error:", error);
    }
  }

  async function castVote(id, button) {
    const a = auth();
    if (!a) {
      setStatus("Log in with Telegram to vote.", true);
      $("lgd-meme-entry").scrollIntoView({ behavior: "smooth" });
      return;
    }
    button.disabled = true;
    try {
      await post("/api/meme/vote", { auth: a, entry_id: id });
      mine.voted.push(id);
      loadEntries();
    } catch (error) {
      button.disabled = false;
      setStatus(error.message, true);
      $("lgd-meme-status").scrollIntoView({ behavior: "smooth", block: "center" });
    }
  }

  // ---------- Start ----------

  async function init() {
    try {
      await loadConfig();
    } catch (error) {
      console.error("Meme config error:", error);
      return;
    }
    try {
      const site = await (await fetch("/api/config")).json();
      const widget = document.createElement("script");
      widget.async = true;
      widget.src = "https://telegram.org/js/telegram-widget.js?22";
      widget.setAttribute("data-telegram-login", site.bot_username);
      widget.setAttribute("data-size", "large");
      widget.setAttribute("data-radius", "99");
      widget.setAttribute("data-onauth", "lgdOnTelegramAuth(user)");
      $("lgd-meme-widget").appendChild(widget);
    } catch (error) {
      console.error("Login widget error:", error);
    }
    tick();
    setInterval(tick, 1000);
    refreshMe();
    setInterval(loadConfig, 30000);
    setInterval(loadEntries, 60000);
  }

  window.addEventListener("lgd-auth", () => { quote = null; refreshMe(); });
  init();
})();
