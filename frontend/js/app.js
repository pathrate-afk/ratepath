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
import { getSyriaRevolutionFlagSvg } from "./syria-flag.js";
import {
  addAssetToWorkspace,
  isWorkspaceCustomized,
  loadWorkspaceFromTelegramCloud,
  loadWorkspaceLocal,
  mergeWorkspaces,
  moveAssetByDelta,
  pinAssetToTop,
  removeAssetFromWorkspace,
  reorderAssetBefore,
  resetWorkspaceDefaults,
  saveWorkspaceLocal,
} from "./workspace.js";
import {
  ensureFreshSession,
  fetchCloudLayout,
  isCloudSyncConfigured,
  sendEmailOtp,
  signOutCloudSync,
  upsertCloudLayout,
  verifyEmailOtp,
} from "./auth_sync.js";
import {
  AFFILIATES_ENABLED,
  CATALOG_FEED_URL,
  CMC_SEARCH_API_URL,
  LIVE_FEED_URL,
  MONETIZATION_CONFIG,
  PUBLIC_SITE_URL,
} from "../monetization.config.js";

const STORAGE_KEYS = {
  LANG: "ratepath_lang",
  FEED_CACHE: "ratepath_cached_feed_v1",
  CATALOG_CACHE: "ratepath_cached_catalog_v1",
  PREV_FEED: "ratepath_prev_feed_v1",
  ALERTS: "ratepath_vip_alerts_v1",
};

const DEFAULT_FEED_ENDPOINTS = [
  "./public/live_feed.json",
  "/frontend/public/live_feed.json",
  "./live_feed.json",
];

