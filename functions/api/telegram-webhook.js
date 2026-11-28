/**
 * RatePath (ريت باث) — High-Speed Interactive @RatePathTerminalBot Webhook
 * Cloudflare Pages Function: `POST /api/telegram-webhook`
 *
 * Production Capabilities & Architecture:
 * - Verifies `X-Telegram-Bot-Api-Secret-Token` against `env.TELEGRAM_WEBHOOK_SECRET`.
 * - Handles BOTH `message` commands/queries AND `callback_query` inline button flows
 *   with immediate `answerCallbackQuery` (<100ms) and in-place `editMessageText` (zero menu spam).
 * - Decoupled 45s in-memory + Upstash Redis batch market price cache (FX, SYP, TRY, EGP,
 *   Gold XAU, Silver XAG, Crypto) — never fans out external market APIs per user.
 * - Strict per-user tier limits persisted across Upstash Redis + Supabase (`telegram_bot_users`
 *   & `user_tracker_profiles`):
 *     * Free: max 3 active assets -> triggers high-converting 3/3 Free Limit Paywall on 4th asset
 *     * Business VIP ($10/mo USDT): max 10 active assets + VIP Channel + priority alerts
 *     * Personal Bot Pro ($19/mo USDT): max 50 active assets + Stocks/News + VIP Channel
 * - Bilingual UX (`ar` default + `en` toggle persisted per user) with exact high-converting
 *   Welcome, Free Limit (3/3), Replace Asset, Target Price, and NOWPayments USDT Upgrade screens.
 * - Preserves VIP AI Desk (`/ask <coin>` or natural AR/EN coin queries with 25/day quota & 15m cache).
 * - Resilient Telegram API wrapper with exponential backoff on 429/5xx & 403 auto-pause.
 */

import { createVipCheckoutSession } from "./create-vip-checkout.js";

const LIVE_FEED_DEFAULT =
  "https://raw.githubusercontent.com/pathrate-afk/ratepath/live-data/live_feed.json";
const CATALOG_FEED_DEFAULT =
  "https://raw.githubusercontent.com/pathrate-afk/ratepath/live-data/catalog_feed.json";

const VIP_DAILY_LIMIT = 25;
const VIP_CACHE_TTL_SECONDS = 900; // 15 minutes
const BATCH_FEED_CACHE_TTL_MS = 45000; // 45s in-memory Edge isolate batch cache
const SUPPORT_HOURLY_LIMIT = 8; // Abuse protection: max 8 AI support messages/hour/user
const SUPPORT_MAX_PROMPT_CHARS = 500; // Abuse protection: max 500 chars per prompt
const SUPPORT_MAX_HISTORY_TURNS = 4; // Keep only last 4 turns (8 messages) per user
const AI_SUPPORT_TIMEOUT_MS = 8000; // 8s strict timeout budget with admin escalation fallback
const VIP_MEMBER_STATUSES = new Set(["member", "administrator", "creator"]);
const RAW_SYRIA_FLAG_EMOJI = "\uD83C\uDDF8\uD83C\uDDFE";
const TG_USER_REDIS_PREFIX = "ratepath:tg_user:";
const TG_USERS_INDEX_REDIS_KEY = "ratepath:tg_users:index";
const SUPPORT_TICKET_REDIS_PREFIX = "ratepath:support_ticket:";
const REF_CLAIMED_REDIS_PREFIX = "ratepath:ref_claimed:";
const REFERRAL_JOIN_TOKENS = 15;
const REFERRAL_PAID_UPGRADE_TOKENS = 100;
const REFERRAL_REDEEM_COST_TOKENS = 100;
const REFERRAL_REDEEM_VIP_DAYS = 7;
const FREE_MAX_BONUS_SLOTS = 3; // +1 free slot per 2 invited friends, up to 6 total free slots

// In-memory fallbacks when Upstash Redis is not configured in local dev / tests
const _localSupportRateLimits = new Map();
const _localSupportTickets = new Map();
const _localRefClaims = new Map();

export const TIER_LIMITS = Object.freeze({
  free: { maxAssets: 3, priceUsd: 0, labelAr: "المجانية", labelEn: "Free" },
  monthly_business_vip: { maxAssets: 10, priceUsd: 10, labelAr: "Business VIP", labelEn: "Business VIP" },
  monthly_personal_bot: { maxAssets: 50, priceUsd: 19, labelAr: "Personal Bot Pro", labelEn: "Personal Bot Pro" },
});

const POPULAR_ASSETS = Object.freeze([
  { symbol: "XAU", category: "metal", en: "Gold (XAU)", ar: "الذهب XAU" },
  { symbol: "XAG", category: "metal", en: "Silver (XAG)", ar: "الفضة XAG" },
  { symbol: "BTC", category: "crypto", en: "Bitcoin (BTC)", ar: "بيتكوين BTC" },
  { symbol: "ETH", category: "crypto", en: "Ethereum (ETH)", ar: "إيثيريوم ETH" },
  { symbol: "SOL", category: "crypto", en: "Solana (SOL)", ar: "سولانا SOL" },
  { symbol: "USDT", category: "crypto", en: "Tether (USDT)", ar: "تيثر USDT" },
  { symbol: "EUR", category: "fiat", en: "Euro (EUR)", ar: "اليورو EUR" },
  { symbol: "TRY", category: "fiat", en: "Turkish Lira (TRY)", ar: "الليرة التركية TRY" },
  { symbol: "EGP", category: "fiat", en: "Egyptian Pound (EGP)", ar: "الجنيه المصري EGP" },
  { symbol: "SYP", category: "fiat", en: "Syrian Pound (SYP)", ar: "الليرة السورية SYP" },
  { symbol: "AAPL", category: "stock", en: "Apple (AAPL)", ar: "سهم أبل AAPL" },
  { symbol: "TSLA", category: "stock", en: "Tesla (TSLA)", ar: "سهم تسلا TSLA" },
]);

const JSON_HEADERS = {
  "Content-Type": "application/json; charset=utf-8",
  "Cache-Control": "no-store",
  "X-Content-Type-Options": "nosniff",
  "X-Robots-Tag": "noindex",
};
const SUPABASE_SERVER_UA = "RatePath-Edge/2.0 (server)";

// Shared in-memory isolate batch price cache (only used for real production `fetch`)
const _memoryFeedCache = {
  key: "",
  fetchedAt: 0,
  liveFeed: null,
  catalogFeed: null,
};

function withTimeout(init = {}, ms = 8000) {
  if (typeof AbortSignal !== "undefined" && typeof AbortSignal.timeout === "function") {
    return { ...init, signal: AbortSignal.timeout(ms) };
  }
  return init;
}

