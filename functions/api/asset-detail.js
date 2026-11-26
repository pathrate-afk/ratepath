/**
 * RatePath (ريت باث) — Cloudflare Pages Function: `GET /api/asset-detail`
 *
 * Per-Asset Deep Analysis (1D / 1W / 1M / 1Y), Interactive Chart Series, Technical Metrics
 * (RSI 14, EMA 20/50, 52W High/Low) & Targeted Asset-Specific News (AR / EN).
 *
 * Supports USD (DXY), Gold (XAU + 24K/21K/18K), Silver (XAG + Gram), ANY World Fiat,
 * Syrian Pound (SP-Today mode), and ANY Crypto coin.
 * Informational data & statistical analytics only — NOT financial advice.
 */

const CORS_HEADERS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, OPTIONS",
  "Access-Control-Allow-Headers": "Content-Type",
  "Content-Type": "application/json; charset=utf-8",
  "Cache-Control": "public, max-age=300, s-maxage=300",
  "X-Content-Type-Options": "nosniff",
  "X-Robots-Tag": "noindex",
};

const FETCH_HEADERS = {
  Accept: "application/json",
  "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) RatePathDetail/2.0",
};

const TROY_OZ_GRAMS = 31.1034768;

const ARABIC_NEWS_TOPICS = {
  USD: "الدولار الأمريكي الاحتياطي الفيدرالي سعر الصرف",
  EUR: "سعر اليورو البنك المركزي الأوروبي",
  GBP: "الجنيه الإسترليني بنك إنجلترا",
  SAR: "الريال السعودي الاقتصاد السعودي",
  AED: "الدرهم الإماراتي اقتصاد الإمارات",
  EGP: "الجنيه المصري البنك المركزي المصري سعر الدولار",
  TRY: "الليرة التركية البنك المركزي التركي",
  SYP: "الليرة السورية سعر الصرف اقتصاد سوريا",
  JOD: "الدينار الأردني الاقتصاد الأردني",
  KWD: "الدينار الكويتي الاقتصاد الكويتي",
  RUB: "الروبل الروسي البنك المركزي الروسي",
  CNY: "اليوان الصيني اقتصاد الصين",
  JPY: "الين الياباني بنك اليابان",
  XAU_OZ: "أسعار الذهب اليوم الأونصة العالمية",
  GRAM_24K: "أسعار الذهب عيار 24 اليوم",
  GRAM_21K: "أسعار الذهب عيار 21 اليوم",
  GRAM_18K: "أسعار الذهب اليوم",
  XAG_OZ: "أسعار الفضة العالمية اليوم",
  XAG_GRAM: "سعر غرام الفضة اليوم",
  BTC: "سعر البيتكوين اليوم أخبار بيتكوين",
  ETH: "سعر إيثيريوم اليوم أخبار الإيثيريوم",
  SOL: "عملة سولانا اليوم أخبار SOL",
  XRP: "عملة ريبل XRP أخبار اليوم",
  DOGE: "عملة دوجكوين DOGE أخبار",
  TON: "عملة تون كوين TON أخبار",
  PEPE: "عملة بيبي PEPE كريبتو",
};

const ENGLISH_NEWS_TOPICS = {
  USD: "US Dollar DXY Federal Reserve forex",
  EUR: "Euro EUR USD ECB currency",
  GBP: "British Pound GBP Bank of England",
  SAR: "Saudi Riyal Saudi economy",
  AED: "UAE Dirham UAE economy",
  EGP: "Egyptian Pound EGP central bank",
  TRY: "Turkish Lira TRY central bank",
  SYP: "Syrian Pound SYP economy exchange rate",
  XAU_OZ: "Gold price XAU bullion spot market",
  GRAM_24K: "Gold price XAU bullion spot market",
  GRAM_21K: "Gold price XAU bullion spot market",
  GRAM_18K: "Gold price XAU bullion spot market",
  XAG_OZ: "Silver price XAG spot market",
  XAG_GRAM: "Silver price XAG spot market",
  BTC: "Bitcoin BTC price crypto market",
  ETH: "Ethereum ETH price crypto market",
  SOL: "Solana SOL price crypto market",
};

function withTimeout(init = {}, ms = 6500) {
  if (typeof AbortSignal !== "undefined" && typeof AbortSignal.timeout === "function") {
    return { ...init, signal: AbortSignal.timeout(ms) };
  }
  return init;
}

