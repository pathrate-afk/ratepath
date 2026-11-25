/**
 * RatePath (ريت باث) — 24/7 Interactive VIP-Only Custom Asset Telegram Webhook
 * Cloudflare Pages Function: `POST /api/telegram-webhook`
 *
 * Security & Capabilities:
 * - Verifies `X-Telegram-Bot-Api-Secret-Token` against `env.TELEGRAM_WEBHOOK_SECRET`.
 * - Handles private 1-on-1 messages (`/start`, `/ask <coin>`, or natural AR/EN queries).
 * - Instant zero-friction VIP gating via Telegram `getChatMember` on `_AR`/`_EN` VIP channels,
 *   `ADMIN_CHAT_ID` bypass, and Supabase `public.vip_subscriptions` fallback.
 * - Non-VIP users receive live spot price + polite RatePath VIP lock notice.
 * - Verified VIP users get 25 requests/day Upstash rate limit, 15-minute shared Upstash cache
 *   (`ratepath:vip_ask:{SYMBOL}:{lang}`), live metrics + fresh coin RSS headlines, and
 *   Groq (`llama-3.3-70b-versatile`) with Gemini Flash fallback returning a crisp 3-part HTML briefing.
 */

const LIVE_FEED_DEFAULT =
  "https://raw.githubusercontent.com/pathrate-afk/ratepath/live-data/live_feed.json";
const CATALOG_FEED_DEFAULT =
  "https://raw.githubusercontent.com/pathrate-afk/ratepath/live-data/catalog_feed.json";

const VIP_DAILY_LIMIT = 25;
const VIP_CACHE_TTL_SECONDS = 900; // 15 minutes
const VIP_MEMBER_STATUSES = new Set(["member", "administrator", "creator"]);
const RAW_SYRIA_FLAG_EMOJI = "\uD83C\uDDF8\uD83C\uDDFE";

const JSON_HEADERS = {
  "Content-Type": "application/json; charset=utf-8",
  "Cache-Control": "no-store",
};

const ASSET_ALIASES = [
  // Precious Metals
  { symbol: "XAU", kind: "gold", en: "Gold (XAU)", ar: "الذهب الفوري (XAU)", terms: ["الذهب", "ذهب", "عيار 21", "عيار 24", "اونصة الذهب", "أونصة الذهب", "gold", "xau", "xau_oz", "xauusd"] },
  { symbol: "XAG", kind: "gold", en: "Silver (XAG)", ar: "الفضة الفورية (XAG)", terms: ["الفضة", "فضة", "silver", "xag", "xag_oz"] },
  // Syrian Pound & Regional / World Fiat
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
  // Top Cryptocurrencies
  { symbol: "BTC", kind: "crypto", en: "Bitcoin (BTC)", ar: "بيتكوين (BTC)", terms: ["البيتكوين", "بيتكوين", "بتكوين", "bitcoin", "btc"] },
  { symbol: "ETH", kind: "crypto", en: "Ethereum (ETH)", ar: "إيثيريوم (ETH)", terms: ["الإيثيريوم", "إيثيريوم", "ايثيريوم", "ايثريوم", "ethereum", "eth"] },
  { symbol: "SOL", kind: "crypto", en: "Solana (SOL)", ar: "سولانا (SOL)", terms: ["سولانا", "السولانا", "solana", "sol"] },
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
];

