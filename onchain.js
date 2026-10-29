// ======================================
// LEGEND ($LGD) LIVE ONCHAIN DATA
// Reads the Pons bonding curve straight from Robinhood Chain.
// ======================================

(function () {
  const RPC = "https://rpc.mainnet.chain.robinhood.com";
  const CURVE = "0x92175de95b1a5f85c719a9dd4fe2e86ed6ce2b6f";
  const SUPPLY = 1e9;
  // Pons curves start with 1.68 virtual ETH and graduate at about 4.2 ETH raised.
  const START_ETH = 1.68;
  const GRAD_ETH = 4.2;
  const $ = id => document.getElementById(id);

  function usd(value) {
    if (!Number.isFinite(value)) return "—";
    if (value >= 1) {
      return new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: value >= 1000 ? 0 : 2 }).format(value);
    }
    // Tiny prices: show significant digits, e.g. $0.000003890
    return "$" + value.toPrecision(4).replace(/(\.\d*?[1-9])0+$/, "$1");
  }

  async function reserves() {
    const res = await fetch(RPC, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ jsonrpc: "2.0", id: 1, method: "eth_call", params: [{ to: CURVE, data: "0x0902f1ac" }, "latest"] })
    });
    const data = await res.json();
    if (!data.result || data.result.length < 130) throw new Error(data.error?.message || "No curve data");
    const word = i => Number(BigInt("0x" + data.result.slice(2 + i * 64, 66 + i * 64))) / 1e18;
    return { eth: word(0), tokens: word(1) };
  }

  async function ethPrice() {
    const res = await fetch("https://api.coinbase.com/v2/prices/ETH-USD/spot");
    const data = await res.json();
    return Number(data.data.amount);
  }

  async function load() {
    try {
      const [curve, ethUsd] = await Promise.all([reserves(), ethPrice().catch(() => NaN)]);
      const priceEth = curve.eth / curve.tokens;
      const raised = Math.max(0, curve.eth - START_ETH);
      const pct = Math.min(100, (raised / GRAD_ETH) * 100);

      $("lgd-live-price").textContent = Number.isFinite(ethUsd) ? usd(priceEth * ethUsd) : priceEth.toExponential(3) + " ETH";
      $("lgd-live-mcap").textContent = Number.isFinite(ethUsd) ? usd(priceEth * SUPPLY * ethUsd) : (priceEth * SUPPLY).toFixed(3) + " ETH";
      $("lgd-live-eth").textContent = raised.toFixed(raised < 0.1 ? 4 : 3) + " ETH";
      $("lgd-live-ethusd").textContent = usd(ethUsd);
      $("lgd-curve-pct").textContent = (pct < 1 && pct > 0 ? pct.toFixed(2) : pct.toFixed(1)) + "%";
      $("lgd-curve-fill").style.width = Math.max(pct, 0.6) + "%";
      if (pct >= 100) $("lgd-curve-note").textContent = "The curve is full. $LGD has graduated to a DEX pool.";
      $("lgd-live-dot").classList.remove("lgd-offline");
      $("lgd-live-status").textContent = "Updated " + new Date().toLocaleTimeString() + " · Robinhood Chain";
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