export function resolveYahooSpec(idRaw, codeRaw, categoryRaw) {
  const id = String(idRaw || codeRaw || "USD").trim().toUpperCase();
  const code = String(codeRaw || id).trim().toUpperCase().replace(/^(CMC_|CG_|DEX_|KC_|CDN_)/, "");
  const cat = String(categoryRaw || "").trim().toLowerCase();

  if (id === "USD" || code === "USD") {
    return {
      ticker: "DX-Y.NYB",
      scale: 1,
      unitLabelEn: "US Dollar Index (DXY)",
      unitLabelAr: "مؤشر الدولار العالمي (DXY)",
      skipYahoo: false,
    };
  }
  if (id === "SYP" || code === "SYP") {
    return {
      ticker: null,
      scale: 1,
      unitLabelEn: "SP-Today Market Rate (SYP)",
      unitLabelAr: "سعر السوق الحر (SP-Today)",
      skipYahoo: true,
    };
  }
  if (id === "XAU_OZ" || code === "XAU") {
    return { ticker: "GC=F", scale: 1, unitLabelEn: "Gold Ounce (USD)", unitLabelAr: "أونصة الذهب ($)", skipYahoo: false };
  }
  if (id === "GRAM_24K") {
    return { ticker: "GC=F", scale: 1 / TROY_OZ_GRAMS, unitLabelEn: "Gold 24K Gram (USD)", unitLabelAr: "غرام ذهب 24 ($)", skipYahoo: false };
  }
  if (id === "GRAM_21K") {
    return { ticker: "GC=F", scale: (21 / 24) / TROY_OZ_GRAMS, unitLabelEn: "Gold 21K Gram (USD)", unitLabelAr: "غرام ذهب 21 ($)", skipYahoo: false };
  }
  if (id === "GRAM_18K") {
    return { ticker: "GC=F", scale: (18 / 24) / TROY_OZ_GRAMS, unitLabelEn: "Gold 18K Gram (USD)", unitLabelAr: "غرام ذهب 18 ($)", skipYahoo: false };
  }
  if (id === "XAG_OZ" || code === "XAG") {
    return { ticker: "SI=F", scale: 1, unitLabelEn: "Silver Ounce (USD)", unitLabelAr: "أونصة الفضة ($)", skipYahoo: false };
  }
  if (id === "XAG_GRAM") {
    return { ticker: "SI=F", scale: 1 / TROY_OZ_GRAMS, unitLabelEn: "Silver Gram 999 (USD)", unitLabelAr: "غرام الفضة ($)", skipYahoo: false };
  }

  if (cat === "fiat" || (/^[A-Z]{3}$/.test(code) && cat !== "crypto")) {
    if (code === "EUR" || code === "GBP" || code === "AUD" || code === "NZD") {
      return {
        ticker: `${code}USD=X`,
        scale: 1,
        unitLabelEn: `${code} / USD`,
        unitLabelAr: `قيمة ${code} مقابل الدولار`,
        skipYahoo: false,
      };
    }
    return {
      ticker: `${code}=X`,
      scale: 1,
      unitLabelEn: `USD / ${code}`,
      unitLabelAr: `سعر الدولار مقابل ${code}`,
      skipYahoo: false,
    };
  }

  // Crypto coin ticker on Yahoo Finance (`BTC-USD`, `ETH-USD`, `SOL-USD`, ...)
  return {
    ticker: `${code}-USD`,
    scale: 1,
    unitLabelEn: `${code} / USD`,
    unitLabelAr: `${code} مقابل الدولار ($)`,
    skipYahoo: false,
    isCrypto: true,
    cryptoSym: code,
  };
}

function roundPrice(n) {
  if (!Number.isFinite(n)) return null;
  if (n >= 100) return Number(n.toFixed(2));
  if (n >= 1) return Number(n.toFixed(4));
  if (n >= 0.01) return Number(n.toFixed(5));
  return Number(n.toFixed(8));
}

function computeEma(vals, period) {
  if (!Array.isArray(vals) || vals.length < period) return null;
  const k = 2 / (period + 1);
  let ema = vals.slice(0, period).reduce((a, b) => a + b, 0) / period;
  for (let i = period; i < vals.length; i++) ema = vals[i] * k + ema * (1 - k);
  return ema;
}

