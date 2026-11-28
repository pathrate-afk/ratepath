/**
 * RatePath (ريت باث) — Programmatic SEO Pair Page Hydrator (100% CSP Compliant)
 *
 * Powers all 10 high-intent SEO routes (`/usd-to-syp`, `/usd-to-try`, `/usd-to-egp`,
 * `/gold-price-today`, `/btc-to-usdt`, `/usdt-to-try`, `/usdt-to-syp`, `/eth-to-usdt`,
 * `/eur-to-usd`, `/silver-price-today`):
 * - Hydrates live auto-updating rates from GitHub `live-data` -> `/public/live_feed.json`
 *   -> localStorage cache -> embedded fallback snapshot (never blank).
 * - Runs live bidirectional Instant Converter for the exact page pair + multi-currency
 *   Gold/Silver breakdown tables.
 * - Supports instant AR <-> EN language toggling without page reload.
 */

import { FALLBACK_FEED_SNAPSHOT, FALLBACK_CATALOG_SNAPSHOT } from "./fallback-snapshot.js";
import { createSyriaRevolutionFlagSvg } from "./syria-flag.js";

const LIVE_FEED_URLS = [
  "https://raw.githubusercontent.com/pathrate-afk/ratepath/live-data/live_feed.json",
  "/public/live_feed.json",
  "/live_feed.json",
];
const CATALOG_FEED_URLS = [
  "https://raw.githubusercontent.com/pathrate-afk/ratepath/live-data/catalog_feed.json",
  "/public/catalog_feed.json",
  "/catalog_feed.json",
];
const LS_FEED_KEY = "ratepath_last_good_live_feed_v2";
const LS_CATALOG_KEY = "ratepath_last_good_catalog_v2";
const LS_LANG_KEY = "ratepath_lang";

let state = {
  lang: "ar",
  slug: "",
  base: "USD",
  quote: "SYP",
  mode: "pair",
  swapped: false,
  amount: 1,
  feed: FALLBACK_FEED_SNAPSHOT,
  catalog: FALLBACK_CATALOG_SNAPSHOT,
  usingFallback: false,
};

function safeNum(val) {
  const n = Number(val);
  return Number.isFinite(n) && n > 0 ? n : null;
}

function formatSmartNumber(num) {
  if (!Number.isFinite(num)) return "—";
  const abs = Math.abs(num);
  let maxDecimals = 2;
  if (abs > 0 && abs < 0.01) maxDecimals = 6;
  else if (abs < 1) maxDecimals = 4;
  else if (abs >= 1000) maxDecimals = 2;
  return new Intl.NumberFormat("en-US", {
    minimumFractionDigits: abs >= 100 ? 2 : maxDecimals > 2 ? 2 : 2,
    maximumFractionDigits: maxDecimals,
  }).format(num);
}