const DEFAULT_CATALOG_ENDPOINTS = [
  "./public/catalog_feed.json",
  "/frontend/public/catalog_feed.json",
  "./catalog_feed.json",
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

function resolveCatalogEndpoints() {
  const customUrl = String(
    CATALOG_FEED_URL || MONETIZATION_CONFIG?.CATALOG_FEED_URL || ""
  ).trim();
  if (/^https?:\/\//i.test(customUrl)) {
    return [customUrl, ...DEFAULT_CATALOG_ENDPOINTS];
  }
  return DEFAULT_CATALOG_ENDPOINTS;
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
  catalogFeed: null,
  catalog: null,
  previousCatalog: null,
  tickDeltas: {},
  conversionResult: null,
  alerts: loadStoredAlerts(),
  workspace: loadWorkspaceLocal(),
  editMode: false,
  workspaceFilter: "all",
  pickerTab: "all",
  pickerQuery: "",
  pickerRemoteResults: [],
  pickerRemoteLoading: false,
  authSession: null,
  pendingOtpEmail: "",
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

async function fetchCatalogFeed() {
  let loadedCatalog = null;
  for (const url of resolveCatalogEndpoints()) {
    try {
      const resp = await fetch(`${url}?t=${Date.now()}`, { cache: "no-store" });
      if (resp.ok) {
        loadedCatalog = await resp.json();
        break;
      }
    } catch {
      // Try next catalog endpoint
    }
  }
  if (loadedCatalog) {
    try {
      localStorage.setItem(STORAGE_KEYS.CATALOG_CACHE, JSON.stringify(loadedCatalog));
    } catch {
      // Catalog may exceed tight quota on old mobile WebViews; keep in memory
    }
    return loadedCatalog;
  }
  try {
    const cached = localStorage.getItem(STORAGE_KEYS.CATALOG_CACHE);
    return cached ? JSON.parse(cached) : null;
  } catch {
    return null;
  }
}

function rebuildCatalogFromState() {
  if (!state.feed) return;
  const prevRaw = localStorage.getItem(STORAGE_KEYS.PREV_FEED);
  const prevFeed = prevRaw ? JSON.parse(prevRaw) : null;

  state.catalog = buildAssetCatalog(
    state.feed,
    state.catalogFeed,
    state.workspace?.added_custom_assets || {}
  );
  state.previousCatalog = prevFeed ? buildAssetCatalog(prevFeed, state.catalogFeed) : null;
  state.tickDeltas = computeTickDeltas(state.catalog, state.previousCatalog);

  if (!state.catalog.byId[state.baseAssetId]) {
    state.baseAssetId = "USD";
  }
}

async function fetchLiveFeed() {
  let loadedFeed = null;
  let fromOfflineCache = false;

  const catalogPromise = fetchCatalogFeed();

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

  const loadedCatalog = await catalogPromise;
  if (loadedCatalog) {
    state.catalogFeed = loadedCatalog;
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

  state.isOffline = fromOfflineCache || !navigator.onLine;
  state.feed = loadedFeed;
  rebuildCatalogFromState();

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

  const tickerIds = ["XAU_OZ", "GRAM_21K", "BTC", "ETH", "EUR", "SAR", "AED", "EGP", "TRY", "SYP"];
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
      const flagHtml =
        id === "SYP"
          ? getSyriaRevolutionFlagSvg("w-[18px] h-[12px] rounded-[2px] shrink-0 ring-1 ring-white/20")
          : "";

      return `
        <button type="button" data-select-asset="${escapeHtml(id)}"
          class="shrink-0 inline-flex items-center gap-2 px-3 py-1 rounded-lg bg-slate-900/80 hover:bg-slate-800/90 border border-slate-800 hover:border-amber-500/40 transition-colors cursor-pointer">
          ${flagHtml}
          <span class="text-xs font-bold text-amber-300 font-mono" dir="ltr">${escapeHtml(item.pairLabel)}</span>
          <span class="text-xs font-bold text-white font-mono tabular-nums" dir="ltr">${escapeHtml(item.spotDisplay)}</span>
          <span class="text-[10px] font-semibold px-1.5 py-0.5 rounded border font-mono ${badgeColor}" dir="ltr">${arrow} ${escapeHtml(delta.pctStr)}</span>
        </button>
      `;
    })
    .join("");
}

function renderConverterBaseFlag() {
  const flagSlot = document.getElementById("converter-base-flag");
  if (!flagSlot) return;
  if (state.baseAssetId === "SYP") {
    flagSlot.innerHTML = getSyriaRevolutionFlagSvg("w-[27px] h-[18px] rounded-[3px] shrink-0 ring-1 ring-white/20");
    flagSlot.classList.remove("hidden");
    flagSlot.classList.add("inline-flex");
  } else {
    flagSlot.innerHTML = "";
    flagSlot.classList.add("hidden");
    flagSlot.classList.remove("inline-flex");
  }
}

function renderAlertAssetFlag() {
  const flagSlot = document.getElementById("alert-asset-flag");
  const alertSelect = document.getElementById("alert-asset-select");
  if (!flagSlot || !alertSelect) return;
  if (alertSelect.value === "SYP") {
    flagSlot.innerHTML = getSyriaRevolutionFlagSvg("w-[21px] h-[14px] rounded-[2px] shrink-0 ring-1 ring-white/20");
    flagSlot.classList.remove("hidden");
    flagSlot.classList.add("inline-flex");
  } else {
    flagSlot.innerHTML = "";
    flagSlot.classList.add("hidden");
    flagSlot.classList.remove("inline-flex");
  }
}

function getLocalizedAssetName(asset) {
  const t = TRANSLATIONS[state.lang] || TRANSLATIONS.en;
  if (t.assets[asset.id]) return t.assets[asset.id];
  return state.lang === "ar"
    ? asset.nameAr || asset.nameEn || asset.code
    : asset.nameEn || asset.code;
}

function getActiveWorkspaceAssets() {
  if (!state.catalog) return [];
  const ids = Array.isArray(state.workspace?.ordered_ids) ? state.workspace.ordered_ids : [];
  return ids.map((id) => state.catalog.byId[id]).filter(Boolean);
}

function formatOptionLabel(asset, name) {
  if (asset.id === "SYP") {
    return state.lang === "ar" ? `🟢 ل.س — ${name}` : `🟢 SYP — ${name}`;
  }
  return `${asset.code} — ${name}`;
}

function populateAssetSelectors() {
  if (!state.catalog) return;
  const convSelect = document.getElementById("converter-asset-select");
  const alertSelect = document.getElementById("alert-asset-select");

  // Converter includes all active visitor workspace assets (in their exact order) + base asset
  const activeAssets = getActiveWorkspaceAssets();
  const selectorAssets = activeAssets.length > 0 ? [...activeAssets] : [...state.catalog.ordered];
  if (!selectorAssets.some((a) => a.id === state.baseAssetId) && state.catalog.byId[state.baseAssetId]) {
    selectorAssets.unshift(state.catalog.byId[state.baseAssetId]);
  }
  if (!selectorAssets.some((a) => a.id === "USD") && state.catalog.byId.USD) {
    selectorAssets.push(state.catalog.byId.USD);
  }

  const optionsHtml = selectorAssets
    .map((asset) => {
      const name = getLocalizedAssetName(asset);
      const selected = asset.id === state.baseAssetId ? "selected" : "";
      const label = formatOptionLabel(asset, name);
      return `<option value="${escapeHtml(asset.id)}" ${selected}>${escapeHtml(label)}</option>`;
    })
    .join("");

  if (convSelect) {
    convSelect.innerHTML = optionsHtml;
    convSelect.value = state.baseAssetId;
  }

  if (alertSelect) {
    const prevVal = alertSelect.value || "XAU_OZ";
    alertSelect.innerHTML = selectorAssets
      .filter((a) => a.id !== "USD")
      .map((asset) => {
        const name = getLocalizedAssetName(asset);
        const label = formatOptionLabel(asset, name);
        return `<option value="${escapeHtml(asset.id)}">${escapeHtml(label)} (${escapeHtml(asset.spotDisplay)})</option>`;
      })
      .join("");
    if ([...alertSelect.options].some((o) => o.value === prevVal)) {
      alertSelect.value = prevVal;
    }
  }

  renderConverterBaseFlag();
  renderAlertAssetFlag();
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

  if (!AFFILIATES_ENABLED || MONETIZATION_CONFIG?.AFFILIATES_ENABLED === false) {
    box.innerHTML = "";
    box.classList.add("hidden");
    return;
  }
  box.classList.remove("hidden");

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

function renderAssetIconBadge(item) {
  if (item.id === "SYP") {
    return `<span class="inline-flex items-center justify-center w-[27px] h-[18px] rounded-[3px] overflow-hidden bg-slate-900 ring-1 ring-white/20 shrink-0" dir="ltr">${getSyriaRevolutionFlagSvg("w-full h-full block")}</span>`;
  }
  if (item.logoUrl && /^https?:\/\//i.test(item.logoUrl)) {
    const fallbackSym = escapeHtml((item.code || "?").slice(0, 3));
    return `<span class="inline-flex items-center justify-center w-6 h-6 rounded-full overflow-hidden bg-slate-800 ring-1 ring-white/10 shrink-0" dir="ltr"><img src="${escapeHtml(item.logoUrl)}" alt="${escapeHtml(item.code)}" loading="lazy" class="rp-coin-logo" onerror="this.outerHTML='<span class=&quot;text-[10px] font-bold text-amber-300 font-mono&quot;>${fallbackSym}</span>'" /></span>`;
  }
  const badgeTone =
    item.category === "gold"
      ? "bg-amber-500/15 text-amber-300 ring-1 ring-amber-400/30"
      : item.category === "crypto"
      ? "bg-sky-500/15 text-sky-300 ring-1 ring-sky-400/30"
      : "bg-slate-800 text-amber-300";
  return `<span class="inline-flex items-center justify-center min-w-6 h-6 px-1 rounded-md ${badgeTone} text-[11px] font-bold font-mono shrink-0" dir="ltr">${escapeHtml(item.symbol || item.code)}</span>`;
}

function renderWorkspaceToolbarState() {
  const t = TRANSLATIONS[state.lang];
  const activeCount = getActiveWorkspaceAssets().length;
  const countBadge = document.getElementById("workspace-count-badge");
  if (countBadge) countBadge.textContent = String(activeCount);

  const editBtn = document.getElementById("btn-toggle-edit-mode");
  const editLabel = document.getElementById("btn-edit-mode-label");
  const editHint = document.getElementById("workspace-edit-hint");
  const resetBtn = document.getElementById("btn-reset-workspace");

  if (editBtn) {
    editBtn.setAttribute("aria-pressed", state.editMode ? "true" : "false");
    editBtn.className = state.editMode
      ? "inline-flex items-center gap-1 px-2.5 py-1.5 rounded-lg text-xs font-extrabold bg-amber-400/20 text-amber-300 border border-amber-400/60 transition-colors"
      : "inline-flex items-center gap-1 px-2.5 py-1.5 rounded-lg text-xs font-bold bg-slate-800 hover:bg-slate-700 text-slate-200 border border-slate-700 transition-colors";
  }
  if (editLabel) editLabel.textContent = state.editMode ? t.btnDoneEditing : t.btnCustomize;
  if (editHint) editHint.classList.toggle("hidden", !state.editMode);

  const showReset = state.editMode || isWorkspaceCustomized(state.workspace);
  if (resetBtn) resetBtn.classList.toggle("hidden", !showReset);

  document.querySelectorAll(".ws-filter-btn").forEach((btn) => {
    const active = btn.getAttribute("data-ws-filter") === state.workspaceFilter;
    btn.className = active
      ? "ws-filter-btn px-2.5 py-1 rounded-lg text-xs font-bold bg-amber-400/20 text-amber-300 border border-amber-400/40 transition-colors"
      : "ws-filter-btn px-2.5 py-1 rounded-lg text-xs font-semibold bg-slate-900 text-slate-300 hover:text-white border border-slate-800 transition-colors";
  });

  const covBadge = document.getElementById("catalog-coverage-badge");
  if (covBadge && state.catalogFeed?.meta) {
    const fm = state.catalogFeed.meta;
    covBadge.textContent = `${fm.fiat_count || 155}+ FX • SYP • Gold/Silver • CMC ${fm.crypto_count || 300}`;
  }
}

function renderConversionMatrix() {
  if (!state.catalog) return;
  const t = TRANSLATIONS[state.lang];

  const activeAssets = getActiveWorkspaceAssets();
  const convertIds = [
    ...new Set([
      ...activeAssets.map((a) => a.id),
      ...state.catalog.ordered.map((a) => a.id),
      state.baseAssetId,
    ]),
  ];
  state.conversionResult = convertAllAssets(
    state.amount,
    state.baseAssetId,
    state.catalog,
    convertIds
  );

  const usdReadout = document.getElementById("usd-equivalent-readout");
  if (usdReadout) {
    usdReadout.textContent = `$${state.conversionResult.usdEquivalentFormatted} USD`;
  }

  // Render Agent 7 Price Guardian screen_vs_real_spread_pct badge when present
  const spreadBadge = document.getElementById("gold-spread-badge");
  const spreadPct = state.feed?.gold?.screen_vs_real_spread_pct;
  if (spreadBadge) {
    if (spreadPct !== undefined && spreadPct !== null && String(spreadPct).trim() !== "") {
      spreadBadge.textContent =
        state.lang === "ar"
          ? `فارق السوق الحقيقي: ${spreadPct}%`
          : `Screen vs Real: ${spreadPct}%`;
      spreadBadge.classList.remove("hidden");
    } else {
      spreadBadge.classList.add("hidden");
    }
  }

  renderWorkspaceToolbarState();

  const visibleAssets = activeAssets.filter(
    (item) => state.workspaceFilter === "all" || item.category === state.workspaceFilter
  );

  const wsGrid = document.getElementById("grid-workspace");
  const emptyBox = document.getElementById("workspace-empty-state");
  if (!wsGrid) return;

  if (activeAssets.length === 0) {
    wsGrid.innerHTML = "";
    emptyBox?.classList.remove("hidden");
    return;
  }
  emptyBox?.classList.add("hidden");

  const isEdit = state.editMode;
  wsGrid.innerHTML = visibleAssets
    .map((item) => {
      const conv = state.conversionResult.conversions[item.id];
      const isBase = item.id === state.baseAssetId;
      const delta = state.tickDeltas[item.id] || { pctStr: "0.00%", direction: item.signal };
      const chg24 = item.change24hPct && item.change24hPct !== "0.00" ? `${item.change24hPct}%` : null;
      const signalDir = chg24
        ? chg24.startsWith("-")
          ? "bearish"
          : "bullish"
        : delta.pctStr !== "0.00%"
        ? delta.direction
        : item.signal;

      const badgeClass =
        signalDir === "bullish"
          ? "bg-emerald-500/12 text-emerald-300 border-emerald-500/30"
          : signalDir === "bearish"
          ? "bg-rose-500/12 text-rose-300 border-rose-500/30"
          : "bg-slate-800 text-slate-300 border-slate-700";

      const signalLabel = chg24
        ? chg24
        : delta.pctStr !== "0.00%"
        ? delta.pctStr
        : signalDir === "bullish"
        ? `▲ ${t.biasBullish}`
        : signalDir === "bearish"
        ? `▼ ${t.biasBearish}`
        : `• ${t.biasNeutral}`;

      const activeRing = isBase
        ? "border-amber-400/80 bg-amber-500/[0.08] ring-1 ring-amber-400/40"
        : "border-slate-800/90 bg-slate-900/70 hover:border-slate-700 hover:bg-slate-800/70";

      const editControlsHtml = isEdit
        ? `
          <div class="flex items-center justify-between gap-1 mb-2 pb-1.5 border-b border-slate-800/90" dir="ltr">
            <span class="rp-drag-handle inline-flex items-center gap-1 px-1.5 py-0.5 rounded bg-slate-800 text-amber-300 text-[11px] font-mono font-bold select-none"
              data-ws-drag-handle="${escapeHtml(item.id)}" title="Drag to reorder">⋮⋮</span>
            <div class="inline-flex items-center gap-1">
              <button type="button" data-ws-pin="${escapeHtml(item.id)}" title="Pin to Top"
                class="w-6 h-6 inline-flex items-center justify-center rounded bg-slate-800 hover:bg-amber-400/20 text-amber-300 text-[11px] transition-colors">📌</button>
              <button type="button" data-ws-up="${escapeHtml(item.id)}" title="Move Up"
                class="w-6 h-6 inline-flex items-center justify-center rounded bg-slate-800 hover:bg-slate-700 text-slate-200 text-xs font-bold transition-colors">↑</button>
              <button type="button" data-ws-down="${escapeHtml(item.id)}" title="Move Down"
                class="w-6 h-6 inline-flex items-center justify-center rounded bg-slate-800 hover:bg-slate-700 text-slate-200 text-xs font-bold transition-colors">↓</button>
              <button type="button" data-ws-remove="${escapeHtml(item.id)}" title="Remove Card"
                class="w-6 h-6 inline-flex items-center justify-center rounded bg-rose-500/20 hover:bg-rose-500/35 text-rose-300 text-xs font-extrabold transition-colors">✕</button>
            </div>
          </div>
        `
        : "";

      const subTag =
        item.id === "SYP" && item.sourceTag === "sp-today"
          ? `<span class="text-[9px] font-mono text-emerald-400/90">SP-Today</span>`
          : item.cmcRank
          ? `<span class="text-[9px] font-mono text-slate-500">#${Number(item.cmcRank)}</span>`
          : isBase
          ? `<span class="text-amber-300 font-bold">BASE</span>`
          : "";

      return `
        <div data-ws-card="${escapeHtml(item.id)}" ${isEdit ? 'draggable="true"' : `data-select-asset="${escapeHtml(item.id)}" role="button" tabindex="0"`}
          class="group relative p-3 rounded-xl border transition-all select-none ${isEdit ? "rp-edit-card" : "cursor-pointer"} ${activeRing}">
          ${editControlsHtml}
          <div class="flex items-center justify-between gap-1.5 mb-1.5">
            <div class="flex items-center gap-1.5 min-w-0">
              ${renderAssetIconBadge(item)}
              <div class="truncate">
                <span class="text-xs font-extrabold text-white font-mono" dir="ltr">${escapeHtml(item.code)}</span>
                <span class="block text-[11px] text-slate-400 truncate">${escapeHtml(getLocalizedAssetName(item))}</span>
              </div>
            </div>
            <span class="text-[10px] font-semibold px-1.5 py-0.5 rounded border shrink-0 font-mono ${badgeClass}" dir="ltr">
              ${escapeHtml(signalLabel)}
            </span>
          </div>
          <div class="text-base sm:text-lg font-extrabold text-slate-50 font-mono tabular-nums truncate" dir="ltr">
            ${escapeHtml(conv?.formatted || "0.00")}
          </div>
          <div class="flex items-center justify-between gap-1 mt-1 text-[10px] text-slate-400 font-mono" dir="ltr">
            <span class="truncate">${escapeHtml(item.pairLabel)}: ${escapeHtml(item.spotDisplay)}</span>
            ${subTag}
          </div>
        </div>
      `;
    })
    .join("");
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
        <div class="text-xs inline-flex items-center gap-1.5">
          ${al.assetId === "SYP" ? getSyriaRevolutionFlagSvg("w-[18px] h-[12px] rounded-[2px] shrink-0 ring-1 ring-white/20") : ""}
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
// 6b. Workspace Persistence, Asset Search Picker & Supabase Email Sync
// ---------------------------------------------------------------------------

let cloudSyncTimer = null;
function scheduleCloudWorkspaceSync() {
  if (!state.authSession) return;
  clearTimeout(cloudSyncTimer);
  cloudSyncTimer = setTimeout(async () => {
    await upsertCloudLayout(state.authSession, state.workspace);
  }, 800);
}

function commitWorkspaceChange(nextWorkspace, { rebuildCatalog = false, toastMsg = null } = {}) {
  state.workspace = saveWorkspaceLocal(nextWorkspace);
  if (rebuildCatalog) {
    rebuildCatalogFromState();
  }
  populateAssetSelectors();
  renderConversionMatrix();
  renderContextualAffiliateCta();
  if (!document.getElementById("modal-asset-picker")?.classList.contains("hidden")) {
    renderAssetPickerResults();
  }
  scheduleCloudWorkspaceSync();
  if (toastMsg) showToast(toastMsg);
}

function openAssetPickerModal() {
  const modal = document.getElementById("modal-asset-picker");
  if (!modal) return;
  const searchInput = document.getElementById("asset-search-input");
  if (searchInput) {
    searchInput.placeholder = TRANSLATIONS[state.lang].pickerSearchPlaceholder;
    searchInput.value = state.pickerQuery;
  }
  renderAssetPickerResults();
  modal.classList.remove("hidden");
  modal.classList.add("flex");
  setTimeout(() => searchInput?.focus(), 40);
  triggerTelegramHaptic("light");
}

function closeAssetPickerModal() {
  const modal = document.getElementById("modal-asset-picker");
  if (!modal) return;
  modal.classList.add("hidden");
  modal.classList.remove("flex");
}

async function runRemoteCmcSearch(queryText) {
  const q = String(queryText || "").trim();
  if (q.length < 2) return;
  state.pickerRemoteLoading = true;
  renderAssetPickerResults();

  try {
    const endpoint = String(CMC_SEARCH_API_URL || "/api/cmc-search").trim();
    const resp = await fetch(`${endpoint}?q=${encodeURIComponent(q)}`);
    if (resp.ok) {
      const data = await resp.json();
      state.pickerRemoteResults = Array.isArray(data?.results) ? data.results : [];
    } else {
      state.pickerRemoteResults = [];
    }
  } catch {
    state.pickerRemoteResults = [];
  } finally {
    state.pickerRemoteLoading = false;
    renderAssetPickerResults();
  }
}

function renderAssetPickerResults() {
  const listEl = document.getElementById("asset-picker-results");
  const remoteBtn = document.getElementById("btn-cmc-remote-search");
  if (!listEl || !state.catalog) return;

  const t = TRANSLATIONS[state.lang];
  const q = state.pickerQuery.trim().toLowerCase();
  const activeSet = new Set(state.workspace?.ordered_ids || []);

  document.querySelectorAll(".picker-tab-btn").forEach((btn) => {
    const active = btn.getAttribute("data-picker-tab") === state.pickerTab;
    btn.className = active
      ? "picker-tab-btn px-2.5 py-1 rounded-lg text-xs font-bold bg-amber-400 text-slate-950"
      : "picker-tab-btn px-2.5 py-1 rounded-lg text-xs font-semibold bg-slate-800 text-slate-300 hover:text-white";
  });

  if (remoteBtn) {
    remoteBtn.classList.toggle("hidden", q.length < 2);
  }

  let localMatches = state.catalog.allSearchable.filter((item) => {
    if (state.pickerTab !== "all" && item.category !== state.pickerTab) return false;
    if (!q) return true;
    const hay = `${item.id} ${item.code} ${item.nameEn || ""} ${item.nameAr || ""} ${getLocalizedAssetName(item)}`.toLowerCase();
    return hay.includes(q);
  });

  // Prioritize exact symbol matches first
  if (q) {
    localMatches.sort((a, b) => {
      const aExact = a.code.toLowerCase() === q ? 0 : a.code.toLowerCase().startsWith(q) ? 1 : 2;
      const bExact = b.code.toLowerCase() === q ? 0 : b.code.toLowerCase().startsWith(q) ? 1 : 2;
      return aExact - bExact;
    });
  }

  // Merge remote CMC long-tail results if available
  const existingIds = new Set(localMatches.map((x) => x.id));
  const combined = [...localMatches.slice(0, 80)];
  for (const rc of state.pickerRemoteResults) {
    if (!rc?.id || existingIds.has(rc.id)) continue;
    combined.push({
      id: rc.id,
      code: rc.symbol,
      symbol: rc.symbol,
      category: "crypto",
      nameEn: rc.name || rc.symbol,
      nameAr: rc.name || rc.symbol,
      spotDisplay: rc.price_usd || "0",
      pairLabel: `${rc.symbol}/USD`,
      logoUrl: rc.logo_url || "",
      change24hPct: rc.change_24h_pct || "0.00",
      cmcId: rc.cmc_id ?? null,
      cmcRank: rc.rank ?? null,
      _remoteDescriptor: rc,
    });
  }

  if (state.pickerRemoteLoading) {
    listEl.innerHTML = `<p class="text-xs text-sky-300 py-6 text-center animate-pulse">${escapeHtml(t.pickerRemoteLoading)}</p>`;
    return;
  }

  if (combined.length === 0) {
    listEl.innerHTML = `
      <div class="py-8 px-4 text-center space-y-3">
        <p class="text-xs text-slate-400">${escapeHtml(t.pickerNoResults)}</p>
        <button type="button" id="btn-inline-remote-cmc"
          class="px-3.5 py-2 rounded-xl text-xs font-bold bg-sky-500/20 hover:bg-sky-500/30 text-sky-300 border border-sky-500/40">
          ${escapeHtml(t.pickerRemoteSearchBtn)}
        </button>
      </div>
    `;
    return;
  }

  listEl.innerHTML = combined
    .map((item) => {
      const isAdded = activeSet.has(item.id);
      const priceText =
        item.category === "fiat"
          ? `1 USD = ${item.spotDisplay} ${item.code}`
          : `$${item.spotDisplay}`;
      const chgBadge =
        item.change24hPct && item.change24hPct !== "0.00"
          ? `<span class="text-[10px] font-mono ${item.change24hPct.startsWith("-") ? "text-rose-400" : "text-emerald-400"}" dir="ltr">${escapeHtml(item.change24hPct)}%</span>`
          : "";
      const rankTag = item.cmcRank
        ? `<span class="text-[10px] font-mono text-slate-500" dir="ltr">#${Number(item.cmcRank)}</span>`
        : "";
      const btnCls = isAdded
        ? "bg-emerald-500/20 text-emerald-300 border-emerald-500/40 hover:bg-rose-500/20 hover:text-rose-300 hover:border-rose-500/40"
        : "bg-amber-400 hover:bg-amber-300 text-slate-950 border-amber-400";

      return `
        <div class="flex items-center justify-between gap-2.5 p-2.5 rounded-xl bg-slate-950/75 border border-slate-800/90 hover:border-slate-700 transition-colors">
          <div class="flex items-center gap-2.5 min-w-0">
            ${renderAssetIconBadge(item)}
            <div class="min-w-0">
              <div class="flex items-center gap-1.5">
                <span class="text-xs font-extrabold text-white font-mono" dir="ltr">${escapeHtml(item.code)}</span>
                ${rankTag}
                ${chgBadge}
              </div>
              <p class="text-[11px] text-slate-400 truncate">${escapeHtml(getLocalizedAssetName(item))}</p>
            </div>
          </div>
          <div class="flex items-center gap-2.5 shrink-0">
            <span class="hidden sm:inline text-xs font-mono text-slate-200 tabular-nums" dir="ltr">${escapeHtml(priceText)}</span>
            <button type="button" data-picker-toggle="${escapeHtml(item.id)}"
              class="px-3 py-1.5 rounded-lg text-xs font-extrabold border transition-colors ${btnCls}">
              ${escapeHtml(isAdded ? t.pickerAddedBtn : t.pickerAddBtn)}
            </button>
          </div>
        </div>
      `;
    })
    .join("");
}

function renderAuthSyncState() {
  const t = TRANSLATIONS[state.lang];
  const dot = document.getElementById("auth-sync-dot");
  const btnLabel = document.getElementById("auth-sync-btn-label");
  const noticeEl = document.getElementById("auth-config-notice");
  const emailForm = document.getElementById("auth-email-form");
  const otpForm = document.getElementById("auth-otp-form");
  const signedInBox = document.getElementById("auth-signed-in-box");
  const emailDisplay = document.getElementById("auth-user-email-display");

  const configured = isCloudSyncConfigured();
  const signedIn = Boolean(state.authSession?.user?.id);

  if (dot) {
    dot.className = signedIn
      ? "w-2 h-2 rounded-full bg-emerald-400 animate-pulse shrink-0"
      : "w-1.5 h-1.5 rounded-full bg-slate-400 shrink-0";
  }
  if (btnLabel) {
    btnLabel.textContent = signedIn ? t.btnEmailSynced : t.btnEmailSync;
  }
  if (noticeEl) {
    noticeEl.classList.toggle("hidden", configured);
  }
  if (signedIn) {
    emailForm?.classList.add("hidden");
    otpForm?.classList.add("hidden");
    signedInBox?.classList.remove("hidden");
    if (emailDisplay) emailDisplay.textContent = state.authSession.user.email || "";
  } else {
    signedInBox?.classList.add("hidden");
    const waitingOtp = Boolean(state.pendingOtpEmail);
    emailForm?.classList.toggle("hidden", waitingOtp || !configured);
    otpForm?.classList.toggle("hidden", !waitingOtp || !configured);
  }
}

function openAuthSyncModal() {
  const modal = document.getElementById("modal-auth-sync");
  if (!modal) return;
  renderAuthSyncState();
  modal.classList.remove("hidden");
  modal.classList.add("flex");
  triggerTelegramHaptic("light");
}

function closeAuthSyncModal() {
  const modal = document.getElementById("modal-auth-sync");
  if (!modal) return;
  modal.classList.add("hidden");
  modal.classList.remove("flex");
}

async function initCloudAndTelegramSync() {
  // 1. Check Telegram Mini App CloudStorage first
  const tgWs = await loadWorkspaceFromTelegramCloud();
  if (tgWs) {
    const mergedTg = mergeWorkspaces(state.workspace, tgWs);
    state.workspace = saveWorkspaceLocal(mergedTg);
  }

  // 2. Check Supabase Email session (Magic Link redirect or stored session)
  const session = await ensureFreshSession();
  if (session) {
    state.authSession = session;
    const cloudLayout = await fetchCloudLayout(session);
    if (cloudLayout) {
      const merged = mergeWorkspaces(state.workspace, cloudLayout);
      state.workspace = saveWorkspaceLocal(merged);
      await upsertCloudLayout(session, state.workspace);
    } else {
      await upsertCloudLayout(session, state.workspace);
    }
    rebuildCatalogFromState();
    recomputeAndRenderAll();
  }
  renderAuthSyncState();
}

function bindWorkspaceDragAndDrop() {
  const grid = document.getElementById("grid-workspace");
  if (!grid) return;

  let draggedId = null;

  // Desktop HTML5 Drag-and-Drop
  grid.addEventListener("dragstart", (e) => {
    if (!state.editMode) return;
    const card = e.target.closest("[data-ws-card]");
    if (!card) return;
    draggedId = card.getAttribute("data-ws-card");
    card.classList.add("rp-dragging");
    if (e.dataTransfer) {
      e.dataTransfer.effectAllowed = "move";
      e.dataTransfer.setData("text/plain", draggedId || "");
    }
  });

  grid.addEventListener("dragover", (e) => {
    if (!state.editMode || !draggedId) return;
    e.preventDefault();
    const targetCard = e.target.closest("[data-ws-card]");
    grid.querySelectorAll(".rp-drag-over").forEach((el) => el.classList.remove("rp-drag-over"));
    if (targetCard && targetCard.getAttribute("data-ws-card") !== draggedId) {
      targetCard.classList.add("rp-drag-over");
    }
  });

  grid.addEventListener("drop", (e) => {
    if (!state.editMode || !draggedId) return;
    e.preventDefault();
    const targetCard = e.target.closest("[data-ws-card]");
    const targetId = targetCard?.getAttribute("data-ws-card");
    grid.querySelectorAll(".rp-drag-over, .rp-dragging").forEach((el) => {
      el.classList.remove("rp-drag-over", "rp-dragging");
    });
    if (targetId && targetId !== draggedId) {
      commitWorkspaceChange(reorderAssetBefore(state.workspace, draggedId, targetId));
      triggerTelegramHaptic("light");
    }
    draggedId = null;
  });

  grid.addEventListener("dragend", () => {
    grid.querySelectorAll(".rp-drag-over, .rp-dragging").forEach((el) => {
      el.classList.remove("rp-drag-over", "rp-dragging");
    });
    draggedId = null;
  });

  // Mobile Touch Pointer Drag-and-Drop on `.rp-drag-handle`
  let touchDragId = null;
  grid.addEventListener("pointerdown", (e) => {
    if (!state.editMode || e.pointerType === "mouse") return;
    const handle = e.target.closest("[data-ws-drag-handle]");
    if (!handle) return;
    touchDragId = handle.getAttribute("data-ws-drag-handle");
    const card = handle.closest("[data-ws-card]");
    card?.classList.add("rp-dragging");
    e.preventDefault();
  });

  window.addEventListener("pointermove", (e) => {
    if (!touchDragId) return;
    const under = document.elementFromPoint(e.clientX, e.clientY)?.closest?.("[data-ws-card]");
    grid.querySelectorAll(".rp-drag-over").forEach((el) => el.classList.remove("rp-drag-over"));
    if (under && under.getAttribute("data-ws-card") !== touchDragId) {
      under.classList.add("rp-drag-over");
    }
  });

  window.addEventListener("pointerup", (e) => {
    if (!touchDragId) return;
    const under = document.elementFromPoint(e.clientX, e.clientY)?.closest?.("[data-ws-card]");
    const targetId = under?.getAttribute("data-ws-card");
    grid.querySelectorAll(".rp-drag-over, .rp-dragging").forEach((el) => {
      el.classList.remove("rp-drag-over", "rp-dragging");
    });
    if (targetId && targetId !== touchDragId) {
      commitWorkspaceChange(reorderAssetBefore(state.workspace, touchDragId, targetId));
      triggerTelegramHaptic("light");
    }
    touchDragId = null;
  });
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
  renderAuthSyncState();
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

  // Event delegation for workspace controls, asset selection, picker toggles & alerts
  document.addEventListener("click", (e) => {
    const pinBtn = e.target.closest("[data-ws-pin]");
    if (pinBtn) {
      const id = pinBtn.getAttribute("data-ws-pin");
      commitWorkspaceChange(pinAssetToTop(state.workspace, id));
      triggerTelegramHaptic("light");
      return;
    }

    const upBtn = e.target.closest("[data-ws-up]");
    if (upBtn) {
      const id = upBtn.getAttribute("data-ws-up");
      commitWorkspaceChange(moveAssetByDelta(state.workspace, id, -1));
      triggerTelegramHaptic("light");
      return;
    }

    const downBtn = e.target.closest("[data-ws-down]");
    if (downBtn) {
      const id = downBtn.getAttribute("data-ws-down");
      commitWorkspaceChange(moveAssetByDelta(state.workspace, id, 1));
      triggerTelegramHaptic("light");
      return;
    }

    const removeBtn = e.target.closest("[data-ws-remove]");
    if (removeBtn) {
      const id = removeBtn.getAttribute("data-ws-remove");
      commitWorkspaceChange(removeAssetFromWorkspace(state.workspace, id), {
        toastMsg: state.lang === "ar" ? "تمت إزالة البطاقة من واجهتك" : "Card removed from workspace",
      });
      triggerTelegramHaptic("medium");
      return;
    }

    const pickerToggle = e.target.closest("[data-picker-toggle]");
    if (pickerToggle) {
      const id = pickerToggle.getAttribute("data-picker-toggle");
      const activeSet = new Set(state.workspace?.ordered_ids || []);
      if (activeSet.has(id)) {
        commitWorkspaceChange(removeAssetFromWorkspace(state.workspace, id));
      } else {
        const localItem = state.catalog?.byId?.[id];
        const remoteItem = state.pickerRemoteResults.find((r) => r.id === id);
        const descriptor = remoteItem || {
          id,
          cmc_id: localItem?.cmcId ?? null,
          code: localItem?.code || id,
          symbol: localItem?.symbol || id,
          name: localItem?.nameEn || id,
          name_en: localItem?.nameEn || id,
          name_ar: localItem?.nameAr || id,
          price_usd: localItem?.category !== "fiat" ? localItem?.spotDisplay : null,
          units_per_usd: localItem?.category === "fiat" ? localItem?.spotDisplay : null,
          change_24h_pct: localItem?.change24hPct || "0.00",
          logo_url: localItem?.logoUrl || "",
          rank: localItem?.cmcRank ?? null,
          category: localItem?.category || "crypto",
        };
        commitWorkspaceChange(addAssetToWorkspace(state.workspace, descriptor, { pinTop: true }), {
          rebuildCatalog: true,
          toastMsg:
            state.lang === "ar"
              ? `تمت إضافة ${descriptor.code} إلى واجهتك`
              : `${descriptor.code} added to your workspace`,
        });
      }
      triggerTelegramHaptic("light");
      return;
    }

    if (e.target.closest("#btn-inline-remote-cmc")) {
      runRemoteCmcSearch(state.pickerQuery);
      return;
    }

    const assetTrigger = e.target.closest("[data-select-asset]");
    if (assetTrigger && !state.editMode) {
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

  // Personal Workspace Toolbar & Filter Pills
  document.getElementById("btn-toggle-edit-mode")?.addEventListener("click", () => {
    state.editMode = !state.editMode;
    renderConversionMatrix();
    triggerTelegramHaptic("medium");
  });

  const handleResetWorkspace = () => {
    commitWorkspaceChange(resetWorkspaceDefaults(), {
      rebuildCatalog: true,
      toastMsg:
        state.lang === "ar"
          ? "تمت استعادة الواجهة الافتراضية بنجاح"
          : "Default workspace layout restored",
    });
    state.workspaceFilter = "all";
    renderConversionMatrix();
  };
  document.getElementById("btn-reset-workspace")?.addEventListener("click", handleResetWorkspace);
  document.getElementById("btn-empty-reset")?.addEventListener("click", handleResetWorkspace);

  document.getElementById("btn-open-asset-picker")?.addEventListener("click", openAssetPickerModal);
  document.getElementById("btn-empty-add")?.addEventListener("click", openAssetPickerModal);
  document.getElementById("btn-close-picker-modal")?.addEventListener("click", closeAssetPickerModal);

  document.querySelectorAll(".ws-filter-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      state.workspaceFilter = btn.getAttribute("data-ws-filter") || "all";
      renderConversionMatrix();
    });
  });

  document.querySelectorAll(".picker-tab-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      state.pickerTab = btn.getAttribute("data-picker-tab") || "all";
      renderAssetPickerResults();
    });
  });

  let remoteSearchDebounce = null;
  document.getElementById("asset-search-input")?.addEventListener("input", (e) => {
    state.pickerQuery = e.target.value || "";
    state.pickerRemoteResults = [];
    renderAssetPickerResults();
    clearTimeout(remoteSearchDebounce);
    if (state.pickerQuery.trim().length >= 3 && (state.pickerTab === "all" || state.pickerTab === "crypto")) {
      remoteSearchDebounce = setTimeout(() => {
        const q = state.pickerQuery.trim().toLowerCase();
        const localHits = (state.catalog?.allSearchable || []).filter((item) =>
          `${item.code} ${item.nameEn || ""}`.toLowerCase().includes(q)
        );
        if (localHits.length < 2) {
          runRemoteCmcSearch(state.pickerQuery);
        }
      }, 450);
    }
  });

  document.getElementById("btn-cmc-remote-search")?.addEventListener("click", () => {
    runRemoteCmcSearch(state.pickerQuery);
  });

  // Supabase Email Sync Modal
  document.getElementById("btn-open-auth-sync")?.addEventListener("click", openAuthSyncModal);
  document.getElementById("btn-close-auth-modal")?.addEventListener("click", closeAuthSyncModal);

  document.getElementById("auth-email-form")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const emailInput = document.getElementById("auth-email-input");
    const email = (emailInput?.value || "").trim();
    const res = await sendEmailOtp(email);
    if (res.ok) {
      state.pendingOtpEmail = res.email;
      const hint = document.getElementById("auth-otp-sent-hint");
      if (hint) {
        hint.textContent =
          state.lang === "ar"
            ? `تم إرسال رمز التحقق ورابط الدخول إلى ${res.email}`
            : `Magic link & 6-digit code sent to ${res.email}`;
      }
      renderAuthSyncState();
    } else {
      showToast(
        state.lang === "ar"
          ? `تعذر الإرسال: ${res.error}`
          : `Could not send code: ${res.error}`
      );
    }
  });

  document.getElementById("btn-auth-back-email")?.addEventListener("click", () => {
    state.pendingOtpEmail = "";
    renderAuthSyncState();
  });

  document.getElementById("auth-otp-form")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const code = (document.getElementById("auth-otp-input")?.value || "").trim();
    const res = await verifyEmailOtp(state.pendingOtpEmail, code);
    if (res.ok && res.session) {
      state.authSession = res.session;
      state.pendingOtpEmail = "";
      const cloudLayout = await fetchCloudLayout(res.session);
      if (cloudLayout) {
        const merged = mergeWorkspaces(state.workspace, cloudLayout);
        state.workspace = saveWorkspaceLocal(merged);
      }
      await upsertCloudLayout(res.session, state.workspace);
      rebuildCatalogFromState();
      recomputeAndRenderAll();
      showToast(
        state.lang === "ar"
          ? "تمت مزامنة واجهتك الخاصة بنجاح!"
          : "Workspace synced across devices!"
      );
    } else {
      showToast(
        state.lang === "ar"
          ? "رمز التحقق غير صحيح أو منتهي الصلاحية"
          : "Invalid or expired verification code"
      );
    }
  });

  document.getElementById("btn-cloud-sync-now")?.addEventListener("click", async () => {
    if (!state.authSession) return;
    const res = await upsertCloudLayout(state.authSession, state.workspace);
    showToast(
      res.ok
        ? state.lang === "ar"
          ? "تم حفظ ومزامنة الترتيب في السحابة"
          : "Workspace synced to cloud"
        : state.lang === "ar"
        ? "تعذر الاتصال بالسحابة حالياً"
        : "Cloud sync temporarily unavailable"
    );
  });

  document.getElementById("btn-auth-signout")?.addEventListener("click", async () => {
    await signOutCloudSync();
    state.authSession = null;
    state.pendingOtpEmail = "";
    renderAuthSyncState();
    showToast(state.lang === "ar" ? "تم تسجيل الخروج (الحفظ المحلي مستمر)" : "Signed out (browser auto-save remains active)");
  });

  bindWorkspaceDragAndDrop();

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
    renderAlertAssetFlag();
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
      closeAssetPickerModal();
      closeAuthSyncModal();
    }
  });
  document.getElementById("modal-quote-card")?.addEventListener("click", (e) => {
    if (e.target.id === "modal-quote-card") closeQuoteCardModal();
  });
  document.getElementById("modal-vip-alerts")?.addEventListener("click", (e) => {
    if (e.target.id === "modal-vip-alerts") closeVipAlertsModal();
  });
  document.getElementById("modal-asset-picker")?.addEventListener("click", (e) => {
    if (e.target.id === "modal-asset-picker") closeAssetPickerModal();
  });
  document.getElementById("modal-auth-sync")?.addEventListener("click", (e) => {
    if (e.target.id === "modal-auth-sync") closeAuthSyncModal();
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
  await initCloudAndTelegramSync();
  setInterval(fetchLiveFeed, 30000);
});