function computeRsi(vals, period = 14) {
  if (!Array.isArray(vals) || vals.length <= period) return null;
  let gSum = 0;
  let lSum = 0;
  for (let i = 1; i <= period; i++) {
    const d = vals[i] - vals[i - 1];
    if (d > 0) gSum += d;
    else lSum += -d;
  }
  let ag = gSum / period;
  let al = lSum / period;
  for (let i = period + 1; i < vals.length; i++) {
    const d = vals[i] - vals[i - 1];
    ag = (ag * (period - 1) + (d > 0 ? d : 0)) / period;
    al = (al * (period - 1) + (d < 0 ? -d : 0)) / period;
  }
  if (al === 0) return ag > 0 ? 100 : 50;
  return 100 - 100 / (1 + ag / al);
}

function downsampleSeries(points, maxPoints = 36) {
  if (!Array.isArray(points) || points.length <= maxPoints) return points || [];
  const step = (points.length - 1) / (maxPoints - 1);
  const out = [];
  for (let i = 0; i < maxPoints; i++) {
    out.push(points[Math.round(i * step)]);
  }
  return out;
}

export function summarizeWindow(points) {
  if (!Array.isArray(points) || points.length < 2) {
    return null;
  }
  const vals = points.map((p) => p.v);
  const first = vals[0];
  const last = vals[vals.length - 1];
  const high = Math.max(...vals);
  const low = Math.min(...vals);
  const changePct = first > 0 ? Number((((last - first) / first) * 100).toFixed(2)) : 0;
  return {
    start: roundPrice(first),
    current: roundPrice(last),
    high: roundPrice(high),
    low: roundPrice(low),
    change_pct: changePct,
    direction: changePct > 0.05 ? "up" : changePct < -0.05 ? "down" : "flat",
    series: downsampleSeries(points, 36).map((p) => ({ t: p.t, v: roundPrice(p.v) })),
  };
}

async function fetchYahooSeries(ticker, range, interval, scale = 1) {
  try {
    const resp = await fetch(
      `https://query1.finance.yahoo.com/v8/finance/chart/${encodeURIComponent(ticker)}?range=${encodeURIComponent(range)}&interval=${encodeURIComponent(interval)}`,
      withTimeout({ headers: FETCH_HEADERS, cf: { cacheTtl: 300, cacheEverything: true } })
    );
    if (!resp.ok) return [];
    const data = await resp.json();
    const res = data?.chart?.result?.[0];
    const ts = res?.timestamp || [];
    const closes = res?.indicators?.quote?.[0]?.close || [];
    const pts = [];
    for (let i = 0; i < Math.min(ts.length, closes.length); i++) {
      const c = Number(closes[i]);
      if (Number.isFinite(c) && c > 0) {
        pts.push({ t: Number(ts[i]) * 1000, v: c * scale });
      }
    }
    return pts;
  } catch {
    return [];
  }
}

async function resolveAlternateCryptoTicker(sym) {
  try {
    const resp = await fetch(
      `https://query1.finance.yahoo.com/v1/finance/search?q=${encodeURIComponent(sym + "-USD")}&quotesCount=5&newsCount=0`,
      withTimeout({ headers: FETCH_HEADERS, cf: { cacheTtl: 600, cacheEverything: true } })
    );
    if (!resp.ok) return null;
    const data = await resp.json();
    const match = (data?.quotes || []).find(
      (q) => String(q?.quoteType || "").toUpperCase() === "CRYPTOCURRENCY" && String(q?.symbol || "").endsWith("-USD")
    );
    return match?.symbol || null;
  } catch {
    return null;
  }
}

function decodeXmlText(raw) {
  return String(raw || "")
    .replace(/<!\[CDATA\[([\s\S]*?)\]\]>/g, "$1")
    .replace(/<[^>]+>/g, " ")
    .replace(/&amp;/g, "&")
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&quot;/g, '"')
    .replace(/&#39;|&apos;/g, "'")
    .replace(/[ \t\r\n]+/g, " ")
    .trim();
}