/** Resolve USD value of 1 unit of any supported asset code. */
function getUsdPerUnit(code, feed, catalog) {
  const sym = String(code || "").toUpperCase();
  if (sym === "USD" || sym === "USDT") {
    const usdtSpot = safeNum(feed?.crypto?.assets?.USDT?.spot_price);
    return sym === "USDT" ? (usdtSpot || 1) : 1;
  }
  // Metals
  if (sym === "XAU" || sym === "XAU_OZ") {
    return safeNum(feed?.gold?.prices?.XAU_OZ) || safeNum(catalog?.metals?.XAU_OZ?.price_usd) || 3653.5;
  }
  if (sym === "GRAM_24K") {
    return safeNum(feed?.gold?.prices?.GRAM_24K) || safeNum(catalog?.metals?.GRAM_24K?.price_usd) || 117.46;
  }
  if (sym === "GRAM_21K") {
    return safeNum(feed?.gold?.prices?.GRAM_21K) || safeNum(catalog?.metals?.GRAM_21K?.price_usd) || 102.78;
  }
  if (sym === "GRAM_18K") {
    return safeNum(feed?.gold?.prices?.GRAM_18K) || safeNum(catalog?.metals?.GRAM_18K?.price_usd) || 88.10;
  }
  if (sym === "XAG" || sym === "XAG_OZ") {
    return safeNum(feed?.gold?.prices?.XAG_OZ) || safeNum(catalog?.metals?.XAG_OZ?.price_usd) || 43.75;
  }
  if (sym === "XAG_GRAM") {
    return safeNum(feed?.gold?.prices?.XAG_GRAM) || safeNum(catalog?.metals?.XAG_GRAM?.price_usd) || 1.41;
  }
  // Crypto
  const cLive = safeNum(feed?.crypto?.assets?.[sym]?.spot_price);
  if (cLive) return cLive;
  if (Array.isArray(catalog?.crypto)) {
    const match = catalog.crypto.find((c) => String(c.symbol || "").toUpperCase() === sym);
    const p = safeNum(match?.price_usd);
    if (p) return p;
  }
  // Fiat (units_per_usd -> invert to USD per 1 unit)
  const fiatLive = safeNum(feed?.fiat?.rates?.[sym]?.units_per_usd);
  if (fiatLive) return 1 / fiatLive;
  const fiatCat = safeNum(catalog?.fiat?.[sym]?.units_per_usd);
  if (fiatCat) return 1 / fiatCat;
  return null;
}

function get24hChange(code, catalog) {
  const sym = String(code || "").toUpperCase();
  if (Array.isArray(catalog?.crypto)) {
    const match = catalog.crypto.find((c) => String(c.symbol || "").toUpperCase() === sym);
    if (match && match.change_24h_pct != null) {
      const n = Number(match.change_24h_pct);
      if (Number.isFinite(n)) return n;
    }
  }
  return null;
}

function computeRate(fromCode, toCode) {
  const usdFrom = getUsdPerUnit(fromCode, state.feed, state.catalog);
  const usdTo = getUsdPerUnit(toCode, state.feed, state.catalog);
  if (!usdFrom || !usdTo) return null;
  return usdFrom / usdTo;
}

function updateUtcClock() {
  const el = document.getElementById("seo-utc-clock");
  if (!el) return;
  const now = new Date();
  const hh = String(now.getUTCHours()).padStart(2, "0");
  const mm = String(now.getUTCMinutes()).padStart(2, "0");
  const ss = String(now.getUTCSeconds()).padStart(2, "0");
  el.textContent = `${hh}:${mm}:${ss} UTC`;
}

function updateFreshnessLabel() {
  const el = document.getElementById("seo-updated-label");
  if (!el) return;
  const stamp = state.feed?.meta?.generated_at || state.feed?.fiat?.updated_at;
  if (!stamp) return;
  const diffMin = Math.max(0, Math.floor((Date.now() - Date.parse(stamp)) / 60000));
  if (state.lang === "ar") {
    el.textContent =
      diffMin <= 2
        ? "تحديث مباشر الآن"
        : diffMin < 60
        ? `آخر تحديث قبل ${diffMin} دقيقة`
        : `آخر تحديث: ${String(stamp).slice(11, 16)} UTC`;
  } else {
    el.textContent =
      diffMin <= 2
        ? "Updated live just now"
        : diffMin < 60
        ? `Updated ${diffMin}m ago`
        : `Updated ${String(stamp).slice(11, 16)} UTC`;
  }
}

function applyLanguage(lang) {
  state.lang = lang === "en" ? "en" : "ar";
  document.documentElement.lang = state.lang;
  document.documentElement.dir = state.lang === "ar" ? "rtl" : "ltr";
  try {
    localStorage.setItem(LS_LANG_KEY, state.lang);
  } catch {
    // Ignore private browsing storage restrictions
  }

  document.querySelectorAll("[data-ar][data-en]").forEach((el) => {
    const val = el.getAttribute(state.lang === "ar" ? "data-ar" : "data-en");
    if (val !== null) el.textContent = val;
  });

  const toggleBtn = document.getElementById("seo-lang-toggle");
  if (toggleBtn) {
    toggleBtn.textContent = state.lang === "ar" ? "🇬🇧 English" : "🇸🇦 العربية";
  }

  renderLivePair();
  updateFreshnessLabel();
}

