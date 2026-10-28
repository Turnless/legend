const grid = document.querySelector('#meme-grid');
const lightbox = document.querySelector('#lightbox');
const lightboxImage = lightbox.querySelector('img');
const lightboxCaption = lightbox.querySelector('figcaption');
let activeMeme = 1;

for (let i = 1; i <= 20; i++) {
  const n = String(i).padStart(2, '0');
  const card = document.createElement('button');
  card.className = 'meme-card reveal';
  card.type = 'button';
  card.setAttribute('aria-label', `Open LEGEND meme ${i}`);
  card.innerHTML = `<img loading="lazy" src="assets/memes/legend-meme-${n}.jpg" alt="LEGEND meme concept ${i}"><div class="meme-caption">MEME ${n}<span>VIEW ↗</span></div>`;
  card.addEventListener('click', () => openMeme(i));
  grid.appendChild(card);
}

function openMeme(index) {
  activeMeme = ((index - 1 + 20) % 20) + 1;
  const n = String(activeMeme).padStart(2, '0');
  lightboxImage.src = `assets/memes/legend-meme-${n}.jpg`;
  lightboxImage.alt = `LEGEND meme concept ${activeMeme}`;
  lightboxCaption.textContent = `LEGEND MEME ${n} / 20`;
  lightbox.classList.add('open');
  lightbox.setAttribute('aria-hidden', 'false');
  document.body.style.overflow = 'hidden';
  lightbox.querySelector('.lightbox-close').focus();
}
function closeMeme() {
  lightbox.classList.remove('open');
  lightbox.setAttribute('aria-hidden', 'true');
  document.body.style.overflow = '';
}
lightbox.querySelector('.lightbox-close').addEventListener('click', closeMeme);
lightbox.querySelector('.lightbox-prev').addEventListener('click', () => openMeme(activeMeme - 1));
lightbox.querySelector('.lightbox-next').addEventListener('click', () => openMeme(activeMeme + 1));
lightbox.addEventListener('click', event => { if (event.target === lightbox) closeMeme(); });
document.addEventListener('keydown', event => {
  if (!lightbox.classList.contains('open')) return;
  if (event.key === 'Escape') closeMeme();
  if (event.key === 'ArrowLeft') openMeme(activeMeme - 1);
  if (event.key === 'ArrowRight') openMeme(activeMeme + 1);
});

const menu = document.querySelector('.menu');
const nav = document.querySelector('#navlinks');
menu.addEventListener('click', () => {
  const open = nav.classList.toggle('open');
  menu.setAttribute('aria-expanded', String(open));
});
nav.querySelectorAll('a').forEach(a => a.addEventListener('click', () => {
  nav.classList.remove('open');
  menu.setAttribute('aria-expanded', 'false');
}));

// Copy the publicly documented Robinhood Chain ID.
document.querySelectorAll('[data-copy]').forEach(button => button.addEventListener('click', async () => {
  const value = button.dataset.copy;
  try {
    await navigator.clipboard.writeText(value);
    button.textContent = 'Copied';
  } catch {
    button.textContent = value;
  }
  window.setTimeout(() => { button.textContent = 'Copy'; }, 1600);
}));

// Reveal sections and meme tiles as they enter the viewport; respect reduced motion.
const revealTargets = document.querySelectorAll('.hero-copy, .hero-art, .mission-grid > *, .story > *, .section-head, .principle-grid article, .manifesto-inner, .community-card, .token > *, .faq-grid > *, .meme-card');
revealTargets.forEach(el => el.classList.add('reveal'));
if ('IntersectionObserver' in window && !window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
  const observer = new IntersectionObserver(entries => {
    entries.forEach(entry => {
      if (entry.isIntersecting) {
        entry.target.classList.add('is-visible');
        observer.unobserve(entry.target);
      }
    });
  }, { threshold: 0.12, rootMargin: '0px 0px -30px 0px' });
  revealTargets.forEach(el => observer.observe(el));
} else {
  revealTargets.forEach(el => el.classList.add('is-visible'));
}

// ======================================
// LEGEND ($LGD) MARKET INTEGRATION
// ======================================

// IMPORTANT:
// Replace this placeholder with the real,
// verified LEGEND token contract address.
const LGD_CA = "0x165dc537ec67ce2c033bcfcac562e6c311c7c213";

const LGD_CHAIN = "robinhood";
const LGD_EXPLORER = "https://robinhoodchain.blockscout.com";

const lgdPrice = document.getElementById("lgd-price");
const lgdChange = document.getElementById("lgd-change");
const lgdLiquidity = document.getElementById("lgd-liquidity");
const lgdVolume = document.getElementById("lgd-volume");
const lgdStatus = document.getElementById("lgd-market-status");
const lgdChart = document.getElementById("lgd-chart-link");
const lgdCAShort = document.getElementById("lgd-ca-short");
const lgdCopyCA = document.getElementById("lgd-copy-ca");
const lgdExplorerLink = document.getElementById("lgd-explorer-link");

const lgdValidCA = /^0x[a-fA-F0-9]{40}$/.test(LGD_CA);

function lgdUSD(value) {
  if (!Number.isFinite(value)) return "—";

  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: 2,
    maximumFractionDigits: value < 1 ? 8 : 2
  }).format(value);
}

