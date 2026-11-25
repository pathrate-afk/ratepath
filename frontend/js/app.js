/**
 * RatePath (ريت باث) — Institutional Terminal Application Controller
 * Connects `/frontend/public/live_feed.json` to Single-Input BigInt Converter,
 * Instant EN/AR Switcher, Wake-Lock, PWA Offline Cache, Telegram WebApp,
 * Dynamic Affiliate CTAs, Canvas PNG Quote Card, and VIP Price Alerts.
 */

import {
  buildAssetCatalog,
  computeTickDeltas,
  convertAllAssets,
  parseDecimalToScaled,
} from "./decimal-math.js";
import { TRANSLATIONS, updateSeoJsonLd } from "./i18n.js";
import {
  downloadCanvasAsPng,
  renderQuoteCardCanvas,
  shareCanvasPng,
} from "./quote-card.js";
import {
  LIVE_FEED_URL,
  MONETIZATION_CONFIG,
  PUBLIC_SITE_URL,
} from "../monetization.config.js";

const STORAGE_KEYS = {
  LANG: "ratepath_lang",
  FEED_CACHE: "ratepath_cached_feed_v1",
  PREV_FEED: "ratepath_prev_feed_v1",
  ALERTS: "ratepath_vip_alerts_v1",
};

const DEFAULT_FEED_ENDPOINTS = [
  "./public/live_feed.json",
  "/frontend/public/live_feed.json",
  "./live_feed.json",
];

function resolveFeedEndpoints() {
  const customUrl = String(
    LIVE_FEED_URL || MONETIZATION_CONFIG?.LIVE_FEED_URL || MONETIZATION_CONFIG?.cloudFeedUrl || ""
  ).trim();
  if (/^https?:\/\//i.test(customUrl)) {
    return [customUrl, ...DEFAULT_FEED_ENDPOINTS];
  }
  return DEFAULT_FEED_ENDPOINTS;
}

function resolveInitialLang() {
  const path = (window.location.pathname || "").toLowerCase();
  const urlLang = (new URLSearchParams(window.location.search).get("lang") || "").toLowerCase();
  if (urlLang === "ar" || path === "/ar" || path.startsWith("/ar/")) {
    try {
      localStorage.setItem(STORAGE_KEYS.LANG, "ar");
    } catch {
      // Ignore storage failures
    }
    return "ar";
  }
  if (urlLang === "en" || path === "/en" || path.startsWith("/en/")) {
    try {
      localStorage.setItem(STORAGE_KEYS.LANG, "en");
    } catch {
      // Ignore storage failures
    }
    return "en";
  }
  return localStorage.getItem(STORAGE_KEYS.LANG) === "ar" ? "ar" : "en";
}

const state = {
  lang: resolveInitialLang(),
  amount: "100",
  baseAssetId: "USD",
  mobileTab: "converter",
  isOffline: false,
  wakeLockSentinel: null,
  wakeLockActive: false,
  feed: null,
  catalog: null,
  previousCatalog: null,
  tickDeltas: {},
  conversionResult: null,
  alerts: loadStoredAlerts(),
};

function escapeHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function safeHref(url) {
  const s = String(url || "").trim();
  const fallback = `${String(PUBLIC_SITE_URL || "https://ratepath.io").replace(/\/+$/, "")}/`;
  return /^https?:\/\//i.test(s) ? s : fallback;
}