function renderLivePair() {
  const fromCode = state.swapped ? state.quote : state.base;
  const toCode = state.swapped ? state.base : state.quote;
  const primaryRate = computeRate(state.base, state.quote);
  const convRate = computeRate(fromCode, toCode);

  const heroVal = document.getElementById("seo-hero-rate");
  if (heroVal && primaryRate) {
    heroVal.textContent = `${formatSmartNumber(primaryRate)} ${state.quote}`;
  }

  const inverseVal = document.getElementById("seo-inverse-rate");
  if (inverseVal && primaryRate) {
    inverseVal.textContent = `1 ${state.quote} = ${formatSmartNumber(1 / primaryRate)} ${state.base}`;
  }

  const chgBadge = document.getElementById("seo-change-badge");
  if (chgBadge) {
    const chg = get24hChange(state.base, state.catalog);
    if (chg !== null) {
      const up = chg >= 0;
      chgBadge.textContent = `${up ? "▲ +" : "🔻 "}${chg.toFixed(2)}% (24h)`;
      chgBadge.className = up
        ? "inline-flex items-center px-2.5 py-1 rounded-full text-xs font-mono font-bold bg-emerald-500/15 text-emerald-300 border border-emerald-500/35"
        : "inline-flex items-center px-2.5 py-1 rounded-full text-xs font-mono font-bold bg-rose-500/15 text-rose-300 border border-rose-500/35";
      chgBadge.hidden = false;
    }
  }

  // Converter labels & calculated output
  const fromLbl = document.getElementById("conv-from-code");
  const toLbl = document.getElementById("conv-to-code");
  const outEl = document.getElementById("conv-output");
  const unitHint = document.getElementById("conv-unit-rate");
  if (fromLbl) fromLbl.textContent = fromCode;
  if (toLbl) toLbl.textContent = toCode;
  if (outEl && convRate) {
    const converted = (Number(state.amount) || 0) * convRate;
    outEl.textContent = `${formatSmartNumber(converted)} ${toCode}`;
  }
  if (unitHint && convRate) {
    unitHint.textContent = `1 ${fromCode} = ${formatSmartNumber(convRate)} ${toCode}`;
  }

  // Quick conversion table rows (`data-quick-qty`)
  document.querySelectorAll("[data-quick-qty]").forEach((row) => {
    const qty = Number(row.getAttribute("data-quick-qty"));
    const cell = row.querySelector("[data-quick-out]");
    if (cell && convRate && Number.isFinite(qty)) {
      cell.textContent = `${formatSmartNumber(qty * convRate)} ${toCode}`;
    }
  });

  // Metals breakdown cells (`data-metal-unit` + `data-metal-ccy`)
  document.querySelectorAll("[data-metal-unit][data-metal-ccy]").forEach((cell) => {
    const unit = cell.getAttribute("data-metal-unit");
    const ccy = cell.getAttribute("data-metal-ccy");
    const r = computeRate(unit, ccy);
    if (r) {
      cell.textContent = `${formatSmartNumber(r)} ${ccy}`;
    }
  });
}

async function fetchJsonWithTimeout(url, timeoutMs = 6000) {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    const resp = await fetch(url, { cache: "no-cache", signal: ctrl.signal });
    if (!resp.ok) return null;
    return await resp.json();
  } catch {
    return null;
  } finally {
    clearTimeout(timer);
  }
}