function lgdCompactUSD(value) {
  if (!Number.isFinite(value)) return "—";

  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    notation: "compact",
    maximumFractionDigits: 2
  }).format(value);
}

// Show the contract address and its explorer link.
if (lgdValidCA) {
  lgdCAShort.textContent =
    LGD_CA.slice(0, 6) + "..." + LGD_CA.slice(-4);

  lgdExplorerLink.href =
    `${LGD_EXPLORER}/address/${LGD_CA}`;

  lgdExplorerLink.textContent = "View contract on explorer ↗";
} else {
  lgdCAShort.textContent = "Contract not configured";
  lgdExplorerLink.href = LGD_EXPLORER;
}

// Copy the contract address.
lgdCopyCA.addEventListener("click", async () => {
  if (!lgdValidCA) {
    lgdCopyCA.textContent = "Not set";
    setTimeout(() => {
      lgdCopyCA.textContent = "Copy CA";
    }, 1500);
    return;
  }

  try {
    await navigator.clipboard.writeText(LGD_CA);
    lgdCopyCA.textContent = "Copied!";
  } catch {
    // Fallback for browsers where clipboard access is blocked.
    const input = document.createElement("textarea");
    input.value = LGD_CA;
    input.style.position = "fixed";
    input.style.opacity = "0";

    document.body.appendChild(input);
    input.select();

    const copied = document.execCommand("copy");
    input.remove();

    lgdCopyCA.textContent = copied ? "Copied!" : "Copy failed";
  }

  setTimeout(() => {
    lgdCopyCA.textContent = "Copy CA";
  }, 1800);
});

function lgdSetStatus(message) {
  lgdStatus.textContent = message;
}

function lgdSetChange(value) {
  if (!Number.isFinite(value)) {
    lgdChange.textContent = "—";
    lgdChange.classList.remove("lgd-positive", "lgd-negative");
    return;
  }

  const sign = value > 0 ? "+" : "";

  lgdChange.textContent = `${sign}${value.toFixed(2)}%`;

  lgdChange.classList.toggle("lgd-positive", value > 0);
  lgdChange.classList.toggle("lgd-negative", value < 0);
}

async function lgdLoadMarketData() {
  if (!lgdValidCA) {
    lgdSetStatus(
      "Add the verified token contract address to enable market data."
    );
    return;
  }

  lgdSetStatus("Fetching the latest available market data...");

  try {
    const response = await fetch(
      `https://api.dexscreener.com/latest/dex/tokens/${LGD_CA}`,
      { cache: "no-store" }
    );

    if (!response.ok) {
      throw new Error(`Market API returned ${response.status}`);
    }

    const data = await response.json();

    const pairs = (data.pairs || []).filter(pair =>
      pair.chainId === LGD_CHAIN &&
      (
        pair.baseToken?.address?.toLowerCase() === LGD_CA.toLowerCase() ||
        pair.quoteToken?.address?.toLowerCase() === LGD_CA.toLowerCase()
      )
    );

    if (!pairs.length) {
      lgdPrice.textContent = "—";
      lgdChange.textContent = "—";
      lgdLiquidity.textContent = "—";
      lgdVolume.textContent = "—";

      lgdSetStatus(
        "No indexed Robinhood Chain trading pair found yet. " +
        "Price data will appear when a supported market is available."
      );

      lgdChart.href = "https://dexscreener.com";
      lgdChart.textContent = "Explore markets ↗";
      return;
    }

    // Prefer the pair with the greatest reported USD liquidity.
    pairs.sort(
      (a, b) =>
        (b.liquidity?.usd || 0) - (a.liquidity?.usd || 0)
    );

    const pair = pairs[0];

    // The token price depends on whether LEGEND is the base or quote token.
    const isBase =
      pair.baseToken.address.toLowerCase() === LGD_CA.toLowerCase();

    const price = Number(
      isBase ? pair.priceUsd : pair.priceNative
    );

    // priceNative is denominated in the base token, not necessarily USD.
    // Only use the USD price when LEGEND is the base token.
    if (!isBase || !Number.isFinite(price)) {
      lgdPrice.textContent = "—";
      lgdSetStatus(
        "A market pair was found, but its USD price could not be safely resolved."
      );
    } else {
      lgdPrice.textContent = lgdUSD(price);
      lgdSetStatus(
        `Market data via DexScreener · Updated ${new Date().toLocaleTimeString()}`
      );
    }

    const change = Number(pair.priceChange?.h24);
    lgdSetChange(Number.isFinite(change) ? change : NaN);

    const liquidity = Number(pair.liquidity?.usd);
    lgdLiquidity.textContent = Number.isFinite(liquidity)
      ? lgdCompactUSD(liquidity)
      : "—";

    const volume = Number(pair.volume?.h24);
    lgdVolume.textContent = Number.isFinite(volume)
      ? lgdCompactUSD(volume)
      : "—";

    if (pair.url) {
      lgdChart.href = pair.url;
      lgdChart.textContent = "View LEGEND market chart ↗";
    }
  } catch (error) {
    console.error("LEGEND market data error:", error);

    lgdSetStatus(
      "Market data is temporarily unavailable. Please try again later."
    );
  }
}

if (lgdValidCA) {
  lgdLoadMarketData();

  // Refresh every 30 seconds.
  window.setInterval(lgdLoadMarketData, 30000);
}
