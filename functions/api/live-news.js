/**
 * RatePath (ريت باث) — Cloudflare Pages Function: `GET /api/live-news?lang=ar|en`
 *
 * Continuous Hourly (10-minute Edge-cached) High-Impact Market, Whale, Crypto & Corporate News Engine:
 * - Pulls authentic Arabic & English breaking economy, whale/USDT supply, crypto surge/crash,
 *   and corporate bankruptcy/earnings wires directly at Cloudflare Edge.
 * - Computes live 24h USDT & USDC treasury mint/burn deltas via DefiLlama.
 * - Strictly blocks personal-finance advice noise (401(k), lifestyle, etc.).
 * - Powers BOTH the Top-of-Page Breaking Headlines Bar and the full News Details section below.
 */

const CORS_HEADERS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, OPTIONS",
  "Access-Control-Allow-Headers": "Content-Type",
  "Content-Type": "application/json; charset=utf-8",
  "Cache-Control": "public, max-age=600, s-maxage=600",
  "X-Content-Type-Options": "nosniff",
  "X-Robots-Tag": "noindex",
};

const FETCH_HEADERS = {
  Accept: "application/rss+xml, application/xml, text/xml, application/json",
  "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) RatePathNews/2.0",
};

const NOISE_RE =
  /(401\(k\)|stay-at-home|my husband|my wife|should i retire|social security|wedding|divorce|horoscope|credit card tips|best savings account|gift guide|holiday shopping|inheritance|برجك|الأبراج|وصفات طبخ|كرة القدم|الدوري|مباراة|تقنية الفار|كأس العالم|السجون التونسية|سجن|معتقل|investingpro|خصم\s*\d+%|اشترك الآن في|عرض خاص)/i;

const MARKET_RELEVANCE_AR_RE =
  /(دولار|يورو|ذهب|فضة|نفط|برنت|أوبك|بيتكوين|إيثيريوم|عملة|عملات|كريبتو|سهم|أسهم|بورصة|بنك|مركزي|فائدة|تضخم|اقتصاد|سوق|أسواق|شركة|شركات|تداول|أرباح|خسائر|إفلاس|usdt|usdc|btc|eth|xau|ليرة|ريال|درهم|جنيه|دينار|استثمار|سندات|عقود|تعدين|حيتان|محافظ|سيولة)/i;

const WHALE_RE = /(usdt|usdc|tether|whale|حيتان|محافظ|محفظة|طباعة|معروض|سيولة|stablecoin|mint|burn|hack|اختراق)/i;
const CORP_RE = /(bankrupt|إفلاس|افلاس|خسارة|خسائر|أرباح|إغلاق|اغلاق|تسريح|layoffs|earnings|شركة|شركات|سهم|أسهم|stock|tesla|apple|nvidia|amazon|microsoft|aramco|أرامكو|بنك|مصرف)/i;
const CRYPTO_RE = /(bitcoin|btc|ethereum|eth|solana|sol|crypto|بيتكوين|إيثيريوم|عملات رقمية|عملة رقمية|سولانا|ريبل|xrp|binance|bybit|coinbase)/i;
const UP_RE = /(surge|soar|rally|jump|rise|gain|record high|mint|inflow|ارتفاع|صعود|قفزة|مكاسب|أرباح قياسية|طباعة|انتعاش)/i;
const DOWN_RE = /(crash|plunge|drop|fall|slump|bankrupt|collapse|loss|hack|layoffs|shutdown|هبوط|انهيار|تراجع|خسارة|خسائر|إفلاس|افلاس|إغلاق|اختراق|تسريح)/i;

const FEEDS_EN = [
  { cat: "crypto", source: "CoinTelegraph", url: "https://cointelegraph.com/rss" },
  { cat: "crypto", source: "CoinDesk", url: "https://www.coindesk.com/arc/outboundfeeds/rss/" },
  { cat: "corporate", source: "CNBC Finance", url: "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=10000664" },
  { cat: "macro", source: "CNBC Economy", url: "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=20910258" },
  { cat: "macro", source: "Investing.com", url: "https://www.investing.com/rss/news_14.rss" },
];

const FEEDS_AR = [
  { cat: "macro", source: "سكاي نيوز اقتصاد", url: "https://www.skynewsarabia.com/rss/v1/business.xml" },
  { cat: "crypto", source: "كوين تليغراف عربي", url: "https://ar.cointelegraph.com/rss" },
  { cat: "corporate", source: "Investing عربي", url: "https://sa.investing.com/rss/news.rss", strictMarket: true },
  { cat: "crypto", source: "Investing كريبتو", url: "https://sa.investing.com/rss/news_301.rss" },
  { cat: "macro", source: "Investing عملات", url: "https://sa.investing.com/rss/news_1.rss" },
];

