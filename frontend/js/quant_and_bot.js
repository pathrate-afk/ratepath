/**
 * RatePath (ريت باث) — Quant & Macro Signal Radar, Personal AI Tracker Bot Hub,
 * Global Stocks/Company News Search, and Geo-Filtered Trading Bots Affiliate Shelf.
 *
 * Legal & Ethical Notice: Informational data tools & statistical analytics only —
 * never financial advice or guaranteed price outcomes.
 */

import {
  AFFILIATES_ENABLED,
  MONETIZATION_CONFIG,
} from "../monetization.config.js";
import { selectGeoCompliantPartners } from "./affiliates.js";

const LOCAL_WATCHLIST_KEY = "ratepath_personal_bot_watchlist_v1";

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
}) {
  const localState = {
    quantData: null,
    quantLoading: false,
    botProfile: null,
    watchlist: loadLocalWatchlist(),
    searchResults: [],
    searchLoading: false,
    selectedIntel: null,
    intelLoading: false,
    selectedPlan: "monthly_personal_bot",
    dailyDigest: true,
    digestHourUtc: 7,
  };

  // -------------------------------------------------------------------------
  // 1. Live Quant & Macro Probabilistic Radar + Real 30-Day Track Record
  // -------------------------------------------------------------------------

  async function fetchQuantSignals() {
    if (localState.quantLoading) return;
    localState.quantLoading = true;
    renderQuantRadar();
    try {
      const resp = await fetch("/api/strategist-signals", { cache: "default" });
      if (resp.ok) {
        localState.quantData = await resp.json();
      }
    } catch {
      // Graceful offline fallback
    } finally {
      localState.quantLoading = false;
      renderQuantRadar();
    }
  }

  function renderQuantRadar() {
    const body = document.getElementById("quant-radar-body");
    const trackBadge = document.getElementById("quant-track-record-badge");
    const macroBar = document.getElementById("quant-macro-bar");
    if (!body) return;

    const appState = getState();
    const isAr = appState.lang === "ar";
    const data = localState.quantData;

    if (!data) {
      body.innerHTML = `<div class="p-3 rounded-xl bg-slate-950/60 border border-slate-800 text-xs text-slate-400 text-center">${
        localState.quantLoading
          ? isAr
            ? "جارٍ حساب مؤشرات EMA/RSI والتمويل ومؤشر الدولار..."
            : "Computing live EMA/RSI, derivatives funding & DXY signals..."
          : isAr
          ? "اضغط تحديث لجلب رادار الأرقام المباشر."
          : "Tap refresh to load live quantitative metrics."
      }</div>`;
      return;
    }

    // Honest 30-Day Real Track Record Badge (NEVER fabricated)
    if (trackBadge) {
      const tr = data.track_record_30d || {};
      const graded = Number(tr.graded_count_30d || 0);
      const pending = Number(tr.pending_count || 0);
      if (graded > 0 && tr.avg_accuracy_pct != null) {
        trackBadge.textContent = isAr
          ? `سجل الدقة (30 يوم): ${tr.avg_accuracy_pct}% (${graded} تقييم فعلي)`
          : `30d Live Accuracy: ${tr.avg_accuracy_pct}% (${graded} graded)`;
        trackBadge.className =
          "inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-[10px] font-mono font-bold bg-emerald-500/15 text-emerald-300 border border-emerald-500/35";
      } else {
        trackBadge.textContent = isAr
          ? `سجل التقييم الحي: قيد الرصد (${pending} توقع بانتظار 24س)`
          : `Live Grading: Collecting 24h samples (${pending} pending)`;
        trackBadge.className =
          "inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-[10px] font-mono font-bold bg-slate-800 text-slate-300 border border-slate-700";
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

    const rows = Array.isArray(data.signals) ? data.signals : [];
    body.innerHTML = `
      <div class="grid grid-cols-1 sm:grid-cols-2 gap-2">
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
                ? "🟢 ⬆️ ميل صاعد"
                : "🟢 ⬆️ Bullish Lean"
              : isDown
              ? isAr
                ? "🔴 ⬇️ ميل هابط"
                : "🔴 ⬇️ Bearish Lean"
              : isAr
              ? "⚪ محايد (انتظار)"
              : "⚪ Neutral (Wait)";
            const scoreNum = Number(sig.score || 0);
            const scoreText = `${scoreNum > 0 ? "+" : ""}${scoreNum}`;
            const rsiText = sig.rsi14 != null ? `RSI ${sig.rsi14}` : "";
            const fundText =
              sig.funding_8h_pct != null ? `Fund ${sig.funding_8h_pct}%` : "";
            const metaLine = [rsiText, fundText, `Score ${scoreText}`]
              .filter(Boolean)
              .join(" · ");
            const reason = isAr ? sig.reason_ar : sig.reason_en;
            return `
              <div class="p-2.5 rounded-xl bg-slate-950/75 border border-slate-800/90 space-y-1.5">
                <div class="flex items-center justify-between gap-2">
                  <span class="text-xs font-extrabold text-white">${esc(ASSET_LABELS[sig.asset] || sig.asset)}</span>
                  <span class="px-2 py-0.5 rounded-md text-[10px] font-bold border ${badgeCls}">${badgeLabel}</span>
                </div>
                <div class="flex items-center justify-between text-[10px] font-mono text-slate-400" dir="ltr">
                  <span>${sig.price != null ? esc(sig.price) : "—"}</span>
                  <span>${esc(metaLine)}</span>
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
            ? `<span class="text-[10px] font-mono ${chg.startsWith("-") ? "text-rose-400" : "text-emerald-400"}" dir="ltr">${chg.startsWith("-") ? "🔴 ⬇️" : "🟢 ⬆️"} ${esc(chg)}%</span>`
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
    const appState = getState();
    const isAr = appState.lang === "ar";
    const listEl = document.getElementById("pbot-watchlist-container");
    const statusBox = document.getElementById("pbot-account-status");
    const linkBtn = document.getElementById("btn-pbot-link-telegram");
    const signInBtn = document.getElementById("btn-pbot-signin");
    const tierBadge = document.getElementById("pbot-tier-badge");

    const prof = localState.botProfile;
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
      if (!isSignedIn) {
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
      signInBtn.classList.toggle("hidden", isSignedIn);
    }
    if (linkBtn) {
      linkBtn.classList.toggle("hidden", !isSignedIn);
      if (isSignedIn && prof?.telegram_link_url) {
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
                    placeholder="${isAr ? "🟢 تنبيه صعود ≥" : "🟢 Alert Above ≥"}"
                    class="h-8 px-2.5 rounded-lg bg-slate-900 border border-slate-700 text-[11px] font-mono text-emerald-300 placeholder:text-slate-500" />
                  <input type="text" inputmode="decimal" dir="ltr" data-pbot-below="${idx}"
                    value="${esc(item.alert_below || "")}"
                    placeholder="${isAr ? "🔴 تنبيه هبوط ≤" : "🔴 Alert Below ≤"}"
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
    const shelf = document.getElementById("bots-affiliate-shelf");
    const grid = document.getElementById("bots-affiliate-grid");
    if (!shelf || !grid) return;

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
      maxCount: 4,
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
  // 4. Event Wiring (Search, Watchlist Save, Pro Bot USDT Checkout)
  // -------------------------------------------------------------------------

  async function launchUsdtPlanCheckout(planTier, networkSelectId, btnEl) {
    const appState = getState();
    const isAr = appState.lang === "ar";
    const payCurrency = document.getElementById(networkSelectId)?.value || "usdttrc20";
    const tgUserId = (document.getElementById("vip-tg-user-id")?.value || "").trim();
    const checkoutUrl = MONETIZATION_CONFIG?.vipPayments?.checkoutApiUrl || "/api/create-vip-checkout";

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
      showToast(
        isAr
          ? "تعذر إنشاء فاتورة USDT الآن؛ حاول مرة أخرى أو تواصل عبر @RatePathTerminalBot."
          : "Could not initialize USDT checkout right now; please retry or message @RatePathTerminalBot."
      );
    } catch {
      showToast(
        isAr
          ? "تعذر الاتصال ببوابة الدفع حالياً."
          : "Payment gateway unreachable right now."
      );
    } finally {
      if (btnEl) btnEl.disabled = false;
    }
  }

  function bindEvents() {
    document.getElementById("btn-refresh-quant")?.addEventListener("click", () => {
      fetchQuantSignals();
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
          showToast(
            getState().lang === "ar"
              ? `وصلت لحد باقتك (${maxAllowed} أصول). اشترك في باقة البوت الشخصي (19$/شهر USDT) لمتابعة حتى 50 أصلاً.`
              : `Reached your plan limit (${maxAllowed} assets). Upgrade to Personal Bot Pro ($19/mo USDT) for up to 50.`
          );
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

    // Save threshold inputs on change
    document.getElementById("pbot-watchlist-container")?.addEventListener("change", (e) => {
      const aboveIdx = e.target.getAttribute?.("data-pbot-above");
      const belowIdx = e.target.getAttribute?.("data-pbot-below");
      if (aboveIdx !== null && localState.watchlist[Number(aboveIdx)]) {
        localState.watchlist[Number(aboveIdx)].alert_above = e.target.value.trim() || null;
        saveWatchlistToCloud();
      } else if (belowIdx !== null && localState.watchlist[Number(belowIdx)]) {
        localState.watchlist[Number(belowIdx)].alert_below = e.target.value.trim() || null;
        saveWatchlistToCloud();
      }
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
      renderPersonalBotHub();
      renderBotsAffiliateShelf();
      fetchQuantSignals();
    },
    renderAll() {
      renderQuantRadar();
      renderPersonalBotHub();
      renderBotsAffiliateShelf();
    },
    syncPersonalBotProfile,
    renderBotsAffiliateShelf,
  };
}