const IGNORE_TICKERS = new Set([
  "ASK", "START", "HELP", "NEWS", "PRICE", "ANALYSIS", "RATE", "COIN", "CRYPTO",
  "WHAT", "HOW", "ABOUT", "TODAY", "NOW", "THE", "FOR", "AND", "WITH", "STATUS",
  "CHECK", "TELL", "GIVE", "SHOW", "UPDATE", "MARKET", "LIVE", "USD",
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

/**
 * Extract normalized asset descriptor from `/ask <coin>` or natural AR/EN text.
 */
export function parseUserAssetQuery(rawText, userLangCode = "") {
  const text = String(rawText || "").trim();
  const lang = detectQueryLang(text, userLangCode);
  const cleaned = text.replace(/^\/ask(@\w+)?\s*/i, "").trim();
  const low = cleaned.toLowerCase();

  // 1. Check curated Arabic & English aliases first (longest term match first)
  let bestMatch = null;
  let bestLen = 0;
  for (const entry of ASSET_ALIASES) {
    for (const term of entry.terms) {
      const tLow = term.toLowerCase();
      const isAsciiWord = /^[a-z0-9_]+$/.test(tLow);
      const matched = isAsciiWord
        ? new RegExp(`(^|[^a-z0-9_])${tLow}([^a-z0-9_]|$)`, "i").test(low)
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

  // 2. Fallback: extract any explicit English ticker (e.g. "وضع ARB" or "/ask RENDER")
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
// Upstash Redis REST Helpers (Rate Limit 25/day + 15m Shared Cache)
// ---------------------------------------------------------------------------

async function upstashCmd(env, commandArray, fetchImpl = fetch) {
  const url = String(env?.UPSTASH_REDIS_REST_URL || "").trim().replace(/\/+$/, "");
  const token = String(env?.UPSTASH_REDIS_REST_TOKEN || "").trim();
  if (!url || !token || url.toLowerCase() === "placeholder" || token.toLowerCase() === "placeholder") {
    return null;
  }
  try {
    const resp = await fetchImpl(url, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${token}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify(commandArray),
    });
    if (!resp.ok) return null;
    const body = await resp.json();
    return body?.result ?? null;
  } catch {
    return null;
  }
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
  if (!uidStr) return { isVip: false, reason: "missing_user_id" };

  // 1. Admin bypass
  if (env?.ADMIN_CHAT_ID && uidStr === String(env.ADMIN_CHAT_ID).trim()) {
    return { isVip: true, reason: "admin" };
  }

  // 2. Telegram getChatMember on VIP AR & VIP EN channels
  const botToken = String(env?.TELEGRAM_BOT_TOKEN || "").trim();
  const vipChannels = [env?.TELEGRAM_VIP_CHANNEL_AR, env?.TELEGRAM_VIP_CHANNEL_EN]
    .map((c) => String(c || "").trim())
    .filter((c) => c && c.toLowerCase() !== "placeholder");

  if (botToken && vipChannels.length > 0) {
    for (const chatId of vipChannels) {
      try {
        const resp = await fetchImpl(
          `https://api.telegram.org/bot${botToken}/getChatMember?chat_id=${encodeURIComponent(chatId)}&user_id=${encodeURIComponent(uidStr)}`
        );
        if (!resp.ok) continue;
        const data = await resp.json();
        const status = String(data?.result?.status || "").toLowerCase();
        if (data?.ok && VIP_MEMBER_STATUSES.has(status)) {
          return { isVip: true, reason: `telegram_channel:${chatId}` };
        }
      } catch {
        // Continue to next channel / Supabase fallback
      }
    }
  }

  // 3. Supabase `public.vip_subscriptions` fallback check
  const supaUrl = String(env?.SUPABASE_URL || "").trim().replace(/\/+$/, "");
  const supaKey = String(env?.SUPABASE_KEY || "").trim();
  if (supaUrl && supaKey && supaUrl.toLowerCase() !== "placeholder" && supaKey.toLowerCase() !== "placeholder") {
    const restBase = supaUrl.endsWith("/rest/v1") ? supaUrl : `${supaUrl}/rest/v1`;
    try {
      const resp = await fetchImpl(
        `${restBase}/vip_subscriptions?telegram_user_id=eq.${encodeURIComponent(uidStr)}&payment_status=in.(finished,confirmed,active)&select=id,expires_at&limit=1`,
        {
          headers: {
            apikey: supaKey,
            Authorization: `Bearer ${supaKey}`,
          },
        }
      );
      if (resp.ok) {
        const rows = await resp.json();
        if (Array.isArray(rows) && rows.length > 0) {
          const exp = rows[0]?.expires_at;
          if (!exp || Date.parse(exp) > Date.now()) {
            return { isVip: true, reason: "supabase_vip_subscription" };
          }
        }
      }
    } catch {
      // Fall through safely to non-VIP
    }
  }

  return { isVip: false, reason: "non_vip" };
}

// ---------------------------------------------------------------------------
// Live Asset Snapshot & Targeted RSS Headlines
// ---------------------------------------------------------------------------

export async function resolveLiveAssetSnapshot(parsedAsset, env, fetchImpl = fetch) {
  const sym = parsedAsset.symbol;
  const liveUrl = String(env?.LIVE_FEED_URL || LIVE_FEED_DEFAULT).trim();
  const catalogUrl = String(env?.CATALOG_FEED_URL || CATALOG_FEED_DEFAULT).trim();

  let liveFeed = null;
  let catalogFeed = null;
  try {
    const [rLive, rCat] = await Promise.all([
      fetchImpl(liveUrl, { cf: { cacheTtl: 60, cacheEverything: true } }).catch(() => null),
      fetchImpl(catalogUrl, { cf: { cacheTtl: 90, cacheEverything: true } }).catch(() => null),
    ]);
    if (rLive?.ok) liveFeed = await rLive.json();
    if (rCat?.ok) catalogFeed = await rCat.json();
  } catch {
    // Graceful fallback below
  }

  // Gold & Silver
  if (sym === "XAU") {
    const oz = liveFeed?.gold?.prices?.XAU_OZ || catalogFeed?.metals?.XAU_OZ?.price_usd;
    const g21 = liveFeed?.gold?.prices?.GRAM_21K || catalogFeed?.metals?.GRAM_21K?.price_usd;
    const g24 = liveFeed?.gold?.prices?.GRAM_24K || catalogFeed?.metals?.GRAM_24K?.price_usd;
    if (oz) {
      return {
        found: true,
        symbol: "XAU",
        priceFormattedEn: `$${oz} / oz (21K: $${g21 || "-"} · 24K: $${g24 || "-"})`,
        priceFormattedAr: `$${oz} للأونصة (عيار 21: $${g21 || "-"} · عيار 24: $${g24 || "-"})`,
        change24h: null,
        source: liveFeed?.gold?.source || "swissquote",
      };
    }
  }
  if (sym === "XAG") {
    const xag = catalogFeed?.metals?.XAG_OZ?.price_usd;
    if (xag) {
      return {
        found: true,
        symbol: "XAG",
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
  if (cmcCoin || liveCrypto) {
    const price = liveCrypto?.spot_price || cmcCoin?.price_usd;
    const chg = cmcCoin?.change_24h_pct || null;
    return {
      found: true,
      symbol: sym,
      name: cmcCoin?.name || parsedAsset.displayEn,
      rank: cmcCoin?.rank || null,
      priceFormattedEn: `$${price}`,
      priceFormattedAr: `$${price}`,
      change24h: chg,
      source: "coinmarketcap",
    };
  }

  // Long-tail Crypto fallback via CoinMarketCap public search / CoinGecko
  try {
    const cgResp = await fetchImpl(
      `https://api.coingecko.com/api/v3/search?query=${encodeURIComponent(sym)}`,
      { headers: { Accept: "application/json", "User-Agent": "RatePath-VIPBot/2.0" } }
    );
    if (cgResp.ok) {
      const cgData = await cgResp.json();
      const match = (cgData?.coins || []).find(
        (c) => String(c.symbol || "").toUpperCase() === sym
      ) || cgData?.coins?.[0];
      if (match?.id) {
        const pResp = await fetchImpl(
          `https://api.coingecko.com/api/v3/simple/price?ids=${encodeURIComponent(match.id)}&vs_currencies=usd&include_24hr_change=true`
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
    // Graceful miss
  }

  return { found: false, symbol: sym, priceFormattedEn: "N/A", priceFormattedAr: "غير متوفر حالياً", change24h: null, source: "none" };
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
    const resp = await fetchImpl(rssUrl, {
      headers: { "User-Agent": "RatePath-VIPBot/2.0", Accept: "application/rss+xml, text/xml" },
    });
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
// VIP AI Briefing Generator (Groq llama-3.3-70b-versatile -> Gemini Flash -> Fallback)
// ---------------------------------------------------------------------------

async function generateVipAiBrief(parsedAsset, snapshot, headlines, env, fetchImpl = fetch) {
  const lang = parsedAsset.lang;
  const isAr = lang === "ar";
  const label = isAr ? parsedAsset.displayAr : parsedAsset.displayEn;
  const priceLine = isAr ? snapshot.priceFormattedAr : snapshot.priceFormattedEn;
  const chgText = snapshot.change24h ? ` (${snapshot.change24h}% 24h)` : "";

  const sysPrompt = isAr
    ? [
        "أنت محلل ريت باث (RatePath VIP) الذكي. قدّم إجابة فورية ومختصرة بلغة عربية فصحى سلسة يفهمها المبتدئ بدون تعقيد.",
        "استخدم التنسيق التالي حرفياً مع سطر فارغ (\\n\\n) بين كل قسم ولا تكتب أي روابط:",
        "📌 <b>السعر والزخم:</b>\n[جملة واحدة واضحة تضم السعر وحالة الزخم]",
        "📰 <b>أهم أخبار العملة الآن:</b>\n[نقطتان قصيرتان بأسلوب واضح عن أهم الأخبار المحركة]",
        "💡 <b>الخلاصة واتجاه السيولة:</b>\n[جملتان عمليتان عن مسار السيولة وما يجب مراقبته]",
        "قاعدة صارمة: لا تكتب أبداً رمز علم سوريا الإيموجي (U+1F1F8 U+1F1FE)، واكتب '🟢 الليرة السورية' أو '🟢 ل.س'.",
      ].join("\n")
    : [
        "You are the RatePath VIP desk analyst. Write a crisp, beginner-friendly briefing in clean Telegram HTML with ZERO URLs.",
        "Use this exact 3-block layout separated by blank lines (\\n\\n):",
        "📌 <b>Price &amp; Momentum:</b>\n[1 clear sentence with live price and momentum]",
        "📰 <b>Latest Headlines:</b>\n[2 crisp bullet points summarizing the active catalysts]",
        "💡 <b>Bottom Line &amp; Money Flow:</b>\n[1-2 plain sentences on liquidity direction and what to watch]",
        "STRICT RULE: NEVER output the raw Unicode Syria flag emoji (U+1F1F8 U+1F1FE); always use '🟢 SYP'.",
      ].join("\n");

  const usrPrompt = JSON.stringify({
    asset: label,
    symbol: snapshot.symbol,
    live_price: `${priceLine}${chgText}`,
    rank: snapshot.rank || null,
    recent_headlines: headlines,
  });

  // 1. Primary: Groq (`llama-3.3-70b-versatile` with resilient model fallback)
  const groqKey = String(env?.GROQ_API_KEY_1 || env?.GROQ_API_KEY || "").trim();
  if (groqKey && groqKey.toLowerCase() !== "placeholder") {
    for (const modelName of ["llama-3.3-70b-versatile", "openai/gpt-oss-120b"]) {
      try {
        const resp = await fetchImpl("https://api.groq.com/openai/v1/chat/completions", {
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
        });
        if (!resp.ok) continue;
        const data = await resp.json();
        const content = data?.choices?.[0]?.message?.content;
        if (typeof content === "string" && content.trim().length > 40) {
          return sanitizeSyriaEmoji(normalizePreserveNewlines(content), lang);
        }
      } catch {
        // Try next model / Gemini fallback
      }
    }
  }

  // 2. Secondary: Gemini Flash Fallback
  const geminiKey = String(env?.GEMINI_API_KEY_1 || env?.GEMINI_API_KEY || "").trim();
  if (geminiKey && geminiKey.toLowerCase() !== "placeholder") {
    for (const gModel of ["gemini-2.5-flash", "gemini-3.5-flash-lite"]) {
      try {
        const resp = await fetchImpl(
          `https://generativelanguage.googleapis.com/v1beta/models/${gModel}:generateContent?key=${encodeURIComponent(geminiKey)}`,
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              systemInstruction: { parts: [{ text: sysPrompt }] },
              contents: [{ role: "user", parts: [{ text: usrPrompt }] }],
            }),
          }
        );
        if (!resp.ok) continue;
        const data = await resp.json();
        const textOut = data?.candidates?.[0]?.content?.parts?.[0]?.text;
        if (typeof textOut === "string" && textOut.trim().length > 40) {
          return sanitizeSyriaEmoji(normalizePreserveNewlines(textOut), lang);
        }
      } catch {
        // Continue to next model / deterministic fallback
      }
    }
  }

  // 3. Deterministic structured fallback (guaranteed 3-part HTML with \n\n spacing)
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
// Message Formatters (Welcome / Non-VIP Lock / Quota)
// ---------------------------------------------------------------------------

export function buildWelcomeMessage(lang, isVip) {
  if (lang === "ar") {
    const badge = isVip ? "✅ <b>حسابك مفعّل على باقة VIP</b>" : "🔒 <b>التحليل الفوري بالذكاء الاصطناعي متاح لمشتركي VIP</b>";
    return normalizePreserveNewlines(
      `👋 <b>أهلاً بك في بوت ريت باث (RatePath)</b>\n\n` +
        `${badge}\n\n` +
        `💬 اكتب اسم أي عملة أو معدن مباشرة، مثل:\n` +
        `• اخبار عملة سولانا\n` +
        `• وضع XRP\n` +
        `• تحليل الذهب\n` +
        `• الليرة السورية\n` +
        `• /ask BTC`
    );
  }
  const badgeEn = isVip ? "✅ <b>Your RatePath VIP access is active</b>" : "🔒 <b>On-demand AI news &amp; flow briefings are exclusive to RatePath VIP</b>";
  return normalizePreserveNewlines(
    `👋 <b>Welcome to the RatePath Interactive Desk</b>\n\n` +
      `${badgeEn}\n\n` +
      `💬 Send any asset name or ticker directly, such as:\n` +
      `• /ask SOL\n` +
      `• XRP outlook\n` +
      `• Gold analysis\n` +
      `• SYP rate`
  );
}

export function buildNonVipPriceLockMessage(parsedAsset, snapshot, publicSiteUrl = "https://ratepath.io") {
  const isAr = parsedAsset.lang === "ar";
  const siteBase = String(publicSiteUrl || "https://ratepath.io").replace(/\/+$/, "");
  const symPrefix = parsedAsset.symbol === "SYP" ? "🟢 " : "📊 ";
  const label = isAr ? parsedAsset.displayAr : parsedAsset.displayEn;
  const priceText = isAr ? snapshot.priceFormattedAr : snapshot.priceFormattedEn;
  const chgPart = snapshot.change24h ? ` (${snapshot.change24h}%)` : "";

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

async function sendTelegramReply(chatId, htmlText, env, fetchImpl = fetch) {
  const botToken = String(env?.TELEGRAM_BOT_TOKEN || "").trim();
  if (!botToken) return false;
  const resp = await fetchImpl(`https://api.telegram.org/bot${botToken}/sendMessage`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      chat_id: chatId,
      text: normalizePreserveNewlines(htmlText),
      parse_mode: "HTML",
      disable_web_page_preview: true,
    }),
  });
  return resp.ok;
}

// ---------------------------------------------------------------------------
// Core Update Processor (Exported for direct unit testing & Cloudflare runtime)
// ---------------------------------------------------------------------------

export async function processTelegramUpdate(update, env, fetchImpl = fetch) {
  const msg = update?.message;
  if (!msg || !msg.text || msg.chat?.type !== "private") {
    return { handled: false, reason: "ignored_non_private_or_empty" };
  }

  const chatId = msg.chat.id;
  const userId = msg.from?.id;
  const userLang = msg.from?.language_code || "";
  const rawText = String(msg.text || "").trim();
  const lang = detectQueryLang(rawText, userLang);

  // Check VIP membership
  const vipStatus = await checkVipMembership(userId, env, fetchImpl);

  if (/^\/(start|help)\b/i.test(rawText)) {
    const welcome = buildWelcomeMessage(lang, vipStatus.isVip);
    await sendTelegramReply(chatId, welcome, env, fetchImpl);
    return { handled: true, mode: "welcome", isVip: vipStatus.isVip };
  }

  const parsed = parseUserAssetQuery(rawText, userLang);
  if (!parsed) {
    const hint =
      lang === "ar"
        ? "💬 اكتب اسم العملة أو الرمز المطلوب (مثل: <code>سولانا</code> أو <code>XRP</code> أو <code>الذهب</code> أو <code>الليرة السورية</code>)."
        : "💬 Please include a coin or currency symbol (e.g. <code>SOL</code>, <code>XRP</code>, <code>Gold</code>, or <code>SYP</code>).";
    await sendTelegramReply(chatId, hint, env, fetchImpl);
    return { handled: true, mode: "unknown_asset", isVip: vipStatus.isVip };
  }

  // Non-VIP path: Live spot price + polite VIP lock notice
  if (!vipStatus.isVip) {
    const snap = await resolveLiveAssetSnapshot(parsed, env, fetchImpl);
    const lockMsg = buildNonVipPriceLockMessage(parsed, snap, env?.PUBLIC_SITE_URL);
    await sendTelegramReply(chatId, lockMsg, env, fetchImpl);
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
    JSON.stringify({ ok: true, service: "ratepath_vip_telegram_webhook" }),
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
  } catch (err) {
    // Return 200 to Telegram so transient downstream errors never create retry storms
    return new Response(
      JSON.stringify({ ok: false, error: String(err?.message || "internal_error") }),
      { status: 200, headers: JSON_HEADERS }
    );
  }
}
