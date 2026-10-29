// ======================================
// LEGEND ($LGD) LIVE ONCHAIN DATA
// Price, market cap and graduation progress from /api/lgd/market.
// ======================================

(function () {
  const $ = id => document.getElementById(id);

  function usd(value) {
    if (!Number.isFinite(value)) return "—";
    if (value >= 1) {
      return new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: value >= 1000 ? 0 : 2 }).format(value);
    }
    // Tiny prices: show significant digits, e.g. $0.000003890
    return "$" + value.toPrecision(4).replace(/(\.\d*?[1-9])0+$/, "$1");
  }

  // The server reads the chain: the bonding curve before graduation,
  // the Uniswap v4 pool after it.
  async function load() {
    try {
      const res = await fetch("/api/lgd/market", { cache: "no-store" });
      if (!res.ok) throw new Error("Market API returned " + res.status);
      const m = await res.json();
      const pct = (m.progress || 0) * 100;

      $("lgd-live-price").textContent = m.price_usd ? usd(m.price_usd) : "—";
      $("lgd-live-mcap").textContent = m.mcap_usd ? usd(m.mcap_usd) : "—";
      $("lgd-live-ethusd").textContent = usd(m.eth_usd);
      $("lgd-live-eth").textContent = m.raised_eth == null ? "—" : m.raised_eth.toFixed(m.raised_eth < 0.1 ? 4 : 3) + " ETH";
      $("lgd-curve-pct").textContent = (pct < 1 && pct > 0 ? pct.toFixed(2) : pct.toFixed(1)) + "%";
      $("lgd-curve-fill").style.width = Math.max(pct, 0.6) + "%";

      const where = m.phase === "dex" ? "Uniswap v4 pool" : "Pons bonding curve";
      if (m.phase === "dex") {
        $("lgd-curve-note").textContent = "The curve is full. $LGD has graduated and now trades in its Uniswap v4 pool.";
      } else if (m.phase === "graduating") {
        $("lgd-curve-note").textContent = "The curve is full. $LGD is moving to its Uniswap v4 pool right now.";
      } else {
        $("lgd-curve-note").textContent = `When the curve raises ${m.threshold_eth} ETH, $LGD graduates to a Uniswap v4 pool.`;
      }
      $("lgd-live-dot").classList.toggle("lgd-offline", !m.price_usd);
      $("lgd-live-status").textContent = m.price_usd
        ? "Updated " + new Date().toLocaleTimeString() + " · " + where
        : "Price will be back once the pool is live.";
    } catch (error) {
      console.error("Onchain data error:", error);
      $("lgd-live-dot").classList.add("lgd-offline");
      $("lgd-live-status").textContent = "Live data is unavailable right now. Retrying...";
    }
  }

  const copy = $("lgd-copy-ca");
  copy.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(copy.dataset.value);
      copy.textContent = "Copied!";
    } catch {
      copy.textContent = "Copy failed";
    }
    setTimeout(() => { copy.textContent = "Copy address"; }, 1600);
  });

  load();
  setInterval(load, 30000);
})();