function withTimeout(init = {}, ms = 6000) {
  if (typeof AbortSignal !== "undefined" && typeof AbortSignal.timeout === "function") {
    return { ...init, signal: AbortSignal.timeout(ms) };
  }
  return init;
}

function decodeHtmlEntities(s) {
  return String(s || "")
    .replace(/<!\[CDATA\[([\s\S]*?)\]\]>/g, "$1")
    .replace(/<[^>]+>/g, " ")
    .replace(/&amp;/g, "&")
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&quot;/g, '"')
    .replace(/&#39;|&apos;/g, "'")
    .replace(/&nbsp;/g, " ")
    .replace(/[ \t\r\n]+/g, " ")
    .trim();
}

function classifyItem(title, summary, defaultCat) {
  const blob = `${title} ${summary}`;
  let category = defaultCat;
  if (WHALE_RE.test(blob)) category = "whale";
  else if (CORP_RE.test(blob)) category = "corporate";
  else if (CRYPTO_RE.test(blob)) category = "crypto";

  let direction = "neutral";
  if (DOWN_RE.test(blob) && !UP_RE.test(title)) direction = "down";
  else if (UP_RE.test(blob) && !DOWN_RE.test(title)) direction = "up";

  return { category, direction };
}

async function fetchRssFeed(feed) {
  try {
    const resp = await fetch(
      feed.url,
      withTimeout({ headers: FETCH_HEADERS, cf: { cacheTtl: 300, cacheEverything: true } })
    );
    if (!resp.ok) return [];
    const xml = await resp.text();
    const items = [];
    const itemRe = /<item\b[^>]*>([\s\S]*?)<\/item>/gi;
    let m;
    while ((m = itemRe.exec(xml)) !== null && items.length < 6) {
      const block = m[1];
      const titleM = block.match(/<title[^>]*>([\s\S]*?)<\/title>/i);
      const linkM = block.match(/<link[^>]*>([\s\S]*?)<\/link>/i);
      const descM = block.match(/<description[^>]*>([\s\S]*?)<\/description>/i);
      const pubM = block.match(/<pubDate[^>]*>([\s\S]*?)<\/pubDate>/i);

      const title = decodeHtmlEntities(titleM?.[1] || "");
      const url = decodeHtmlEntities(linkM?.[1] || "").trim();
      const rawDesc = decodeHtmlEntities(descM?.[1] || "");
      const summary = rawDesc.length >= 25 ? rawDesc.slice(0, 320) : title;
      if (title.length < 12 || !/^https?:\/\//i.test(url) || NOISE_RE.test(`${title} ${summary}`)) continue;
      if (feed.strictMarket && !MARKET_RELEVANCE_AR_RE.test(`${title} ${summary}`)) continue;

      const parsedMs = pubM?.[1] ? Date.parse(pubM[1].trim()) : NaN;
      const publishedAt = Number.isFinite(parsedMs) ? new Date(parsedMs).toISOString() : new Date().toISOString();
      const { category, direction } = classifyItem(title, summary, feed.cat);

      items.push({
        id: `ln-${Math.abs(hashCode(url)).toString(36)}`,
        title: title.slice(0, 180),
        summary,
        source: feed.source,
        url,
        published_at: publishedAt,
        category,
        direction,
      });
    }
    return items;
  } catch {
    return [];
  }
}

async function fetchYahooBackupNews(query = "market OR bitcoin OR gold OR bankruptcy") {
  try {
    const resp = await fetch(
      `https://query1.finance.yahoo.com/v1/finance/search?q=${encodeURIComponent(query)}&quotesCount=0&newsCount=6`,
      withTimeout({ headers: { Accept: "application/json", "User-Agent": "Mozilla/5.0 RatePathNews/2.0" }, cf: { cacheTtl: 300, cacheEverything: true } })
    );
    if (!resp.ok) return [];
    const data = await resp.json();
    const out = [];
    for (const n of data?.news || []) {
      const title = String(n?.title || "").trim();
      const url = String(n?.link || "").trim();
      if (title.length < 12 || !/^https?:\/\//i.test(url) || NOISE_RE.test(title)) continue;
      const { category, direction } = classifyItem(title, title, "corporate");
      out.push({
        id: `yn-${Math.abs(hashCode(url)).toString(36)}`,
        title: title.slice(0, 180),
        summary: title,
        source: String(n?.publisher || "Yahoo Finance"),
        url,
        published_at: n?.providerPublishTime
          ? new Date(Number(n.providerPublishTime) * 1000).toISOString()
          : new Date().toISOString(),
        category,
        direction,
      });
    }
    return out;
  } catch {
    return [];
  }
}

async function fetchStablecoinSupplyCard(lang) {
  try {
    const resp = await fetch(
      "https://stablecoins.llama.fi/stablecoins?includePrices=true",
      withTimeout({ headers: { Accept: "application/json", "User-Agent": "RatePathNews/2.0" }, cf: { cacheTtl: 600, cacheEverything: true } })
    );
    if (!resp.ok) return { usdtDeltaM: null, usdcDeltaM: null, card: null };
    const data = await resp.json();
    let usdtDeltaM = null;
    let usdcDeltaM = null;
    let usdtB = null;
    let usdcB = null;
    for (const row of data?.peggedAssets || []) {
      const sym = String(row?.symbol || "").toUpperCase();
      const curr = Number(row?.circulating?.peggedUSD);
      const prev = Number(row?.circulatingPrevDay?.peggedUSD);
      if (!Number.isFinite(curr) || !Number.isFinite(prev)) continue;
      const deltaM = Number(((curr - prev) / 1e6).toFixed(1));
      const totalB = Number((curr / 1e9).toFixed(2));
      if (sym === "USDT") {
        usdtDeltaM = deltaM;
        usdtB = totalB;
      } else if (sym === "USDC") {
        usdcDeltaM = deltaM;
        usdcB = totalB;
      }
    }
    if (usdtDeltaM === null && usdcDeltaM === null) return { usdtDeltaM, usdcDeltaM, card: null };

    const mainDelta = usdtDeltaM ?? usdcDeltaM ?? 0;
    const isUp = mainDelta >= 0;
    const fmtSignedUsdM = (val) => {
      const n = Number(val ?? 0);
      return n < 0 ? `-$${Math.abs(n)}M` : `+$${n}M`;
    };
    const uFmt = fmtSignedUsdM(usdtDeltaM);
    const cFmt = fmtSignedUsdM(usdcDeltaM);

    const card = {
      id: "ln-stablecoin-treasury",
      title:
        lang === "ar"
          ? `سيولة الحيتان والعملات المستقرة: تغيّر معروض USDT بـ ${uFmt} وUSDC بـ ${cFmt} خلال 24 ساعة`
          : `Stablecoin Treasury Flow: USDT ${uFmt} & USDC ${cFmt} (24h Supply)`,
      summary:
        lang === "ar"
          ? `رصد مباشر من شبكة البلوكشين عبر DefiLlama: إجمالي معروض USDT يبلغ ${usdtB ?? "-"} مليار دولار، وUSDC يبلغ ${usdcB ?? "-"} مليار دولار. زيادة المعروض تعكس ضخ سيولة جديدة للأسواق، بينما تراجعه يشير لعمليات استرداد وخروج سيولة.`
          : `Live on-chain telemetry via DefiLlama: total USDT supply stands at $${usdtB ?? "-"}B and USDC at $${usdcB ?? "-"}B. Net mints add fresh buying liquidity, while redemptions indicate capital outflows.`,
      source: "DefiLlama On-Chain",
      url: "https://defillama.com/stablecoins",
      published_at: new Date().toISOString(),
      category: "whale",
      direction: isUp ? "up" : "down",
    };
    return { usdtDeltaM, usdcDeltaM, card };
  } catch {
    return { usdtDeltaM: null, usdcDeltaM: null, card: null };
  }
}

function hashCode(str) {
  let h = 0;
  for (let i = 0; i < str.length; i++) {
    h = (Math.imul(31, h) + str.charCodeAt(i)) | 0;
  }
  return h;
}

export async function onRequestOptions() {
  return new Response(null, { status: 204, headers: CORS_HEADERS });
}

export async function onRequestGet(context) {
  const url = new URL(context.request.url);
  const lang = String(url.searchParams.get("lang") || "ar").toLowerCase() === "en" ? "en" : "ar";
  const targetFeeds = lang === "ar" ? FEEDS_AR : FEEDS_EN;

  const [stableOutcome, ...rssBatches] = await Promise.all([
    fetchStablecoinSupplyCard(lang),
    ...targetFeeds.map((f) => fetchRssFeed(f)),
  ]);

  let merged = rssBatches.flat();
  if (merged.length < 4) {
    const backup = await fetchYahooBackupNews();
    merged = merged.concat(backup);
  }

  // Deduplicate by normalized title and sort newest first
  const seen = new Set();
  const deduped = [];
  for (const item of merged) {
    const key = item.title.toLowerCase().slice(0, 60);
    if (seen.has(key)) continue;
    seen.add(key);
    deduped.push(item);
  }
  deduped.sort((a, b) => Date.parse(b.published_at) - Date.parse(a.published_at));

  const finalItems = stableOutcome.card ? [stableOutcome.card, ...deduped.slice(0, 11)] : deduped.slice(0, 12);

  return new Response(
    JSON.stringify({
      generated_at: new Date().toISOString(),
      lang,
      usdt_24h_delta_m: stableOutcome.usdtDeltaM,
      usdc_24h_delta_m: stableOutcome.usdcDeltaM,
      count: finalItems.length,
      items: finalItems,
    }),
    { status: 200, headers: CORS_HEADERS }
  );
}