function sleepMs(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/**
 * Strictly sanitize AI-generated Telegram HTML:
 * - Converts <br> to newline, strips dangerous/unsupported tags (including <a href=...>, <script>, <div>)
 * - Preserves ONLY balanced <b>, <i>, <code> tags and escapes all stray <, >, & characters
 *   so Telegram `parse_mode: "HTML"` NEVER fails with HTTP 400 and phishing links cannot be injected.
 */
export function sanitizeTelegramHtml(raw) {
  let s = String(raw ?? "").replace(/<br\s*\/?>/gi, "\n");
  s = s.replace(/<(script|style)[^>]*>[\s\S]*?<\/\1>/gi, "");
  const placeholders = [];
  s = s.replace(/<(\/?)(b|i|code)\s*>/gi, (_m, slash, tag) => {
    const idx = placeholders.length;
    placeholders.push(`<${slash ? "/" : ""}${tag.toLowerCase()}>`);
    return `\u0000TAG${idx}\u0000`;
  });
  s = s.replace(/<\/?[a-zA-Z][^>]*>/g, "");
  s = s
    .replace(/&(?!(?:amp|lt|gt|quot|#39);)/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
  s = s.replace(/\u0000TAG(\d+)\u0000/g, (_m, idxStr) => placeholders[Number(idxStr)] || "");
  return s;
}

const ASSET_ALIASES = [
  { symbol: "XAU", kind: "gold", en: "Gold (XAU)", ar: "الذهب الفوري (XAU)", terms: ["الذهب", "ذهب", "عيار 21", "عيار 24", "اونصة الذهب", "أونصة الذهب", "gold", "xau", "xau_oz", "xauusd"] },
  { symbol: "XAG", kind: "gold", en: "Silver (XAG)", ar: "الفضة الفورية (XAG)", terms: ["الفضة", "فضة", "silver", "xag", "xag_oz"] },
  { symbol: "SYP", kind: "fiat", en: "SYP", ar: "الليرة السورية", terms: ["الليرة السورية", "ليرة سورية", "الليره السوريه", "ليره سوريه", "سوريا", "سوري", "دمشق", "syp", "syrian pound"] },
  { symbol: "TRY", kind: "fiat", en: "Turkish Lira (TRY)", ar: "الليرة التركية (TRY)", terms: ["الليرة التركية", "ليرة تركية", "تركيا", "turkish lira", "try"] },
  { symbol: "EGP", kind: "fiat", en: "Egyptian Pound (EGP)", ar: "الجنيه المصري (EGP)", terms: ["الجنيه المصري", "جنيه مصري", "مصر", "egyptian pound", "egp"] },
  { symbol: "SAR", kind: "fiat", en: "Saudi Riyal (SAR)", ar: "الريال السعودي (SAR)", terms: ["الريال السعودي", "ريال سعودي", "السعودية", "saudi riyal", "sar"] },
  { symbol: "AED", kind: "fiat", en: "UAE Dirham (AED)", ar: "الدرهم الإماراتي (AED)", terms: ["الدرهم الإماراتي", "درهم اماراتي", "الامارات", "aed", "dirham"] },
  { symbol: "EUR", kind: "fiat", en: "Euro (EUR)", ar: "اليورو (EUR)", terms: ["اليورو", "يورو", "euro", "eur"] },
  { symbol: "GBP", kind: "fiat", en: "British Pound (GBP)", ar: "الجنيه الإسترليني (GBP)", terms: ["الاسترليني", "الإسترليني", "الباوند", "british pound", "gbp"] },
  { symbol: "JOD", kind: "fiat", en: "Jordanian Dinar (JOD)", ar: "الدينار الأردني (JOD)", terms: ["الدينار الأردني", "دينار اردني", "الاردن", "jod"] },
  { symbol: "KWD", kind: "fiat", en: "Kuwaiti Dinar (KWD)", ar: "الدينار الكويتي (KWD)", terms: ["الدينار الكويتي", "دينار كويتي", "الكويت", "kwd"] },
  { symbol: "IQD", kind: "fiat", en: "Iraqi Dinar (IQD)", ar: "الدينار العراقي (IQD)", terms: ["الدينار العراقي", "دينار عراقي", "العراق", "iqd"] },
  { symbol: "LBP", kind: "fiat", en: "Lebanese Pound (LBP)", ar: "الليرة اللبنانية (LBP)", terms: ["الليرة اللبنانية", "ليرة لبنانية", "لبنان", "lbp"] },
  { symbol: "BTC", kind: "crypto", en: "Bitcoin (BTC)", ar: "بيتكوين (BTC)", terms: ["البيتكوين", "بيتكوين", "بتكوين", "bitcoin", "btc"] },
  { symbol: "ETH", kind: "crypto", en: "Ethereum (ETH)", ar: "إيثيريوم (ETH)", terms: ["الإيثيريوم", "إيثيريوم", "ايثيريوم", "ايثريوم", "ethereum", "eth"] },
  { symbol: "SOL", kind: "crypto", en: "Solana (SOL)", ar: "سولانا (SOL)", terms: ["سولانا", "السولانا", "solana", "sol"] },
  { symbol: "USDT", kind: "crypto", en: "Tether (USDT)", ar: "تيثر (USDT)", terms: ["تيثر", "usdt", "tether"] },
  { symbol: "XRP", kind: "crypto", en: "XRP (Ripple)", ar: "ريبل (XRP)", terms: ["ريبل", "الريبل", "ripple", "xrp"] },
  { symbol: "BNB", kind: "crypto", en: "BNB", ar: "عملة BNB", terms: ["بينانس", "bnb"] },
  { symbol: "TON", kind: "crypto", en: "Toncoin (TON)", ar: "تون كوين (TON)", terms: ["تونكوين", "تون كوين", "toncoin", "ton"] },
  { symbol: "DOGE", kind: "crypto", en: "Dogecoin (DOGE)", ar: "دوجكوين (DOGE)", terms: ["دوجكوين", "دوج", "dogecoin", "doge"] },
  { symbol: "ADA", kind: "crypto", en: "Cardano (ADA)", ar: "كاردانو (ADA)", terms: ["كاردانو", "cardano", "ada"] },
  { symbol: "PEPE", kind: "crypto", en: "Pepe (PEPE)", ar: "بيبي (PEPE)", terms: ["بيبي", "pepe"] },
  { symbol: "SHIB", kind: "crypto", en: "Shiba Inu (SHIB)", ar: "شيبا إينو (SHIB)", terms: ["شيبا", "shiba", "shib"] },
  { symbol: "AVAX", kind: "crypto", en: "Avalanche (AVAX)", ar: "أفالانش (AVAX)", terms: ["افالانش", "أفالانش", "avalanche", "avax"] },
  { symbol: "LINK", kind: "crypto", en: "Chainlink (LINK)", ar: "تشين لينك (LINK)", terms: ["تشين لينك", "chainlink", "link"] },
  { symbol: "SUI", kind: "crypto", en: "Sui (SUI)", ar: "سوي (SUI)", terms: ["سوي", "sui"] },
  { symbol: "TRX", kind: "crypto", en: "TRON (TRX)", ar: "ترون (TRX)", terms: ["ترون", "tron", "trx"] },
  { symbol: "LTC", kind: "crypto", en: "Litecoin (LTC)", ar: "لايتكوين (LTC)", terms: ["لايتكوين", "litecoin", "ltc"] },
  { symbol: "AAPL", kind: "stock", en: "Apple Inc. (AAPL)", ar: "سهم أبل (AAPL)", terms: ["سهم أبل", "سهم ابل", "أبل", "apple", "aapl"] },
  { symbol: "TSLA", kind: "stock", en: "Tesla Inc. (TSLA)", ar: "سهم تسلا (TSLA)", terms: ["سهم تسلا", "تسلا", "tesla", "tsla"] },
  { symbol: "NVDA", kind: "stock", en: "NVIDIA Corp. (NVDA)", ar: "سهم إنفيديا (NVDA)", terms: ["إنفيديا", "انفيديا", "nvidia", "nvda"] },
  { symbol: "MSFT", kind: "stock", en: "Microsoft Corp. (MSFT)", ar: "سهم مايكروسوفت (MSFT)", terms: ["مايكروسوفت", "microsoft", "msft"] },
  { symbol: "AMZN", kind: "stock", en: "Amazon (AMZN)", ar: "سهم أمازون (AMZN)", terms: ["أمازون", "امازون", "amazon", "amzn"] },
  { symbol: "GOOGL", kind: "stock", en: "Alphabet / Google (GOOGL)", ar: "سهم جوجل (GOOGL)", terms: ["جوجل", "غوغل", "alphabet", "google", "googl"] },
  { symbol: "META", kind: "stock", en: "Meta Platforms (META)", ar: "سهم ميتا (META)", terms: ["ميتا", "فيسبوك", "meta"] },
  { symbol: "2222.SR", kind: "stock", en: "Saudi Aramco (2222.SR)", ar: "سهم أرامكو (2222.SR)", terms: ["أرامكو", "ارامكو", "aramco", "2222.sr"] },
  { symbol: "SPY", kind: "stock", en: "S&P 500 ETF (SPY)", ar: "مؤشر S&P 500 (SPY)", terms: ["اس اند بي", "إس آند بي", "s&p 500", "spy"] },
];

const IGNORE_TICKERS = new Set([
  "ASK", "START", "HELP", "NEWS", "PRICE", "ANALYSIS", "RATE", "COIN", "CRYPTO",
  "WHAT", "HOW", "ABOUT", "TODAY", "NOW", "THE", "FOR", "AND", "WITH", "STATUS",
  "CHECK", "TELL", "GIVE", "SHOW", "UPDATE", "MARKET", "LIVE", "USD", "UPGRADE",
  "VIP", "PRO", "WATCHLIST", "MENU", "LANG", "SUPPORT", "ADD",
]);

export function escapeHtml(str) {
  return String(str ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

export function sanitizeSyriaEmoji(text, lang = "ar") {
  const replacement = lang === "ar" ? "🟢 ل.س" : "🟢 SYP";
  return String(text ?? "")
    .replaceAll(`${RAW_SYRIA_FLAG_EMOJI}\uFE0F`, replacement)
    .replaceAll(RAW_SYRIA_FLAG_EMOJI, replacement);
}

export function normalizePreserveNewlines(text) {
  const unified = String(text ?? "").replace(/\r\n/g, "\n").replace(/\r/g, "\n");
  const lines = unified.split("\n").map((ln) => ln.replace(/[ \t]+/g, " ").trim());
  return lines.join("\n").trim().replace(/\n{3,}/g, "\n\n");
}

export function verifyWebhookSecret(providedHeader, expectedSecret) {
  const a = String(providedHeader || "");
  const b = String(expectedSecret || "");
  if (!a || !b || b.toLowerCase() === "placeholder") return false;
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) {
    diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  }
  return diff === 0;
}

export function detectQueryLang(text, userLangCode = "") {
  if (/[\u0600-\u06FF]/.test(String(text || ""))) return "ar";
  if (String(userLangCode || "").toLowerCase().startsWith("ar")) return "ar";
  return "en";
}

export function resolveDefaultLangFromTelegram(userLangCode = "") {
  return String(userLangCode || "").trim().toLowerCase().startsWith("ar") ? "ar" : "en";
}

export function resolveAdminTelegramId(env) {
  const candidates = [env?.ADMIN_TELEGRAM_ID, env?.ADMIN_CHAT_ID];
  for (const raw of candidates) {
    const clean = String(raw || "").trim();
    if (clean && clean.toLowerCase() !== "placeholder") return clean;
  }
  return null;
}

function isAdminTelegramUser(userId, env) {
  const uidStr = String(userId || "").trim();
  if (!uidStr) return false;
  const adminIds = [env?.ADMIN_TELEGRAM_ID, env?.ADMIN_CHAT_ID]
    .map((v) => String(v || "").trim())
    .filter((v) => v && v.toLowerCase() !== "placeholder");
  return adminIds.includes(uidStr);
}

export function parseUserAssetQuery(rawText, userLangCode = "") {
  const text = String(rawText || "").trim();
  const lang = detectQueryLang(text, userLangCode);
  const cleaned = text.replace(/^\/ask(@\w+)?\s*/i, "").trim();
  const low = cleaned.toLowerCase();

  let bestMatch = null;
  let bestLen = 0;
  for (const entry of ASSET_ALIASES) {
    for (const term of entry.terms) {
      const tLow = term.toLowerCase();
      const isAsciiWord = /^[a-z0-9_.]+$/.test(tLow);
      const matched = isAsciiWord
        ? new RegExp(`(^|[^a-z0-9_.])${tLow.replace(/\./g, "\\.")}([^a-z0-9_.]|$)`, "i").test(low)
        : low.includes(tLow);
      if (matched && tLow.length > bestLen) {
        bestLen = tLow.length;
        bestMatch = entry;
      }
    }
  }
  if (bestMatch) {
    return {
      symbol: bestMatch.symbol,
      kind: bestMatch.kind,
      displayEn: bestMatch.en,
      displayAr: bestMatch.ar,
      lang,
      rawQuery: cleaned,
    };
  }

  const tickerMatches = cleaned.match(/\b[A-Za-z][A-Za-z0-9]{1,9}\b/g) || [];
  for (const rawTok of tickerMatches) {
    const sym = rawTok.toUpperCase();
    if (!IGNORE_TICKERS.has(sym)) {
      return {
        symbol: sym,
        kind: "crypto",
        displayEn: sym,
        displayAr: sym,
        lang,
        rawQuery: cleaned,
      };
    }
  }

  return null;
}

// ---------------------------------------------------------------------------
// Upstash Redis + Supabase Per-User Profile Store (Tier, Lang, Watchlist, State)
// ---------------------------------------------------------------------------

async function upstashCmd(env, commandArray, fetchImpl = fetch) {
  const url = String(env?.UPSTASH_REDIS_REST_URL || "").trim().replace(/\/+$/, "");
  const token = String(env?.UPSTASH_REDIS_REST_TOKEN || "").trim();
  if (!url || !token || url.toLowerCase() === "placeholder" || token.toLowerCase() === "placeholder") {
    return null;
  }
  try {
    const resp = await fetchImpl(
      url,
      withTimeout({
        method: "POST",
        headers: {
          Authorization: `Bearer ${token}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify(commandArray),
      }, 5000)
    );
    if (!resp.ok) return null;
    const body = await resp.json();
    return body?.result ?? null;
  } catch {
    return null;
  }
}

function getSupaConfig(env) {
  const url = String(env?.SUPABASE_URL || "").trim().replace(/\/+$/, "");
  const key = String(env?.SUPABASE_KEY || "").trim();
  if (!url || !key || url.toLowerCase() === "placeholder" || key.toLowerCase() === "placeholder") {
    return null;
  }
  const restBase = url.endsWith("/rest/v1") ? url : `${url}/rest/v1`;
  return {
    restBase,
    headers: {
      apikey: key,
      Authorization: `Bearer ${key}`,
      "User-Agent": SUPABASE_SERVER_UA,
      "Content-Type": "application/json",
    },
  };
}

export function normalizePlanTier(rawTier) {
  const t = String(rawTier || "free").trim().toLowerCase();
  if (t === "monthly_personal_bot" || t === "pro19" || t === "pro") return "monthly_personal_bot";
  if (t === "monthly_business_vip" || t === "vip10" || t === "vip" || t === "monthly") return "monthly_business_vip";
  return "free";
}

export function getTierMeta(planTier) {
  return TIER_LIMITS[normalizePlanTier(planTier)] || TIER_LIMITS.free;
}

export function getReferralBonusSlots(profile) {
  const count = Math.max(0, Number(profile?.referral_count || 0));
  return Math.min(FREE_MAX_BONUS_SLOTS, Math.floor(count / 2));
}

export function getEffectiveMaxAssets(profile) {
  const tier = normalizePlanTier(profile?.plan_tier);
  if (tier !== "free") return getTierMeta(tier).maxAssets;
  return TIER_LIMITS.free.maxAssets + getReferralBonusSlots(profile);
}

export async function loadBotUserProfile(userId, env, fetchImpl = fetch, defaultLang = "ar") {
  const uid = String(userId || "").trim();
  const baseProfile = {
    telegram_user_id: uid,
    lang: defaultLang === "en" ? "en" : "ar",
    plan_tier: "free",
    expires_at: null,
    alerts_paused: false,
    watchlist: [],
    ui_state: {},
    referred_by: null,
    referral_tokens: 0,
    referral_count: 0,
    paid_referral_count: 0,
    _isNew: true,
  };
  if (!uid) return baseProfile;

  // 1. Fast path: Upstash Redis hot user profile (<50ms)
  const rawRedis = await upstashCmd(env, ["GET", `${TG_USER_REDIS_PREFIX}${uid}`], fetchImpl);
  if (typeof rawRedis === "string" && rawRedis.trim()) {
    try {
      const parsed = JSON.parse(rawRedis);
      if (parsed && typeof parsed === "object") {
        const merged = {
          ...baseProfile,
          ...parsed,
          telegram_user_id: uid,
          lang: parsed.lang === "en" ? "en" : "ar",
          plan_tier: normalizePlanTier(parsed.plan_tier),
          watchlist: Array.isArray(parsed.watchlist) ? parsed.watchlist : [],
          ui_state: parsed.ui_state && typeof parsed.ui_state === "object" ? parsed.ui_state : {},
          referred_by: parsed.referred_by ? String(parsed.referred_by) : null,
          referral_tokens: Math.max(0, Number(parsed.referral_tokens || 0)),
          referral_count: Math.max(0, Number(parsed.referral_count || 0)),
          paid_referral_count: Math.max(0, Number(parsed.paid_referral_count || 0)),
          _isNew: false,
        };
        // Enforce expiry cleanly if expires_at is in the past
        if (merged.plan_tier !== "free" && merged.expires_at && Date.parse(merged.expires_at) <= Date.now()) {
          merged.plan_tier = "free";
        }
        return merged;
      }
    } catch {
      // Fall through to Supabase
    }
  }

  // 2. Supabase fallback (`telegram_bot_users` + linked `user_tracker_profiles`)
  const supa = getSupaConfig(env);
  if (supa) {
    try {
      const [tgResp, webResp] = await Promise.all([
        fetchImpl(
          `${supa.restBase}/telegram_bot_users?telegram_user_id=eq.${encodeURIComponent(uid)}&select=*&limit=1`,
          withTimeout({ headers: supa.headers }, 4000)
        ).catch(() => null),
        fetchImpl(
          `${supa.restBase}/user_tracker_profiles?telegram_chat_id=eq.${encodeURIComponent(uid)}&select=email,plan_tier,lang,watchlist&limit=1`,
          withTimeout({ headers: supa.headers }, 4000)
        ).catch(() => null),
      ]);
      if (tgResp?.ok) {
        const rows = await tgResp.json();
        if (Array.isArray(rows) && rows.length > 0) {
          const r = rows[0];
          const prof = {
            ...baseProfile,
            lang: r.lang === "en" ? "en" : "ar",
            plan_tier: normalizePlanTier(r.plan_tier),
            expires_at: r.expires_at || null,
            alerts_paused: Boolean(r.alerts_paused),
            watchlist: Array.isArray(r.watchlist) ? r.watchlist : [],
            ui_state: r.ui_state && typeof r.ui_state === "object" ? r.ui_state : {},
            referred_by: r.referred_by ? String(r.referred_by) : null,
            referral_tokens: Math.max(0, Number(r.referral_tokens || 0)),
            referral_count: Math.max(0, Number(r.referral_count || 0)),
            paid_referral_count: Math.max(0, Number(r.paid_referral_count || 0)),
            _isNew: false,
          };
          if (prof.plan_tier !== "free" && prof.expires_at && Date.parse(prof.expires_at) <= Date.now()) {
            prof.plan_tier = "free";
          }
          return prof;
        }
      }
      if (webResp?.ok) {
        const rows = await webResp.json();
        if (Array.isArray(rows) && rows.length > 0) {
          const w = rows[0];
          return {
            ...baseProfile,
            lang: w.lang === "en" ? "en" : "ar",
            plan_tier: normalizePlanTier(w.plan_tier),
            watchlist: Array.isArray(w.watchlist) ? w.watchlist : [],
            _isNew: false,
          };
        }
      }
    } catch {
      // Keep default profile
    }
  }

  return baseProfile;
}

export async function saveBotUserProfile(profile, env, fetchImpl = fetch) {
  const uid = String(profile?.telegram_user_id || "").trim();
  if (!uid) return false;

  const clean = {
    telegram_user_id: uid,
    lang: profile.lang === "en" ? "en" : "ar",
    plan_tier: normalizePlanTier(profile.plan_tier),
    expires_at: profile.expires_at || null,
    alerts_paused: Boolean(profile.alerts_paused),
    watchlist: Array.isArray(profile.watchlist) ? profile.watchlist : [],
    ui_state: profile.ui_state && typeof profile.ui_state === "object" ? profile.ui_state : {},
    referred_by: profile.referred_by ? String(profile.referred_by) : null,
    referral_tokens: Math.max(0, Number(profile.referral_tokens || 0)),
    referral_count: Math.max(0, Number(profile.referral_count || 0)),
    paid_referral_count: Math.max(0, Number(profile.paid_referral_count || 0)),
    updated_at: new Date().toISOString(),
  };

  const ops = [
    upstashCmd(env, ["SET", `${TG_USER_REDIS_PREFIX}${uid}`, JSON.stringify(clean)], fetchImpl),
    upstashCmd(env, ["SADD", TG_USERS_INDEX_REDIS_KEY, uid], fetchImpl),
  ];

  const supa = getSupaConfig(env);
  if (supa) {
    ops.push(
      fetchImpl(
        `${supa.restBase}/telegram_bot_users?on_conflict=telegram_user_id`,
        withTimeout({
          method: "POST",
          headers: { ...supa.headers, Prefer: "resolution=merge-duplicates,return=minimal" },
          body: JSON.stringify({
            telegram_user_id: uid,
            lang: clean.lang,
            plan_tier: clean.plan_tier,
            expires_at: clean.expires_at,
            alerts_paused: clean.alerts_paused,
            watchlist: clean.watchlist,
            ui_state: clean.ui_state,
            referred_by: clean.referred_by,
            referral_tokens: clean.referral_tokens,
            referral_count: clean.referral_count,
            paid_referral_count: clean.paid_referral_count,
          }),
        }, 4000)
      ).catch(() => null)
    );
    ops.push(
      fetchImpl(
        `${supa.restBase}/user_tracker_profiles?telegram_chat_id=eq.${encodeURIComponent(uid)}`,
        withTimeout({
          method: "PATCH",
          headers: { ...supa.headers, Prefer: "return=minimal" },
          body: JSON.stringify({
            lang: clean.lang,
            watchlist: clean.watchlist,
          }),
        }, 4000)
      ).catch(() => null)
    );
  }

  await Promise.allSettled(ops);
  return true;
}

export async function checkAndIncrementVipRateLimit(userId, env, fetchImpl = fetch, nowDate = new Date()) {
  const dayTag = nowDate.toISOString().slice(0, 10);
  const key = `ratepath:vip_rl:${userId}:${dayTag}`;
  const count = await upstashCmd(env, ["INCR", key], fetchImpl);
  if (count === null) {
    return { allowed: true, count: 1, limit: VIP_DAILY_LIMIT };
  }
  const numCount = Number(count);
  if (numCount === 1) {
    await upstashCmd(env, ["EXPIRE", key, "86400"], fetchImpl);
  }
  return { allowed: numCount <= VIP_DAILY_LIMIT, count: numCount, limit: VIP_DAILY_LIMIT };
}

export async function getVipCachedBrief(symbol, lang, env, fetchImpl = fetch) {
  const key = `ratepath:vip_ask:${symbol}:${lang}`;
  const val = await upstashCmd(env, ["GET", key], fetchImpl);
  return typeof val === "string" && val.trim() ? val : null;
}

export async function setVipCachedBrief(symbol, lang, htmlText, env, fetchImpl = fetch) {
  const key = `ratepath:vip_ask:${symbol}:${lang}`;
  await upstashCmd(env, ["SET", key, htmlText, "EX", String(VIP_CACHE_TTL_SECONDS)], fetchImpl);
}

// ---------------------------------------------------------------------------
// Zero-Friction VIP Membership Gate (Admin + Telegram VIP Channels + Supabase)
// ---------------------------------------------------------------------------

export async function checkVipMembership(userId, env, fetchImpl = fetch) {
  const uidStr = String(userId || "").trim();
  if (!uidStr) return { isVip: false, planTier: "free", reason: "missing_user_id" };

  // 1. Admin bypass (supports both ADMIN_TELEGRAM_ID and legacy ADMIN_CHAT_ID)
  if (isAdminTelegramUser(uidStr, env)) {
    return { isVip: true, planTier: "monthly_personal_bot", reason: "admin" };
  }

  // 2. Supabase `public.vip_subscriptions` check (captures exact tier VIP $10 vs Pro $19!)
  const supa = getSupaConfig(env);
  if (supa) {
    try {
      const resp = await fetchImpl(
        `${supa.restBase}/vip_subscriptions?telegram_user_id=eq.${encodeURIComponent(uidStr)}&payment_status=in.(finished,confirmed,active)&select=id,plan_tier,expires_at&order=created_at.desc&limit=5`,
        withTimeout({ headers: supa.headers }, 4000)
      );
      if (resp.ok) {
        const rows = await resp.json();
        if (Array.isArray(rows) && rows.length > 0) {
          let bestTier = null;
          for (const r of rows) {
            if (r?.expires_at && Date.parse(r.expires_at) <= Date.now()) continue;
            const norm = normalizePlanTier(r?.plan_tier || "monthly_business_vip");
            if (norm === "monthly_personal_bot") {
              return { isVip: true, planTier: "monthly_personal_bot", reason: "supabase_vip_subscription" };
            }
            bestTier = "monthly_business_vip";
          }
          if (bestTier) {
            return { isVip: true, planTier: bestTier, reason: "supabase_vip_subscription" };
          }
        }
      }
    } catch {
      // Continue to Telegram channel membership fallback
    }
  }

  // 3. Telegram getChatMember on VIP AR & VIP EN / VIP_CHANNEL_ID channels
  const botToken = String(env?.TELEGRAM_BOT_TOKEN || "").trim();
  const vipChannels = [env?.TELEGRAM_VIP_CHANNEL_AR, env?.TELEGRAM_VIP_CHANNEL_EN, env?.VIP_CHANNEL_ID]
    .map((c) => String(c || "").trim())
    .filter((c, i, arr) => c && c.toLowerCase() !== "placeholder" && arr.indexOf(c) === i);

  if (botToken && vipChannels.length > 0) {
    for (const chatId of vipChannels) {
      try {
        const resp = await fetchImpl(
          `https://api.telegram.org/bot${botToken}/getChatMember?chat_id=${encodeURIComponent(chatId)}&user_id=${encodeURIComponent(uidStr)}`,
          withTimeout({}, 4000)
        );
        if (!resp.ok) continue;
        const data = await resp.json();
        const status = String(data?.result?.status || "").toLowerCase();
        if (data?.ok && VIP_MEMBER_STATUSES.has(status)) {
          return { isVip: true, planTier: "monthly_business_vip", reason: `telegram_channel:${chatId}` };
        }
      } catch {
        // Continue safely
      }
    }
  }

  return { isVip: false, planTier: "free", reason: "non_vip" };
}

// ---------------------------------------------------------------------------
// Decoupled Batch Market Feed Cache (45s In-Memory Isolate Cache)
// ---------------------------------------------------------------------------

async function getBatchMarketFeeds(env, fetchImpl = fetch) {
  const liveUrl = String(env?.LIVE_FEED_URL || LIVE_FEED_DEFAULT).trim();
  const catalogUrl = String(env?.CATALOG_FEED_URL || CATALOG_FEED_DEFAULT).trim();
  const cacheKey = `${liveUrl}|${catalogUrl}`;
  const isProdFetch = fetchImpl === fetch;

  if (
    isProdFetch &&
    _memoryFeedCache.key === cacheKey &&
    _memoryFeedCache.liveFeed &&
    Date.now() - _memoryFeedCache.fetchedAt < BATCH_FEED_CACHE_TTL_MS
  ) {
    return { liveFeed: _memoryFeedCache.liveFeed, catalogFeed: _memoryFeedCache.catalogFeed };
  }

  let liveFeed = null;
  let catalogFeed = null;
  try {
    const [rLive, rCat] = await Promise.all([
      fetchImpl(liveUrl, withTimeout({ cf: { cacheTtl: 45, cacheEverything: true } }, 5000)).catch(() => null),
      fetchImpl(catalogUrl, withTimeout({ cf: { cacheTtl: 60, cacheEverything: true } }, 5000)).catch(() => null),
    ]);
    if (rLive?.ok) liveFeed = await rLive.json();
    if (rCat?.ok) catalogFeed = await rCat.json();
  } catch {
    // Use stale cache if present
  }

  if (isProdFetch && (liveFeed || catalogFeed)) {
    _memoryFeedCache.key = cacheKey;
    _memoryFeedCache.fetchedAt = Date.now();
    _memoryFeedCache.liveFeed = liveFeed || _memoryFeedCache.liveFeed;
    _memoryFeedCache.catalogFeed = catalogFeed || _memoryFeedCache.catalogFeed;
  }

  return {
    liveFeed: liveFeed || (isProdFetch ? _memoryFeedCache.liveFeed : null),
    catalogFeed: catalogFeed || (isProdFetch ? _memoryFeedCache.catalogFeed : null),
  };
}

export async function resolveLiveAssetSnapshot(parsedAsset, env, fetchImpl = fetch) {
  const sym = parsedAsset.symbol;
  const { liveFeed, catalogFeed } = await getBatchMarketFeeds(env, fetchImpl);

  // Gold & Silver
  if (sym === "XAU") {
    const oz = liveFeed?.gold?.prices?.XAU_OZ || catalogFeed?.metals?.XAU_OZ?.price_usd;
    const g21 = liveFeed?.gold?.prices?.GRAM_21K || catalogFeed?.metals?.GRAM_21K?.price_usd;
    const g24 = liveFeed?.gold?.prices?.GRAM_24K || catalogFeed?.metals?.GRAM_24K?.price_usd;
    if (oz) {
      return {
        found: true,
        symbol: "XAU",
        numericPrice: Number(oz),
        priceFormattedEn: `$${oz} / oz (21K: $${g21 || "-"} · 24K: $${g24 || "-"})`,
        priceFormattedAr: `$${oz} للأونصة (عيار 21: $${g21 || "-"} · عيار 24: $${g24 || "-"})`,
        change24h: null,
        source: liveFeed?.gold?.source || "swissquote",
      };
    }
  }
  if (sym === "XAG") {
    const xag = catalogFeed?.metals?.XAG_OZ?.price_usd || liveFeed?.gold?.prices?.XAG_OZ;
    if (xag) {
      return {
        found: true,
        symbol: "XAG",
        numericPrice: Number(xag),
        priceFormattedEn: `$${xag} / oz`,
        priceFormattedAr: `$${xag} للأونصة`,
        change24h: null,
        source: "swissquote_xag",
      };
    }
  }

  // Fiat & SP-Today SYP
  const catFiat = catalogFeed?.fiat?.[sym];
  const liveFiat = liveFeed?.fiat?.rates?.[sym];
  if (catFiat || liveFiat) {
    const rate = catFiat?.units_per_usd || liveFiat?.units_per_usd;
    const rateNum = Number(rate);
    const validSyp = sym !== "SYP" || (Number.isFinite(rateNum) && rateNum > 50 && rateNum < 500);
    if (rate && validSyp) {
      const sypExtraAr =
        sym === "SYP" && catFiat?.buy_rate && catFiat?.sell_rate
          ? ` (شراء ${Number(catFiat.buy_rate).toFixed(2)} · مبيع ${Number(catFiat.sell_rate).toFixed(2)})`
          : "";
      const sypExtraEn =
        sym === "SYP" && catFiat?.buy_rate && catFiat?.sell_rate
          ? ` (Buy ${Number(catFiat.buy_rate).toFixed(2)} · Sell ${Number(catFiat.sell_rate).toFixed(2)})`
          : "";
      const cleanRate = sym === "SYP" ? Number(rate).toFixed(2) : String(rate);
      return {
        found: true,
        symbol: sym,
        numericPrice: Number(cleanRate),
        priceFormattedEn: sym === "SYP" ? `${cleanRate} SYP${sypExtraEn}` : `1 USD = ${cleanRate} ${sym}`,
        priceFormattedAr: sym === "SYP" ? `${cleanRate} ل.س${sypExtraAr}` : `1 دولار = ${cleanRate} ${sym}`,
        change24h: null,
        source: catFiat?.source || liveFeed?.fiat?.source || "fx_feed",
      };
    }
  }

  // Crypto in catalog_feed Top 300 or live_feed
  const liveCrypto = liveFeed?.crypto?.assets?.[sym];
  const cmcCoin = Array.isArray(catalogFeed?.crypto)
    ? catalogFeed.crypto.find((c) => String(c.symbol || "").toUpperCase() === sym)
    : null;
  if (cmcCoin || liveCrypto || sym === "USDT") {
    const price = liveCrypto?.spot_price || cmcCoin?.price_usd || "1.0000";
    const chg = cmcCoin?.change_24h_pct || null;
    return {
      found: true,
      symbol: sym,
      numericPrice: Number(price),
      name: cmcCoin?.name || parsedAsset.displayEn,
      rank: cmcCoin?.rank || null,
      priceFormattedEn: `$${price}`,
      priceFormattedAr: `$${price}`,
      change24h: chg,
      source: "coinmarketcap",
    };
  }

  // Long-tail Crypto fallback via CoinGecko
  if (parsedAsset.kind !== "stock") {
    try {
      const cgResp = await fetchImpl(
        `https://api.coingecko.com/api/v3/search?query=${encodeURIComponent(sym)}`,
        withTimeout({ headers: { Accept: "application/json", "User-Agent": "RatePath-VIPBot/2.0" } }, 5000)
      );
      if (cgResp.ok) {
        const cgData = await cgResp.json();
        const match = (cgData?.coins || []).find(
          (c) => String(c.symbol || "").toUpperCase() === sym
        ) || cgData?.coins?.[0];
        if (match?.id) {
          const pResp = await fetchImpl(
            `https://api.coingecko.com/api/v3/simple/price?ids=${encodeURIComponent(match.id)}&vs_currencies=usd&include_24hr_change=true`,
            withTimeout({}, 5000)
          );
          if (pResp.ok) {
            const pData = await pResp.json();
            const row = pData?.[match.id];
            if (row?.usd) {
              const p = Number(row.usd);
              const pStr = p >= 1 ? p.toFixed(2) : p >= 0.01 ? p.toFixed(4) : p.toFixed(6);
              const chgNum = Number(row.usd_24h_change);
              const chgStr = Number.isFinite(chgNum)
                ? `${chgNum >= 0 ? "+" : ""}${chgNum.toFixed(2)}`
                : null;
              return {
                found: true,
                symbol: String(match.symbol || sym).toUpperCase(),
                numericPrice: p,
                name: match.name || sym,
                rank: match.market_cap_rank || null,
                priceFormattedEn: `$${pStr}`,
                priceFormattedAr: `$${pStr}`,
                change24h: chgStr,
                source: "coingecko_longtail",
              };
            }
          }
        }
      }
    } catch {
      // Fall through to stock lookup
    }
  }

  // Global Stocks via Yahoo Finance chart API
  try {
    const yResp = await fetchImpl(
      `https://query1.finance.yahoo.com/v8/finance/chart/${encodeURIComponent(sym)}?range=5d&interval=1d`,
      withTimeout({ headers: { Accept: "application/json", "User-Agent": "Mozilla/5.0 RatePathBot/2.0" } }, 5000)
    );
    if (yResp.ok) {
      const yData = await yResp.json();
      const meta = yData?.chart?.result?.[0]?.meta;
      const p = Number(meta?.regularMarketPrice);
      if (Number.isFinite(p) && p > 0) {
        const prev = Number(meta?.chartPreviousClose || meta?.previousClose || p);
        const chg = prev > 0 ? ((p - prev) / prev) * 100 : 0;
        const curr = String(meta?.currency || "USD");
        const pStr = p >= 1 ? p.toFixed(2) : p.toFixed(4);
        const chgStr = `${chg >= 0 ? "+" : ""}${chg.toFixed(2)}`;
        return {
          found: true,
          symbol: String(meta?.symbol || sym).toUpperCase(),
          numericPrice: p,
          name: meta?.shortName || parsedAsset.displayEn || sym,
          rank: null,
          priceFormattedEn: `${pStr} ${curr}`,
          priceFormattedAr: `${pStr} ${curr}`,
          change24h: chgStr,
          source: "yahoo_stock",
        };
      }
    }
  } catch {
    // Graceful miss
  }

  return { found: false, symbol: sym, numericPrice: null, priceFormattedEn: "N/A", priceFormattedAr: "غير متوفر حالياً", change24h: null, source: "none" };
}

async function fetchTargetedAssetHeadlines(parsedAsset, fetchImpl = fetch) {
  const sym = parsedAsset.symbol;
  const searchTopic =
    sym === "XAU"
      ? "Gold price XAU central bank"
      : sym === "SYP"
      ? "Syrian pound SYP exchange rate economy"
      : `${parsedAsset.displayEn || sym} ${sym} market news`;

  const rssUrl = `https://news.google.com/rss/search?q=${encodeURIComponent(searchTopic)}&hl=en-US&gl=US&ceid=US:en`;
  try {
    const resp = await fetchImpl(rssUrl, withTimeout({
      headers: { "User-Agent": "RatePath-VIPBot/2.0", Accept: "application/rss+xml, text/xml" },
    }, 5000));
    if (!resp.ok) return [];
    const xml = await resp.text();
    const titles = [];
    const itemRe = /<item>[\s\S]*?<title>(?:<!\[CDATA\[)?([\s\S]*?)(?:\]\]>)?<\/title>[\s\S]*?<\/item>/gi;
    let m;
    while ((m = itemRe.exec(xml)) !== null && titles.length < 4) {
      const clean = m[1].replace(/<[^>]+>/g, " ").replace(/[ \t]+/g, " ").trim();
      if (clean.length >= 12) titles.push(clean);
    }
    return titles;
  } catch {
    return [];
  }
}

// ---------------------------------------------------------------------------
// VIP AI Briefing Generator (Groq -> Gemini Flash -> Deterministic Fallback)
// ---------------------------------------------------------------------------

function formatDirectionBadge(change24h) {
  if (!change24h || String(change24h).trim() === "0.00") return "";
  const raw = String(change24h).trim();
  const isDown = raw.startsWith("-");
  const cleanNum = raw.replace(/^[+-]/, "");
  return isDown ? ` 🔻 (-${cleanNum}%)` : ` ▲ (+${cleanNum}%)`;
}

async function generateVipAiBrief(parsedAsset, snapshot, headlines, env, fetchImpl = fetch) {
  const lang = parsedAsset.lang;
  const isAr = lang === "ar";
  const label = isAr ? parsedAsset.displayAr : parsedAsset.displayEn;
  const priceLine = isAr ? snapshot.priceFormattedAr : snapshot.priceFormattedEn;
  const chgText = formatDirectionBadge(snapshot.change24h);

  const sysPrompt = isAr
    ? [
        "أنت محلل ريت باث الذكي. قدّم إجابة فورية ومختصرة ومفيدة بلغة عربية فصحى سلسة بدون أي حشو أو تكرار كلمة VIP.",
        "استخدم التنسيق التالي حرفياً مع سطر فارغ (\\n\\n) بين كل قسم ولا تكتب أي روابط:",
        "📌 <b>السعر والزخم:</b>\n[جملة واحدة واضحة تضم السعر وحالة الصعود ▲ أو الهبوط 🔻 بدون كرات ملونة]",
        "📰 <b>أهم الأخبار المؤثرة الآن:</b>\n[نقطتان قصيرتان ومباشرتان عن السبب الحقيقي وراء الحركة]",
        "💡 <b>الخلاصة ومسار السيولة:</b>\n[جملتان عمليتان بلغة الأرقام فقط دون وعد بصعود أو هبوط مؤكد]",
        "قاعدة صارمة: الخدمة معلوماتية فقط — لا تقدّم نصيحة استثمارية ولا تكتب 'القناعة X/100' ولا 'VIP' ولا 'ذاكرة 100 عام'، ولا تكتب رمز علم سوريا الإيموجي (U+1F1F8 U+1F1FE)، واكتب 'الليرة السورية' أو 'ل.س'.",
      ].join("\n")
    : [
        "You are the RatePath market desk analyst. Write a crisp, zero-fluff briefing in clean Telegram HTML with ZERO URLs and ZERO repetitive VIP tags.",
        "Use ▲ for rising moves and 🔻 for falling moves (NO colored balls). Exact 3-block layout separated by blank lines (\\n\\n):",
        "📌 <b>Price &amp; Momentum:</b>\n[1 clear sentence with live price and ▲ / 🔻 momentum]",
        "📰 <b>Key Drivers Now:</b>\n[2 crisp bullet points explaining the real news catalyst]",
        "💡 <b>Liquidity Takeaway:</b>\n[1-2 plain data-driven sentences — never guarantee rises or falls]",
        "STRICT RULE: Informational data analytics ONLY (not financial advice). NEVER output 'Conviction X/100', '100-year memory', or raw Unicode Syria flag emoji (U+1F1F8 U+1F1FE); always use 'SYP'.",
      ].join("\n");

  const usrPrompt = JSON.stringify({
    asset: label,
    symbol: snapshot.symbol,
    live_price: `${priceLine}${chgText}`,
    rank: snapshot.rank || null,
    recent_headlines: headlines,
  });

  const groqKeys = [env?.GROQ_API_KEY_1, env?.GROQ_API_KEY_2, env?.GROQ_API_KEY_3, env?.GROQ_API_KEY]
    .map((k) => String(k || "").trim())
    .filter((k, i, arr) => k && k.toLowerCase() !== "placeholder" && arr.indexOf(k) === i);
  for (const groqKey of groqKeys) {
    for (const modelName of ["llama-3.3-70b-versatile", "openai/gpt-oss-120b"]) {
      try {
        const resp = await fetchImpl(
          "https://api.groq.com/openai/v1/chat/completions",
          withTimeout({
            method: "POST",
            headers: {
              Authorization: `Bearer ${groqKey}`,
              "Content-Type": "application/json",
            },
            body: JSON.stringify({
              model: modelName,
              temperature: 0.3,
              max_tokens: 450,
              messages: [
                { role: "system", content: sysPrompt },
                { role: "user", content: usrPrompt },
              ],
            }),
          })
        );
        if (!resp.ok) continue;
        const data = await resp.json();
        const content = data?.choices?.[0]?.message?.content;
        if (typeof content === "string" && content.trim().length > 40) {
          return sanitizeTelegramHtml(sanitizeSyriaEmoji(normalizePreserveNewlines(content), lang));
        }
      } catch {
        // Try next key/model
      }
    }
  }

  const geminiKeys = [env?.GEMINI_API_KEY_1, env?.GEMINI_API_KEY_2, env?.GEMINI_API_KEY_3, env?.GEMINI_API_KEY]
    .map((k) => String(k || "").trim())
    .filter((k, i, arr) => k && k.toLowerCase() !== "placeholder" && arr.indexOf(k) === i);
  for (const geminiKey of geminiKeys) {
    for (const gModel of ["gemini-3.5-flash-lite", "gemini-2.5-flash"]) {
      try {
        const resp = await fetchImpl(
          `https://generativelanguage.googleapis.com/v1beta/models/${gModel}:generateContent?key=${encodeURIComponent(geminiKey)}`,
          withTimeout({
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              systemInstruction: { parts: [{ text: sysPrompt }] },
              contents: [{ role: "user", parts: [{ text: usrPrompt }] }],
            }),
          })
        );
        if (!resp.ok) continue;
        const data = await resp.json();
        const textOut = data?.candidates?.[0]?.content?.parts?.[0]?.text;
        if (typeof textOut === "string" && textOut.trim().length > 40) {
          return sanitizeTelegramHtml(sanitizeSyriaEmoji(normalizePreserveNewlines(textOut), lang));
        }
      } catch {
        // Fallback
      }
    }
  }

  const assetPrefix = snapshot.symbol === "SYP" ? "🟢 " : "";
  if (isAr) {
    const newsLines =
      headlines.length > 0
        ? headlines.slice(0, 2).map((h) => `• ${escapeHtml(h)}`).join("\n")
        : "• لا توجد عناوين عاجلة استثنائية حالياً؛ الحركة تتبع السيولة العامة في السوق.";
    return sanitizeSyriaEmoji(
      normalizePreserveNewlines(
        `📌 <b>السعر والزخم:</b>\n${assetPrefix}${escapeHtml(label)}: <code>${escapeHtml(priceLine)}</code>${escapeHtml(chgText)}\n\n` +
          `📰 <b>أهم أخبار العملة الآن:</b>\n${newsLines}\n\n` +
          `💡 <b>الخلاصة واتجاه السيولة:</b>\nراقب ثبات السيولة حول المستويات الحالية وتجنّب ملاحقة الحركات السريعة قبل تأكيد الاتجاه.`
      ),
      "ar"
    );
  }

  const newsLinesEn =
    headlines.length > 0
      ? headlines.slice(0, 2).map((h) => `• ${escapeHtml(h)}`).join("\n")
      : "• No acute breaking catalyst right now; price action is tracking broader liquidity.";
  return sanitizeSyriaEmoji(
    normalizePreserveNewlines(
      `📌 <b>Price &amp; Momentum:</b>\n${assetPrefix}${escapeHtml(label)}: <code>${escapeHtml(priceLine)}</code>${escapeHtml(chgText)}\n\n` +
        `📰 <b>Latest Headlines:</b>\n${newsLinesEn}\n\n` +
        `💡 <b>Bottom Line &amp; Money Flow:</b>\nWatch whether spot demand holds current levels before scaling exposure.`
    ),
    "en"
  );
}

// ---------------------------------------------------------------------------
// AI Support Assistant, Rate Limiting & Admin Escalation Bridge
// ---------------------------------------------------------------------------

const ESCALATION_KEYWORDS_RE =
  /(paid|didn'?t activate|did not activate|not activated|payment issue|payment problem|txid|transaction hash|order\s*id|refund|talk to human|human support|real person|speak to admin|دفعت|تم الدفع|لم يتفعل|ما تفعل|مشكلة دفع|مشكله دفع|مشكلة في الدفع|تحويل للإدارة|موظف|بشري|رقم الطلب|معرف العملية|استرداد)/i;

const TXID_OR_ORDER_RE =
  /(\b(?:RPVIP|RPBOT)-[A-Z0-9-]{6,}\b|\b\d{5,20}_(?:vip10|pro19)_\d{8,}\b|\b0x[a-fA-F0-9]{32,64}\b|\b[a-fA-F0-9]{48,64}\b)/;

export function detectSupportEscalationTrigger(text) {
  const s = String(text || "").trim();
  if (!s) return { shouldEscalate: false, reason: null };
  if (TXID_OR_ORDER_RE.test(s)) return { shouldEscalate: true, reason: "order_or_txid_detected" };
  if (ESCALATION_KEYWORDS_RE.test(s)) return { shouldEscalate: true, reason: "payment_or_human_keyword" };
  return { shouldEscalate: false, reason: null };
}

export async function checkAndIncrementSupportRateLimit(userId, env, fetchImpl = fetch, nowDate = new Date()) {
  const hourTag = nowDate.toISOString().slice(0, 13).replace(/[-:T]/g, "");
  const key = `ratepath:support_rl:${userId}:${hourTag}`;
  const redisCount = await upstashCmd(env, ["INCR", key], fetchImpl);
  if (redisCount !== null) {
    const numCount = Number(redisCount);
    if (numCount === 1) {
      await upstashCmd(env, ["EXPIRE", key, "3600"], fetchImpl);
    }
    return { allowed: numCount <= SUPPORT_HOURLY_LIMIT, count: numCount, limit: SUPPORT_HOURLY_LIMIT };
  }
  const prev = _localSupportRateLimits.get(key) || 0;
  const next = prev + 1;
  _localSupportRateLimits.set(key, next);
  if (_localSupportRateLimits.size > 2000) {
    _localSupportRateLimits.clear();
  }
  return { allowed: next <= SUPPORT_HOURLY_LIMIT, count: next, limit: SUPPORT_HOURLY_LIMIT };
}

export function buildAiSupportSystemPrompt() {
  return [
    "You are the official AI Support & Troubleshooting Assistant for RatePath Terminal (@RatePathTerminalBot).",
    "",
    "EXACT RATEPATH KNOWLEDGE BASE:",
    "- Website: https://ratepathlive.com (live FX rates including USD/SYP, USD/TRY, USD/EGP, EUR, SAR, AED, Gold XAU/24K/21K/18K, Silver XAG, Crypto BTC/ETH/SOL/USDT, and 1200x630 PNG Quote Card generator).",
    "- Plans & Limits:",
    "  * Free Plan: up to 3 active assets in watchlist with live breakout/drop price alerts.",
    "  * Business VIP ($10/mo USDT): up to 10 active assets + private RatePath VIP Channel (Quant Signal Matrix + 8h Funding Carry spreads) + priority alert delivery.",
    "  * Personal Bot Pro ($19/mo USDT): up to 50 active assets + Global Stocks (AAPL, TSLA, NVDA, Aramco, etc.) & Company News alerts + full VIP Channel access + top priority.",
    "- Payments: Direct USDT via NOWPayments supporting TRC-20 (TRON), BEP-20 (BSC), TON (Telegram), Solana, and Arbitrum. No forced auto-renewal. Activation and single-use VIP Channel invite link delivery happen automatically within 1-5 minutes after blockchain confirmation.",
    "- Common Troubleshooting:",
    "  * Add/remove/replace assets: tap /start -> 'Add Asset' or 'My Watchlist' (or 'Replace an Existing Asset' if at 3/3 Free limit).",
    "  * Set target price alerts: select an asset in My Watchlist -> tap 'Set Alert' -> choose quick % (+2%/-2%/+5%/-5%) or type your exact target number.",
    "  * Switch language anytime: tap /start -> [ 🇬🇧 English ] or [ 🇸🇦 العربية ].",
    "  * Pending USDT payment: blockchain confirmations usually take 1-5 minutes; if still not active after 10 minutes, send the Order ID or TXID to escalate directly to a human admin.",
    "",
    "STRICT RULES:",
    "1. Always reply in the EXACT SAME language the user wrote in (Arabic if they write Arabic; English if they write English).",
    "2. Keep answers concise (max 4-6 short lines), friendly, clear, and action-oriented.",
    "3. NEVER give financial, trading, or investment advice. NEVER promise profits or predict guaranteed market direction.",
    "4. NEVER invent discounts, promo codes, free trials beyond the 3-asset Free plan, or unbuilt features.",
    "5. NEVER output the raw Unicode Syria flag emoji (U+1F1F8 U+1F1FE); always write '🟢 SYP' (EN) or '🟢 ل.س' (AR).",
    "6. If the user reports a payment issue, missing activation, bug, or asks for a human, tell them to share their Order ID or TXID and confirm that their case is being escalated to the human admin team.",
  ].join("\n");
}

export async function generateAiSupportReply(userText, historyTurns, replyLang, env, fetchImpl = fetch) {
  const sysPrompt = buildAiSupportSystemPrompt();
  const safeHistory = Array.isArray(historyTurns) ? historyTurns.slice(-(SUPPORT_MAX_HISTORY_TURNS * 2)) : [];
  const messages = [
    { role: "system", content: sysPrompt },
    ...safeHistory.map((m) => ({
      role: m.role === "assistant" ? "assistant" : "user",
      content: String(m.content || "").slice(0, SUPPORT_MAX_PROMPT_CHARS),
    })),
    { role: "user", content: String(userText || "").slice(0, SUPPORT_MAX_PROMPT_CHARS) },
  ];

  const deadlineMs = Date.now() + AI_SUPPORT_TIMEOUT_MS;
  const remainingMs = () => Math.max(0, deadlineMs - Date.now());

  // 1. Primary: Existing Groq Key Pool (`GROQ_API_KEY_1..3` / `GROQ_API_KEY`)
  const groqKeys = [env?.GROQ_API_KEY_1, env?.GROQ_API_KEY_2, env?.GROQ_API_KEY_3, env?.GROQ_API_KEY]
    .map((k) => String(k || "").trim())
    .filter((k, i, arr) => k && k.toLowerCase() !== "placeholder" && arr.indexOf(k) === i);
  for (const groqKey of groqKeys) {
    if (remainingMs() < 900) break;
    for (const modelName of ["llama-3.3-70b-versatile", "openai/gpt-oss-120b"]) {
      const budget = remainingMs();
      if (budget < 900) break;
      try {
        const resp = await fetchImpl(
          "https://api.groq.com/openai/v1/chat/completions",
          withTimeout({
            method: "POST",
            headers: {
              Authorization: `Bearer ${groqKey}`,
              "Content-Type": "application/json",
            },
            body: JSON.stringify({
              model: modelName,
              temperature: 0.25,
              max_tokens: 320,
              messages,
            }),
          }, Math.min(budget, 5000))
        );
        if (!resp.ok) continue;
        const data = await resp.json();
        const content = data?.choices?.[0]?.message?.content;
        if (typeof content === "string" && content.trim().length > 10) {
          return {
            ok: true,
            provider: "groq",
            text: sanitizeTelegramHtml(sanitizeSyriaEmoji(normalizePreserveNewlines(content), replyLang)),
          };
        }
      } catch {
        // Fail over cleanly to next key/provider
      }
    }
  }

  // 2. Secondary: Existing Gemini Key Pool (`GEMINI_API_KEY_1..3` / `GEMINI_API_KEY`)
  const geminiKeys = [env?.GEMINI_API_KEY_1, env?.GEMINI_API_KEY_2, env?.GEMINI_API_KEY_3, env?.GEMINI_API_KEY]
    .map((k) => String(k || "").trim())
    .filter((k, i, arr) => k && k.toLowerCase() !== "placeholder" && arr.indexOf(k) === i);
  for (const geminiKey of geminiKeys) {
    if (remainingMs() < 900) break;
    for (const gModel of ["gemini-2.5-flash", "gemini-3.5-flash-lite"]) {
      const budget = remainingMs();
      if (budget < 900) break;
      try {
        const contents = messages
          .filter((m) => m.role !== "system")
          .map((m) => ({
            role: m.role === "assistant" ? "model" : "user",
            parts: [{ text: m.content }],
          }));
        const resp = await fetchImpl(
          `https://generativelanguage.googleapis.com/v1beta/models/${gModel}:generateContent?key=${encodeURIComponent(geminiKey)}`,
          withTimeout({
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              systemInstruction: { parts: [{ text: sysPrompt }] },
              contents,
            }),
          }, Math.min(budget, 5000))
        );
        if (!resp.ok) continue;
        const data = await resp.json();
        const textOut = data?.candidates?.[0]?.content?.parts?.[0]?.text;
        if (typeof textOut === "string" && textOut.trim().length > 10) {
          return {
            ok: true,
            provider: "gemini",
            text: sanitizeTelegramHtml(sanitizeSyriaEmoji(normalizePreserveNewlines(textOut), replyLang)),
          };
        }
      } catch {
        // Fail over to OpenRouter
      }
    }
  }

  // 3. Tertiary: Existing OpenRouter Key (`OPENROUTER_API_KEY`)
  const orKey = String(env?.OPENROUTER_API_KEY || "").trim();
  if (orKey && orKey.toLowerCase() !== "placeholder" && remainingMs() >= 900) {
    try {
      const resp = await fetchImpl(
        "https://openrouter.ai/api/v1/chat/completions",
        withTimeout({
          method: "POST",
          headers: {
            Authorization: `Bearer ${orKey}`,
            "Content-Type": "application/json",
            "HTTP-Referer": "https://ratepathlive.com",
            "X-Title": "RatePath Support Bot",
          },
          body: JSON.stringify({
            model: "google/gemini-2.0-flash-exp:free",
            temperature: 0.25,
            max_tokens: 320,
            messages,
          }),
        }, Math.min(remainingMs(), 5000))
      );
      if (resp.ok) {
        const data = await resp.json();
        const content = data?.choices?.[0]?.message?.content;
        if (typeof content === "string" && content.trim().length > 10) {
          return {
            ok: true,
            provider: "openrouter",
            text: sanitizeTelegramHtml(sanitizeSyriaEmoji(normalizePreserveNewlines(content), replyLang)),
          };
        }
      }
    } catch {
      // Fall through to admin escalation fallback
    }
  }

  return { ok: false, provider: "none", text: "" };
}

export async function forwardTicketToAdmin({
  userId,
  username,
  profile,
  userMessage,
  reason,
  env,
  fetchImpl = fetch,
}) {
  const adminId = resolveAdminTelegramId(env);
  if (!adminId) return { forwarded: false, reason: "missing_admin_id" };

  const tierLabel = getTierMeta(profile?.plan_tier).labelEn;
  const langCode = (profile?.lang || "ar").toUpperCase();
  const handleStr = username ? `@${escapeHtml(username)}` : "N/A";
  const ticketHtml =
    `🎫 <b>RatePath Support Ticket</b>\n\n` +
    `• <b>User ID:</b> <code>${escapeHtml(String(userId))}</code>\n` +
    `• <b>Username:</b> ${handleStr}\n` +
    `• <b>Current Tier:</b> <code>${escapeHtml(tierLabel)}</code>\n` +
    `• <b>Language:</b> <code>${escapeHtml(langCode)}</code>\n` +
    `• <b>Trigger:</b> <code>${escapeHtml(reason || "user_request")}</code>\n\n` +
    `💬 <b>Message:</b>\n${escapeHtml(String(userMessage || "(No message text)").slice(0, 1000))}\n\n` +
    `↩️ <i>Reply directly to this message to send your answer back to the user.</i>`;

  const sendRes = await sendTelegramReply(adminId, ticketHtml, env, fetchImpl);
  const adminMsgId = sendRes?.data?.result?.message_id;
  if (sendRes?.ok && adminMsgId) {
    const ticketMeta = JSON.stringify({
      userId: String(userId),
      lang: profile?.lang === "en" ? "en" : "ar",
      createdAt: new Date().toISOString(),
    });
    _localSupportTickets.set(String(adminMsgId), ticketMeta);
    await upstashCmd(
      env,
      ["SET", `${SUPPORT_TICKET_REDIS_PREFIX}${adminMsgId}`, ticketMeta, "EX", "604800"],
      fetchImpl
    );
  }
  return { forwarded: Boolean(sendRes?.ok), adminMsgId: adminMsgId || null };
}

async function handleAdminTicketReply(msg, env, fetchImpl = fetch) {
  if (!isAdminTelegramUser(msg?.from?.id, env)) return null;
  const adminChatId = String(msg?.chat?.id || msg?.from?.id || "");
  const replied = msg?.reply_to_message;
  if (!replied) return null;

  const replyText = String(msg.text || "").trim();
  if (!replyText) return null;

  // 1. Lookup target user from Redis / memory ticket map by replied message_id
  let targetUserId = null;
  let targetLang = detectQueryLang(replyText, "ar");
  const repliedId = String(replied.message_id || "");
  if (repliedId) {
    const rawTicket =
      (await upstashCmd(env, ["GET", `${SUPPORT_TICKET_REDIS_PREFIX}${repliedId}`], fetchImpl)) ||
      _localSupportTickets.get(repliedId) ||
      null;
    if (typeof rawTicket === "string" && rawTicket.trim()) {
      try {
        const parsed = JSON.parse(rawTicket);
        if (parsed?.userId) targetUserId = String(parsed.userId);
        if (parsed?.lang) targetLang = parsed.lang === "en" ? "en" : "ar";
      } catch {
        // Fallback to regex parsing below
      }
    }
  }

  // 2. Resilient regex fallback directly from forwarded ticket text (`User ID: 123456`)
  if (!targetUserId) {
    const m = String(replied.text || "").match(/User ID:\s*(\d{5,20})/i);
    if (m) targetUserId = m[1];
  }
  if (!targetUserId) return null;

  const isAr = targetLang === "ar";
  const deliveredHtml = isAr
    ? `👨‍💻 <b>رد فريق دعم RatePath:</b>\n\n${escapeHtml(replyText)}\n\n<i>يمكنك الرد مباشرة هنا إذا احتجت أي مساعدة إضافية.</i>`
    : `👨‍💻 <b>RatePath Support Team Reply:</b>\n\n${escapeHtml(replyText)}\n\n<i>You can reply right here if you need anything else.</i>`;

  const userRes = await sendTelegramReply(targetUserId, deliveredHtml, env, fetchImpl, buildSupportModeKeyboard(targetLang));
  const confirmAdmin = userRes?.ok
    ? `✅ Reply delivered to user <code>${escapeHtml(targetUserId)}</code>.`
    : `⚠️ Could not deliver reply to user <code>${escapeHtml(targetUserId)}</code> (blocked or unreachable).`;
  await sendTelegramReply(adminChatId, confirmAdmin, env, fetchImpl);
  return { handled: true, mode: "admin_reply_bridged", targetUserId, delivered: Boolean(userRes?.ok) };
}

async function handleSupportUserMessage({ msg, chatId, userId, rawText, profile, env, fetchImpl = fetch }) {
  const replyLang = detectQueryLang(rawText, profile.lang);
  const isAr = replyLang === "ar";

  // 1. Prompt length abuse protection (max 500 chars)
  if (rawText.length > SUPPORT_MAX_PROMPT_CHARS) {
    const tooLongText = isAr
      ? `⚠️ رسالتك طويلة جداً (${rawText.length} حرفاً). يرجى اختصار سؤالك إلى أقل من <b>${SUPPORT_MAX_PROMPT_CHARS} حرف</b> لكي أساعدك فوراً.`
      : `⚠️ Your message is too long (${rawText.length} characters). Please keep it under <b>${SUPPORT_MAX_PROMPT_CHARS} characters</b> so I can help right away.`;
    await sendTelegramReply(chatId, tooLongText, env, fetchImpl, buildSupportModeKeyboard(replyLang));
    return { handled: true, mode: "support_prompt_too_long", length: rawText.length, maxChars: SUPPORT_MAX_PROMPT_CHARS };
  }

  // 2. Auto-escalation trigger (TXID / Order ID / payment & human keywords / active escalated session)
  const esc = detectSupportEscalationTrigger(rawText);
  if (esc.shouldEscalate || profile.ui_state?.escalated === true) {
    const reason = esc.reason || "follow_up_after_escalation";
    const ticketRes = await forwardTicketToAdmin({
      userId,
      username: msg.from?.username || "",
      profile,
      userMessage: rawText,
      reason,
      env,
      fetchImpl,
    });
    const prevHist = Array.isArray(profile.ui_state?.history) ? profile.ui_state.history : [];
    profile.ui_state = {
      mode: "support",
      escalated: true,
      history: prevHist.slice(-(SUPPORT_MAX_HISTORY_TURNS * 2)),
    };
    await saveBotUserProfile(profile, env, fetchImpl);

    const escReply =
      reason === "order_or_txid_detected"
        ? isAr
          ? `✅ <b>تم استلام رقم الطلب / معرّف العملية وتحويل تذكرتك للإدارة فوراً!</b>\n\nسيقوم المشرف بمراجعة معاملتك والرد عليك مباشرة في هذه المحادثة.`
          : `✅ <b>Order ID / TXID received and forwarded to our admin team!</b>\n\nAn admin will review your transaction and reply to you directly in this chat.`
        : isAr
        ? `👤 <b>تم تحويل رسالتك إلى فريق الدعم البشري بنجاح.</b>\n\nلتسريع التحقق من الدفع أو التفعيل، يرجى إرسال <b>رقم الطلب (Order ID)</b> أو <b>معرّف العملية (TXID)</b> هنا مباشرة وسيرد عليك المشرف في نفس هذه المحادثة.`
        : `👤 <b>Your message has been forwarded to our human support team.</b>\n\nTo speed up payment or activation verification, please share your <b>Order ID</b> or <b>TXID</b> right here and an admin will reply directly in this chat.`;

    await sendTelegramReply(chatId, escReply, env, fetchImpl, {
      inline_keyboard: [[{ text: isAr ? "❌ إنهاء الدعم" : "❌ Exit Support", callback_data: "cb:support_exit" }]],
    });
    return {
      handled: true,
      mode: "support_escalated",
      reason,
      forwarded: ticketRes.forwarded,
      adminMsgId: ticketRes.adminMsgId,
    };
  }

  // 3. Hourly abuse protection rate limit (max 8 AI support messages/hour/user)
  const rl = await checkAndIncrementSupportRateLimit(userId, env, fetchImpl);
  if (!rl.allowed) {
    const rlText = isAr
      ? `⏳ وصلت للحد المسموح من رسائل الدعم الذكي (${rl.limit} رسائل في الساعة) لحماية سرعة الخدمة.\n\nيمكنك الضغط على «👤 تحويل للإدارة» للتواصل المباشر أو المحاولة بعد ساعة.`
      : `⏳ You've reached the hourly AI support limit (${rl.limit} messages/hour) to protect response speed.\n\nTap «👤 Talk to Human» to reach our admin team directly, or try again next hour.`;
    await sendTelegramReply(chatId, rlText, env, fetchImpl, buildSupportModeKeyboard(replyLang));
    return { handled: true, mode: "support_rate_limited", count: rl.count, limit: rl.limit };
  }

  // 4. Generate AI Support response using existing project AI providers & 4-turn rolling history
  const prevHistory = Array.isArray(profile.ui_state?.history) ? profile.ui_state.history : [];
  const aiRes = await generateAiSupportReply(rawText, prevHistory, replyLang, env, fetchImpl);

  if (aiRes.ok && aiRes.text) {
    const updatedHistory = [
      ...prevHistory,
      { role: "user", content: rawText },
      { role: "assistant", content: aiRes.text },
    ].slice(-(SUPPORT_MAX_HISTORY_TURNS * 2));
    profile.ui_state = {
      mode: "support",
      escalated: false,
      history: updatedHistory,
    };
    await saveBotUserProfile(profile, env, fetchImpl);
    await sendTelegramReply(chatId, aiRes.text, env, fetchImpl, buildSupportModeKeyboard(replyLang));
    return {
      handled: true,
      mode: "support_ai_reply",
      provider: aiRes.provider,
      lang: replyLang,
      historyMessages: updatedHistory.length,
    };
  }

  // 5. Safe Fallback: AI failed or timed out (>8s) -> auto-forward to human admin without crashing
  const fallbackTicket = await forwardTicketToAdmin({
    userId,
    username: msg.from?.username || "",
    profile,
    userMessage: rawText,
    reason: "ai_timeout_or_failure",
    env,
    fetchImpl,
  });
  const fallbackText = isAr
    ? `⏳ <b>المساعد الذكي مشغول حالياً — تم تحويل رسالتك تلقائياً إلى فريق الدعم البشري.</b>\n\nسيقوم المشرف بالرد عليك هنا في أقرب وقت ممكن (إذا كان استفسارك حول دفع USDT يرجى إرسال رقم الطلب أو TXID).`
    : `⏳ <b>AI support is temporarily unavailable — your message has been forwarded automatically to our human support team.</b>\n\nAn admin will reply right here shortly (if this is about a USDT payment, please share your Order ID or TXID).`;
  await sendTelegramReply(chatId, fallbackText, env, fetchImpl, {
    inline_keyboard: [[{ text: isAr ? "❌ إنهاء الدعم" : "❌ Exit Support", callback_data: "cb:support_exit" }]],
  });
  return {
    handled: true,
    mode: "support_ai_fallback_escalated",
    forwarded: fallbackTicket.forwarded,
    adminMsgId: fallbackTicket.adminMsgId,
  };
}

function buildSupportModeKeyboard(lang) {
  const isAr = lang !== "en";
  return {
    inline_keyboard: [
      [
        { text: isAr ? "👤 تحويل للإدارة" : "👤 Talk to Human", callback_data: "cb:support_human" },
        { text: isAr ? "❌ إنهاء الدعم" : "❌ Exit Support", callback_data: "cb:support_exit" },
      ],
    ],
  };
}

export function buildSupportGreetingView(lang) {
  const isAr = lang !== "en";
  const text = isAr
    ? `💬 <b>الدعم الذكي — RatePath Terminal</b>\n\nأهلاً بك! اسألني أي سؤال عن:\n• إضافة العملات وضبط تنبيهات الأسعار\n• باقات VIP (10$) و Pro (19$) والدفع بـ USDT\n• التحقق من تفعيل الاشتراك أو حل أي مشكلة\n\n✏️ اكتب سؤالك الآن وسأجيبك فوراً (أو اضغط «تحويل للإدارة» للتواصل مع الدعم البشري).`
    : `💬 <b>AI Support — RatePath Terminal</b>\n\nHi! Ask me anything about:\n• Adding assets &amp; setting instant breakout alerts\n• Business VIP ($10/mo), Pro ($19/mo) &amp; USDT payments\n• Verifying an activation or troubleshooting\n\n✏️ Type your question below for an instant answer (or tap «Talk to Human» to reach our admin team).`;
  return {
    text,
    replyMarkup: buildSupportModeKeyboard(isAr ? "ar" : "en"),
  };
}

// ---------------------------------------------------------------------------
// Bilingual Conversion Views & Inline Keyboards (AR + EN Exact Copy)
// ---------------------------------------------------------------------------

export function buildWelcomeView(profile, publicSiteUrl = "https://ratepathlive.com") {
  const siteUrl = String(publicSiteUrl || "https://ratepathlive.com").replace(/\/+$/, "");
  const lang = profile?.lang === "en" ? "en" : "ar";
  const tierMeta = getTierMeta(profile?.plan_tier);
  const currentCount = Array.isArray(profile?.watchlist) ? profile.watchlist.length : 0;
  const maxLimit = getEffectiveMaxAssets(profile);
  const isFree = normalizePlanTier(profile?.plan_tier) === "free";

  if (lang === "ar") {
    const planLabel = isFree ? "المجانية" : tierMeta.labelAr;
    const text =
      `أهلاً بك في RatePath Terminal ⚡\n\n` +
      `منصّتك الفورية لمتابعة أسعار الصرف، الذهب، والكريبتو وتلقي تنبيهات الأسعار مباشرة على تيليجرام — بدون ضوضاء أو إعلانات مزعجة.\n\n` +
      `📊 باقتك الحالية: ${planLabel} (${currentCount} / ${maxLimit} أصول)\n\n` +
      `ابدأ الآن بضغطة واحدة:\n` +
      `• اختر 3 أصول مجانية لمتابعتها (مثل: الذهب XAU، البيتكوين BTC، الدولار مقابل الليرة).\n` +
      `• اضبط السعر المستهدف ليصلك تنبيه فوري لحظة الاختراق أو الهبوط.\n\n` +
      `🌐 لوحة الأسعار والمحوّل المباشر:\n` +
      `${siteUrl}`;

    const addBtnLabel =
      currentCount === 0
        ? "➕ إضافة أول أصل (مجاني)"
        : `➕ إضافة أصل جديد (${currentCount}/${maxLimit})`;

    return {
      text,
      replyMarkup: {
        inline_keyboard: [
          [{ text: addBtnLabel, callback_data: "cb:add_menu" }],
          [
            { text: `📊 قائمة متابعتي (${currentCount}/${maxLimit})`, callback_data: "cb:watchlist" },
            { text: "🌐 فتح الموقع المباشر", url: siteUrl },
          ],
          [
            { text: "💎 مزايا باقة VIP ($10/شهر)", callback_data: "cb:upgrade_vip" },
            { text: "💬 الدعم الذكي", callback_data: "cb:support" },
          ],
          [{ text: "🇬🇧 English", callback_data: "cb:lang:en" }],
          [{ text: "🎁 ادعُ صديقاً واربح", callback_data: "cb:referral" }],
        ],
      },
    };
  }

  const planLabelEn = isFree ? "Free" : tierMeta.labelEn;
  const textEn =
    `Welcome to RatePath Terminal ⚡\n\n` +
    `Your real-time hub for FX rates, gold, and crypto with instant Telegram price alerts — zero noise, zero pop-ups.\n\n` +
    `📊 Current Plan: ${planLabelEn} (${currentCount} / ${maxLimit} assets)\n\n` +
    `Get started in one tap:\n` +
    `• Pick 3 free assets to track (e.g., Gold XAU, Bitcoin BTC, USD/TRY, USD/SYP, USD/EGP).\n` +
    `• Set a target price to get an instant alert the moment it breaks above or drops below.\n\n` +
    `🌐 Live Terminal & Instant Converter:\n` +
    `${siteUrl}`;

  const addBtnLabelEn =
    currentCount === 0
      ? "➕ Add First Asset (Free)"
      : `➕ Add New Asset (${currentCount}/${maxLimit})`;

  return {
    text: textEn,
    replyMarkup: {
      inline_keyboard: [
        [{ text: addBtnLabelEn, callback_data: "cb:add_menu" }],
        [
          { text: `📊 My Watchlist (${currentCount}/${maxLimit})`, callback_data: "cb:watchlist" },
          { text: "🌐 Open Live Site", url: siteUrl },
        ],
        [
          { text: "💎 VIP Plans ($10/mo)", callback_data: "cb:upgrade_vip" },
          { text: "💬 AI Support", callback_data: "cb:support" },
        ],
        [{ text: "🇸🇦 العربية", callback_data: "cb:lang:ar" }],
        [{ text: "🎁 Invite & Earn", callback_data: "cb:referral" }],
      ],
    },
  };
}

export function buildReferralView(profile, botUsername = "RatePathTerminalBot") {
  const isAr = profile?.lang !== "en";
  const uid = String(profile?.telegram_user_id || "").trim();
  const cleanBot = String(botUsername || "RatePathTerminalBot").replace(/^@/, "");
  const refLink = `https://t.me/${cleanBot}?start=ref_${uid}`;
  const tokens = Math.max(0, Number(profile?.referral_tokens || 0));
  const refCount = Math.max(0, Number(profile?.referral_count || 0));
  const paidRefs = Math.max(0, Number(profile?.paid_referral_count || 0));
  const bonusSlots = getReferralBonusSlots(profile);
  const currentFreeCap = TIER_LIMITS.free.maxAssets + bonusSlots;

  const shareTextAr = encodeURIComponent(
    "⚡ تابع أسعار الدولار (سوريا/تركيا/مصر) والذهب والبيتكوين مع تنبيهات تيليجرام مجانية عبر RatePath:"
  );
  const shareTextEn = encodeURIComponent(
    "⚡ Track live FX rates, Gold XAU & Crypto with free instant Telegram breakout alerts on RatePath:"
  );
  const shareUrl = `https://t.me/share/url?url=${encodeURIComponent(refLink)}&text=${isAr ? shareTextAr : shareTextEn}`;

  const rows = [
    [{ text: isAr ? "📤 مشاركة رابط الدعوة فوراً" : "📤 Share Invite Link Now", url: shareUrl }],
  ];
  if (tokens >= REFERRAL_REDEEM_COST_TOKENS) {
    rows.push([
      {
        text: isAr
          ? `💎 استبدال ${REFERRAL_REDEEM_COST_TOKENS} نقطة = ${REFERRAL_REDEEM_VIP_DAYS} أيام VIP مجاناً`
          : `💎 Redeem ${REFERRAL_REDEEM_COST_TOKENS} Tokens → ${REFERRAL_REDEEM_VIP_DAYS} Free VIP Days`,
        callback_data: "cb:ref_redeem",
      },
    ]);
  }
  rows.push([{ text: isAr ? "🔙 الرئيسية" : "🔙 Home", callback_data: "cb:home" }]);

  const text = isAr
    ? `🎁 <b>برنامج الدعوات والمكافآت — RatePath</b>\n\n` +
      `شارك رابطك الخاص مع أصدقائك واربح مكافآت فورية:\n` +
      `• <b>+${REFERRAL_JOIN_TOKENS} نقطة</b> لكل صديق جديد ينضم عبر رابطك.\n` +
      `• <b>+1 خانة مجانية إضافية</b> في قائمة متابعتك لكل صديقين (حتى <b>6 أصول مجانية</b>).\n` +
      `• <b>+${REFERRAL_PAID_UPGRADE_TOKENS} نقطة</b> فور ترقية صديقك لباقة VIP أو Pro (كل 100 نقطة = <b>${REFERRAL_REDEEM_VIP_DAYS} أيام VIP مجاناً</b>!).\n\n` +
      `📊 <b>إحصائياتك الحالية:</b>\n` +
      `• رصيد النقاط: <code>${tokens}</code> نقطة\n` +
      `• الأصدقاء المدعوون: <code>${refCount}</code> (ترقيات مدفوعة: <code>${paidRefs}</code>)\n` +
      `• خاناتك المجانية المفتوحة: <code>${currentFreeCap} / 6</code> (+${bonusSlots} إضافية)\n\n` +
      `🔗 <b>رابط دعوتك الشخصي (اضغط للنسخ):</b>\n<code>${escapeHtml(refLink)}</code>`
    : `🎁 <b>Invite &amp; Earn — RatePath Referral Program</b>\n\n` +
      `Share your personal invite link and earn instant rewards:\n` +
      `• <b>+${REFERRAL_JOIN_TOKENS} Referral Tokens</b> for every new friend who joins via your link.\n` +
      `• <b>+1 Extra Free Watchlist Slot</b> for every 2 invited friends (up to <b>6 free slots</b>).\n` +
      `• <b>+${REFERRAL_PAID_UPGRADE_TOKENS} Referral Tokens</b> whenever an invited friend upgrades to VIP or Pro (100 tokens = <b>${REFERRAL_REDEEM_VIP_DAYS} Free VIP Days</b>!).\n\n` +
      `📊 <b>Your Referral Stats:</b>\n` +
      `• Token Balance: <code>${tokens}</code> tokens\n` +
      `• Friends Invited: <code>${refCount}</code> (Paid upgrades: <code>${paidRefs}</code>)\n` +
      `• Unlocked Free Watchlist Slots: <code>${currentFreeCap} / 6</code> (+${bonusSlots} bonus)\n\n` +
      `🔗 <b>Your Personal Invite Link (tap to copy):</b>\n<code>${escapeHtml(refLink)}</code>`;

  return { text, refLink, replyMarkup: { inline_keyboard: rows } };
}

export async function processReferralJoin({
  newUserId,
  newUsername,
  referrerId,
  profile,
  env,
  fetchImpl = fetch,
}) {
  const uidStr = String(newUserId || "").trim();
  const refStr = String(referrerId || "").trim();
  if (!uidStr || !refStr || !/^\d{5,20}$/.test(refStr)) {
    return { credited: false, reason: "invalid_referrer_id" };
  }
  // Anti-self-referral check
  if (uidStr === refStr) {
    return { credited: false, reason: "self_referral_blocked" };
  }
  // Anti-duplicate check (already referred, already existing user, or claim key exists)
  const claimKey = `${REF_CLAIMED_REDIS_PREFIX}${uidStr}`;
  const existingClaim =
    (await upstashCmd(env, ["GET", claimKey], fetchImpl)) || _localRefClaims.get(uidStr) || null;
  if (existingClaim || profile?.referred_by || profile?._isNew === false) {
    return { credited: false, reason: "duplicate_or_existing_user" };
  }

  // Record referral claim atomically before awarding points
  _localRefClaims.set(uidStr, refStr);
  await upstashCmd(env, ["SET", claimKey, refStr], fetchImpl);
  profile.referred_by = refStr;

  const referrerProfile = await loadBotUserProfile(refStr, env, fetchImpl, "ar");
  const prevBonus = getReferralBonusSlots(referrerProfile);
  referrerProfile.referral_count = Math.max(0, Number(referrerProfile.referral_count || 0)) + 1;
  referrerProfile.referral_tokens =
    Math.max(0, Number(referrerProfile.referral_tokens || 0)) + REFERRAL_JOIN_TOKENS;
  const nextBonus = getReferralBonusSlots(referrerProfile);
  const slotUnlocked = nextBonus > prevBonus;
  const newFreeLimit = TIER_LIMITS.free.maxAssets + nextBonus;

  await saveBotUserProfile(referrerProfile, env, fetchImpl);

  // Best-effort Supabase referral_events ledger insert
  const supa = getSupaConfig(env);
  if (supa) {
    await fetchImpl(
      `${supa.restBase}/referral_events`,
      withTimeout({
        method: "POST",
        headers: { ...supa.headers, Prefer: "return=minimal" },
        body: JSON.stringify({
          referrer_user_id: refStr,
          referred_user_id: uidStr,
          event_type: "signup",
          tokens_delta: REFERRAL_JOIN_TOKENS,
        }),
      }, 4000)
    ).catch(() => null);
  }

  // Instant celebratory notification to referrer
  const refIsAr = referrerProfile.lang !== "en";
  const friendLabel = newUsername ? `@${escapeHtml(newUsername)}` : refIsAr ? "صديق جديد" : "a new friend";
  const slotLineAr = slotUnlocked
    ? `\n🔓 <b>مكافأة إضافية:</b> فُتحت لك <b>خانة مجانية جديدة</b>! حدّك المجاني الآن <b>${newFreeLimit} أصول</b>.`
    : "";
  const slotLineEn = slotUnlocked
    ? `\n🔓 <b>Bonus Unlocked:</b> You just unlocked <b>+1 extra free watchlist slot</b>! Your free limit is now <b>${newFreeLimit} assets</b>.`
    : "";

  const notifyHtml = refIsAr
    ? `🎉 <b>مبروك! انضم ${friendLabel} عبر رابط دعوتك!</b>\n\n` +
      `🪙 حصلت فوراً على <b>+${REFERRAL_JOIN_TOKENS} نقطة مكافآت</b>.\n` +
      `📊 رصيدك الآن: <code>${referrerProfile.referral_tokens}</code> نقطة • إجمالي المدعوين: <code>${referrerProfile.referral_count}</code>` +
      `${slotLineAr}\n\n` +
      `💡 إذا قام صديقك بالترقية لباقة VIP أو Pro ستحصل تلقائياً على <b>+${REFERRAL_PAID_UPGRADE_TOKENS} نقطة إضافية</b> (= ${REFERRAL_REDEEM_VIP_DAYS} أيام VIP مجاناً)!`
    : `🎉 <b>Congrats! ${friendLabel} just joined via your invite link!</b>\n\n` +
      `🪙 You earned <b>+${REFERRAL_JOIN_TOKENS} referral tokens</b> instantly.\n` +
      `📊 New Balance: <code>${referrerProfile.referral_tokens}</code> tokens • Friends Invited: <code>${referrerProfile.referral_count}</code>` +
      `${slotLineEn}\n\n` +
      `💡 If they upgrade to Business VIP or Pro, you'll automatically earn <b>+${REFERRAL_PAID_UPGRADE_TOKENS} bonus tokens</b> (= ${REFERRAL_REDEEM_VIP_DAYS} Free VIP Days)!`;

  await sendTelegramReply(refStr, notifyHtml, env, fetchImpl, {
    inline_keyboard: [
      [{ text: refIsAr ? "🎁 لوحة الدعوات ومشاركة الرابط" : "🎁 Open Invite Dashboard", callback_data: "cb:referral" }],
    ],
  });

  return {
    credited: true,
    referrerId: refStr,
    tokensAwarded: REFERRAL_JOIN_TOKENS,
    referrerTokens: referrerProfile.referral_tokens,
    referrerCount: referrerProfile.referral_count,
    slotUnlocked,
    newFreeLimit,
  };
}

export function buildWelcomeMessage(lang, isVip) {
  const view = buildWelcomeView({
    lang: lang === "en" ? "en" : "ar",
    plan_tier: isVip ? "monthly_business_vip" : "free",
    watchlist: [],
  });
  return view.text;
}

export function buildFreeLimitPaywallView(profile) {
  const lang = profile?.lang === "en" ? "en" : "ar";
  const wl = Array.isArray(profile?.watchlist) ? profile.watchlist : [];
  const maxLim = getEffectiveMaxAssets(profile);
  const labelOf = (idx) => {
    const it = wl[idx];
    if (!it) return "—";
    const nm = lang === "ar" ? it.name_ar || it.symbol : it.name_en || it.symbol;
    return nm.includes(it.symbol) ? nm : `${nm} (${it.symbol})`;
  };
  const activeLines = wl
    .slice(0, Math.max(3, maxLim))
    .map((_it, idx) => `${idx === 0 ? "1️⃣" : idx === 1 ? "2️⃣" : idx === 2 ? "3️⃣" : `${idx + 1}.`} ${labelOf(idx)}`)
    .join("\n");

  if (lang === "ar") {
    const text =
      `🔒 وصلت للحد الأقصى للباقة المجانية (${wl.length} / ${maxLim} أصول)\n\n` +
      `الأصول النشطة في قائمتك حالياً:\n` +
      `${activeLines}\n\n` +
      `تحتاج لمتابعة المزيد من العملات، الذهب، أو الكريبتو؟\n` +
      `بترقية حسابك إلى باقة Business VIP (+ البوت الشخصي) ستحصل فوراً على:\n\n` +
      `✅ متابعة حتى 10 أصول مخصّصة بتنبيهات لحظية.\n` +
      `✅ دخول مباشر لقناة RatePath VIP الخاصة (تحليلات كمّية + فروقات التمويل Funding).\n` +
      `✅ أولوية سرعة في إرسال تنبيهات كسر الأسعار.\n\n` +
      `💰 الاشتراك الشهري: 10$ فقط بعملة USDT (تفعيل فوري).`;

    const rows = [
      [{ text: "🚀 الترقية الآن — 10$ USDT", callback_data: "cb:upgrade_vip" }],
      [{ text: "🔄 استبدال أصل من قائمتي الحالية", callback_data: "cb:replace_menu" }],
      [{ text: "👑 مقارنة مع باقة Pro (50 أصل — 19$)", callback_data: "cb:upgrade_pro" }],
    ];
    if (maxLim < TIER_LIMITS.free.maxAssets + FREE_MAX_BONUS_SLOTS) {
      rows.push([{ text: "🎁 ادعُ صديقين لفتح خانة مجانية (+1)", callback_data: "cb:referral" }]);
    }
    return { text, replyMarkup: { inline_keyboard: rows } };
  }

  const textEn =
    `🔒 Free Plan Limit Reached (${wl.length} / ${maxLim} assets)\n\n` +
    `Your active watchlist:\n` +
    `${activeLines}\n\n` +
    `Need to track more currencies, gold, or crypto?\n` +
    `Upgrade to Business VIP (+ Personal Bot) and unlock immediately:\n\n` +
    `✅ Track up to 10 custom assets with real-time alerts.\n` +
    `✅ Direct access to the private RatePath VIP Channel (Quant Matrix + 8h Funding Spreads).\n` +
    `✅ Priority breakout alert delivery.\n\n` +
    `💰 Monthly Plan: Just $10 USDT (instant activation).`;

  const rowsEn = [
    [{ text: "🚀 Upgrade Now — $10 USDT", callback_data: "cb:upgrade_vip" }],
    [{ text: "🔄 Replace an Existing Asset", callback_data: "cb:replace_menu" }],
    [{ text: "👑 Compare with Pro (50 Assets — $19)", callback_data: "cb:upgrade_pro" }],
  ];
  if (maxLim < TIER_LIMITS.free.maxAssets + FREE_MAX_BONUS_SLOTS) {
    rowsEn.push([{ text: "🎁 Invite 2 Friends (+1 Free Slot)", callback_data: "cb:referral" }]);
  }
  return { text: textEn, replyMarkup: { inline_keyboard: rowsEn } };
}

async function resolveUsdtCheckoutUrl(userId, planTier, lang, env, fetchImpl = fetch) {
  const siteBase = String(env?.PUBLIC_SITE_URL || "https://ratepathlive.com").replace(/\/+$/, "");
  try {
    const session = await createVipCheckoutSession(
      {
        plan_tier: planTier,
        lang,
        pay_currency: "usdttrc20",
        telegram_user_id: String(userId || ""),
      },
      env,
      fetchImpl
    );
    if (session?.status === 200 && session?.payload?.invoice_url) {
      return {
        url: session.payload.invoice_url,
        orderId: session.payload.order_id,
        liveInvoice: true,
      };
    }
  } catch {
    // Graceful fallback below
  }

  const staticFallback =
    planTier === "monthly_personal_bot"
      ? String(env?.NOWPAYMENTS_PRO_URL || env?.NOWPAYMENTS_VIP_URL || "").trim()
      : String(env?.NOWPAYMENTS_VIP_URL || "").trim();
  if (/^https:\/\//i.test(staticFallback) && !/placeholder|your_/i.test(staticFallback)) {
    return { url: staticFallback, orderId: null, liveInvoice: false };
  }
  return {
    url: `${siteBase}/?lang=${lang}#personal-bot-hub`,
    orderId: null,
    liveInvoice: false,
  };
}

export async function buildUpgradeVipView(userId, lang, env, fetchImpl = fetch) {
  const isAr = lang !== "en";
  const checkout = await resolveUsdtCheckoutUrl(userId, "monthly_business_vip", isAr ? "ar" : "en", env, fetchImpl);

  if (isAr) {
    const text =
      `💎 باقة Business VIP + البوت الشخصي\n` +
      `السعر: 10$ USDT / شهرياً — بدون تجديد تلقائي إجباري\n\n` +
      `ماذا يُفتح لك فور إتمام الدفع؟\n` +
      `━━━━━━━━━━━━━━━━━━\n` +
      `🤖 البوت الشخصي: متابعة وتنبيهات فورية لـ 10 أصول (عملات، ذهب، كريبتو).\n` +
      `📡 قناة VIP الخاصة: ملخصات السوق اليومية، محفّزات الاقتصاد الكلي، وفروقات التمويل (8h Funding).\n` +
      `⚡ تنبيهات كسر السعر: إشعار لحظي عند صعود أو هبوط الأصل عن السعر الذي تحدده.\n` +
      `━━━━━━━━━━━━━━━━━━\n\n` +
      `💳 الشبكات المدعومة للدفع المباشر (NOWPayments):\n` +
      `• USDT (TRC-20 • TRON)\n` +
      `• USDT (BEP-20 • BSC)\n` +
      `• USDT (TON • Telegram)\n` +
      `• USDT (Solana / Arbitrum)\n\n` +
      `📌 خطوات التفعيل (أقل من دقيقتين):\n` +
      `1. اضغط على زر "ادفع 10$ USDT الآن".\n` +
      `2. اختر الشبكة المفضّلة لديك وأرسل المبلغ.\n` +
      `3. يتم تفعيل حسابك وإرسال رابط قناة VIP تلقائياً فور تأكيد الشبكة.\n\n` +
      `⚖️ ملاحظة شفافة: الاشتراك هو لخدمة برمجية لمتابعة البيانات وتنبيهات الأسعار فقط، ولا يُعد نصيحة استثمارية أو ضماناً للربح.`;

    return {
      text,
      checkoutUrl: checkout.url,
      orderId: checkout.orderId,
      replyMarkup: {
        inline_keyboard: [
          [{ text: "💳 ادفع 10$ USDT الآن (تفعيل فوري)", url: checkout.url }],
          [{ text: "👑 أريد باقة Pro — 50 أصل + أسهم وأخبار (19$)", callback_data: "cb:upgrade_pro" }],
          [
            { text: "💬 الدعم الذكي", callback_data: "cb:support" },
            { text: "🔙 رجوع", callback_data: "cb:home" },
          ],
        ],
      },
    };
  }

  const textEn =
    `💎 Business VIP + Personal Bot\n` +
    `Price: $10 USDT / month — no forced auto-renewal\n\n` +
    `What unlocks immediately after payment?\n` +
    `━━━━━━━━━━━━━━━━━━\n` +
    `🤖 Personal Bot: Track & set instant alerts for 10 assets (FX, Gold, Crypto).\n` +
    `📡 Private VIP Channel: Daily market briefs, macro catalysts & 8h Funding Carry spreads.\n` +
    `⚡ Breakout Alerts: Instant ping when your target price crosses above or drops below.\n` +
    `━━━━━━━━━━━━━━━━━━\n\n` +
    `💳 Supported Direct USDT Networks (NOWPayments):\n` +
    `• USDT (TRC-20 • TRON)\n` +
    `• USDT (BEP-20 • BSC)\n` +
    `• USDT (TON • Telegram)\n` +
    `• USDT (Solana / Arbitrum)\n\n` +
    `📌 Activation in under 2 minutes:\n` +
    `1. Tap "Pay $10 USDT Now".\n` +
    `2. Choose your preferred network and send the exact amount.\n` +
    `3. Your tier upgrades and your private VIP Channel invite link arrives automatically once confirmed.\n\n` +
    `⚖️ Transparent Notice: This subscription is a market data-monitoring and alert software service only — not financial advice or a guarantee of profit.`;

  return {
    text: textEn,
    checkoutUrl: checkout.url,
    orderId: checkout.orderId,
    replyMarkup: {
      inline_keyboard: [
        [{ text: "💳 Pay $10 USDT Now (Instant)", url: checkout.url }],
        [{ text: "👑 Upgrade to Pro — 50 Assets + Stocks & News ($19)", callback_data: "cb:upgrade_pro" }],
        [
          { text: "💬 AI Support", callback_data: "cb:support" },
          { text: "🔙 Back", callback_data: "cb:home" },
        ],
      ],
    },
  };
}

export async function buildUpgradeProView(userId, lang, env, fetchImpl = fetch) {
  const isAr = lang !== "en";
  const checkout = await resolveUsdtCheckoutUrl(userId, "monthly_personal_bot", isAr ? "ar" : "en", env, fetchImpl);

  if (isAr) {
    const text =
      `👑 باقة Personal Bot Pro + قناة VIP\n` +
      `السعر: 19$ USDT / شهرياً — بدون تجديد تلقائي إجباري\n\n` +
      `الفرق بين الباقتين:\n` +
      `━━━━━━━━━━━━━━━━━━\n` +
      `💎 Business VIP (10$): 10 أصول + قناة VIP + تنبيهات فورية.\n` +
      `👑 Personal Bot Pro (19$): 50 أصلاً + أسهم الشركات العالمية (AAPL/TSLA/NVDA/أرامكو) + أخبار الشركات + دخول كامل لقناة VIP + أعلى أولوية سرعة.\n` +
      `━━━━━━━━━━━━━━━━━━\n\n` +
      `💳 الدفع المباشر عبر NOWPayments (TRC-20 / BEP-20 / TON / SOL / ARB) والتفعيل تلقائي فور تأكيد الشبكة.\n\n` +
      `⚖️ ملاحظة شفافة: خدمة برمجية لمتابعة البيانات وتنبيهات الأسعار فقط وليست نصيحة استثمارية.`;

    return {
      text,
      checkoutUrl: checkout.url,
      orderId: checkout.orderId,
      replyMarkup: {
        inline_keyboard: [
          [{ text: "💳 ادفع 19$ USDT الآن (تفعيل Pro فوري)", url: checkout.url }],
          [{ text: "💎 العودة لباقة Business VIP (10$ USDT)", callback_data: "cb:upgrade_vip" }],
          [
            { text: "💬 الدعم الذكي", callback_data: "cb:support" },
            { text: "🔙 رجوع", callback_data: "cb:home" },
          ],
        ],
      },
    };
  }

  const textEn =
    `👑 Personal Bot Pro + VIP Channel\n` +
    `Price: $19 USDT / month — no forced auto-renewal\n\n` +
    `Plan Comparison:\n` +
    `━━━━━━━━━━━━━━━━━━\n` +
    `💎 Business VIP ($10): 10 assets + VIP Channel + priority alerts.\n` +
    `👑 Personal Bot Pro ($19): 50 assets + Global Stocks (AAPL/TSLA/NVDA/Aramco) + Company News + full VIP Channel + top alert priority.\n` +
    `━━━━━━━━━━━━━━━━━━\n\n` +
    `💳 Direct USDT checkout via NOWPayments (TRC-20 / BEP-20 / TON / SOL / ARB) with automatic activation.\n\n` +
    `⚖️ Transparent Notice: Data monitoring & alert software only — not investment advice.`;

  return {
    text: textEn,
    checkoutUrl: checkout.url,
    orderId: checkout.orderId,
    replyMarkup: {
      inline_keyboard: [
        [{ text: "💳 Pay $19 USDT Now (Instant Pro)", url: checkout.url }],
        [{ text: "💎 Switch to Business VIP ($10 USDT)", callback_data: "cb:upgrade_vip" }],
        [
          { text: "💬 AI Support", callback_data: "cb:support" },
          { text: "🔙 Back", callback_data: "cb:home" },
        ],
      ],
    },
  };
}

function buildAddAssetPickerView(profile) {
  const isAr = profile.lang !== "en";
  const maxAssets = getEffectiveMaxAssets(profile);
  const existingSyms = new Set((profile.watchlist || []).map((w) => String(w.symbol).toUpperCase()));
  const count = existingSyms.size;

  const buttons = [];
  let row = [];
  for (const a of POPULAR_ASSETS) {
    const label = `${existingSyms.has(a.symbol) ? "✓ " : ""}${isAr ? a.ar : a.en}`;
    row.push({ text: label, callback_data: `cb:add_pick:${a.symbol}` });
    if (row.length === 2) {
      buttons.push(row);
      row = [];
    }
  }
  if (row.length) buttons.push(row);
  buttons.push([
    { text: isAr ? "✏️ إدخال رمز عملة/سهم مخصص" : "✏️ Enter Custom Symbol", callback_data: "cb:add_custom" },
  ]);
  buttons.push([
    { text: isAr ? `📊 قائمتي (${count}/${maxAssets})` : `📊 My Watchlist (${count}/${maxAssets})`, callback_data: "cb:watchlist" },
    { text: isAr ? "🔙 الرئيسية" : "🔙 Home", callback_data: "cb:home" },
  ]);

  const text = isAr
    ? `➕ <b>اختر أصلاً لإضافته إلى قائمة المتابعة (${count}/${maxAssets})</b>\n\nاضغط على أي عملة أو معدن أدناه لإضافته فوراً وضبط سعر التنبيه:`
    : `➕ <b>Pick an Asset to Track (${count}/${maxAssets})</b>\n\nTap any currency, metal, crypto, or stock below to add it and set a price alert:`;

  return { text, replyMarkup: { inline_keyboard: buttons } };
}

function buildWatchlistView(profile, liveFeed, catalogFeed) {
  const isAr = profile.lang !== "en";
  const tierMeta = getTierMeta(profile.plan_tier);
  const maxAssets = getEffectiveMaxAssets(profile);
  const items = Array.isArray(profile.watchlist) ? profile.watchlist : [];

  const lines = items.map((it, idx) => {
    const sym = String(it.symbol || "").toUpperCase();
    const hi = it.alert_above ? ` ▲≥<code>${escapeHtml(it.alert_above)}</code>` : "";
    const lo = it.alert_below ? ` 🔻≤<code>${escapeHtml(it.alert_below)}</code>` : "";
    let spotTag = "";
    if (sym === "XAU" && liveFeed?.gold?.prices?.XAU_OZ) spotTag = ` ($${liveFeed.gold.prices.XAU_OZ})`;
    else if (sym === "XAG" && catalogFeed?.metals?.XAG_OZ?.price_usd) spotTag = ` ($${catalogFeed.metals.XAG_OZ.price_usd})`;
    else if (liveFeed?.crypto?.assets?.[sym]?.spot_price) spotTag = ` ($${liveFeed.crypto.assets[sym].spot_price})`;
    else if (catalogFeed?.fiat?.[sym]?.units_per_usd) spotTag = ` (${catalogFeed.fiat[sym].units_per_usd})`;
    else if (liveFeed?.fiat?.rates?.[sym]?.units_per_usd) spotTag = ` (${liveFeed.fiat.rates[sym].units_per_usd})`;
    else if (sym === "USDT") spotTag = " ($1.00)";
    return `${idx + 1}. <b>${escapeHtml(sym)}</b>${spotTag}${hi}${lo}`;
  });

  const rows = [];
  for (const it of items.slice(0, 8)) {
    const sym = String(it.symbol || "").toUpperCase();
    rows.push([
      { text: isAr ? `🎯 ضبط تنبيه ${sym}` : `🎯 Set ${sym} Alert`, callback_data: `cb:set_alert:${sym}` },
      { text: isAr ? `🗑 حذف ${sym}` : `🗑 Remove ${sym}`, callback_data: `cb:del:${sym}` },
    ]);
  }
  rows.push([
    { text: isAr ? "➕ إضافة أصل" : "➕ Add Asset", callback_data: "cb:add_menu" },
    { text: isAr ? "💎 ترقية الباقة" : "💎 Upgrade", callback_data: "cb:upgrade_vip" },
  ]);
  rows.push([{ text: isAr ? "🔙 الرئيسية" : "🔙 Home", callback_data: "cb:home" }]);

  const text = isAr
    ? `📊 <b>قائمة متابعتك (${items.length} / ${maxAssets} أصول)</b>\n` +
      `الباقة: <b>${tierMeta.labelAr}</b>\n\n` +
      `${lines.length ? lines.join("\n") : "قائمتك فارغة حالياً — اضغط «➕ إضافة أصل» للبدء مجاناً."}\n\n` +
      `⚖️ <i>تنبيهات معلوماتية لمتابعة الأرقام فقط وليست نصيحة استثمارية.</i>`
    : `📊 <b>Your Watchlist (${items.length} / ${maxAssets} assets)</b>\n` +
      `Plan: <b>${tierMeta.labelEn}</b>\n\n` +
      `${lines.length ? lines.join("\n") : "Your watchlist is empty — tap «➕ Add Asset» to start free."}\n\n` +
      `⚖️ <i>Informational price alerts only — not financial advice.</i>`;

  return { text, replyMarkup: { inline_keyboard: rows } };
}

function buildReplaceAssetView(profile) {
  const isAr = profile.lang !== "en";
  const items = Array.isArray(profile.watchlist) ? profile.watchlist : [];
  const rows = items.slice(0, 10).map((it) => {
    const sym = String(it.symbol || "").toUpperCase();
    const nm = isAr ? it.name_ar || sym : it.name_en || sym;
    return [{ text: `🗑 ${nm} (${sym})`, callback_data: `cb:replace_del:${sym}` }];
  });
  rows.push([
    { text: isAr ? "🚀 الترقية إلى 10 أصول (10$)" : "🚀 Upgrade to 10 Assets ($10)", callback_data: "cb:upgrade_vip" },
  ]);
  rows.push([{ text: isAr ? "🔙 رجوع" : "🔙 Back", callback_data: "cb:home" }]);

  const text = isAr
    ? "🔄 <b>استبدال أصل من قائمتك</b>\n\nاختر الأصل الذي تريد حذفه لإفساح مكان لأصل جديد:"
    : "🔄 <b>Replace an Asset in Your Watchlist</b>\n\nSelect an asset to remove and free up a slot:";
  return { text, replyMarkup: { inline_keyboard: rows } };
}

function buildTargetPricePromptView(symbol, currentPriceNum, lang) {
  const isAr = lang !== "en";
  const hasPrice = Number.isFinite(currentPriceNum) && currentPriceNum > 0;
  const priceLine = hasPrice
    ? isAr
      ? `📌 السعر الحالي لـ <b>${escapeHtml(symbol)}</b>: <code>${currentPriceNum}</code>`
      : `📌 Current price for <b>${escapeHtml(symbol)}</b>: <code>${currentPriceNum}</code>`
    : isAr
    ? `🎯 ضبط تنبيه السعر لأصل <b>${escapeHtml(symbol)}</b>`
    : `🎯 Set target price alert for <b>${escapeHtml(symbol)}</b>`;

  const rows = [];
  if (hasPrice) {
    rows.push([
      { text: isAr ? "▲ صعود +2%" : "▲ Breakout +2%", callback_data: `cb:qalert:${symbol}:+2` },
      { text: isAr ? "🔻 هبوط -2%" : "🔻 Drop -2%", callback_data: `cb:qalert:${symbol}:-2` },
    ]);
    rows.push([
      { text: isAr ? "▲ صعود +5%" : "▲ Breakout +5%", callback_data: `cb:qalert:${symbol}:+5` },
      { text: isAr ? "🔻 هبوط -5%" : "🔻 Drop -5%", callback_data: `cb:qalert:${symbol}:-5` },
    ]);
  }
  rows.push([
    { text: isAr ? "📊 قائمة متابعتي" : "📊 My Watchlist", callback_data: "cb:watchlist" },
    { text: isAr ? "🔙 الرئيسية" : "🔙 Home", callback_data: "cb:home" },
  ]);

  const text = isAr
    ? `✅ <b>${escapeHtml(symbol)} جاهز للمتابعة!</b>\n\n${priceLine}\n\nاختر تنبيهاً سريعاً أدناه، أو <b>أرسل السعر المستهدف كرقم</b> في رسالة (مثال: <code>${hasPrice ? (currentPriceNum * 1.02).toFixed(currentPriceNum >= 100 ? 2 : 4) : "3700"}</code>):`
    : `✅ <b>${escapeHtml(symbol)} is ready!</b>\n\n${priceLine}\n\nPick a quick alert below, or <b>reply with your exact target number</b> (e.g. <code>${hasPrice ? (currentPriceNum * 1.02).toFixed(currentPriceNum >= 100 ? 2 : 4) : "3700"}</code>):`;

  return { text, replyMarkup: { inline_keyboard: rows } };
}

export function buildNonVipPriceLockMessage(parsedAsset, snapshot, publicSiteUrl = "https://ratepathlive.com") {
  const isAr = parsedAsset.lang === "ar";
  const siteBase = String(publicSiteUrl || "https://ratepathlive.com").replace(/\/+$/, "");
  const symPrefix = parsedAsset.symbol === "SYP" ? "🟢 " : "📊 ";
  const label = isAr ? parsedAsset.displayAr : parsedAsset.displayEn;
  const priceText = isAr ? snapshot.priceFormattedAr : snapshot.priceFormattedEn;
  const chgPart = formatDirectionBadge(snapshot.change24h);

  if (isAr) {
    return sanitizeSyriaEmoji(
      normalizePreserveNewlines(
        `${symPrefix}<b>${escapeHtml(label)}:</b> <code>${escapeHtml(priceText)}</code>${escapeHtml(chgPart)}\n\n` +
          `🔒 <b>ميزة حصرية لمشتركي RatePath VIP:</b>\n` +
          `الأخبار اللحظية المخصّصة لكل عملة وتحليل مسار السيولة بالذكاء الاصطناعي متاحة حصرياً لأعضاء قناة RatePath VIP.\n\n` +
          `🌐 للتفاصيل والاشتراك: ${siteBase}/?lang=ar`
      ),
      "ar"
    );
  }

  return sanitizeSyriaEmoji(
    normalizePreserveNewlines(
      `${symPrefix}<b>${escapeHtml(label)}:</b> <code>${escapeHtml(priceText)}</code>${escapeHtml(chgPart)}\n\n` +
        `🔒 <b>RatePath VIP Exclusive:</b>\n` +
        `On-demand coin news and AI liquidity-flow analysis are available exclusively to RatePath VIP channel members.\n\n` +
        `🌐 Details &amp; VIP access: ${siteBase}/?lang=en`
    ),
    "en"
  );
}

// ---------------------------------------------------------------------------
// Resilient Telegram Bot API Transport (Backoff on 429/5xx, 403 Detection)
// ---------------------------------------------------------------------------

async function tgApiPost(method, bodyObj, env, fetchImpl = fetch, maxAttempts = 3) {
  const botToken = String(env?.TELEGRAM_BOT_TOKEN || "").trim();
  if (!botToken) return { ok: false, status: 0, blocked: false };

  for (let attempt = 0; attempt < maxAttempts; attempt++) {
    try {
      const resp = await fetchImpl(
        `https://api.telegram.org/bot${botToken}/${method}`,
        withTimeout({
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(bodyObj),
        }, 8000)
      );
      if (resp.ok) {
        const data = await resp.json().catch(() => ({ ok: true }));
        return { ok: true, status: resp.status, blocked: false, data };
      }
      if (resp.status === 403) {
        return { ok: false, status: 403, blocked: true };
      }
      if (resp.status === 429 && attempt < maxAttempts - 1) {
        let waitSec = 1 << attempt;
        try {
          const j = await resp.json();
          const ra = Number(j?.parameters?.retry_after);
          if (Number.isFinite(ra) && ra > 0) waitSec = Math.min(ra, 5);
        } catch {
          // Use backoff
        }
        await sleepMs(waitSec * 1000);
        continue;
      }
      if (resp.status >= 500 && attempt < maxAttempts - 1) {
        await sleepMs((1 << attempt) * 500);
        continue;
      }
      return { ok: false, status: resp.status, blocked: false };
    } catch {
      if (attempt < maxAttempts - 1) {
        await sleepMs((1 << attempt) * 500);
        continue;
      }
      return { ok: false, status: 0, blocked: false };
    }
  }
  return { ok: false, status: 0, blocked: false };
}

async function answerCallbackQuery(callbackQueryId, env, fetchImpl = fetch, text = "") {
  if (!callbackQueryId) return false;
  const payload = { callback_query_id: callbackQueryId };
  if (text) payload.text = text.slice(0, 180);
  const res = await tgApiPost("answerCallbackQuery", payload, env, fetchImpl, 1);
  return res.ok;
}

function hasTelegramHtmlTags(text) {
  return /<\/?(?:b|strong|i|em|u|ins|s|strike|del|code|pre|a|tg-spoiler)\b/i.test(String(text || ""));
}

async function sendTelegramReply(chatId, htmlText, env, fetchImpl = fetch, replyMarkup = null) {
  const normalized = normalizePreserveNewlines(htmlText);
  const useHtml = hasTelegramHtmlTags(normalized);
  const payload = {
    chat_id: chatId,
    text: normalized,
    disable_web_page_preview: true,
  };
  if (useHtml) payload.parse_mode = "HTML";
  if (replyMarkup) payload.reply_markup = replyMarkup;

  const res = await tgApiPost("sendMessage", payload, env, fetchImpl);
  if (res.ok || res.blocked || !useHtml) return res;

  // Plain-text fallback if HTML formatting failed
  const plainPayload = {
    chat_id: chatId,
    text: normalized.replace(/<\/?[a-zA-Z][^>]*>/g, ""),
    disable_web_page_preview: true,
  };
  if (replyMarkup) plainPayload.reply_markup = replyMarkup;
  return tgApiPost("sendMessage", plainPayload, env, fetchImpl, 1);
}

async function editOrSendTelegram(chatId, messageId, htmlText, env, fetchImpl = fetch, replyMarkup = null) {
  if (!messageId) {
    return sendTelegramReply(chatId, htmlText, env, fetchImpl, replyMarkup);
  }
  const normalized = normalizePreserveNewlines(htmlText);
  const useHtml = hasTelegramHtmlTags(normalized);
  const payload = {
    chat_id: chatId,
    message_id: messageId,
    text: normalized,
    disable_web_page_preview: true,
  };
  if (useHtml) payload.parse_mode = "HTML";
  if (replyMarkup) payload.reply_markup = replyMarkup;

  const res = await tgApiPost("editMessageText", payload, env, fetchImpl, 2);
  if (res.ok || res.blocked) return res;
  // Fallback to sendMessage if message was too old or deleted
  return sendTelegramReply(chatId, htmlText, env, fetchImpl, replyMarkup);
}

// ---------------------------------------------------------------------------
// Account Linking & Watchlist Mutation Helpers
// ---------------------------------------------------------------------------

async function linkPersonalTrackerAccount(linkToken, chatId, env, fetchImpl = fetch) {
  const supa = getSupaConfig(env);
  if (!supa) return { linked: false, reason: "supabase_unconfigured" };
  try {
    const getResp = await fetchImpl(
      `${supa.restBase}/user_tracker_profiles?link_token=eq.${encodeURIComponent(linkToken)}&select=user_id,email,lang,plan_tier,watchlist&limit=1`,
      { headers: supa.headers }
    );
    if (!getResp.ok) return { linked: false, reason: "lookup_failed" };
    const rows = await getResp.json();
    if (!Array.isArray(rows) || rows.length === 0) {
      return { linked: false, reason: "invalid_or_expired_token" };
    }
    const row = rows[0];
    const patchResp = await fetchImpl(
      `${supa.restBase}/user_tracker_profiles?user_id=eq.${encodeURIComponent(row.user_id)}`,
      {
        method: "PATCH",
        headers: { ...supa.headers, Prefer: "return=minimal" },
        body: JSON.stringify({
          telegram_chat_id: String(chatId),
          link_token: null,
        }),
      }
    );
    if (!patchResp.ok) return { linked: false, reason: "patch_failed" };
    return { linked: true, profile: row };
  } catch {
    return { linked: false, reason: "network_error" };
  }
}

function tryAddAssetToProfile(profile, assetSpec) {
  const maxAssets = getEffectiveMaxAssets(profile);
  const wl = Array.isArray(profile.watchlist) ? [...profile.watchlist] : [];
  const sym = String(assetSpec.symbol || "").trim().toUpperCase();
  const existsIdx = wl.findIndex((it) => String(it.symbol || "").toUpperCase() === sym);

  if (existsIdx !== -1) {
    return { added: false, alreadyExists: true, limitReached: false, profile };
  }
  if (wl.length >= maxAssets) {
    return { added: false, alreadyExists: false, limitReached: true, profile };
  }

  wl.push({
    symbol: sym,
    category: assetSpec.category || "crypto",
    name_en: assetSpec.en || assetSpec.name_en || sym,
    name_ar: assetSpec.ar || assetSpec.name_ar || sym,
    alert_above: null,
    alert_below: null,
    one_time: true,
    news_alerts: true,
    added_at: new Date().toISOString(),
  });

  return {
    added: true,
    alreadyExists: false,
    limitReached: false,
    profile: { ...profile, watchlist: wl, alerts_paused: false },
  };
}

// ---------------------------------------------------------------------------
// Callback Query Handler (Instant <300ms Inline Keyboard Interactions)
// ---------------------------------------------------------------------------

async function handleCallbackQuery(cbq, env, fetchImpl = fetch) {
  const cbId = cbq.id;
  const chatId = cbq.message?.chat?.id || cbq.from?.id;
  const messageId = cbq.message?.message_id;
  const userId = cbq.from?.id;
  const data = String(cbq.data || "").trim();

  // 1. Answer callback immediately to eliminate Telegram loading spinner (<100ms)
  await answerCallbackQuery(cbId, env, fetchImpl);

  if (!chatId || !userId) {
    return { handled: false, reason: "missing_callback_chat" };
  }

  const defaultLang = resolveDefaultLangFromTelegram(cbq.from?.language_code);
  let profile = await loadBotUserProfile(userId, env, fetchImpl, defaultLang);

  // Sync live tier status if free in cache
  if (profile.plan_tier === "free") {
    const vip = await checkVipMembership(userId, env, fetchImpl);
    if (vip.isVip) {
      profile.plan_tier = vip.planTier;
    }
  }

  // Language switch (`cb:lang:en` / `cb:lang:ar`)
  if (data === "cb:lang:en" || data === "cb:lang:ar") {
    profile.lang = data === "cb:lang:en" ? "en" : "ar";
    profile.ui_state = {};
    await saveBotUserProfile(profile, env, fetchImpl);
    const view = buildWelcomeView(profile, env?.PUBLIC_SITE_URL);
    await editOrSendTelegram(chatId, messageId, view.text, env, fetchImpl, view.replyMarkup);
    return { handled: true, mode: "lang_switched", lang: profile.lang };
  }

  // Home menu
  if (data === "cb:home") {
    profile.ui_state = {};
    await saveBotUserProfile(profile, env, fetchImpl);
    const view = buildWelcomeView(profile, env?.PUBLIC_SITE_URL);
    await editOrSendTelegram(chatId, messageId, view.text, env, fetchImpl, view.replyMarkup);
    return { handled: true, mode: "home" };
  }

  // Add Asset Menu -> Immediately check Free Limit Paywall (respecting bonus referral slots up to 6)!
  if (data === "cb:add_menu") {
    const maxLim = getEffectiveMaxAssets(profile);
    if (profile.watchlist.length >= maxLim && profile.plan_tier === "free") {
      const paywall = buildFreeLimitPaywallView(profile);
      await editOrSendTelegram(chatId, messageId, paywall.text, env, fetchImpl, paywall.replyMarkup);
      return { handled: true, mode: "free_limit_paywall", count: profile.watchlist.length };
    }
    const picker = buildAddAssetPickerView(profile);
    await editOrSendTelegram(chatId, messageId, picker.text, env, fetchImpl, picker.replyMarkup);
    return { handled: true, mode: "add_menu" };
  }

  // Pick a specific asset (`cb:add_pick:BTC`)
  if (data.startsWith("cb:add_pick:")) {
    const sym = data.slice("cb:add_pick:".length).trim().toUpperCase();
    const spec =
      POPULAR_ASSETS.find((a) => a.symbol === sym) ||
      (() => {
        const al = ASSET_ALIASES.find((a) => a.symbol === sym);
        return al
          ? { symbol: al.symbol, category: al.kind === "gold" ? "metal" : al.kind, en: al.en, ar: al.ar }
          : { symbol: sym, category: "crypto", en: sym, ar: sym };
      })();

    const res = tryAddAssetToProfile(profile, spec);
    if (res.limitReached) {
      if (profile.plan_tier === "free") {
        const paywall = buildFreeLimitPaywallView(profile);
        await editOrSendTelegram(chatId, messageId, paywall.text, env, fetchImpl, paywall.replyMarkup);
        return { handled: true, mode: "free_limit_paywall", attempted: sym };
      }
      const maxLim = getTierMeta(profile.plan_tier).maxAssets;
      const capMsg =
        profile.lang === "ar"
          ? `⚠️ وصلت للحد الأقصى لباقتك (${maxLim} أصول). احذف أصلاً أو ترقّ لباقة Pro (50 أصلاً).`
          : `⚠️ You reached your plan limit (${maxLim} assets). Remove one or upgrade to Pro (50 assets).`;
      await editOrSendTelegram(chatId, messageId, capMsg, env, fetchImpl, {
        inline_keyboard: [[{ text: profile.lang === "ar" ? "👑 باقة Pro (50 أصل)" : "👑 Pro Plan (50 Assets)", callback_data: "cb:upgrade_pro" }]],
      });
      return { handled: true, mode: "tier_limit_reached", attempted: sym };
    }

    profile = res.profile;
    profile.ui_state = { awaiting: "target_price", symbol: sym };
    await saveBotUserProfile(profile, env, fetchImpl);

    const snap = await resolveLiveAssetSnapshot(
      { symbol: sym, kind: spec.category === "metal" ? "gold" : spec.category, displayEn: spec.en, displayAr: spec.ar, lang: profile.lang },
      env,
      fetchImpl
    );
    const promptView = buildTargetPricePromptView(sym, snap.numericPrice, profile.lang);
    await editOrSendTelegram(chatId, messageId, promptView.text, env, fetchImpl, promptView.replyMarkup);
    return { handled: true, mode: "asset_added", symbol: sym, count: profile.watchlist.length };
  }

  // Custom asset text prompt
  if (data === "cb:add_custom") {
    const maxLim = getEffectiveMaxAssets(profile);
    if (profile.watchlist.length >= maxLim && profile.plan_tier === "free") {
      const paywall = buildFreeLimitPaywallView(profile);
      await editOrSendTelegram(chatId, messageId, paywall.text, env, fetchImpl, paywall.replyMarkup);
      return { handled: true, mode: "free_limit_paywall" };
    }
    profile.ui_state = { awaiting: "custom_symbol" };
    await saveBotUserProfile(profile, env, fetchImpl);
    const txt =
      profile.lang === "ar"
        ? "✏️ أرسل الآن رمز أو اسم العملة أو السهم الذي تريد متابعته (مثال: <code>XRP</code> أو <code>NVDA</code> أو <code>الذهب</code> أو <code>الليرة التركية</code>):"
        : "✏️ Send the currency, metal, crypto, or stock symbol to track (e.g. <code>XRP</code>, <code>NVDA</code>, <code>Gold</code>, or <code>TRY</code>):";
    await editOrSendTelegram(chatId, messageId, txt, env, fetchImpl, {
      inline_keyboard: [[{ text: profile.lang === "ar" ? "🔙 رجوع" : "🔙 Back", callback_data: "cb:add_menu" }]],
    });
    return { handled: true, mode: "awaiting_custom_symbol" };
  }

  // Watchlist screen
  if (data === "cb:watchlist") {
    profile.ui_state = {};
    await saveBotUserProfile(profile, env, fetchImpl);
    const { liveFeed, catalogFeed } = await getBatchMarketFeeds(env, fetchImpl);
    const view = buildWatchlistView(profile, liveFeed, catalogFeed);
    await editOrSendTelegram(chatId, messageId, view.text, env, fetchImpl, view.replyMarkup);
    return { handled: true, mode: "watchlist_shown", count: profile.watchlist.length };
  }

  // Replace menu (from 3/3 paywall)
  if (data === "cb:replace_menu") {
    const view = buildReplaceAssetView(profile);
    await editOrSendTelegram(chatId, messageId, view.text, env, fetchImpl, view.replyMarkup);
    return { handled: true, mode: "replace_menu" };
  }

  // Delete an asset (`cb:del:BTC` or `cb:replace_del:BTC`)
  if (data.startsWith("cb:del:") || data.startsWith("cb:replace_del:")) {
    const isReplace = data.startsWith("cb:replace_del:");
    const sym = data.split(":")[2]?.trim().toUpperCase();
    profile.watchlist = (profile.watchlist || []).filter(
      (it) => String(it.symbol || "").toUpperCase() !== sym
    );
    profile.ui_state = {};
    await saveBotUserProfile(profile, env, fetchImpl);

    if (isReplace) {
      const picker = buildAddAssetPickerView(profile);
      await editOrSendTelegram(chatId, messageId, picker.text, env, fetchImpl, picker.replyMarkup);
      return { handled: true, mode: "asset_replaced_open_add", removed: sym };
    }
    const { liveFeed, catalogFeed } = await getBatchMarketFeeds(env, fetchImpl);
    const view = buildWatchlistView(profile, liveFeed, catalogFeed);
    await editOrSendTelegram(chatId, messageId, view.text, env, fetchImpl, view.replyMarkup);
    return { handled: true, mode: "asset_removed", removed: sym };
  }

  // Set target price for existing watched asset (`cb:set_alert:BTC`)
  if (data.startsWith("cb:set_alert:")) {
    const sym = data.slice("cb:set_alert:".length).trim().toUpperCase();
    profile.ui_state = { awaiting: "target_price", symbol: sym };
    await saveBotUserProfile(profile, env, fetchImpl);
    const item = (profile.watchlist || []).find((w) => String(w.symbol).toUpperCase() === sym);
    const snap = await resolveLiveAssetSnapshot(
      { symbol: sym, kind: item?.category === "metal" ? "gold" : item?.category || "crypto", displayEn: sym, displayAr: sym, lang: profile.lang },
      env,
      fetchImpl
    );
    const view = buildTargetPricePromptView(sym, snap.numericPrice, profile.lang);
    await editOrSendTelegram(chatId, messageId, view.text, env, fetchImpl, view.replyMarkup);
    return { handled: true, mode: "awaiting_target_price", symbol: sym };
  }

  // Quick % alert (`cb:qalert:XAU:+2`)
  if (data.startsWith("cb:qalert:")) {
    const parts = data.split(":");
    const sym = String(parts[2] || "").toUpperCase();
    const pct = Number(parts[3] || 0);
    const item = (profile.watchlist || []).find((w) => String(w.symbol).toUpperCase() === sym);
    if (item && Number.isFinite(pct) && pct !== 0) {
      const snap = await resolveLiveAssetSnapshot(
        { symbol: sym, kind: item.category === "metal" ? "gold" : item.category || "crypto", displayEn: sym, displayAr: sym, lang: profile.lang },
        env,
        fetchImpl
      );
      if (Number.isFinite(snap.numericPrice) && snap.numericPrice > 0) {
        const target = snap.numericPrice * (1 + pct / 100);
        const decimals = target >= 100 ? 2 : target >= 1 ? 4 : 6;
        const targetStr = target.toFixed(decimals);
        if (pct > 0) {
          item.alert_above = targetStr;
          item.triggered_above_target = null;
        } else {
          item.alert_below = targetStr;
          item.triggered_below_target = null;
        }
        item.one_time = true;
        profile.ui_state = {};
        await saveBotUserProfile(profile, env, fetchImpl);

        const confirmTxt =
          profile.lang === "ar"
            ? `✅ <b>تم ضبط التنبيه الفوري بنجاح!</b>\n\n🎯 <b>${escapeHtml(sym)}</b> (${pct > 0 ? "اختراق صعودي ▲ ≥" : "هبوط 🔻 ≤"} <code>${targetStr}</code>)\n\nسيصلك إشعار لحظي فور وصول السعر لهذا المستوى.`
            : `✅ <b>Instant Alert Armed!</b>\n\n🎯 <b>${escapeHtml(sym)}</b> (${pct > 0 ? "Crosses Above ▲ ≥" : "Drops Below 🔻 ≤"} <code>${targetStr}</code>)\n\nYou'll get an instant Telegram alert the moment price hits this level.`;
        await editOrSendTelegram(chatId, messageId, confirmTxt, env, fetchImpl, {
          inline_keyboard: [
            [
              { text: profile.lang === "ar" ? "📊 قائمة متابعتي" : "📊 My Watchlist", callback_data: "cb:watchlist" },
              { text: profile.lang === "ar" ? "➕ إضافة أصل" : "➕ Add Asset", callback_data: "cb:add_menu" },
            ],
            [{ text: profile.lang === "ar" ? "🔙 الرئيسية" : "🔙 Home", callback_data: "cb:home" }],
          ],
        });
        return { handled: true, mode: "alert_set", symbol: sym, target: targetStr };
      }
    }
  }

  // Upgrade VIP ($10/mo USDT)
  if (data === "cb:upgrade_vip") {
    const view = await buildUpgradeVipView(userId, profile.lang, env, fetchImpl);
    await editOrSendTelegram(chatId, messageId, view.text, env, fetchImpl, view.replyMarkup);
    return { handled: true, mode: "upgrade_vip_shown", orderId: view.orderId };
  }

  // Upgrade Pro ($19/mo USDT)
  if (data === "cb:upgrade_pro") {
    const view = await buildUpgradeProView(userId, profile.lang, env, fetchImpl);
    await editOrSendTelegram(chatId, messageId, view.text, env, fetchImpl, view.replyMarkup);
    return { handled: true, mode: "upgrade_pro_shown", orderId: view.orderId };
  }

  // Enter AI Support Mode (`cb:support`)
  if (data === "cb:support") {
    const prevHist = Array.isArray(profile.ui_state?.history) ? profile.ui_state.history : [];
    profile.ui_state = {
      mode: "support",
      escalated: false,
      history: prevHist.slice(-(SUPPORT_MAX_HISTORY_TURNS * 2)),
    };
    await saveBotUserProfile(profile, env, fetchImpl);
    const view = buildSupportGreetingView(profile.lang);
    await editOrSendTelegram(chatId, messageId, view.text, env, fetchImpl, view.replyMarkup);
    return { handled: true, mode: "support_entered", lang: profile.lang };
  }

  // Exit Support Mode (`cb:support_exit`)
  if (data === "cb:support_exit") {
    profile.ui_state = {};
    await saveBotUserProfile(profile, env, fetchImpl);
    const view = buildWelcomeView(profile, env?.PUBLIC_SITE_URL);
    await editOrSendTelegram(chatId, messageId, view.text, env, fetchImpl, view.replyMarkup);
    return { handled: true, mode: "support_exited", lang: profile.lang };
  }

  // Talk to Human Admin Escalation (`cb:support_human`)
  if (data === "cb:support_human") {
    const prevHist = Array.isArray(profile.ui_state?.history) ? profile.ui_state.history : [];
    const lastUserTurn = [...prevHist].reverse().find((m) => m.role === "user");
    const summaryMsg = lastUserTurn?.content
      ? `[User tapped Talk to Human]\nLast question: ${lastUserTurn.content}`
      : "[User tapped Talk to Human button]";
    const ticketRes = await forwardTicketToAdmin({
      userId,
      username: cbq.from?.username || "",
      profile,
      userMessage: summaryMsg,
      reason: "talk_to_human_button",
      env,
      fetchImpl,
    });
    profile.ui_state = {
      mode: "support",
      escalated: true,
      history: prevHist.slice(-(SUPPORT_MAX_HISTORY_TURNS * 2)),
    };
    await saveBotUserProfile(profile, env, fetchImpl);

    const isAr = profile.lang !== "en";
    const escText = isAr
      ? `👤 <b>تم تحويل طلبك إلى فريق الدعم البشري بنجاح!</b>\n\nإذا كانت مشكلتك تتعلق بدفع USDT أو تفعيل باقة، يرجى إرسال <b>رقم الطلب (Order ID)</b> أو <b>معرّف العملية (TXID)</b> هنا مباشرة وسيرد عليك المشرف في نفس هذه المحادثة.`
      : `👤 <b>Your request has been escalated to our human admin team!</b>\n\nIf this is about a USDT payment or activation, please reply right here with your <b>Order ID</b> or <b>TXID</b> and an admin will reply directly in this chat.`;
    await editOrSendTelegram(chatId, messageId, escText, env, fetchImpl, {
      inline_keyboard: [[{ text: isAr ? "❌ إنهاء الدعم" : "❌ Exit Support", callback_data: "cb:support_exit" }]],
    });
    return {
      handled: true,
      mode: "support_escalated",
      reason: "talk_to_human_button",
      forwarded: ticketRes.forwarded,
      adminMsgId: ticketRes.adminMsgId,
    };
  }

  // Viral Referral Dashboard (`cb:referral`)
  if (data === "cb:referral") {
    profile.ui_state = {};
    const view = buildReferralView(profile, env?.BOT_USERNAME);
    await editOrSendTelegram(chatId, messageId, view.text, env, fetchImpl, view.replyMarkup);
    return { handled: true, mode: "referral_shown", refLink: view.refLink, tokens: profile.referral_tokens };
  }

  // Redeem 100 Referral Tokens -> +7 Free Business VIP Days (`cb:ref_redeem`)
  if (data === "cb:ref_redeem") {
    const isAr = profile.lang !== "en";
    const curTokens = Math.max(0, Number(profile.referral_tokens || 0));
    if (curTokens < REFERRAL_REDEEM_COST_TOKENS) {
      const shortMsg = isAr
        ? `⚠️ رصيدك الحالي <code>${curTokens}</code> نقطة. تحتاج إلى <b>${REFERRAL_REDEEM_COST_TOKENS} نقطة</b> لاستبدالها بـ ${REFERRAL_REDEEM_VIP_DAYS} أيام VIP مجاناً.`
        : `⚠️ Your balance is <code>${curTokens}</code> tokens. You need <b>${REFERRAL_REDEEM_COST_TOKENS} tokens</b> to redeem ${REFERRAL_REDEEM_VIP_DAYS} free VIP days.`;
      await editOrSendTelegram(chatId, messageId, shortMsg, env, fetchImpl, {
        inline_keyboard: [[{ text: isAr ? "🎁 لوحة الدعوات" : "🎁 Invite Dashboard", callback_data: "cb:referral" }]],
      });
      return { handled: true, mode: "referral_redeem_insufficient", tokens: curTokens };
    }

    profile.referral_tokens = curTokens - REFERRAL_REDEEM_COST_TOKENS;
    if (profile.plan_tier === "free") {
      profile.plan_tier = "monthly_business_vip";
    }
    const baseMs = Math.max(Date.now(), Date.parse(profile.expires_at || "") || 0);
    profile.expires_at = new Date(baseMs + REFERRAL_REDEEM_VIP_DAYS * 86400 * 1000).toISOString();
    profile.alerts_paused = false;
    await saveBotUserProfile(profile, env, fetchImpl);

    const okMsg = isAr
      ? `🎉 <b>تم استبدال ${REFERRAL_REDEEM_COST_TOKENS} نقطة بنجاح!</b>\n\n💎 تمت إضافة <b>+${REFERRAL_REDEEM_VIP_DAYS} أيام Business VIP</b> إلى حسابك (فعّالة حتى <code>${profile.expires_at.slice(0, 10)}</code>).\n🪙 رصيدك المتبقي: <code>${profile.referral_tokens}</code> نقطة.`
      : `🎉 <b>Redeemed ${REFERRAL_REDEEM_COST_TOKENS} Tokens Successfully!</b>\n\n💎 Added <b>+${REFERRAL_REDEEM_VIP_DAYS} Free Business VIP Days</b> to your account (active until <code>${profile.expires_at.slice(0, 10)}</code>).\n🪙 Remaining Balance: <code>${profile.referral_tokens}</code> tokens.`;
    await editOrSendTelegram(chatId, messageId, okMsg, env, fetchImpl, {
      inline_keyboard: [
        [{ text: isAr ? "➕ إضافة أصول الآن (حتى 10)" : "➕ Add Assets Now (Up to 10)", callback_data: "cb:add_menu" }],
        [{ text: isAr ? "🔙 الرئيسية" : "🔙 Home", callback_data: "cb:home" }],
      ],
    });
    return {
      handled: true,
      mode: "referral_redeemed_vip",
      tokensLeft: profile.referral_tokens,
      expiresAt: profile.expires_at,
    };
  }

  return { handled: false, reason: "unknown_callback_data" };
}

// ---------------------------------------------------------------------------
// Core Update Processor (Messages + Callback Queries)
// ---------------------------------------------------------------------------

export async function processTelegramUpdate(update, env, fetchImpl = fetch) {
  // Fast path: Inline button callback query
  if (update?.callback_query) {
    return handleCallbackQuery(update.callback_query, env, fetchImpl);
  }

  const msg = update?.message;
  if (!msg || !msg.text || msg.chat?.type !== "private") {
    return { handled: false, reason: "ignored_non_private_or_empty" };
  }

  // 0. Admin Reply Bridge: Admin replies directly to a forwarded support ticket in Telegram
  const adminBridgeRes = await handleAdminTicketReply(msg, env, fetchImpl);
  if (adminBridgeRes) {
    return adminBridgeRes;
  }

  const chatId = msg.chat.id;
  const userId = msg.from?.id;
  const userLang = msg.from?.language_code || "";
  const defaultLang = resolveDefaultLangFromTelegram(userLang);
  const rawText = String(msg.text || "").trim();
  const inferredLang = detectQueryLang(rawText, userLang);

  // 1. 1-Click Personal Bot Account Pairing (`/start link_<TOKEN>`)
  const linkMatch = rawText.match(/^\/start(?:@\w+)?\s+link_([A-Za-z0-9_-]{8,40})$/i);
  if (linkMatch) {
    const pairRes = await linkPersonalTrackerAccount(linkMatch[1], chatId, env, fetchImpl);
    const prefLang = pairRes.profile?.lang === "en" ? "en" : defaultLang;
    const replyText = pairRes.linked
      ? prefLang === "ar"
        ? `✅ <b>تم ربط حسابك ببوت المتابعة الشخصي بنجاح!</b>\n\n🔔 ستصلك هنا تنبيهات الأسعار وملخصات الأخبار المخصصة للعملات والذهب والأسهم التي تضيفها من حسابك في https://ratepathlive.com.\n\n📋 أرسل <code>/watchlist</code> في أي وقت لعرض قائمتك.\n\nℹ️ <i>خدمة معلوماتية لمتابعة البيانات فقط وليست توصية استثمارية.</i>`
        : `✅ <b>Your RatePath Personal Tracker Bot is Now Linked!</b>\n\n🔔 You will receive private price &amp; company-news alerts here for every currency, metal, crypto, or stock in your https://ratepathlive.com watchlist.\n\n📋 Send <code>/watchlist</code> anytime to inspect your tracked items.\n\nℹ️ <i>Informational market monitoring only — not investment advice.</i>`
      : defaultLang === "ar"
      ? "⚠️ رابط الربط غير صالح أو تم استخدامه مسبقاً. افتح نافذة «البوت الشخصي» في https://ratepathlive.com لإنشاء رابط جديد."
      : "⚠️ This pairing link is invalid or already used. Open the Personal Bot modal on https://ratepathlive.com to generate a fresh link.";
    await sendTelegramReply(chatId, replyText, env, fetchImpl);
    return { handled: true, mode: "personal_bot_linked", linked: pairRes.linked };
  }

  // 2. Deep-link direct upgrade (`/start vip_ar`, `/start vip_en`, `/upgrade`)
  const vipStartMatch = rawText.match(/^\/start(?:@\w+)?\s+vip_(ar|en)$/i);
  if (vipStartMatch || /^\/upgrade(?:@\w+)?\b/i.test(rawText)) {
    const profile = await loadBotUserProfile(
      userId,
      env,
      fetchImpl,
      vipStartMatch ? vipStartMatch[1].toLowerCase() : defaultLang
    );
    const targetLang = vipStartMatch ? vipStartMatch[1].toLowerCase() : profile.lang;
    profile.lang = targetLang;
    profile.ui_state = {};
    await saveBotUserProfile(profile, env, fetchImpl);
    const view = await buildUpgradeVipView(userId, targetLang, env, fetchImpl);
    await sendTelegramReply(chatId, view.text, env, fetchImpl, view.replyMarkup);
    return { handled: true, mode: "upgrade_vip_shown", orderId: view.orderId, lang: targetLang };
  }

  // 2b. `/support` command (enters AI Support Mode, or answers inline question directly)
  const supportCmdMatch = rawText.match(/^\/support(?:@\w+)?(?:\s+([\s\S]+))?$/i);
  if (supportCmdMatch) {
    const profile = await loadBotUserProfile(userId, env, fetchImpl, defaultLang);
    const prevHist = Array.isArray(profile.ui_state?.history) ? profile.ui_state.history : [];
    profile.ui_state = {
      mode: "support",
      escalated: false,
      history: prevHist.slice(-(SUPPORT_MAX_HISTORY_TURNS * 2)),
    };
    await saveBotUserProfile(profile, env, fetchImpl);

    const inlineQuestion = String(supportCmdMatch[1] || "").trim();
    if (inlineQuestion) {
      return handleSupportUserMessage({
        msg,
        chatId,
        userId,
        rawText: inlineQuestion,
        profile,
        env,
        fetchImpl,
      });
    }

    const view = buildSupportGreetingView(profile.lang);
    await sendTelegramReply(chatId, view.text, env, fetchImpl, view.replyMarkup);
    return { handled: true, mode: "support_entered", lang: profile.lang };
  }

  // 2c. `/referral` & `/invite` command -> Viral Referral Dashboard
  if (/^(\/(?:referral|invite)(?:@\w+)?\b|دعوة\s*$)/i.test(rawText)) {
    const profile = await loadBotUserProfile(userId, env, fetchImpl, defaultLang);
    profile.ui_state = {};
    await saveBotUserProfile(profile, env, fetchImpl);
    const view = buildReferralView(profile, env?.BOT_USERNAME);
    await sendTelegramReply(chatId, view.text, env, fetchImpl, view.replyMarkup);
    return { handled: true, mode: "referral_shown", refLink: view.refLink, tokens: profile.referral_tokens };
  }

  // 3. `/start`, `/start ref_<REFERRER_ID>`, `/menu`, `/help` -> Exact Bilingual Welcome + Viral Referral Tracking
  if (/^\/(start|menu|help)\b/i.test(rawText)) {
    const profile = await loadBotUserProfile(userId, env, fetchImpl, defaultLang);
    const refStartMatch = rawText.match(/^\/start(?:@\w+)?\s+ref_(\d{5,20})$/i);
    let referralResult = null;
    if (refStartMatch) {
      referralResult = await processReferralJoin({
        newUserId: userId,
        newUsername: msg.from?.username || "",
        referrerId: refStartMatch[1],
        profile,
        env,
        fetchImpl,
      });
    }
    const vipStatus = await checkVipMembership(userId, env, fetchImpl);
    if (vipStatus.isVip && profile.plan_tier === "free") {
      profile.plan_tier = vipStatus.planTier;
    }
    profile.alerts_paused = false;
    profile.ui_state = {};
    await saveBotUserProfile(profile, env, fetchImpl);

    const view = buildWelcomeView(profile, env?.PUBLIC_SITE_URL);
    await sendTelegramReply(chatId, view.text, env, fetchImpl, view.replyMarkup);
    return {
      handled: true,
      mode: "welcome",
      isVip: vipStatus.isVip,
      lang: profile.lang,
      referral: referralResult,
    };
  }

  // 4. `/watchlist`, `/my`, `قائمتي`
  if (/^(\/(?:watchlist|my)\b|قائمتي\s*$)/i.test(rawText)) {
    const profile = await loadBotUserProfile(userId, env, fetchImpl, defaultLang);
    const { liveFeed, catalogFeed } = await getBatchMarketFeeds(env, fetchImpl);
    const view = buildWatchlistView(profile, liveFeed, catalogFeed);
    await sendTelegramReply(chatId, view.text, env, fetchImpl, view.replyMarkup);
    return { handled: true, mode: "watchlist_shown", count: profile.watchlist.length };
  }

  // 5. Session Mode Handling: AI Support Mode OR Interactive Wizard (custom symbol / target price)
  const profile = await loadBotUserProfile(userId, env, fetchImpl, defaultLang);
  if (profile.ui_state?.mode === "support" && !rawText.startsWith("/")) {
    return handleSupportUserMessage({ msg, chatId, userId, rawText, profile, env, fetchImpl });
  }
  if (profile.ui_state?.awaiting === "target_price" && profile.ui_state?.symbol) {
    const cleanedNum = rawText
      .replace(/,/g, "")
      .replace(/[\u0660-\u0669]/g, (d) => String(d.charCodeAt(0) - 0x0660))
      .replace(/\u066B/g, ".")
      .trim();
    const numVal = Number(cleanedNum);
    if (Number.isFinite(numVal) && numVal > 0) {
      const sym = String(profile.ui_state.symbol).toUpperCase();
      const item = (profile.watchlist || []).find((w) => String(w.symbol).toUpperCase() === sym);
      if (item) {
        const snap = await resolveLiveAssetSnapshot(
          { symbol: sym, kind: item.category === "metal" ? "gold" : item.category || "crypto", displayEn: sym, displayAr: sym, lang: profile.lang },
          env,
          fetchImpl
        );
        const isAbove = !Number.isFinite(snap.numericPrice) || numVal >= snap.numericPrice;
        if (isAbove) {
          item.alert_above = cleanedNum;
          item.triggered_above_target = null;
        } else {
          item.alert_below = cleanedNum;
          item.triggered_below_target = null;
        }
        item.one_time = true;
        profile.ui_state = {};
        await saveBotUserProfile(profile, env, fetchImpl);

        const msgTxt =
          profile.lang === "ar"
            ? `✅ <b>تم حفظ تنبيه السعر لأصل ${escapeHtml(sym)}!</b>\n🎯 المستوى: <code>${escapeHtml(cleanedNum)}</code> (${isAbove ? "اختراق صعودي ▲ ≥" : "هبوط 🔻 ≤"})`
            : `✅ <b>Target Price Saved for ${escapeHtml(sym)}!</b>\n🎯 Target: <code>${escapeHtml(cleanedNum)}</code> (${isAbove ? "Crosses Above ▲ ≥" : "Drops Below 🔻 ≤"})`;
        await sendTelegramReply(chatId, msgTxt, env, fetchImpl, {
          inline_keyboard: [[{ text: profile.lang === "ar" ? "📊 قائمة متابعتي" : "📊 My Watchlist", callback_data: "cb:watchlist" }]],
        });
        return { handled: true, mode: "alert_set", symbol: sym, target: cleanedNum };
      }
    }
    // Clear wizard state if user sent a non-number query and proceed normally
    profile.ui_state = {};
    await saveBotUserProfile(profile, env, fetchImpl);
  }

  if (profile.ui_state?.awaiting === "custom_symbol") {
    profile.ui_state = {};
    const parsedCustom = parseUserAssetQuery(rawText, profile.lang);
    if (parsedCustom) {
      const addRes = tryAddAssetToProfile(profile, {
        symbol: parsedCustom.symbol,
        category: parsedCustom.kind === "gold" ? "metal" : parsedCustom.kind,
        en: parsedCustom.displayEn,
        ar: parsedCustom.displayAr,
      });
      if (addRes.limitReached && profile.plan_tier === "free") {
        const paywall = buildFreeLimitPaywallView(profile);
        await sendTelegramReply(chatId, paywall.text, env, fetchImpl, paywall.replyMarkup);
        return { handled: true, mode: "free_limit_paywall", attempted: parsedCustom.symbol };
      }
      const nextProf = addRes.profile;
      nextProf.ui_state = { awaiting: "target_price", symbol: parsedCustom.symbol };
      await saveBotUserProfile(nextProf, env, fetchImpl);
      const snap = await resolveLiveAssetSnapshot(parsedCustom, env, fetchImpl);
      const view = buildTargetPricePromptView(parsedCustom.symbol, snap.numericPrice, nextProf.lang);
      await sendTelegramReply(chatId, view.text, env, fetchImpl, view.replyMarkup);
      return { handled: true, mode: "asset_added", symbol: parsedCustom.symbol, count: nextProf.watchlist.length };
    }
    await saveBotUserProfile(profile, env, fetchImpl);
  }

  // 6. On-Demand Coin / Currency / Stock Query (`/ask <coin>` or natural AR/EN query)
  const vipStatus = await checkVipMembership(userId, env, fetchImpl);
  const parsed = parseUserAssetQuery(rawText, userLang);
  if (!parsed) {
    const hint =
      inferredLang === "ar"
        ? "💬 اكتب اسم العملة أو الرمز المطلوب (مثل: <code>سولانا</code> أو <code>XRP</code> أو <code>الذهب</code> أو <code>الليرة السورية</code>)، أو اضغط /start لعرض القائمة."
        : "💬 Send a coin or currency symbol (e.g. <code>SOL</code>, <code>XRP</code>, <code>Gold</code>, or <code>SYP</code>), or tap /start for the main menu.";
    await sendTelegramReply(chatId, hint, env, fetchImpl);
    return { handled: true, mode: "unknown_asset", isVip: vipStatus.isVip };
  }

  // Non-VIP path: Live spot price + polite VIP lock notice + quick Add/Upgrade inline buttons
  if (!vipStatus.isVip) {
    const snap = await resolveLiveAssetSnapshot(parsed, env, fetchImpl);
    const lockMsg = buildNonVipPriceLockMessage(parsed, snap, env?.PUBLIC_SITE_URL);
    const lockButtons = {
      inline_keyboard: [
        [
          {
            text: parsed.lang === "ar" ? `➕ متابعة ${parsed.symbol} مجاناً` : `➕ Track ${parsed.symbol} Free`,
            callback_data: `cb:add_pick:${parsed.symbol}`,
          },
          {
            text: parsed.lang === "ar" ? "💎 ترقية VIP (10$)" : "💎 Upgrade VIP ($10)",
            callback_data: "cb:upgrade_vip",
          },
        ],
      ],
    };
    await sendTelegramReply(chatId, lockMsg, env, fetchImpl, lockButtons);
    return { handled: true, mode: "non_vip_locked", symbol: parsed.symbol, isVip: false };
  }

  // Verified VIP path: Rate Limit (25/day) -> 15m Shared Cache -> Live Data + RSS + AI Briefing
  const rl = await checkAndIncrementVipRateLimit(userId, env, fetchImpl);
  if (!rl.allowed) {
    const limitMsg =
      parsed.lang === "ar"
        ? `⏳ وصلت إلى الحد اليومي (${rl.limit} طلب مخصّص يومياً) لضمان سرعة الخدمة للجميع.\n\nيتجدد رصيدك تلقائياً عند منتصف الليل UTC.`
        : `⏳ You have reached today's fair-use quota (${rl.limit} custom briefings/day).\n\nYour quota resets automatically at 00:00 UTC.`;
    await sendTelegramReply(chatId, limitMsg, env, fetchImpl);
    return { handled: true, mode: "vip_rate_limited", count: rl.count, isVip: true };
  }

  const cachedHtml = await getVipCachedBrief(parsed.symbol, parsed.lang, env, fetchImpl);
  if (cachedHtml) {
    await sendTelegramReply(chatId, cachedHtml, env, fetchImpl);
    return { handled: true, mode: "vip_cache_hit", symbol: parsed.symbol, isVip: true };
  }

  const [snapshot, headlines] = await Promise.all([
    resolveLiveAssetSnapshot(parsed, env, fetchImpl),
    fetchTargetedAssetHeadlines(parsed, fetchImpl),
  ]);
  const briefHtml = await generateVipAiBrief(parsed, snapshot, headlines, env, fetchImpl);

  await setVipCachedBrief(parsed.symbol, parsed.lang, briefHtml, env, fetchImpl);
  await sendTelegramReply(chatId, briefHtml, env, fetchImpl);
  return { handled: true, mode: "vip_live_generated", symbol: parsed.symbol, isVip: true };
}

// ---------------------------------------------------------------------------
// Cloudflare Pages Function Entrypoints
// ---------------------------------------------------------------------------

export async function onRequestGet() {
  return new Response(
    JSON.stringify({ ok: true, service: "ratepath_terminal_telegram_webhook" }),
    { status: 200, headers: JSON_HEADERS }
  );
}

export async function onRequestPost(context) {
  const { request, env } = context;
  const providedSecret = request.headers.get("X-Telegram-Bot-Api-Secret-Token");
  if (!verifyWebhookSecret(providedSecret, env?.TELEGRAM_WEBHOOK_SECRET)) {
    return new Response(
      JSON.stringify({ ok: false, error: "unauthorized_webhook_secret" }),
      { status: 401, headers: JSON_HEADERS }
    );
  }

  let update;
  try {
    update = await request.json();
  } catch {
    return new Response(JSON.stringify({ ok: false, error: "invalid_json" }), {
      status: 400,
      headers: JSON_HEADERS,
    });
  }

  try {
    const result = await processTelegramUpdate(update, env, fetch);
    return new Response(JSON.stringify({ ok: true, ...result }), {
      status: 200,
      headers: JSON_HEADERS,
    });
  } catch {
    // Always return 200 to Telegram so transient errors never cause retry storms
    return new Response(
      JSON.stringify({ ok: false, error: "internal_error" }),
      { status: 200, headers: JSON_HEADERS }
    );
  }
}