async function refreshLiveFeeds() {
  let freshFeed = null;
  for (const url of LIVE_FEED_URLS) {
    freshFeed = await fetchJsonWithTimeout(url, 5500);
    if (freshFeed && freshFeed.fiat && freshFeed.gold) break;
  }
  let freshCat = null;
  for (const url of CATALOG_FEED_URLS) {
    freshCat = await fetchJsonWithTimeout(url, 5500);
    if (freshCat && freshCat.meta) break;
  }

  if (freshFeed) {
    state.feed = freshFeed;
    state.usingFallback = false;
    try {
      localStorage.setItem(LS_FEED_KEY, JSON.stringify(freshFeed));
    } catch {
      // Ignore quota errors
    }
  } else {
    try {
      const cached = JSON.parse(localStorage.getItem(LS_FEED_KEY) || "null");
      if (cached && cached.fiat) state.feed = cached;
    } catch {
      // Keep embedded fallback
    }
  }

  if (freshCat) {
    state.catalog = freshCat;
    try {
      localStorage.setItem(LS_CATALOG_KEY, JSON.stringify(freshCat));
    } catch {
      // Ignore quota errors
    }
  } else {
    try {
      const cachedC = JSON.parse(localStorage.getItem(LS_CATALOG_KEY) || "null");
      if (cachedC && cachedC.meta) state.catalog = cachedC;
    } catch {
      // Keep embedded fallback
    }
  }

  renderLivePair();
  updateFreshnessLabel();
}

function mountSyriaFlagBadges() {
  document.querySelectorAll("[data-syria-flag-slot]").forEach((slot) => {
    slot.replaceChildren(createSyriaRevolutionFlagSvg("w-6 h-4 rounded-[3px] shadow-sm inline-block"));
  });
}

function initSeoPairPage() {
  const body = document.body;
  state.slug = body.getAttribute("data-seo-slug") || "";
  state.base = (body.getAttribute("data-base") || "USD").toUpperCase();
  state.quote = (body.getAttribute("data-quote") || "SYP").toUpperCase();
  state.mode = body.getAttribute("data-mode") || "pair";

  // Resolve initial language: URL `?lang=` > saved preference > page default (`ar`)
  const qsLang = new URLSearchParams(window.location.search).get("lang");
  let savedLang = null;
  try {
    savedLang = localStorage.getItem(LS_LANG_KEY);
  } catch {
    savedLang = null;
  }
  const initialLang = qsLang === "en" || qsLang === "ar" ? qsLang : savedLang === "en" ? "en" : "ar";

  mountSyriaFlagBadges();

  // Converter input & quick amount chips
  const amountInput = document.getElementById("conv-amount");
  if (amountInput) {
    state.amount = Number(amountInput.value) || 1;
    amountInput.addEventListener("input", () => {
      const cleaned = amountInput.value.replace(/,/g, "").trim();
      const val = Number(cleaned);
      state.amount = Number.isFinite(val) && val >= 0 ? val : 0;
      renderLivePair();
    });
  }

  document.querySelectorAll("[data-chip-amount]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const val = Number(btn.getAttribute("data-chip-amount")) || 1;
      state.amount = val;
      if (amountInput) amountInput.value = String(val);
      renderLivePair();
    });
  });

  const swapBtn = document.getElementById("conv-swap");
  if (swapBtn) {
    swapBtn.addEventListener("click", () => {
      state.swapped = !state.swapped;
      renderLivePair();
    });
  }

  const langBtn = document.getElementById("seo-lang-toggle");
  if (langBtn) {
    langBtn.addEventListener("click", () => {
      applyLanguage(state.lang === "ar" ? "en" : "ar");
    });
  }

  // Hydrate immediately from cached/embedded snapshot, then pull live feed
  applyLanguage(initialLang);
  updateUtcClock();
  setInterval(updateUtcClock, 1000);
  refreshLiveFeeds();
  setInterval(refreshLiveFeeds, 60000);
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", initSeoPairPage);
} else {
  initSeoPairPage();
}
