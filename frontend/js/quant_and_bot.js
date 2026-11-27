/**
 * RatePath (ريت باث) — Quant & Macro Signal Radar, Personal AI Tracker Bot Hub,
 * Global Stocks/Company News Search, and Geo-Filtered Trading Bots Affiliate Shelf.
 *
 * Legal & Ethical Notice: Informational data tools & statistical analytics only —
 * never financial advice or guaranteed price outcomes.
 */

import {
  AFFILIATES_ENABLED,
  FEATURE_FLAGS,
  MONETIZATION_CONFIG,
} from "../monetization.config.js";
import { selectGeoCompliantPartners } from "./affiliates.js";
import { isCloudSyncConfigured } from "./auth_sync.js";

const LOCAL_WATCHLIST_KEY = "ratepath_personal_bot_watchlist_v1";
const QUANT_CACHE_KEY = "ratepath_cached_quant_v1";
const LIVE_NEWS_CACHE_PREFIX = "ratepath_cached_live_news_v1_";

function readCachedJson(key) {
  try {
    const raw = localStorage.getItem(key);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

function writeCachedJson(key, payload) {
  try {
    localStorage.setItem(key, JSON.stringify({ cachedAt: Date.now(), payload }));
  } catch {
    // Ignore storage limits
  }
}

function esc(v) {
  return String(v ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function safeUrl(u) {
  const s = String(u || "").trim();
  return /^https?:\/\//i.test(s) ? s : "https://ratepathlive.com/";
}

function loadLocalWatchlist() {
  try {
    const raw = localStorage.getItem(LOCAL_WATCHLIST_KEY);
    const parsed = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

function saveLocalWatchlist(items) {
  try {
    localStorage.setItem(LOCAL_WATCHLIST_KEY, JSON.stringify(items));
  } catch {
    // Ignore storage quota errors
  }
}

export function createQuantAndBotController({
  getState,
  showToast,
  openAuthModal,
  jumpToNewsTab,
  syncBodyScrollLock,
  setSectionAvailability,
}) {
  const cachedQuantEntry = readCachedJson(QUANT_CACHE_KEY);
  const cachedNewsAr = readCachedJson(`${LIVE_NEWS_CACHE_PREFIX}ar`);
  const cachedNewsEn = readCachedJson(`${LIVE_NEWS_CACHE_PREFIX}en`);
  const localState = {
    quantData: cachedQuantEntry?.payload || null,
    quantCachedAt: cachedQuantEntry?.cachedAt || null,
    quantLoadedOnce: false,
    quantLoading: false,
    liveNewsByLang: {
      ar: cachedNewsAr?.payload || null,
      en: cachedNewsEn?.payload || null,
    },
    liveNewsLoadedOnce: false,
    liveNewsLoading: false,
    botProfile: null,
    watchlist: loadLocalWatchlist(),
    searchResults: [],
    searchLoading: false,
    selectedIntel: null,
    intelLoading: false,
    selectedPlan: "monthly_personal_bot",
    upgradeSelectedPlan: "monthly_personal_bot",
    dailyDigest: true,
    digestHourUtc: 7,
  };

  const getFlags = () => FEATURE_FLAGS || MONETIZATION_CONFIG?.FEATURE_FLAGS || {};

  // -------------------------------------------------------------------------
  // 0. Continuous Hourly Live Breaking News Bar (Top) + Full Details (Bottom)
  // -------------------------------------------------------------------------

  async function fetchLiveHourlyNews(forceLang = null) {
    const flags = getFlags();
    if (flags.showLiveNews === false && flags.showBreakingBar === false) {
      document.getElementById("top-breaking-news-bar")?.classList.add("hidden");
      setSectionAvailability?.("news", false);
      return;
    }
    const lang = forceLang || getState().lang || "ar";
    if (localState.liveNewsLoading) return;
    localState.liveNewsLoading = true;
    try {
      const resp = await fetch(`/api/live-news?lang=${encodeURIComponent(lang)}`, { cache: "default" });
      if (resp.ok) {
        const fresh = await resp.json();
        if (fresh && Array.isArray(fresh.items) && fresh.items.length > 0) {
          localState.liveNewsByLang[lang] = fresh;
          writeCachedJson(`${LIVE_NEWS_CACHE_PREFIX}${lang}`, fresh);
        }
      }
    } catch {
      // Resilient fallback: retain cached snapshot cleanly
    } finally {
      localState.liveNewsLoading = false;
      localState.liveNewsLoadedOnce = true;
      renderLiveBreakingNews();
    }
  }

  function renderLiveBreakingNews() {
    const flags = getFlags();
    const barEl = document.getElementById("top-breaking-news-bar");
    const strip = document.getElementById("top-breaking-headlines-strip");
    const listEl = document.getElementById("live-hourly-news-list");
    const fallbackNewsEl = document.getElementById("news-feed-list");
    const usdtPill = document.getElementById("top-usdt-flow-pill");
    const appState = getState();
    const isAr = appState.lang === "ar";
    const data = localState.liveNewsByLang[appState.lang];

    if (flags.showBreakingBar === false) {
      barEl?.classList.add("hidden");
    }

    const hasLiveItems = Boolean(data && Array.isArray(data.items) && data.items.length > 0);
    if (!hasLiveItems) {
      if (strip && localState.liveNewsLoading && flags.showBreakingBar !== false) {
        strip.innerHTML = `<span class="text-xs text-slate-400">${
          isAr ? "جارٍ جلب أحدث عناوين الاقتصاد والعملات والشركات..." : "Loading live hourly market headlines..."
        }</span>`;
      } else if (localState.liveNewsLoadedOnce && flags.autoHideEmptySections !== false) {
        barEl?.classList.add("hidden");
        const hasFallbackMacro = Boolean(fallbackNewsEl && fallbackNewsEl.children.length > 0);
        if (!hasFallbackMacro || flags.showLiveNews === false) {
          setSectionAvailability?.("news", false);
        }
      }
      return;
    }

    if (flags.showBreakingBar !== false) {
      barEl?.classList.remove("hidden");
    }
    setSectionAvailability?.("news", flags.showLiveNews !== false);

    if (usdtPill && data.usdt_24h_delta_m != null) {
      const d = Number(data.usdt_24h_delta_m);
      const isUp = d >= 0;
      const sign = isUp ? "+" : "";
      usdtPill.innerHTML = `USDT 24h <span class="${isUp ? "text-emerald-400" : "text-rose-500"}">${isUp ? "▲" : "▼"} ${sign}${esc(d)}M</span>`;
      usdtPill.classList.remove("hidden");
    }

    const catMeta = (cat) => {
      if (cat === "whale") return { icon: "🐋", label: isAr ? "حيتان وUSDT" : "Whales/USDT", cls: "bg-sky-500/15 text-sky-300 border-sky-500/35" };
      if (cat === "corporate") return { icon: "🏢", label: isAr ? "شركات" : "Corporate", cls: "bg-amber-500/15 text-amber-300 border-amber-500/35" };
      if (cat === "crypto") return { icon: "🪙", label: isAr ? "كريبتو" : "Crypto", cls: "bg-emerald-500/15 text-emerald-300 border-emerald-500/35" };
      return { icon: "🌍", label: isAr ? "اقتصاد دولي" : "Global Macro", cls: "bg-slate-800 text-slate-200 border-slate-700" };
    };

    const arrowHtml = (dir) => {
      if (dir === "up") return `<span class="text-emerald-400 font-extrabold" aria-hidden="true">▲</span>`;
      if (dir === "down") return `<span class="text-rose-500 font-extrabold" aria-hidden="true">▼</span>`;
      return "";
    };

    // 1. Top-of-Page Breaking Headlines Bar (crisp titles that pull visitors into full details below)
    if (strip) {
      strip.innerHTML = data.items
        .slice(0, 7)
        .map((item) => {
          const cm = catMeta(item.category);
          return `
            <button type="button" data-jump-news-id="${esc(item.id)}"
              class="shrink-0 max-w-[80vw] sm:max-w-[340px] md:max-w-[380px] inline-flex items-center gap-1.5 px-2.5 sm:px-3 py-1.5 rounded-xl bg-slate-950/90 hover:bg-slate-800 border border-slate-800 hover:border-amber-400/50 text-start transition-colors cursor-pointer">
              <span class="shrink-0">${cm.icon}</span>
              ${arrowHtml(item.direction)}
              <span class="text-xs font-bold text-slate-100 truncate">${esc(item.title)}</span>
            </button>
          `;
        })
        .join("");
    }

    // 2. Full Hourly News Details Cards below inside `#news-panel`
    if (listEl) {
      listEl.innerHTML = data.items
        .map((item) => {
          const cm = catMeta(item.category);
          const pubShort = String(item.published_at || "").slice(0, 16).replace("T", " ") + " UTC";
          const hasSeparateSummary = item.summary && item.summary.trim() !== item.title.trim();
          return `
            <article id="news-card-${esc(item.id)}" class="p-3.5 rounded-xl bg-slate-950/80 border border-slate-800/90 hover:border-amber-400/35 transition-all space-y-2">
              <div class="flex flex-wrap items-center justify-between gap-2 text-[11px]">
                <div class="flex items-center gap-1.5 flex-wrap">
                  <span class="inline-flex items-center gap-1 px-2 py-0.5 rounded-md text-[10px] font-bold border ${cm.cls}">
                    ${cm.icon} ${esc(cm.label)}
                  </span>
                  ${arrowHtml(item.direction)}
                  <a href="${safeUrl(item.url)}" target="_blank" rel="noopener noreferrer"
                    class="font-bold text-amber-300 hover:text-amber-200 underline decoration-amber-500/40">
                    ${esc(item.source)} ↗
                  </a>
                </div>
                <span class="text-[10px] text-slate-400 font-mono" dir="ltr">${esc(pubShort)}</span>
              </div>
              <h3 class="text-xs sm:text-sm font-extrabold text-white leading-snug">
                ${esc(item.title)}
              </h3>
              ${
                hasSeparateSummary
                  ? `<p class="text-xs text-slate-300 leading-relaxed">${esc(item.summary)}</p>`
                  : ""
              }
            </article>
          `;
        })
        .join("");
    }
  }

  // -------------------------------------------------------------------------
  // 1. Live Quant & Macro Probabilistic Radar + Real 30-Day Track Record
  // -------------------------------------------------------------------------

  async function fetchQuantSignals() {
    const flags = getFlags();
    if (flags.showQuantRadar === false) {
      setSectionAvailability?.("quant", false);
      return;
    }
    if (localState.quantLoading) return;
    localState.quantLoading = true;
    renderQuantRadar();
    try {
      const resp = await fetch("/api/strategist-signals", { cache: "default" });
      if (resp.ok) {
        const fresh = await resp.json();
        if (fresh && Array.isArray(fresh.signals) && fresh.signals.length > 0) {
          localState.quantData = fresh;
          localState.quantCachedAt = Date.now();
          writeCachedJson(QUANT_CACHE_KEY, fresh);
        }
      }
    } catch {
      // Graceful fallback to last cached snapshot
    } finally {
      localState.quantLoading = false;
      localState.quantLoadedOnce = true;
      renderQuantRadar();
    }
  }

  function renderQuantRadar() {
    const flags = getFlags();
    const body = document.getElementById("quant-radar-body");
    const trackBadge = document.getElementById("quant-track-record-badge");
    const macroBar = document.getElementById("quant-macro-bar");
    if (!body) return;

    if (flags.showQuantRadar === false) {
      setSectionAvailability?.("quant", false);
      return;
    }

    const appState = getState();
    const isAr = appState.lang === "ar";
    const data = localState.quantData;
    const rows = Array.isArray(data?.signals) ? data.signals : [];

    if (rows.length === 0) {
      if (localState.quantLoadedOnce && flags.autoHideEmptySections !== false) {
        // Auto-hide empty section cleanly instead of showing blank cards or errors
        setSectionAvailability?.("quant", false);
        return;
      }
      if (trackBadge) trackBadge.classList.add("hidden");
      body.innerHTML = `<div class="p-3 rounded-xl bg-slate-950/60 border border-slate-800 text-xs text-slate-400 text-center">${
        isAr
          ? "جارٍ حساب مؤشرات EMA/RSI والتمويل ومؤشر الدولار..."
          : "Computing live EMA/RSI, derivatives funding & DXY signals..."
      }</div>`;
      return;
    }

    setSectionAvailability?.("quant", true);

    // Honest 30-Day Real Track Record Badge — auto-hidden if 0 graded entries exist
    if (trackBadge) {
      const tr = data.track_record_30d || {};
      const graded = Number(tr.graded_count_30d || 0);
      if (graded > 0 && tr.avg_accuracy_pct != null) {
        trackBadge.textContent = isAr
          ? `سجل الدقة (30 يوم): ${tr.avg_accuracy_pct}% (${graded} تقييم فعلي)`
          : `30d Live Accuracy: ${tr.avg_accuracy_pct}% (${graded} graded)`;
        trackBadge.className =
          "inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-[10px] font-mono font-bold bg-emerald-500/15 text-emerald-300 border border-emerald-500/35";
      } else {
        trackBadge.classList.add("hidden");
      }
    }

    // Macro Context Strip (DXY + US10Y)
    if (macroBar) {
      const mc = data.macro_context || {};
      const pills = [];
      if (mc.dxy_price != null) {
        const up = Number(mc.dxy_24h_pct || 0) >= 0;
        pills.push(
          `<span class="px-2 py-0.5 rounded-md bg-slate-950 border border-slate-800 text-[10px] font-mono text-slate-300" dir="ltr">DXY ${esc(mc.dxy_price)} <strong class="${up ? "text-emerald-400" : "text-rose-400"}">${up ? "▲" : "▼"}${esc(Math.abs(mc.dxy_24h_pct || 0))}%</strong></span>`
        );
      }
      if (mc.us10y_yield != null) {
        const up = Number(mc.us10y_24h_pct || 0) >= 0;
        pills.push(
          `<span class="px-2 py-0.5 rounded-md bg-slate-950 border border-slate-800 text-[10px] font-mono text-slate-300" dir="ltr">US10Y ${esc(mc.us10y_yield)}% <strong class="${up ? "text-amber-300" : "text-sky-300"}">${up ? "▲" : "▼"}${esc(Math.abs(mc.us10y_24h_pct || 0))}%</strong></span>`
        );
      }
      macroBar.innerHTML = pills.join("");
    }

    const ASSET_LABELS = {
      XAU: isAr ? "الذهب XAU" : "Gold XAU",
      XAG: isAr ? "الفضة XAG" : "Silver XAG",
      EUR: isAr ? "اليورو EUR" : "Euro EUR",
      GBP: isAr ? "الإسترليني GBP" : "Pound GBP",
      BTC: isAr ? "بيتكوين BTC" : "Bitcoin BTC",
      ETH: isAr ? "إيثيريوم ETH" : "Ethereum ETH",
      SOL: isAr ? "سولانا SOL" : "Solana SOL",
    };

    body.innerHTML = `
      <div class="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-2 gap-2">
        ${rows
          .map((sig) => {
            const isUp = sig.bias === "bullish";
            const isDown = sig.bias === "bearish";
            const badgeCls = isUp
              ? "bg-emerald-500/15 text-emerald-300 border-emerald-500/35"
              : isDown
              ? "bg-rose-500/15 text-rose-300 border-rose-500/35"
              : "bg-slate-800/90 text-slate-300 border-slate-700";
            const badgeLabel = isUp
              ? isAr
                ? `<span class="text-emerald-400">▲</span> صعود مرجّح`
                : `<span class="text-emerald-400">▲</span> Bullish Lean`
              : isDown
              ? isAr
                ? `<span class="text-rose-500">▼</span> هبوط مرجّح`
                : `<span class="text-rose-500">▼</span> Bearish Lean`
              : isAr
              ? "➖ مستقر (انتظار)"
              : "➖ Neutral (Wait)";
            const scoreNum = Number(sig.score || 0);
            const scoreText = `${scoreNum > 0 ? "+" : ""}${scoreNum}`;
            const metaSpans = [
              sig.rsi14 != null ? `<span class="whitespace-nowrap">RSI ${esc(sig.rsi14)}</span>` : "",
              sig.funding_8h_pct != null ? `<span class="whitespace-nowrap">Fund ${esc(sig.funding_8h_pct)}%</span>` : "",
              `<span class="whitespace-nowrap">Score ${esc(scoreText)}</span>`,
            ]
              .filter(Boolean)
              .join(`<span class="text-slate-600" aria-hidden="true">·</span>`);
            const reason = isAr ? sig.reason_ar : sig.reason_en;
            return `
              <div class="p-2.5 rounded-xl bg-slate-950/75 border border-slate-800/90 space-y-1.5 min-w-0">
                <div class="flex items-center justify-between gap-2">
                  <span class="text-xs font-extrabold text-white truncate">${esc(ASSET_LABELS[sig.asset] || sig.asset)}</span>
                  <span class="px-2 py-0.5 rounded-md text-[10px] font-bold border shrink-0 ${badgeCls}">${badgeLabel}</span>
                </div>
                <div class="flex flex-wrap items-center justify-between gap-x-2.5 gap-y-0.5 text-[10px] font-mono text-slate-400" dir="ltr">
                  <span class="font-bold text-slate-200 shrink-0">${sig.price != null ? esc(sig.price) : "—"}</span>
                  <span class="inline-flex flex-wrap items-center gap-x-1.5 gap-y-0.5">${metaSpans}</span>
                </div>
                <p class="text-[11px] text-slate-300 leading-snug">${esc(reason)}</p>
              </div>
            `;
          })
          .join("")}
      </div>
    `;
  }

  // -------------------------------------------------------------------------
  // 2. Personal AI Tracker Bot Hub (Currencies + Gold + Crypto + Global Stocks & News)
  // -------------------------------------------------------------------------

  async function syncPersonalBotProfile() {
    const appState = getState();
    const token = appState.authSession?.access_token;
    if (!token) {
      localState.botProfile = null;
      renderPersonalBotHub();
      return;
    }
    try {
      const resp = await fetch("/api/personal-bot", {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (resp.ok) {
        const data = await resp.json();
        if (data?.ok && data.profile) {
          localState.botProfile = data.profile;
          if (Array.isArray(data.profile.watchlist) && data.profile.watchlist.length > 0) {
            localState.watchlist = data.profile.watchlist;
            saveLocalWatchlist(localState.watchlist);
          } else if (localState.watchlist.length > 0) {
            // Auto-push locally staged watchlist to user's newly signed-in profile
            await saveWatchlistToCloud();
          }
          localState.dailyDigest = data.profile.daily_digest_enabled !== false;
          localState.digestHourUtc = Number(data.profile.digest_hour_utc ?? 7);
        }
      }
    } catch {
      // Keep local state resilient
    }
    renderPersonalBotHub();
  }

  async function saveWatchlistToCloud({ unlinkTelegram = false } = {}) {
    saveLocalWatchlist(localState.watchlist);
    const appState = getState();
    const token = appState.authSession?.access_token;
    if (!token) {
      renderPersonalBotHub();
      return { ok: true, localOnly: true };
    }
    try {
      const resp = await fetch("/api/personal-bot", {
        method: "POST",
        headers: {
          Authorization: `Bearer ${token}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          lang: appState.lang,
          watchlist: localState.watchlist,
          daily_digest_enabled: localState.dailyDigest,
          digest_hour_utc: localState.digestHourUtc,
          unlink_telegram: unlinkTelegram,
        }),
      });
      const data = await resp.json().catch(() => ({}));
      if (resp.ok && data?.ok && data.profile) {
        localState.botProfile = data.profile;
        localState.watchlist = data.profile.watchlist || [];
        saveLocalWatchlist(localState.watchlist);
        if (data.truncated_to_plan_limit) {
          showToast(
            appState.lang === "ar"
              ? `تم حفظ الحد الأقصى لباقتك (${data.profile.limits?.maxAssets || 3} أصول). يمكنك الترقية لمتابعة حتى 50 أصلاً.`
              : `Saved up to your plan limit (${data.profile.limits?.maxAssets || 3} assets). Upgrade for up to 50.`
          );
        }
        renderPersonalBotHub();
        return { ok: true };
      }
    } catch {
      // Keep local save
    }
    renderPersonalBotHub();
    return { ok: false };
  }

  async function searchTrackerAssets(query) {
    const q = String(query || "").trim();
    if (q.length < 2) {
      localState.searchResults = [];
      renderPersonalBotSearchResults();
      return;
    }
    localState.searchLoading = true;
    renderPersonalBotSearchResults();
    try {
      const [stockRes, fxCryptoRes] = await Promise.all([
        fetch(`/api/asset-intel?q=${encodeURIComponent(q)}`).then((r) => (r.ok ? r.json() : null)).catch(() => null),
        fetch(`/api/cmc-search?q=${encodeURIComponent(q)}`).then((r) => (r.ok ? r.json() : null)).catch(() => null),
      ]);
      const merged = [];
      const seen = new Set();
      for (const s of stockRes?.results || []) {
        const key = `stock:${s.symbol}`;
        if (seen.has(key)) continue;
        seen.add(key);
        merged.push({
          symbol: s.symbol,
          category: "stock",
          name_en: s.name_en || s.symbol,
          name_ar: s.name_ar || s.name_en || s.symbol,
          price: s.price ? `${s.price} ${s.currency || "USD"}` : "Live",
          raw_price: s.price || "",
          change_24h_pct: s.change_24h_pct || "0.00",
          exchange: s.exchange || "Stock",
        });
      }
      for (const c of fxCryptoRes?.results || []) {
        const sym = String(c.symbol || c.code || "").toUpperCase();
        const cat = c.category === "fiat" ? "fiat" : "crypto";
        const key = `${cat}:${sym}`;
        if (!sym || seen.has(key)) continue;
        seen.add(key);
        merged.push({
          symbol: sym,
          category: cat,
          name_en: c.name_en || c.name || sym,
          name_ar: c.name_ar || c.name || sym,
          price: cat === "fiat" ? `${c.units_per_usd} / USD` : `$${c.price_usd}`,
          raw_price: cat === "fiat" ? c.units_per_usd : c.price_usd,
          change_24h_pct: c.change_24h_pct || "0.00",
          exchange: cat === "fiat" ? "FX" : "Crypto",
        });
      }
      localState.searchResults = merged.slice(0, 8);
    } catch {
      localState.searchResults = [];
    } finally {
      localState.searchLoading = false;
      renderPersonalBotSearchResults();
    }
  }

  async function inspectAssetIntel(item) {
    localState.intelLoading = true;
    localState.selectedIntel = { symbol: item.symbol, name: item.name_ar || item.name_en || item.symbol, headlines: [] };
    renderIntelPreview();
    try {
      const resp = await fetch(
        `/api/asset-intel?symbol=${encodeURIComponent(item.symbol)}&category=${encodeURIComponent(item.category || "stock")}&name=${encodeURIComponent(item.name_en || item.symbol)}`
      );
      if (resp.ok) {
        const data = await resp.json();
        localState.selectedIntel = {
          symbol: item.symbol,
          name_en: item.name_en || item.symbol,
          name_ar: item.name_ar || item.name_en || item.symbol,
          quote: data.quote || null,
          headlines: Array.isArray(data.headlines) ? data.headlines : [],
        };
      }
    } catch {
      // Keep empty headlines
    } finally {
      localState.intelLoading = false;
      renderIntelPreview();
    }
  }

  function renderPersonalBotSearchResults() {
    const box = document.getElementById("pbot-search-results");
    if (!box) return;
    const isAr = getState().lang === "ar";
    if (localState.searchLoading) {
      box.classList.remove("hidden");
      box.innerHTML = `<div class="p-2.5 text-xs text-slate-400 text-center">${
        isAr ? "جارٍ البحث المباشر عن الأسهم والعملات وأخبار الشركات..." : "Searching global stocks, currencies & crypto live..."
      }</div>`;
      return;
    }
    if (!localState.searchResults.length) {
      box.classList.add("hidden");
      box.innerHTML = "";
      return;
    }
    box.classList.remove("hidden");
    const watchedSyms = new Set(localState.watchlist.map((w) => w.symbol));
    box.innerHTML = localState.searchResults
      .map((item, idx) => {
        const added = watchedSyms.has(item.symbol);
        const catTag =
          item.category === "stock"
            ? isAr
              ? "سهم/شركة"
              : "Stock"
            : item.category === "fiat"
            ? isAr
              ? "عملة"
              : "FX"
            : isAr
            ? "كريبتو"
            : "Crypto";
        const chg = String(item.change_24h_pct || "0.00");
        const chgBadge =
          chg !== "0.00"
            ? `<span class="text-[10px] font-mono font-bold ${chg.startsWith("-") ? "text-rose-500" : "text-emerald-400"}" dir="ltr">${chg.startsWith("-") ? "▼" : "▲"} ${esc(chg)}%</span>`
            : "";
        return `
          <div class="flex items-center justify-between gap-2 p-2 rounded-lg bg-slate-950/85 border border-slate-800 hover:border-amber-400/40">
            <div class="min-w-0">
              <div class="flex items-center gap-1.5 flex-wrap">
                <span class="text-xs font-extrabold font-mono text-amber-300" dir="ltr">${esc(item.symbol)}</span>
                <span class="px-1.5 py-0.5 rounded text-[9px] font-bold bg-slate-800 text-slate-300">${esc(catTag)}</span>
                <span class="text-xs font-semibold text-white truncate">${esc(isAr ? item.name_ar : item.name_en)}</span>
              </div>
              <div class="flex items-center gap-2 text-[10px] font-mono text-slate-400 mt-0.5" dir="ltr">
                <span>${esc(item.price)}</span>
                ${chgBadge}
              </div>
            </div>
            <div class="flex items-center gap-1.5 shrink-0">
              <button type="button" data-pbot-news-idx="${idx}"
                class="px-2 py-1 rounded-lg text-[10px] font-bold bg-slate-800 hover:bg-slate-700 text-sky-300 border border-sky-500/30">
                ${isAr ? "📰 أخبار" : "📰 News"}
              </button>
              <button type="button" data-pbot-add-idx="${idx}" ${added ? "disabled" : ""}
                class="px-2.5 py-1 rounded-lg text-[11px] font-extrabold ${
                  added
                    ? "bg-emerald-500/20 text-emerald-300 border border-emerald-500/35"
                    : "bg-amber-400 hover:bg-amber-300 text-slate-950"
                }">
                ${added ? (isAr ? "✓ مضاف" : "✓ Added") : isAr ? "+ متابعة" : "+ Track"}
              </button>
            </div>
          </div>
        `;
      })
      .join("");
  }

  function renderIntelPreview() {
    const box = document.getElementById("pbot-intel-preview");
    if (!box) return;
    const isAr = getState().lang === "ar";
    if (!localState.selectedIntel) {
      box.classList.add("hidden");
      box.innerHTML = "";
      return;
    }
    box.classList.remove("hidden");
    const intel = localState.selectedIntel;
    if (localState.intelLoading) {
      box.innerHTML = `<div class="p-3 rounded-xl bg-slate-950 border border-sky-500/30 text-xs text-slate-300">${
        isAr ? `جارٍ جلب أحدث أخبار ${esc(intel.symbol)}...` : `Fetching latest headlines for ${esc(intel.symbol)}...`
      }</div>`;
      return;
    }
    const quoteLine = intel.quote?.price
      ? `<span class="font-mono text-amber-300 ms-2" dir="ltr">${esc(intel.quote.price)} ${esc(intel.quote.currency || "USD")} (${esc(intel.quote.change_24h_pct)}%)</span>`
      : "";
    const newsHtml =
      intel.headlines?.length > 0
        ? intel.headlines
            .map(
              (h) => `
                <a href="${safeUrl(h.url)}" target="_blank" rel="noopener noreferrer"
                  class="block p-2 rounded-lg bg-slate-900/90 hover:bg-slate-800 border border-slate-800 text-[11px] text-slate-200 leading-snug transition-colors">
                  <span class="font-semibold">${esc(h.title)}</span>
                  <span class="block text-[10px] text-sky-300 mt-0.5">${esc(h.source)} ↗</span>
                </a>
              `
            )
            .join("")
        : `<p class="text-[11px] text-slate-400">${
            isAr ? "لا توجد أخبار جديدة الآن لهذا الرمز." : "No recent headlines found for this symbol."
          }</p>`;

    box.innerHTML = `
      <div class="p-3 rounded-xl bg-slate-950/90 border border-sky-500/35 space-y-2">
        <div class="flex items-center justify-between gap-2">
          <div class="text-xs font-extrabold text-white">
            📰 ${esc(isAr ? intel.name_ar : intel.name_en)} (<span class="font-mono text-sky-300">${esc(intel.symbol)}</span>)${quoteLine}
          </div>
          <button type="button" id="btn-close-intel-preview" class="text-[11px] text-slate-400 hover:text-white px-1.5">✕</button>
        </div>
        <div class="space-y-1.5">${newsHtml}</div>
      </div>
    `;
  }

  function renderPersonalBotHub() {
    const flags = getFlags();
    const hubEl = document.getElementById("personal-bot-hub");
    if (hubEl) {
      hubEl.classList.toggle("hidden", flags.showPersonalBot === false);
    }
    if (flags.showPersonalBot === false) return;

    const appState = getState();
    const isAr = appState.lang === "ar";
    const listEl = document.getElementById("pbot-watchlist-container");
    const statusBox = document.getElementById("pbot-account-status");
    const linkBtn = document.getElementById("btn-pbot-link-telegram");
    const signInBtn = document.getElementById("btn-pbot-signin");
    const tierBadge = document.getElementById("pbot-tier-badge");

    const prof = localState.botProfile;
    const cloudConfigured = isCloudSyncConfigured();
    const isSignedIn = Boolean(appState.authSession?.access_token);
    const tier = prof?.plan_tier || "free";
    const maxAssets = prof?.limits?.maxAssets || 3;

    if (tierBadge) {
      const label =
        tier === "monthly_personal_bot"
          ? isAr
            ? `باقة البوت الشخصي Pro (${localState.watchlist.length}/${maxAssets})`
            : `Personal Bot Pro (${localState.watchlist.length}/${maxAssets})`
          : tier === "monthly_business_vip"
          ? isAr
            ? `باقة VIP (${localState.watchlist.length}/${maxAssets})`
            : `Business VIP (${localState.watchlist.length}/${maxAssets})`
          : isAr
          ? `مجاني (${localState.watchlist.length}/${maxAssets} أصول)`
          : `Free (${localState.watchlist.length}/${maxAssets} assets)`;
      tierBadge.textContent = label;
    }

    if (statusBox) {
      if (!cloudConfigured) {
        statusBox.innerHTML = isAr
          ? `⚡ <strong>قائمة متابعتك محفوظة تلقائياً على جهازك!</strong> فعّل باقة <strong>VIP (10$)</strong> أو <strong>البوت الشخصي Pro (19$)</strong> لتنبيهات الاختراق الفورية عبر <code>@RatePathTerminalBot</code>.`
          : `⚡ <strong>Your watchlist auto-saves on this device!</strong> Upgrade to <strong>Business VIP ($10/mo)</strong> or <strong>Personal Bot Pro ($19/mo)</strong> for instant Telegram breakout alerts via <code>@RatePathTerminalBot</code>.`;
      } else if (!isSignedIn) {
        statusBox.innerHTML = isAr
          ? `🔒 <strong>سجّل بالإيميل (مجاني)</strong> لحفظ قائمتك وربطها ببوت التلغرام الخاص بك لتصلك الإشعارات الفورية.`
          : `🔒 <strong>Sign in by Email (Free)</strong> to save your watchlist and pair it with your private Telegram Bot alerts.`;
      } else if (prof?.telegram_linked) {
        statusBox.innerHTML = isAr
          ? `✅ <strong>تلغرام مربوط ونشط!</strong> ستصلك تنبيهات الأسعار وأخبار الشركات مباشرة على حسابك الخاص في <code>@RatePathTerminalBot</code>.`
          : `✅ <strong>Telegram Bot Paired &amp; Active!</strong> Price alerts and custom company news will arrive directly in your <code>@RatePathTerminalBot</code> chat.`;
      } else {
        statusBox.innerHTML = isAr
          ? `🔗 حسابك (<code>${esc(appState.authSession.user.email)}</code>) جاهز — اضغط <strong>ربط تلغرام</strong> لتفعيل الإشعارات الخاصة فوراً.`
          : `🔗 Account (<code>${esc(appState.authSession.user.email)}</code>) ready — tap <strong>Link Telegram</strong> to activate private alerts.`;
      }
    }

    if (signInBtn) {
      signInBtn.classList.toggle("hidden", !cloudConfigured || isSignedIn);
    }
    if (linkBtn) {
      linkBtn.classList.toggle("hidden", !cloudConfigured || !isSignedIn);
      if (cloudConfigured && isSignedIn && prof?.telegram_link_url) {
        linkBtn.setAttribute("href", safeUrl(prof.telegram_link_url));
        linkBtn.textContent = prof.telegram_linked
          ? isAr
            ? "🤖 فتح البوت الشخصي على تلغرام ↗"
            : "🤖 Open My Telegram Bot ↗"
          : isAr
          ? "🔗 ربط حسابي ببوت التلغرام بضغطة زر ↗"
          : "🔗 Link My Telegram Bot (1-Click) ↗";
      }
    }

    if (listEl) {
      if (!localState.watchlist.length) {
        listEl.innerHTML = `
          <div class="p-3 rounded-xl bg-slate-950/60 border border-dashed border-slate-800 text-xs text-slate-400 text-center">
            ${
              isAr
                ? "ابحث أعلاه عن أي عملة (مثل RUB أو BTC) أو سهم شركة (مثل AAPL أو TSLA أو أرامكو) واضغط «+ متابعة» لبناء بوتك الشخصي."
                : "Search above for any currency (RUB, EUR), crypto (BTC, PEPE), or global stock (AAPL, NVDA, TSLA) and tap «+ Track»."
            }
          </div>
        `;
      } else {
        listEl.innerHTML = localState.watchlist
          .map((item, idx) => {
            const catIcon =
              item.category === "stock"
                ? "📈"
                : item.category === "fiat"
                ? "💱"
                : item.category === "metal"
                ? "🥇"
                : "🪙";
            return `
              <div class="p-2.5 rounded-xl bg-slate-950/80 border border-slate-800 space-y-2">
                <div class="flex items-center justify-between gap-2">
                  <div class="min-w-0 flex items-center gap-1.5">
                    <span>${catIcon}</span>
                    <span class="text-xs font-extrabold font-mono text-amber-300" dir="ltr">${esc(item.symbol)}</span>
                    <span class="text-xs font-semibold text-white truncate">${esc(isAr ? item.name_ar : item.name_en)}</span>
                  </div>
                  <div class="flex items-center gap-1 shrink-0">
                    <button type="button" data-pbot-instant-alert="${esc(item.symbol)}"
                      class="px-2 py-0.5 rounded-md text-[10px] font-bold bg-amber-400/15 hover:bg-amber-400/25 text-amber-300 border border-amber-400/35">
                      ${isAr ? "⚡ تنبيه اختراق" : "⚡ Breakout Alert"}
                    </button>
                    <button type="button" data-pbot-watch-news="${idx}"
                      class="px-2 py-0.5 rounded-md text-[10px] font-bold bg-slate-800 hover:bg-slate-700 text-sky-300">
                      ${isAr ? "📰 أخبار" : "📰 News"}
                    </button>
                    <button type="button" data-pbot-remove="${idx}"
                      class="px-2 py-0.5 rounded-md text-[10px] font-bold bg-rose-500/15 hover:bg-rose-500/25 text-rose-300">
                      ✕
                    </button>
                  </div>
                </div>
                <div class="grid grid-cols-2 gap-2">
                  <input type="text" inputmode="decimal" dir="ltr" data-pbot-above="${idx}"
                    value="${esc(item.alert_above || "")}"
                    placeholder="${isAr ? "▲ تنبيه صعود ≥" : "▲ Alert Above ≥"}"
                    class="h-8 px-2.5 rounded-lg bg-slate-900 border border-slate-700 text-[11px] font-mono text-emerald-300 placeholder:text-slate-500" />
                  <input type="text" inputmode="decimal" dir="ltr" data-pbot-below="${idx}"
                    value="${esc(item.alert_below || "")}"
                    placeholder="${isAr ? "▼ تنبيه هبوط ≤" : "▼ Alert Below ≤"}"
                    class="h-8 px-2.5 rounded-lg bg-slate-900 border border-slate-700 text-[11px] font-mono text-rose-300 placeholder:text-slate-500" />
                </div>
              </div>
            `;
          })
          .join("");
      }
    }

    renderPersonalBotSearchResults();
    renderIntelPreview();
  }

  // -------------------------------------------------------------------------
  // 3. Geo-Filtered Automated Trading Bots & Smart Alerts Affiliate Shelf
  // -------------------------------------------------------------------------

  function renderBotsAffiliateShelf() {
    const flags = getFlags();
    const shelf = document.getElementById("bots-affiliate-shelf");
    const grid = document.getElementById("bots-affiliate-grid");
    if (!shelf || !grid) return;

    if (flags.showPartnersShelf === false) {
      shelf.classList.add("hidden");
      return;
    }

    const appState = getState();
    const isAr = appState.lang === "ar";
    const affEnabled = Boolean(
      AFFILIATES_ENABLED && MONETIZATION_CONFIG?.AFFILIATES_ENABLED !== false
    );
    const partners = selectGeoCompliantPartners({
      countryCode: appState.visitorCountry,
      category: "bots",
      affiliatesConfig: MONETIZATION_CONFIG?.affiliates || {},
      affiliatesEnabled: affEnabled,
      maxCount: 6,
    });

    if (partners.length === 0) {
      shelf.classList.add("hidden");
      return;
    }
    shelf.classList.remove("hidden");

    grid.innerHTML = partners
      .map((p) => {
        const title = isAr ? p.botCtaAr || p.ctaAr : p.botCtaEn || p.ctaEn;
        const desc = isAr ? p.botBadgeAr || p.badgeAr : p.botBadgeEn || p.badgeEn;
        return `
          <a href="${safeUrl(p.url)}" target="_blank" rel="noopener noreferrer sponsored"
            class="flex flex-col justify-between gap-2 p-3 rounded-xl bg-slate-950/80 hover:bg-slate-900 border border-slate-800 hover:border-amber-400/50 transition-colors">
            <div class="space-y-1">
              <div class="flex items-center justify-between gap-2">
                <span class="text-xs font-extrabold text-white">${esc(p.name)}</span>
                <span class="px-1.5 py-0.5 rounded text-[9px] font-mono font-bold bg-amber-400/15 text-amber-300 border border-amber-400/30">${esc(p.payout || "Partner")}</span>
              </div>
              <p class="text-[11px] text-slate-400 leading-snug">${esc(desc)}</p>
            </div>
            <span class="inline-flex items-center justify-between text-[11px] font-bold text-amber-300 pt-1 border-t border-slate-800/80">
              <span>${esc(title)}</span>
              <span aria-hidden="true">↗</span>
            </span>
          </a>
        `;
      })
      .join("");
  }

  // -------------------------------------------------------------------------
  // 4. High-Converting Free-to-USDT Upgrade Modal & Checkout Wiring
  // -------------------------------------------------------------------------

  function renderUpgradePlanSelection() {
    document.querySelectorAll(".upgrade-plan-card").forEach((card) => {
      const plan = card.getAttribute("data-upgrade-plan");
      const active = plan === localState.upgradeSelectedPlan;
      card.classList.toggle("border-amber-400", active);
      card.classList.toggle("border-2", active);
      card.classList.toggle("bg-amber-500/10", active);
      card.classList.toggle("ring-1", active);
      card.classList.toggle("ring-amber-400/35", active);
      card.classList.toggle("border-slate-700", !active);
      card.classList.toggle("border", !active);
      card.classList.toggle("bg-slate-950/80", !active);
    });
  }

  function openUpgradeModal({
    reasonType = "limit",
    assetSymbol = "",
    defaultPlan = "monthly_personal_bot",
  } = {}) {
    const modal = document.getElementById("modal-usdt-upgrade");
    if (!modal) return;
    const isAr = getState().lang === "ar";
    localState.upgradeSelectedPlan = defaultPlan;

    const reasonEl = document.getElementById("upgrade-modal-reason");
    if (reasonEl) {
      const symTag = assetSymbol ? ` (${assetSymbol})` : "";
      if (reasonType === "breakout") {
        reasonEl.textContent = isAr
          ? `تنبيهات الاختراق الفورية على تلغرام${symTag} متاحة فوراً مع باقة VIP (10$/شهر) أو البوت الشخصي Pro (19$/شهر) عبر USDT.`
          : `Instant Telegram Breakout Alerts${symTag} unlock immediately with Business VIP ($10/mo) or Personal Bot Pro ($19/mo) via direct USDT.`;
      } else {
        reasonEl.textContent = isAr
          ? `وصلت للحد المجاني (3 أصول) أثناء إضافة${symTag || " أصل جديد"}. فعّل باقة VIP (10$/شهر) أو البوت الشخصي Pro (19$/شهر) بـ USDT لمتابعة حتى 50 أصلاً مع تنبيهات فورية.`
          : `You reached the 3-asset Free limit${symTag ? ` while adding ${assetSymbol}` : ""}. Upgrade with USDT ($10/mo or $19/mo) to track up to 50 assets with instant Telegram breakout alerts.`;
      }
    }

    renderUpgradePlanSelection();
    modal.classList.remove("hidden");
    modal.classList.add("flex");
    syncBodyScrollLock?.();
  }

  function closeUpgradeModal() {
    const modal = document.getElementById("modal-usdt-upgrade");
    if (!modal) return;
    modal.classList.add("hidden");
    modal.classList.remove("flex");
    syncBodyScrollLock?.();
  }

  async function launchUsdtPlanCheckout(
    planTier,
    networkSelectId,
    btnEl,
    tgInputId = "vip-tg-user-id"
  ) {
    const appState = getState();
    const isAr = appState.lang === "ar";
    const payCurrency = document.getElementById(networkSelectId)?.value || "usdttrc20";
    const tgUserId = (document.getElementById(tgInputId)?.value || "").trim();
    const vipCfg = MONETIZATION_CONFIG?.vipPayments || {};
    const checkoutUrl = vipCfg.checkoutApiUrl || "/api/create-vip-checkout";
    const directFallback = String(
      planTier === "monthly_personal_bot"
        ? vipCfg.nowPaymentsPersonalBotUrl || vipCfg.nowPaymentsVipUrl || ""
        : vipCfg.nowPaymentsVipUrl || ""
    ).trim();

    if (btnEl) btnEl.disabled = true;
    try {
      const resp = await fetch(checkoutUrl, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          plan_tier: planTier,
          lang: appState.lang,
          pay_currency: payCurrency,
          telegram_user_id: tgUserId || null,
          email: appState.authSession?.user?.email || null,
        }),
      });
      const data = await resp.json().catch(() => ({}));
      if (resp.ok && data?.ok && typeof data.invoice_url === "string" && data.invoice_url.startsWith("https://")) {
        window.location.href = data.invoice_url;
        return;
      }
      if (/^https:\/\//i.test(directFallback) && !/placeholder|your_/i.test(directFallback)) {
        window.open(directFallback, "_blank", "noopener,noreferrer");
        return;
      }
      showToast(
        isAr
          ? "تعذر إنشاء فاتورة USDT الآن؛ حاول مرة أخرى أو تواصل عبر @RatePathTerminalBot."
          : "Could not initialize USDT checkout right now; please retry or message @RatePathTerminalBot."
      );
    } catch {
      if (/^https:\/\//i.test(directFallback) && !/placeholder|your_/i.test(directFallback)) {
        window.open(directFallback, "_blank", "noopener,noreferrer");
      } else {
        showToast(
          isAr
            ? "تعذر الاتصال ببوابة الدفع حالياً."
            : "Payment gateway unreachable right now."
        );
      }
    } finally {
      if (btnEl) btnEl.disabled = false;
    }
  }

  function bindEvents() {
    document.getElementById("btn-refresh-quant")?.addEventListener("click", () => {
      fetchQuantSignals();
    });

    document.getElementById("btn-refresh-live-news")?.addEventListener("click", () => {
      fetchLiveHourlyNews(getState().lang);
    });

    document.getElementById("btn-jump-all-news")?.addEventListener("click", () => {
      if (typeof jumpToNewsTab === "function") jumpToNewsTab();
      document.getElementById("news-panel")?.scrollIntoView({ behavior: "smooth", block: "start" });
    });

    document.addEventListener("click", (e) => {
      const jumpBtn = e.target.closest("[data-jump-news-id]");
      if (jumpBtn) {
        const nid = jumpBtn.getAttribute("data-jump-news-id");
        if (typeof jumpToNewsTab === "function") jumpToNewsTab();
        setTimeout(() => {
          const card = document.getElementById(`news-card-${nid}`) || document.getElementById("news-panel");
          if (card) {
            card.scrollIntoView({ behavior: "smooth", block: "center" });
            card.classList.add("rp-news-highlight");
            setTimeout(() => card.classList.remove("rp-news-highlight"), 2500);
          }
        }, 60);
      }
    });

    document.getElementById("btn-open-personal-bot")?.addEventListener("click", () => {
      const hub = document.getElementById("personal-bot-hub");
      if (hub) {
        hub.scrollIntoView({ behavior: "smooth", block: "start" });
        document.getElementById("pbot-search-input")?.focus();
      }
    });

    document.getElementById("btn-pbot-signin")?.addEventListener("click", () => {
      openAuthModal();
    });

    let searchTimer = null;
    document.getElementById("pbot-search-input")?.addEventListener("input", (e) => {
      clearTimeout(searchTimer);
      const q = String(e.target.value || "").trim();
      if (q.length < 2) {
        localState.searchResults = [];
        renderPersonalBotSearchResults();
        return;
      }
      searchTimer = setTimeout(() => searchTrackerAssets(q), 350);
    });

    // Quick stock/asset preset pills
    document.addEventListener("click", (e) => {
      const quickChip = e.target.closest("[data-pbot-quick]");
      if (quickChip) {
        const q = quickChip.getAttribute("data-pbot-quick") || "";
        const inp = document.getElementById("pbot-search-input");
        if (inp) inp.value = q;
        searchTrackerAssets(q);
        return;
      }

      const addBtn = e.target.closest("[data-pbot-add-idx]");
      if (addBtn) {
        const idx = Number(addBtn.getAttribute("data-pbot-add-idx"));
        const item = localState.searchResults[idx];
        if (!item) return;
        const maxAllowed = localState.botProfile?.limits?.maxAssets || 3;
        if (localState.watchlist.length >= maxAllowed) {
          openUpgradeModal({
            reasonType: "limit",
            assetSymbol: item.symbol,
            defaultPlan: "monthly_personal_bot",
          });
          return;
        }
        if (!localState.watchlist.some((w) => w.symbol === item.symbol)) {
          localState.watchlist.unshift({
            symbol: item.symbol,
            category: item.category,
            name_en: item.name_en,
            name_ar: item.name_ar,
            alert_above: null,
            alert_below: null,
            news_alerts: true,
          });
          saveWatchlistToCloud();
          showToast(
            getState().lang === "ar"
              ? `تمت إضافة ${item.symbol} إلى بوتك الشخصي`
              : `${item.symbol} added to your Personal Bot watchlist`
          );
        }
        return;
      }

      const instantBtn = e.target.closest("[data-pbot-instant-alert]");
      if (instantBtn) {
        const sym = instantBtn.getAttribute("data-pbot-instant-alert") || "";
        const tier = localState.botProfile?.plan_tier || "free";
        if (tier === "free") {
          openUpgradeModal({
            reasonType: "breakout",
            assetSymbol: sym,
            defaultPlan: "monthly_business_vip",
          });
        } else {
          showToast(
            getState().lang === "ar"
              ? `✅ تنبيهات الاختراق الفورية مفعّلة لـ ${sym} على تلغرام`
              : `✅ Instant Telegram breakout alerts are active for ${sym}`
          );
        }
        return;
      }

      const planCard = e.target.closest("[data-upgrade-plan]");
      if (planCard) {
        localState.upgradeSelectedPlan =
          planCard.getAttribute("data-upgrade-plan") || "monthly_personal_bot";
        renderUpgradePlanSelection();
        return;
      }

      const newsSearchBtn = e.target.closest("[data-pbot-news-idx]");
      if (newsSearchBtn) {
        const idx = Number(newsSearchBtn.getAttribute("data-pbot-news-idx"));
        const item = localState.searchResults[idx];
        if (item) inspectAssetIntel(item);
        return;
      }

      const newsWatchBtn = e.target.closest("[data-pbot-watch-news]");
      if (newsWatchBtn) {
        const idx = Number(newsWatchBtn.getAttribute("data-pbot-watch-news"));
        const item = localState.watchlist[idx];
        if (item) inspectAssetIntel(item);
        return;
      }

      const remBtn = e.target.closest("[data-pbot-remove]");
      if (remBtn) {
        const idx = Number(remBtn.getAttribute("data-pbot-remove"));
        localState.watchlist.splice(idx, 1);
        saveWatchlistToCloud();
        return;
      }

      if (e.target.closest("#btn-close-intel-preview")) {
        localState.selectedIntel = null;
        renderIntelPreview();
      }
    });

    // Save threshold inputs on change & offer instant breakout alert upgrade for free users
    document.getElementById("pbot-watchlist-container")?.addEventListener("change", (e) => {
      const aboveIdx = e.target.getAttribute?.("data-pbot-above");
      const belowIdx = e.target.getAttribute?.("data-pbot-below");
      const val = String(e.target.value || "").trim();
      let updatedItem = null;
      if (aboveIdx !== null && localState.watchlist[Number(aboveIdx)]) {
        updatedItem = localState.watchlist[Number(aboveIdx)];
        updatedItem.alert_above = val || null;
        saveWatchlistToCloud();
      } else if (belowIdx !== null && localState.watchlist[Number(belowIdx)]) {
        updatedItem = localState.watchlist[Number(belowIdx)];
        updatedItem.alert_below = val || null;
        saveWatchlistToCloud();
      }
      if (updatedItem && val && (localState.botProfile?.plan_tier || "free") === "free") {
        openUpgradeModal({
          reasonType: "breakout",
          assetSymbol: updatedItem.symbol,
          defaultPlan: "monthly_business_vip",
        });
      }
    });

    document.getElementById("btn-close-upgrade-modal")?.addEventListener("click", closeUpgradeModal);
    document.getElementById("modal-usdt-upgrade")?.addEventListener("click", (e) => {
      if (e.target.id === "modal-usdt-upgrade") closeUpgradeModal();
    });
    document.getElementById("upgrade-usdt-form")?.addEventListener("submit", async (e) => {
      e.preventDefault();
      const btn = document.getElementById("btn-upgrade-usdt-submit");
      await launchUsdtPlanCheckout(
        localState.upgradeSelectedPlan || "monthly_personal_bot",
        "upgrade-usdt-network",
        btn,
        "upgrade-tg-user-id"
      );
    });

    document.getElementById("btn-pbot-save-now")?.addEventListener("click", async () => {
      const res = await saveWatchlistToCloud();
      const isAr = getState().lang === "ar";
      if (res.localOnly) {
        showToast(
          isAr
            ? "تم الحفظ محلياً! سجّل بالإيميل لربط التنبيهات ببوت التلغرام."
            : "Saved locally! Sign in by email to pair with Telegram alerts."
        );
      } else if (res.ok) {
        showToast(
          isAr
            ? "✅ تم حفظ قائمتك ومزامنتها مع بوت التلغرام الشخصي!"
            : "✅ Watchlist saved & synced with your Personal Telegram Bot!"
        );
      }
    });

    document.getElementById("pbot-pro-checkout-form")?.addEventListener("submit", async (e) => {
      e.preventDefault();
      const planTier = document.getElementById("pbot-plan-select")?.value || "monthly_personal_bot";
      const btn = document.getElementById("btn-pbot-usdt-checkout");
      await launchUsdtPlanCheckout(planTier, "pbot-usdt-network", btn);
    });
  }

  return {
    init() {
      bindEvents();
      renderQuantRadar();
      renderLiveBreakingNews();
      renderPersonalBotHub();
      renderBotsAffiliateShelf();
      fetchQuantSignals();
      fetchLiveHourlyNews();
      setInterval(() => fetchLiveHourlyNews(), 600000);
    },
    renderAll() {
      renderQuantRadar();
      renderLiveBreakingNews();
      renderPersonalBotHub();
      renderBotsAffiliateShelf();
      const lang = getState().lang;
      if (!localState.liveNewsByLang[lang]) {
        fetchLiveHourlyNews(lang);
      }
    },
    syncPersonalBotProfile,
    renderBotsAffiliateShelf,
    openUpgradeModal,
    closeUpgradeModal,
  };
}