const CLEAN_YAHOO_NEWS_QUERIES = {
  USD: "US Dollar Federal Reserve DXY",
  EUR: "Euro ECB currency",
  GBP: "British Pound Bank of England",
  SAR: "Saudi economy Aramco",
  AED: "UAE Dubai economy",
  EGP: "Egypt economy pound IMF",
  TRY: "Turkey lira central bank",
  SYP: "Syria economy currency",
  RUB: "Russian ruble economy",
  XAU_OZ: "Gold price bullion",
  GRAM_24K: "Gold price bullion",
  GRAM_21K: "Gold price bullion",
  GRAM_18K: "Gold price bullion",
  XAG_OZ: "Silver price metal",
  XAG_GRAM: "Silver price metal",
  BTC: "Bitcoin crypto",
  ETH: "Ethereum crypto",
  SOL: "Solana crypto",
  XRP: "Ripple XRP crypto",
};

const ASSET_FILTER_KEYWORDS_AR = {
  USD: ["دولار", "الفيدرالي", "أمريك", "سندات", "usd", "dxy"],
  EUR: ["يورو", "أوروب", "المركزي الأوروبي", "eur"],
  GBP: ["إسترليني", "استرليني", "بريطاني", "إنجلترا", "gbp"],
  SAR: ["سعود", "الريال", "أرامكو", "تاسي", "sar"],
  AED: ["إمارات", "دبي", "أبوظبي", "الدرهم", "aed"],
  EGP: ["مصر", "الجنيه المصري", "القاهرة", "egp"],
  TRY: ["ترك", "الليرة التركية", "أنقرة", "try"],
  SYP: ["سوري", "دمشق", "الليرة السورية", "syp"],
  XAU_OZ: ["ذهب", "الذهب", "الأونصة", "سبائك", "xau", "gold"],
  GRAM_24K: ["ذهب", "الذهب", "الأونصة", "xau", "gold"],
  GRAM_21K: ["ذهب", "الذهب", "الأونصة", "xau", "gold"],
  GRAM_18K: ["ذهب", "الذهب", "الأونصة", "xau", "gold"],
  XAG_OZ: ["فضة", "الفضة", "معادن", "xag", "silver"],
  XAG_GRAM: ["فضة", "الفضة", "معادن", "xag", "silver"],
  BTC: ["بيتكوين", "بتكوين", "btc", "bitcoin", "كريبتو"],
  ETH: ["إيثيريوم", "ايثيريوم", "eth", "ethereum"],
  SOL: ["سولانا", "sol", "solana"],
  XRP: ["ريبل", "xrp", "ripple"],
};

async function fetchRssArticles(url, defaultSource) {
  try {
    const resp = await fetch(
      url,
      withTimeout({
        headers: {
          "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) RatePathDetail/2.0",
          Accept: "application/rss+xml, application/xml, text/xml",
        },
        cf: { cacheTtl: 300, cacheEverything: true },
      }, 5500)
    );
    if (!resp.ok) return [];
    const xml = await resp.text();
    const items = [];
    const itemRe = /<item\b[^>]*>([\s\S]*?)<\/item>/gi;
    let m;
    while ((m = itemRe.exec(xml)) !== null && items.length < 18) {
      const block = m[1];
      const titleM = block.match(/<title[^>]*>([\s\S]*?)<\/title>/i);
      const linkM = block.match(/<link[^>]*>([\s\S]*?)<\/link>/i);
      const pubM = block.match(/<pubDate[^>]*>([\s\S]*?)<\/pubDate>/i);
      const srcM = block.match(/<source[^>]*>([\s\S]*?)<\/source>/i);
      const title = decodeXmlText(titleM?.[1] || "");
      const link = decodeXmlText(linkM?.[1] || "").trim();
      if (title.length >= 10 && /^https?:\/\//i.test(link)) {
        const ms = pubM?.[1] ? Date.parse(pubM[1].trim()) : NaN;
        items.push({
          title: title.slice(0, 175),
          url: link,
          source: decodeXmlText(srcM?.[1] || defaultSource),
          published_at: Number.isFinite(ms) ? new Date(ms).toISOString() : new Date().toISOString(),
        });
      }
    }
    return items;
  } catch {
    return [];
  }
}