function loadStoredAlerts() {
  try {
    const raw = localStorage.getItem(STORAGE_KEYS.ALERTS);
    const parsed = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

function saveStoredAlerts() {
  try {
    localStorage.setItem(STORAGE_KEYS.ALERTS, JSON.stringify(state.alerts));
  } catch {
    // Ignore storage quota errors
  }
}

function triggerTelegramHaptic(style = "light") {
  try {
    const tg = window.Telegram?.WebApp;
    if (tg?.HapticFeedback?.impactOccurred) {
      tg.HapticFeedback.impactOccurred(style);
    }
  } catch {
    // Non-Telegram browser
  }
}

let toastTimer = null;
function showToast(message) {
  const toastEl = document.getElementById("toast-notification");
  const toastMsgEl = document.getElementById("toast-message");
  if (!toastEl || !toastMsgEl) return;
  toastMsgEl.textContent = message;
  toastEl.classList.remove("opacity-0", "pointer-events-none", "translate-y-3");
  toastEl.classList.add("opacity-100", "translate-y-0");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => {
    toastEl.classList.add("opacity-0", "pointer-events-none", "translate-y-3");
    toastEl.classList.remove("opacity-100", "translate-y-0");
  }, 3200);
}

// ---------------------------------------------------------------------------
// 1. Telegram Mini App & Offline-First PWA Registration
// ---------------------------------------------------------------------------

function initTelegramMiniApp() {
  const tg = window.Telegram?.WebApp;
  if (!tg) return;
  try {
    tg.ready();
    tg.expand();
    if (typeof tg.setHeaderColor === "function") tg.setHeaderColor("#070B14");
    if (typeof tg.setBackgroundColor === "function") tg.setBackgroundColor("#070B14");
    const tgLang = tg.initDataUnsafe?.user?.language_code;
    if (!localStorage.getItem(STORAGE_KEYS.LANG) && tgLang?.startsWith("ar")) {
      state.lang = "ar";
    }
  } catch {
    // Safe fallback outside Telegram container
  }
}

function registerServiceWorker() {
  if (!("serviceWorker" in navigator) || !window.location.protocol.startsWith("http")) {
    return;
  }
  const host = window.location.hostname;
  const isLocal = host === "localhost" || host === "127.0.0.1" || host === "[::1]";
  if (isLocal) {
    // Bypass SW caching on localhost and clear any stale local SW registrations
    navigator.serviceWorker
      .getRegistrations()
      .then((regs) => Promise.all(regs.map((r) => r.unregister())))
      .catch(() => {});
    return;
  }
  navigator.serviceWorker.register("./sw.js").catch(() => {});
}

// ---------------------------------------------------------------------------
// 2. Screen Wake-Lock Controller
// ---------------------------------------------------------------------------

async function toggleWakeLock() {
  const t = TRANSLATIONS[state.lang];
  if (!("wakeLock" in navigator)) {
    showToast(t.wakeLockUnsupported);
    renderWakeLockButton();
    return;
  }

  try {
    if (state.wakeLockSentinel) {
      await state.wakeLockSentinel.release();
      state.wakeLockSentinel = null;
      state.wakeLockActive = false;
      showToast(state.lang === "ar" ? "تم إيقاف تثبيت إضاءة الشاشة" : "Screen Wake-Lock released");
    } else {
      const sentinel = await navigator.wakeLock.request("screen");
      state.wakeLockSentinel = sentinel;
      state.wakeLockActive = true;
      sentinel.addEventListener("release", () => {
        state.wakeLockActive = false;
        state.wakeLockSentinel = null;
        renderWakeLockButton();
      });
      showToast(state.lang === "ar" ? "تم تثبيت إضاءة الشاشة لمتابعة السوق" : "Screen Wake-Lock active");
    }
  } catch {
    state.wakeLockActive = false;
    showToast(t.wakeLockUnsupported);
  }
  renderWakeLockButton();
}

function renderWakeLockButton() {
  const btn = document.getElementById("btn-wake-lock");
  const label = document.getElementById("wake-lock-label");
  if (!btn || !label) return;
  const t = TRANSLATIONS[state.lang];
  if (!("wakeLock" in navigator)) {
    label.textContent = t.wakeLockUnsupported;
    btn.setAttribute("aria-pressed", "false");
    return;
  }
  label.textContent = state.wakeLockActive ? t.wakeLockOn : t.wakeLockOff;
  btn.setAttribute("aria-pressed", state.wakeLockActive ? "true" : "false");
  btn.classList.toggle("border-amber-400/70", state.wakeLockActive);
  btn.classList.toggle("bg-amber-500/15", state.wakeLockActive);
  btn.classList.toggle("text-amber-300", state.wakeLockActive);
}

// ---------------------------------------------------------------------------
// 3. Feed Loader (Network + Offline Cache Fallback)
// ---------------------------------------------------------------------------

async function fetchLiveFeed() {
  let loadedFeed = null;
  let fromOfflineCache = false;

  for (const url of resolveFeedEndpoints()) {
    try {
      const resp = await fetch(`${url}?t=${Date.now()}`, { cache: "no-store" });
      if (resp.ok) {
        loadedFeed = await resp.json();
        break;
      }
    } catch {
      // Try next endpoint path
    }
  }

  if (!loadedFeed) {
    const cachedRaw = localStorage.getItem(STORAGE_KEYS.FEED_CACHE);
    if (cachedRaw) {
      loadedFeed = JSON.parse(cachedRaw);
      fromOfflineCache = true;
    }
  } else {
    const existingCached = localStorage.getItem(STORAGE_KEYS.FEED_CACHE);
    if (existingCached && existingCached !== JSON.stringify(loadedFeed)) {
      localStorage.setItem(STORAGE_KEYS.PREV_FEED, existingCached);
    }
    localStorage.setItem(STORAGE_KEYS.FEED_CACHE, JSON.stringify(loadedFeed));
  }

  if (!loadedFeed) return;

  const prevRaw = localStorage.getItem(STORAGE_KEYS.PREV_FEED);
  const prevFeed = prevRaw ? JSON.parse(prevRaw) : null;

  state.isOffline = fromOfflineCache || !navigator.onLine;
  state.feed = loadedFeed;
  state.catalog = buildAssetCatalog(loadedFeed);
  state.previousCatalog = prevFeed ? buildAssetCatalog(prevFeed) : null;
  state.tickDeltas = computeTickDeltas(state.catalog, state.previousCatalog);

  if (!state.catalog.byId[state.baseAssetId]) {
    state.baseAssetId = "USD";
  }

  recomputeAndRenderAll();
  evaluateLocalPriceAlerts();
}

// ---------------------------------------------------------------------------
// 4. Instant Bilingual (EN LTR / AR RTL) & Zero-CLS Rendering
// ---------------------------------------------------------------------------

function applyLanguageAttributes() {
  const htmlEl = document.documentElement;
  const isAr = state.lang === "ar";
  htmlEl.setAttribute("lang", isAr ? "ar" : "en");
  htmlEl.setAttribute("dir", isAr ? "rtl" : "ltr");

  const t = TRANSLATIONS[state.lang];
  document.title = isAr
    ? "ريت باث (RatePath) | المنصة المؤسسية للعملات والذهب والأصول الرقمية"
    : "RatePath (ريت باث) | Institutional Cross-Asset & Macro Terminal";

  document.querySelectorAll("[data-i18n]").forEach((el) => {
    const key = el.getAttribute("data-i18n");
    if (key && typeof t[key] === "string") {
      el.textContent = t[key];
    }
  });

  document.querySelectorAll("[data-i18n-title]").forEach((el) => {
    const key = el.getAttribute("data-i18n-title");
    if (key && typeof t[key] === "string") {
      el.setAttribute("title", t[key]);
    }
  });

  const btnEn = document.getElementById("btn-lang-en");
  const btnAr = document.getElementById("btn-lang-ar");
  if (btnEn && btnAr) {
    btnEn.className = !isAr
      ? "px-2.5 py-1 rounded-md text-xs font-bold bg-amber-400 text-slate-950 shadow-sm transition-colors"
      : "px-2.5 py-1 rounded-md text-xs font-semibold text-slate-300 hover:text-white transition-colors";
    btnAr.className = isAr
      ? "px-2.5 py-1 rounded-md text-xs font-bold bg-amber-400 text-slate-950 shadow-sm transition-colors"
      : "px-2.5 py-1 rounded-md text-xs font-semibold text-slate-300 hover:text-white transition-colors";
  }

  renderWakeLockButton();
}

function renderStatusAndTopTicker() {
  if (!state.feed || !state.catalog) return;
  const t = TRANSLATIONS[state.lang];

  const statusText = document.getElementById("status-text");
  const statusDot = document.getElementById("status-dot");
  const statusBadge = document.getElementById("status-badge");
  const updatedEl = document.getElementById("last-updated-time");

  const healthy = !state.isOffline && state.feed.meta?.status === "healthy";
  if (statusText) statusText.textContent = healthy ? t.liveHealthy : t.offlineMode;
  if (statusDot) {
    statusDot.className = healthy
      ? "w-2 h-2 rounded-full bg-emerald-400 animate-pulse"
      : "w-2 h-2 rounded-full bg-amber-400";
  }
  if (statusBadge) {
    statusBadge.className = healthy
      ? "inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[11px] font-semibold bg-emerald-500/10 text-emerald-300 border border-emerald-500/30"
      : "inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[11px] font-semibold bg-amber-500/10 text-amber-300 border border-amber-500/30";
  }
  if (updatedEl) {
    updatedEl.textContent = (state.feed.meta?.generated_at || "").replace("T", " ").replace("Z", " UTC");
  }

  const tickerContainer = document.getElementById("top-live-ticker");
  if (!tickerContainer) return;

  const tickerIds = ["XAU_OZ", "GRAM_21K", "BTC", "ETH", "EUR", "SAR", "AED", "EGP", "TRY"];
  tickerContainer.innerHTML = tickerIds
    .map((id) => {
      const item = state.catalog.byId[id];
      if (!item) return "";
      const delta = state.tickDeltas[id] || { pctStr: "0.00%", direction: "neutral" };
      const badgeColor =
        delta.direction === "bullish"
          ? "text-emerald-400 bg-emerald-500/10 border-emerald-500/25"
          : delta.direction === "bearish"
          ? "text-rose-400 bg-rose-500/10 border-rose-500/25"
          : "text-slate-300 bg-slate-800/80 border-slate-700/60";
      const arrow =
        delta.direction === "bullish" ? "▲" : delta.direction === "bearish" ? "▼" : "•";

      return `
        <button type="button" data-select-asset="${escapeHtml(id)}"
          class="shrink-0 inline-flex items-center gap-2 px-3 py-1 rounded-lg bg-slate-900/80 hover:bg-slate-800/90 border border-slate-800 hover:border-amber-500/40 transition-colors cursor-pointer">
          <span class="text-xs font-bold text-amber-300 font-mono" dir="ltr">${escapeHtml(item.pairLabel)}</span>
          <span class="text-xs font-bold text-white font-mono tabular-nums" dir="ltr">${escapeHtml(item.spotDisplay)}</span>
          <span class="text-[10px] font-semibold px-1.5 py-0.5 rounded border font-mono ${badgeColor}" dir="ltr">${arrow} ${escapeHtml(delta.pctStr)}</span>
        </button>
      `;
    })
    .join("");
}

function populateAssetSelectors() {
  if (!state.catalog) return;
  const t = TRANSLATIONS[state.lang];
  const convSelect = document.getElementById("converter-asset-select");
  const alertSelect = document.getElementById("alert-asset-select");

  const optionsHtml = state.catalog.ordered
    .map((asset) => {
      const name = t.assets[asset.id] || asset.code;
      const selected = asset.id === state.baseAssetId ? "selected" : "";
      return `<option value="${escapeHtml(asset.id)}" ${selected}>${escapeHtml(asset.code)} — ${escapeHtml(name)}</option>`;
    })
    .join("");

  if (convSelect) {
    convSelect.innerHTML = optionsHtml;
    convSelect.value = state.baseAssetId;
  }

  if (alertSelect) {
    const prevVal = alertSelect.value || "XAU_OZ";
    alertSelect.innerHTML = state.catalog.ordered
      .filter((a) => a.id !== "USD")
      .map((asset) => {
        const name = t.assets[asset.id] || asset.code;
        return `<option value="${escapeHtml(asset.id)}">${escapeHtml(asset.code)} — ${escapeHtml(name)} (${escapeHtml(asset.spotDisplay)})</option>`;
      })
      .join("");
    if ([...alertSelect.options].some((o) => o.value === prevVal)) {
      alertSelect.value = prevVal;
    }
  }
}

function applyLanguageAwareTelegramLinks(lang = state.lang) {
  const tgCfg = MONETIZATION_CONFIG?.telegram || {};
  const isAr = lang === "ar";

  const publicUrl = isAr
    ? tgCfg.publicChannelAr || tgCfg.publicChannelUrl
    : tgCfg.publicChannelEn || tgCfg.publicChannelUrl;
  const vipUrl = isAr
    ? tgCfg.vipChannelAr || tgCfg.vipChannelUrl
    : tgCfg.vipChannelEn || tgCfg.vipChannelUrl;

  const setHref = (id, url) => {
    const el = document.getElementById(id);
    if (el && url) el.setAttribute("href", safeHref(url));
  };

  setHref("vip-channel-link", vipUrl);
  setHref("public-channel-link", publicUrl);
  setHref("telegram-bot-link", tgCfg.botUrl);

  document.querySelectorAll('[data-tg-channel="public"]').forEach((el) => {
    if (publicUrl) el.setAttribute("href", safeHref(publicUrl));
  });
  document.querySelectorAll('[data-tg-channel="vip"]').forEach((el) => {
    if (vipUrl) el.setAttribute("href", safeHref(vipUrl));
  });
}

function applyMonetizationConfigDom() {
  const vipCfg = MONETIZATION_CONFIG?.vipPayments || {};
  const adCfg = MONETIZATION_CONFIG?.advertising || {};

  applyLanguageAwareTelegramLinks(state.lang);

  const setHref = (id, url) => {
    const el = document.getElementById(id);
    if (el && url) el.setAttribute("href", safeHref(url));
  };

  setHref("nowpayments-vip-link", vipCfg.nowPaymentsVipUrl);
  setHref("aads-partner-link", adCfg.aAdsPartnerUrl);

  const unitId = String(adCfg.aAdsUnitId || "").trim();
  const liveAdBox = document.getElementById("aads-live-unit");
  const placeholderBar = document.getElementById("aads-placeholder-bar");
  if (liveAdBox && /^\d+$/.test(unitId)) {
    const sizeStr = String(adCfg.aAdsSize || "728x90");
    liveAdBox.innerHTML = `<iframe data-aa="${escapeHtml(unitId)}" src="https://ad.a-ads.com/${escapeHtml(unitId)}?size=${escapeHtml(sizeStr)}" style="width:728px;height:90px;border:0;padding:0;overflow:hidden;background-color:transparent;" title="A-Ads Sponsor"></iframe>`;
    liveAdBox.classList.remove("hidden");
    liveAdBox.classList.add("flex");
    placeholderBar?.classList.add("hidden");
  }
}

function renderContextualAffiliateCta() {
  const box = document.getElementById("affiliate-cta-box");
  if (!box || !state.catalog) return;

  const t = TRANSLATIONS[state.lang];
  const aff = MONETIZATION_CONFIG?.affiliates || {};
  const activeAsset = state.catalog.byId[state.baseAssetId] || state.catalog.byId.USD;
  const isFiat = activeAsset.category === "fiat";

  if (isFiat) {
    box.innerHTML = `
      <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-3 p-3.5 rounded-xl bg-gradient-to-r from-emerald-950/60 via-slate-900/90 to-slate-900 border border-emerald-500/30">
        <div class="space-y-0.5">
          <span class="inline-block text-[10px] font-bold tracking-wider uppercase px-2 py-0.5 rounded bg-emerald-500/15 text-emerald-300 border border-emerald-500/30">
            ${escapeHtml(t.affFiatBadge)}
          </span>
          <p class="text-xs sm:text-sm font-bold text-slate-100">${escapeHtml(t.affFiatTitle)}</p>
          <p class="text-[11px] text-slate-400">${escapeHtml(t.affFiatDesc)}</p>
        </div>
        <div class="flex items-center gap-2 shrink-0">
          <a href="${safeHref(aff.wise)}" target="_blank" rel="noopener noreferrer sponsored"
            class="px-3 py-1.5 rounded-lg text-xs font-bold bg-emerald-400 hover:bg-emerald-300 text-slate-950 transition-colors">
            ${escapeHtml(t.affFiatBtnPrimary)} ↗
          </a>
          <a href="${safeHref(aff.remitly)}" target="_blank" rel="noopener noreferrer sponsored"
            class="px-3 py-1.5 rounded-lg text-xs font-semibold bg-slate-800 hover:bg-slate-700 text-emerald-200 border border-emerald-500/30 transition-colors">
            ${escapeHtml(t.affFiatBtnSecondary)}
          </a>
        </div>
      </div>
    `;
  } else {
    box.innerHTML = `
      <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-3 p-3.5 rounded-xl bg-gradient-to-r from-amber-950/55 via-slate-900/90 to-slate-900 border border-amber-500/35">
        <div class="space-y-0.5">
          <span class="inline-block text-[10px] font-bold tracking-wider uppercase px-2 py-0.5 rounded bg-amber-500/15 text-amber-300 border border-amber-500/30">
            ${escapeHtml(t.affCryptoBadge)}
          </span>
          <p class="text-xs sm:text-sm font-bold text-slate-100">${escapeHtml(t.affCryptoTitle)}</p>
          <p class="text-[11px] text-slate-400">${escapeHtml(t.affCryptoDesc)}</p>
        </div>
        <div class="flex items-center gap-2 shrink-0">
          <a href="${safeHref(aff.bybit)}" target="_blank" rel="noopener noreferrer sponsored"
            class="px-3 py-1.5 rounded-lg text-xs font-bold bg-amber-400 hover:bg-amber-300 text-slate-950 transition-colors">
            ${escapeHtml(t.affCryptoBtnPrimary)} ↗
          </a>
          <a href="${safeHref(aff.binance)}" target="_blank" rel="noopener noreferrer sponsored"
            class="px-3 py-1.5 rounded-lg text-xs font-semibold bg-slate-800 hover:bg-slate-700 text-amber-200 border border-amber-500/30 transition-colors">
            ${escapeHtml(t.affCryptoBtnSecondary)}
          </a>
        </div>
      </div>
    `;
  }
}

function renderConversionMatrix() {
  if (!state.catalog) return;
  const t = TRANSLATIONS[state.lang];
  state.conversionResult = convertAllAssets(state.amount, state.baseAssetId, state.catalog);

  const usdReadout = document.getElementById("usd-equivalent-readout");
  if (usdReadout) {
    usdReadout.textContent = `$${state.conversionResult.usdEquivalentFormatted} USD`;
  }

  const renderGroup = (category) =>
    state.catalog.ordered
      .filter((item) => item.category === category)
      .map((item) => {
        const conv = state.conversionResult.conversions[item.id];
        const isBase = item.id === state.baseAssetId;
        const delta = state.tickDeltas[item.id] || { pctStr: "0.00%", direction: item.signal };
        const signalDir = delta.pctStr !== "0.00%" ? delta.direction : item.signal;

        const badgeClass =
          signalDir === "bullish"
            ? "bg-emerald-500/12 text-emerald-300 border-emerald-500/30"
            : signalDir === "bearish"
            ? "bg-rose-500/12 text-rose-300 border-rose-500/30"
            : "bg-slate-800 text-slate-300 border-slate-700";

        const signalLabel =
          delta.pctStr !== "0.00%"
            ? delta.pctStr
            : signalDir === "bullish"
            ? `▲ ${t.biasBullish}`
            : signalDir === "bearish"
            ? `▼ ${t.biasBearish}`
            : `• ${t.biasNeutral}`;

        const activeRing = isBase
          ? "border-amber-400/80 bg-amber-500/[0.08] ring-1 ring-amber-400/40"
          : "border-slate-800/90 bg-slate-900/70 hover:border-slate-700 hover:bg-slate-800/70";

        return `
          <div data-select-asset="${escapeHtml(item.id)}" role="button" tabindex="0"
            class="group relative p-3 rounded-xl border transition-all cursor-pointer select-none ${activeRing}">
            <div class="flex items-center justify-between gap-2 mb-1.5">
              <div class="flex items-center gap-1.5 min-w-0">
                <span class="inline-flex items-center justify-center w-6 h-6 rounded-md bg-slate-800 text-amber-300 text-xs font-bold font-mono shrink-0" dir="ltr">
                  ${escapeHtml(item.symbol)}
                </span>
                <div class="truncate">
                  <span class="text-xs font-extrabold text-white font-mono" dir="ltr">${escapeHtml(item.code)}</span>
                  <span class="block text-[11px] text-slate-400 truncate">${escapeHtml(t.assets[item.id] || item.code)}</span>
                </div>
              </div>
              <span class="text-[10px] font-semibold px-1.5 py-0.5 rounded border shrink-0 ${badgeClass}" dir="ltr">
                ${escapeHtml(signalLabel)}
              </span>
            </div>
            <div class="text-base sm:text-lg font-extrabold text-slate-50 font-mono tabular-nums truncate" dir="ltr">
              ${escapeHtml(conv?.formatted || "0.00")}
            </div>
            <div class="flex items-center justify-between mt-1 text-[10px] text-slate-400 font-mono" dir="ltr">
              <span>${escapeHtml(item.pairLabel)}: ${escapeHtml(item.spotDisplay)}</span>
              ${isBase ? '<span class="text-amber-300 font-bold">BASE</span>' : ""}
            </div>
          </div>
        `;
      })
      .join("");

  const fiatGrid = document.getElementById("grid-fiat");
  const goldGrid = document.getElementById("grid-gold");
  const cryptoGrid = document.getElementById("grid-crypto");

  if (fiatGrid) fiatGrid.innerHTML = renderGroup("fiat");
  if (goldGrid) goldGrid.innerHTML = renderGroup("gold");
  if (cryptoGrid) cryptoGrid.innerHTML = renderGroup("crypto");
}

function renderFundingSpreads() {
  const tbody = document.getElementById("funding-table-body");
  if (!tbody || !state.feed?.crypto?.assets) return;

  const cryptoMap = state.feed.crypto.assets;
  tbody.innerHTML = ["BTC", "ETH", "SOL"]
    .map((sym) => {
      const entry = cryptoMap[sym];
      if (!entry?.funding) return "";
      const f = entry.funding;
      const isNeutral = f.arbitrage_signal === "neutral_carry";
      const pillClass = isNeutral
        ? "bg-slate-800 text-slate-300 border-slate-700"
        : "bg-amber-500/15 text-amber-300 border-amber-500/35";

      return `
        <tr class="border-b border-slate-800/70 hover:bg-slate-800/35 transition-colors">
          <td class="py-2.5 px-3">
            <div class="font-bold text-xs text-white font-mono" dir="ltr">${escapeHtml(sym)}</div>
            <div class="text-[10px] text-slate-400 font-mono" dir="ltr">$${escapeHtml(entry.spot_price)}</div>
          </td>
          <td class="py-2.5 px-2 font-mono text-xs text-slate-200 tabular-nums" dir="ltr">
            <span class="block text-[10px] uppercase text-slate-400">${escapeHtml(f.primary_exchange)}</span>
            ${escapeHtml(f.primary_rate)}
          </td>
          <td class="py-2.5 px-2 font-mono text-xs text-slate-200 tabular-nums" dir="ltr">
            <span class="block text-[10px] uppercase text-slate-400">${escapeHtml(f.secondary_exchange)}</span>
            ${escapeHtml(f.secondary_rate)}
          </td>
          <td class="py-2.5 px-2 font-mono text-xs font-bold text-emerald-300 tabular-nums" dir="ltr">
            ${escapeHtml(f.spread_bps)} bps
          </td>
          <td class="py-2.5 px-2 font-mono text-xs font-bold text-amber-300 tabular-nums" dir="ltr">
            ${escapeHtml(f.annualized_spread_pct)}%
          </td>
          <td class="py-2.5 px-3 text-end">
            <span class="inline-block text-[10px] font-mono font-semibold px-2 py-0.5 rounded border ${pillClass}" dir="ltr">
              ${escapeHtml(f.arbitrage_signal.replace(/_/g, " "))}
            </span>
          </td>
        </tr>
      `;
    })
    .join("");
}

function renderVerifiedNews() {
  const listEl = document.getElementById("news-feed-list");
  if (!listEl || !state.feed?.news) return;

  const t = TRANSLATIONS[state.lang];
  const isAr = state.lang === "ar";

  listEl.innerHTML = state.feed.news
    .map((item) => {
      const copy = isAr ? item.ar : item.en;
      const pw = item.priced_in_warning || {};
      const pwText = isAr ? pw.ar : pw.en;

      const pwStyle = pw.active
        ? pw.severity === "high"
          ? "bg-rose-950/50 border-rose-500/40 text-rose-200"
          : "bg-amber-950/50 border-amber-500/40 text-amber-200"
        : "bg-emerald-950/35 border-emerald-500/30 text-emerald-200";

      const biasPills = Object.entries(item.assets_bias_24h || {})
        .map(([assetKey, dir]) => {
          const cls =
            dir === "bullish"
              ? "bg-emerald-500/15 text-emerald-300 border-emerald-500/30"
              : dir === "bearish"
              ? "bg-rose-500/15 text-rose-300 border-rose-500/30"
              : "bg-slate-800/90 text-slate-300 border-slate-700";
          const dirLabel =
            dir === "bullish" ? t.biasBullish : dir === "bearish" ? t.biasBearish : t.biasNeutral;
          return `<span class="inline-flex items-center gap-1 text-[10px] font-semibold px-2 py-0.5 rounded border ${cls}">
            <strong class="font-mono" dir="ltr">${escapeHtml(assetKey)}</strong> ${escapeHtml(dirLabel)}
          </span>`;
        })
        .join("");

      const pubShort = (item.published_at || "").slice(0, 16).replace("T", " ") + " UTC";

      return `
        <article class="p-4 rounded-xl bg-slate-900/75 border border-slate-800/90 hover:border-slate-700 transition-colors space-y-3">
          <div class="flex flex-wrap items-center justify-between gap-2 text-[11px]">
            <div class="flex items-center gap-2">
              <a href="${safeHref(item.source_url)}" target="_blank" rel="noopener noreferrer"
                class="inline-flex items-center gap-1 font-bold text-amber-300 hover:text-amber-200 underline decoration-amber-500/40">
                <span>${escapeHtml(item.source_name)}</span>
                <span aria-hidden="true">↗</span>
              </a>
              <span class="text-slate-500 font-mono" dir="ltr">${escapeHtml(pubShort)}</span>
            </div>
            <div class="flex items-center gap-1.5">
              ${
                item.auditor_verified
                  ? `<span class="px-2 py-0.5 rounded-full text-[10px] font-bold bg-emerald-500/15 text-emerald-300 border border-emerald-500/30">✓ ${escapeHtml(t.auditorVerified)}</span>`
                  : ""
              }
              <span class="px-2 py-0.5 rounded-full text-[10px] font-mono font-semibold bg-slate-800 text-slate-300 border border-slate-700">
                ${escapeHtml(t.confidenceLabel)}: ${Number(item.confidence_score || 0)}%
              </span>
            </div>
          </div>

          <h3 class="text-sm sm:text-base font-bold text-white leading-snug">
            ${escapeHtml(copy?.title || "")}
          </h3>

          <p class="text-xs text-slate-300 leading-relaxed">
            ${escapeHtml(copy?.summary || "")}
          </p>

          <div class="p-2.5 rounded-lg bg-slate-950/70 border border-slate-800/80">
            <span class="block text-[10px] font-bold uppercase tracking-wider text-amber-400 mb-0.5">
              ${escapeHtml(t.macroThesisLabel)}
            </span>
            <p class="text-xs text-slate-200 leading-relaxed">${escapeHtml(copy?.macro_thesis || "")}</p>
          </div>

          <div class="p-2.5 rounded-lg border text-[11px] leading-relaxed ${pwStyle}">
            <strong class="font-bold">${escapeHtml(pw.active ? t.pricedInActive : t.pricedInFresh)}:</strong>
            ${escapeHtml(pwText || "")}
          </div>

          <div class="flex flex-wrap gap-1.5 pt-0.5">
            ${biasPills}
          </div>
        </article>
      `;
    })
    .join("");
}

// ---------------------------------------------------------------------------
// 5. VIP Telegram Price Alert Engine
// ---------------------------------------------------------------------------

function renderVipAlertsList() {
  const listEl = document.getElementById("active-alerts-list");
  const badgeEl = document.getElementById("vip-badge-count");
  const t = TRANSLATIONS[state.lang];

  if (badgeEl) {
    badgeEl.textContent = String(state.alerts.length);
    badgeEl.classList.toggle("hidden", state.alerts.length === 0);
  }
  if (!listEl) return;

  if (state.alerts.length === 0) {
    listEl.innerHTML = `<p class="text-xs text-slate-400 py-3 text-center">${escapeHtml(t.noActiveAlerts)}</p>`;
    return;
  }

  listEl.innerHTML = state.alerts
    .map(
      (al) => `
      <div class="flex items-center justify-between gap-2 p-2.5 rounded-lg bg-slate-950/80 border border-slate-800">
        <div class="text-xs">
          <span class="font-bold text-amber-300 font-mono" dir="ltr">${escapeHtml(al.assetId)}</span>
          <span class="text-slate-300 mx-1">${al.condition === "above" ? "≥" : "≤"}</span>
          <span class="font-bold text-white font-mono" dir="ltr">${escapeHtml(al.targetPrice)}</span>
          ${
            al.triggered
              ? `<span class="ms-1.5 px-1.5 py-0.5 text-[10px] rounded bg-emerald-500/20 text-emerald-300 font-bold">TRIGGERED</span>`
              : ""
          }
        </div>
        <button type="button" data-delete-alert="${escapeHtml(al.id)}"
          class="text-[11px] font-semibold text-rose-400 hover:text-rose-300 px-2 py-1 rounded hover:bg-rose-500/10">
          ${escapeHtml(t.deleteAlertLabel)}
        </button>
      </div>
    `
    )
    .join("");
}

function evaluateLocalPriceAlerts() {
  if (!state.catalog || state.alerts.length === 0) return;
  let updated = false;

  for (const al of state.alerts) {
    if (al.triggered) continue;
    const item = state.catalog.byId[al.assetId];
    if (!item) continue;
    const currentScaled = parseDecimalToScaled(item.spotDisplay);
    const targetScaled = parseDecimalToScaled(al.targetPrice);
    if (currentScaled === null || targetScaled === null) continue;

    const hit =
      (al.condition === "above" && currentScaled >= targetScaled) ||
      (al.condition === "below" && currentScaled <= targetScaled);

    if (hit) {
      al.triggered = true;
      updated = true;
      showToast(
        state.lang === "ar"
          ? `تنبيه VIP: وصل ${al.assetId} إلى ${item.spotDisplay}!`
          : `VIP Alert Triggered: ${al.assetId} reached ${item.spotDisplay}!`
      );
    }
  }

  if (updated) {
    saveStoredAlerts();
    renderVipAlertsList();
  }
}

// ---------------------------------------------------------------------------
// 6. Responsive Mobile One-Handed Navigation & Modals
// ---------------------------------------------------------------------------

function applyMobileTabVisibility() {
  const isDesktop = window.matchMedia("(min-width: 1024px)").matches;
  const sections = {
    converter: document.getElementById("converter-panel"),
    rates: document.getElementById("rates-panel"),
    funding: document.getElementById("funding-panel"),
    news: document.getElementById("news-panel"),
  };

  if (isDesktop) {
    Object.values(sections).forEach((el) => el?.classList.remove("hidden"));
  } else {
    Object.entries(sections).forEach(([key, el]) => {
      if (!el) return;
      if (state.mobileTab === "converter") {
        // Show converter + quick rates together when on primary thumb tab
        el.classList.toggle("hidden", key !== "converter" && key !== "rates");
      } else {
        el.classList.toggle("hidden", key !== state.mobileTab);
      }
    });
  }

  document.querySelectorAll(".mobile-nav-btn").forEach((btn) => {
    const active = btn.getAttribute("data-tab") === state.mobileTab;
    btn.classList.toggle("text-amber-300", active);
    btn.classList.toggle("text-slate-400", !active);
  });
}

function openQuoteCardModal() {
  const modal = document.getElementById("modal-quote-card");
  const canvas = document.getElementById("quote-card-canvas");
  if (!modal || !canvas) return;
  renderQuoteCardCanvas(canvas, state);
  modal.classList.remove("hidden");
  modal.classList.add("flex");
  triggerTelegramHaptic("medium");
}

function closeQuoteCardModal() {
  const modal = document.getElementById("modal-quote-card");
  if (!modal) return;
  modal.classList.add("hidden");
  modal.classList.remove("flex");
}

function openVipAlertsModal() {
  const modal = document.getElementById("modal-vip-alerts");
  if (!modal) return;
  const alertSelect = document.getElementById("alert-asset-select");
  const priceInput = document.getElementById("alert-price-input");
  if (alertSelect && priceInput && !priceInput.value && state.catalog) {
    const chosen = state.catalog.byId[alertSelect.value] || state.catalog.byId.XAU_OZ;
    if (chosen) priceInput.value = chosen.spotDisplay;
  }
  renderVipAlertsList();
  modal.classList.remove("hidden");
  modal.classList.add("flex");
  triggerTelegramHaptic("medium");
}

function closeVipAlertsModal() {
  const modal = document.getElementById("modal-vip-alerts");
  if (!modal) return;
  modal.classList.add("hidden");
  modal.classList.remove("flex");
}

// ---------------------------------------------------------------------------
// 7. Master Render & Event Wiring
// ---------------------------------------------------------------------------

function recomputeAndRenderAll() {
  applyLanguageAttributes();
  applyLanguageAwareTelegramLinks(state.lang);
  populateAssetSelectors();
  renderStatusAndTopTicker();
  renderConversionMatrix();
  renderContextualAffiliateCta();
  renderFundingSpreads();
  renderVerifiedNews();
  renderVipAlertsList();
  updateSeoJsonLd(state.feed, state.lang);
  applyMobileTabVisibility();

  const canvas = document.getElementById("quote-card-canvas");
  const quoteModal = document.getElementById("modal-quote-card");
  if (canvas && quoteModal && !quoteModal.classList.contains("hidden")) {
    renderQuoteCardCanvas(canvas, state);
  }
}

function setLanguage(lang) {
  if (lang !== "en" && lang !== "ar") return;
  state.lang = lang;
  localStorage.setItem(STORAGE_KEYS.LANG, lang);
  try {
    if (window.location.protocol.startsWith("http") && window.history?.replaceState) {
      const nextUrl = new URL(window.location.href);
      nextUrl.searchParams.set("lang", lang);
      window.history.replaceState({}, "", nextUrl.toString());
    }
  } catch {
    // Ignore history API restrictions
  }
  triggerTelegramHaptic("light");
  recomputeAndRenderAll();
}

function setActiveBaseAsset(assetId) {
  if (!state.catalog?.byId?.[assetId]) return;
  state.baseAssetId = assetId;
  triggerTelegramHaptic("light");
  populateAssetSelectors();
  renderConversionMatrix();
  renderContextualAffiliateCta();
}

function bindEvents() {
  document.getElementById("btn-lang-en")?.addEventListener("click", () => setLanguage("en"));
  document.getElementById("btn-lang-ar")?.addEventListener("click", () => setLanguage("ar"));

  const amountInput = document.getElementById("converter-amount-input");
  if (amountInput) {
    amountInput.value = state.amount;
    amountInput.addEventListener("input", (e) => {
      state.amount = e.target.value;
      renderConversionMatrix();
    });
  }

  document.getElementById("converter-asset-select")?.addEventListener("change", (e) => {
    setActiveBaseAsset(e.target.value);
  });

  document.querySelectorAll(".preset-chip").forEach((chip) => {
    chip.addEventListener("click", () => {
      const val = chip.getAttribute("data-amount") || "100";
      state.amount = val;
      if (amountInput) amountInput.value = val;
      triggerTelegramHaptic("light");
      renderConversionMatrix();
    });
  });

  // Event delegation for clicking any asset card or top ticker pill
  document.addEventListener("click", (e) => {
    const assetTrigger = e.target.closest("[data-select-asset]");
    if (assetTrigger) {
      setActiveBaseAsset(assetTrigger.getAttribute("data-select-asset"));
      return;
    }

    const delAlertBtn = e.target.closest("[data-delete-alert]");
    if (delAlertBtn) {
      const id = delAlertBtn.getAttribute("data-delete-alert");
      state.alerts = state.alerts.filter((a) => a.id !== id);
      saveStoredAlerts();
      renderVipAlertsList();
    }
  });

  document.getElementById("btn-wake-lock")?.addEventListener("click", toggleWakeLock);
  document.getElementById("btn-refresh-feed")?.addEventListener("click", async () => {
    await fetchLiveFeed();
    showToast(state.lang === "ar" ? "تمت مزامنة الأسعار الحية بنجاح" : "Live market feed synchronized");
  });

  document.getElementById("btn-open-quote-card")?.addEventListener("click", openQuoteCardModal);
  document.getElementById("btn-close-quote-modal")?.addEventListener("click", closeQuoteCardModal);
  document.getElementById("btn-download-png")?.addEventListener("click", () => {
    const canvas = document.getElementById("quote-card-canvas");
    downloadCanvasAsPng(canvas, state.baseAssetId);
  });
  document.getElementById("btn-share-png")?.addEventListener("click", async () => {
    const canvas = document.getElementById("quote-card-canvas");
    const res = await shareCanvasPng(canvas, state.baseAssetId);
    if (res === "copied") {
      showToast(state.lang === "ar" ? "تم نسخ بطاقة PNG إلى الحافظة!" : "Quote Card PNG copied to clipboard!");
    } else if (res === "downloaded") {
      showToast(state.lang === "ar" ? "تم تحميل بطاقة PNG" : "Quote Card PNG downloaded");
    }
  });

  document.getElementById("btn-open-vip-alerts")?.addEventListener("click", openVipAlertsModal);
  document.getElementById("btn-close-vip-modal")?.addEventListener("click", closeVipAlertsModal);

  document.getElementById("alert-asset-select")?.addEventListener("change", (e) => {
    const chosen = state.catalog?.byId?.[e.target.value];
    const priceInput = document.getElementById("alert-price-input");
    if (chosen && priceInput) priceInput.value = chosen.spotDisplay;
  });

  document.getElementById("alert-form")?.addEventListener("submit", (e) => {
    e.preventDefault();
    const assetId = document.getElementById("alert-asset-select")?.value || "XAU_OZ";
    const condition = document.getElementById("alert-condition-select")?.value || "above";
    const targetPrice = (document.getElementById("alert-price-input")?.value || "").trim();

    if (parseDecimalToScaled(targetPrice) === null) {
      showToast(state.lang === "ar" ? "يرجى إدخال سعر مستهدف صحيح" : "Please enter a valid target price");
      return;
    }

    state.alerts.unshift({
      id: `al-${Date.now()}`,
      assetId,
      condition,
      targetPrice,
      triggered: false,
    });
    saveStoredAlerts();
    renderVipAlertsList();
    evaluateLocalPriceAlerts();
    showToast(state.lang === "ar" ? "تم تفعيل تنبيه السعر بنجاح" : "Price alert armed");
  });

  document.querySelectorAll(".mobile-nav-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      const tab = btn.getAttribute("data-tab") || "converter";
      if (tab === "vip") {
        openVipAlertsModal();
        return;
      }
      state.mobileTab = tab;
      triggerTelegramHaptic("light");
      applyMobileTabVisibility();
      window.scrollTo({ top: 0, behavior: "smooth" });
    });
  });

  window.addEventListener("resize", applyMobileTabVisibility);
  window.addEventListener("online", () => {
    state.isOffline = false;
    fetchLiveFeed();
  });
  window.addEventListener("offline", () => {
    state.isOffline = true;
    renderStatusAndTopTicker();
  });

  document.addEventListener("visibilitychange", async () => {
    if (document.visibilityState === "visible" && state.wakeLockActive && !state.wakeLockSentinel) {
      try {
        state.wakeLockSentinel = await navigator.wakeLock.request("screen");
      } catch {
        // Ignore if denied
      }
    }
  });

  // Close modals on Escape or backdrop click
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      closeQuoteCardModal();
      closeVipAlertsModal();
    }
  });
  document.getElementById("modal-quote-card")?.addEventListener("click", (e) => {
    if (e.target.id === "modal-quote-card") closeQuoteCardModal();
  });
  document.getElementById("modal-vip-alerts")?.addEventListener("click", (e) => {
    if (e.target.id === "modal-vip-alerts") closeVipAlertsModal();
  });
}

// Boot Sequence
document.addEventListener("DOMContentLoaded", async () => {
  initTelegramMiniApp();
  registerServiceWorker();
  applyMonetizationConfigDom();
  applyLanguageAttributes();
  bindEvents();
  await fetchLiveFeed();
  setInterval(fetchLiveFeed, 30000);
});