async function fetchYahooSearchNews(query) {
  try {
    const resp = await fetch(
      `https://query1.finance.yahoo.com/v1/finance/search?q=${encodeURIComponent(query)}&quotesCount=0&newsCount=6`,
      withTimeout({ headers: FETCH_HEADERS, cf: { cacheTtl: 240, cacheEverything: true } }, 5500)
    );
    if (!resp.ok) return [];
    const data = await resp.json();
    return (data?.news || [])
      .map((n) => ({
        title: String(n?.title || "").trim(),
        url: String(n?.link || "").trim(),
        source: String(n?.publisher || "Yahoo Finance").trim(),
        published_at: n?.providerPublishTime
          ? new Date(Number(n.providerPublishTime) * 1000).toISOString()
          : new Date().toISOString(),
      }))
      .filter((x) => x.title.length >= 10 && /^https?:\/\//i.test(x.url))
      .slice(0, 5);
  } catch {
    return [];
  }
}

async function fetchTargetedAssetNews(id, code, nameHint, ticker, lang) {
  const isAr = lang === "ar";
  const isCrypto = ["BTC", "ETH", "SOL", "XRP", "BNB", "DOGE", "ADA", "PEPE", "TON", "SUI", "TRX", "LTC", "AVAX", "LINK", "SHIB"].includes(code);
  const isMetal = id.startsWith("XAU") || id.startsWith("GRAM_") || id.startsWith("XAG");

  if (isAr) {
    // 1. Cloudflare-proven reliable Arabic financial RSS feeds (Investing AR + CoinTelegraph AR + SkyNews Business)
    const arSources = isCrypto
      ? [
          ["https://sa.investing.com/rss/news_301.rss", "Investing كريبتو"],
          ["https://ar.cointelegraph.com/rss", "كوين تليغراف عربي"],
          ["https://sa.investing.com/rss/news.rss", "Investing عربي"],
        ]
      : isMetal
      ? [
          ["https://sa.investing.com/rss/news_11.rss", "Investing سلع وذهب"],
          ["https://sa.investing.com/rss/news.rss", "Investing عربي"],
          ["https://www.skynewsarabia.com/rss/v1/business.xml", "سكاي نيوز اقتصاد"],
        ]
      : [
          ["https://sa.investing.com/rss/news_1.rss", "Investing عملات"],
          ["https://sa.investing.com/rss/news.rss", "Investing عربي"],
          ["https://www.skynewsarabia.com/rss/v1/business.xml", "سكاي نيوز اقتصاد"],
        ];

    const batches = await Promise.all(arSources.map(([u, s]) => fetchRssArticles(u, s)));
    const allAr = batches.flat();
    const kws = [
      ...(ASSET_FILTER_KEYWORDS_AR[id] || ASSET_FILTER_KEYWORDS_AR[code] || []),
      code.toLowerCase(),
      String(nameHint || "").toLowerCase(),
    ].filter((k) => k && k.length >= 2);

    const exactMatches = allAr.filter((it) => {
      const low = it.title.toLowerCase();
      return kws.some((k) => low.includes(k));
    });

    const chosen = exactMatches.length >= 2 ? exactMatches : [...exactMatches, ...allAr];
    const seen = new Set();
    const dedup = [];
    for (const item of chosen) {
      const k = item.title.slice(0, 55);
      if (seen.has(k)) continue;
      seen.add(k);
      dedup.push(item);
      if (dedup.length >= 5) break;
    }
    if (dedup.length > 0) return dedup;
  }

  // English (or Arabic fallback): Yahoo Finance Search using clean keyword (NEVER raw `DX-Y.NYB` or `BTC-USD`) + CoinTelegraph/CNBC RSS
  const yahooQuery = CLEAN_YAHOO_NEWS_QUERIES[id] || CLEAN_YAHOO_NEWS_QUERIES[code] || `${nameHint || code} ${code} price`;
  const [yItems, rssFallback] = await Promise.all([
    fetchYahooSearchNews(yahooQuery),
    isCrypto
      ? fetchRssArticles("https://cointelegraph.com/rss", "CoinTelegraph")
      : fetchRssArticles("https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=20910258", "CNBC Economy"),
  ]);

  if (yItems.length >= 2) return yItems.slice(0, 5);

  const kwEn = [code.toLowerCase(), String(nameHint || "").toLowerCase()].filter((k) => k && k.length >= 2);
  const filteredRss = rssFallback.filter((it) => kwEn.some((k) => it.title.toLowerCase().includes(k)));
  const merged = [...yItems, ...(filteredRss.length ? filteredRss : rssFallback)];
  const seenEn = new Set();
  return merged
    .filter((it) => {
      const k = it.title.toLowerCase().slice(0, 55);
      if (seenEn.has(k)) return false;
      seenEn.add(k);
      return true;
    })
    .slice(0, 5);
}

function buildBilingualAnalysis({ unitLabelAr, unitLabelEn, timeframes, rsi14, ema20, ema50, spTodayMode }) {
  if (spTodayMode) {
    return {
      ar: "تُعرض الليرة السورية وفق السعر الفعلي المتداول في السوق عبر SP-Today وليس السعر المصرفي الرسمي. يمكنك استخدام الحاسبة أدناه للتحويل الفوري ومتابعة آخر الأخبار الاقتصادية المرتبطة بها.",
      en: "The Syrian Pound (SYP) tracks live street pricing via SP-Today rather than official bank charts. Use the converter below for exact calculations and review the latest related economic headlines.",
    };
  }

  const tf1d = timeframes["1d"];
  const tf1w = timeframes["1w"];
  const tf1m = timeframes["1m"];
  const tf1y = timeframes["1y"];

  if (!tf1d && !tf1m && !tf1y) {
    return {
      ar: `البيانات التاريخية التفصيلية لـ ${unitLabelAr} غير متوفرة حالياً من المزوّد الخارجي، بينما السعر اللحظي وحاسبة التحويل وأحدث الأخبار متاحة أدناه.`,
      en: `Extended multi-timeframe history for ${unitLabelEn} is currently unavailable upstream; instant conversion and live headlines remain active below.`,
    };
  }

  const fmtSign = (n) => (n > 0 ? `+${n}%` : `${n}%`);
  const partsAr = [];
  const partsEn = [];

  if (tf1d && tf1w) {
    partsAr.push(
      `على المدى القصير، سجل ${unitLabelAr} تغيّراً يومياً بـ ${fmtSign(tf1d.change_pct)} وأسبوعياً بـ ${fmtSign(tf1w.change_pct)}.`
    );
    partsEn.push(
      `Short term, ${unitLabelEn} moved ${fmtSign(tf1d.change_pct)} over 24h and ${fmtSign(tf1w.change_pct)} over the past week.`
    );
  }
  if (tf1m && tf1y) {
    partsAr.push(
      `وعلى المدى المتوسط والسنوي، بلغ الأداء الشهري ${fmtSign(tf1m.change_pct)} والسنوي ${fmtSign(tf1y.change_pct)} (نطاق العام: ${tf1y.low} – ${tf1y.high}).`
    );
    partsEn.push(
      `Over the broader horizon, performance stands at ${fmtSign(tf1m.change_pct)} (1M) and ${fmtSign(tf1y.change_pct)} (1Y) within a 52-week range of ${tf1y.low}–${tf1y.high}.`
    );
  }
  if (rsi14 !== null && ema20 !== null && ema50 !== null) {
    const trendAr = ema20 > ema50 ? "يميل الاتجاه الفني العام للصعود (EMA20 أعلى من EMA50)" : "يميل الاتجاه الفني العام للهبوط (EMA20 أدنى من EMA50)";
    const trendEn = ema20 > ema50 ? "Technical structure leans constructive (EMA20 > EMA50)" : "Technical structure leans defensive (EMA20 < EMA50)";
    const rsiNoteAr = rsi14 > 70 ? "مع تشبع شرائي مرتفع" : rsi14 < 30 ? "مع تشبع بيعي واضح" : "ضمن نطاق زخم متوازن";
    const rsiNoteEn = rsi14 > 70 ? "with overbought RSI" : rsi14 < 30 ? "with oversold RSI" : "with balanced RSI momentum";
    partsAr.push(`${trendAr} ومؤشر القوة النسبية RSI(14) عند ${rsi14} ${rsiNoteAr}.`);
    partsEn.push(`${trendEn} and RSI(14) at ${rsi14} ${rsiNoteEn}.`);
  }

  return {
    ar: partsAr.join(" "),
    en: partsEn.join(" "),
  };
}

export async function onRequestOptions() {
  return new Response(null, { status: 204, headers: CORS_HEADERS });
}

export async function onRequestGet(context) {
  const url = new URL(context.request.url);
  const id = (url.searchParams.get("id") || "").replace(/[^A-Za-z0-9._-]/g, "").trim().toUpperCase();
  const code = (url.searchParams.get("code") || id || "USD").replace(/[^A-Za-z0-9._-]/g, "").trim().toUpperCase();
  const category = (url.searchParams.get("category") || "").trim().toLowerCase();
  const nameHint = (url.searchParams.get("name") || "").replace(/[\u0000-\u001F\u007F<>]/g, "").trim();
  const lang = String(url.searchParams.get("lang") || "ar").toLowerCase() === "en" ? "en" : "ar";

  const spec = resolveYahooSpec(id, code, category);
  let activeTicker = spec.ticker;

  let hourlyPts = [];
  let dailyPts = [];

  const newsPromise = fetchTargetedAssetNews(id || code, code, nameHint, activeTicker, lang);

  if (!spec.skipYahoo && activeTicker) {
    [hourlyPts, dailyPts] = await Promise.all([
      fetchYahooSeries(activeTicker, "5d", "1h", spec.scale),
      fetchYahooSeries(activeTicker, "1y", "1d", spec.scale),
    ]);

    // If a meme/long-tail crypto ticker missed on `<SYM>-USD`, try resolving its Yahoo symbol
    if (dailyPts.length < 2 && spec.isCrypto && spec.cryptoSym) {
      const alt = await resolveAlternateCryptoTicker(spec.cryptoSym);
      if (alt && alt !== activeTicker) {
        activeTicker = alt;
        [hourlyPts, dailyPts] = await Promise.all([
          fetchYahooSeries(activeTicker, "5d", "1h", spec.scale),
          fetchYahooSeries(activeTicker, "1y", "1d", spec.scale),
        ]);
      }
    }
  }

  const nowMs = Date.now();
  const cutoff1d = nowMs - 24 * 3600 * 1000;
  const cutoff1w = nowMs - 7 * 24 * 3600 * 1000;
  const cutoff1m = nowMs - 30 * 24 * 3600 * 1000;

  let pts1d = hourlyPts.filter((p) => p.t >= cutoff1d);
  if (pts1d.length < 2) pts1d = hourlyPts.slice(-24);
  if (pts1d.length < 2) pts1d = dailyPts.slice(-2);

  let pts1w = hourlyPts.filter((p) => p.t >= cutoff1w);
  if (pts1w.length < 2) pts1w = dailyPts.slice(-7);

  let pts1m = dailyPts.filter((p) => p.t >= cutoff1m);
  if (pts1m.length < 2) pts1m = dailyPts.slice(-22);

  const pts1y = dailyPts;

  const timeframes = {
    "1d": summarizeWindow(pts1d),
    "1w": summarizeWindow(pts1w),
    "1m": summarizeWindow(pts1m),
    "1y": summarizeWindow(pts1y),
  };

  const dailyVals = dailyPts.map((p) => p.v);
  const rsiRaw = computeRsi(dailyVals, 14);
  const ema20Raw = computeEma(dailyVals, 20);
  const ema50Raw = computeEma(dailyVals, 50);
  const rsi14 = rsiRaw !== null ? Number(rsiRaw.toFixed(1)) : null;
  const ema20 = ema20Raw !== null ? roundPrice(ema20Raw) : null;
  const ema50 = ema50Raw !== null ? roundPrice(ema50Raw) : null;

  const headlines = await newsPromise;
  const analysis = buildBilingualAnalysis({
    unitLabelAr: spec.unitLabelAr,
    unitLabelEn: spec.unitLabelEn,
    timeframes,
    rsi14,
    ema20,
    ema50,
    spTodayMode: spec.skipYahoo,
  });

  return new Response(
    JSON.stringify({
      id: id || code,
      code,
      ticker: activeTicker,
      unit_label_ar: spec.unitLabelAr,
      unit_label_en: spec.unitLabelEn,
      sp_today_mode: Boolean(spec.skipYahoo),
      timeframes,
      technicals: {
        rsi14,
        ema20,
        ema50,
        trend: ema20 && ema50 ? (ema20 > ema50 ? "up" : "down") : "neutral",
      },
      analysis,
      headlines,
      generated_at: new Date().toISOString(),
    }),
    { status: 200, headers: CORS_HEADERS }
  );
}
