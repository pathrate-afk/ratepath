/**
 * RatePath (ريت باث) — Cloudflare Pages Function: `GET /api/asset-intel`
 *
 * Universal Asset & Global Stocks/Company News Lookup for the Personal AI Tracker Bot:
 * - `?q=<query>` -> Searches global stocks & companies (AAPL, NVDA, TSLA, 2222.SR, SPY, GLD, Arabic company names...)
 *   and returns live prices + 24h % changes.
 * - `?symbol=<SYM>&category=<stock|crypto|fiat|metal>&name=<optional>` -> Returns live spot price + latest 4
 *   verified company/market news headlines (Google News RSS).
 * - Informational data service only — not financial advice.
 */

const CORS_HEADERS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, OPTIONS",
  "Access-Control-Allow-Headers": "Content-Type",
  "Content-Type": "application/json; charset=utf-8",
  "Cache-Control": "public, max-age=90, s-maxage=90",
  "X-Content-Type-Options": "nosniff",
  "X-Robots-Tag": "noindex",
};

const FETCH_HEADERS = {
  Accept: "application/json",
  "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) RatePathIntel/2.0",
};

const ARABIC_STOCK_ALIASES = [
  ["أبل", "AAPL", "Apple Inc.", "شركة أبل"],
  ["ابل", "AAPL", "Apple Inc.", "شركة أبل"],
  ["تسلا", "TSLA", "Tesla, Inc.", "شركة تسلا"],
  ["إنفيديا", "NVDA", "NVIDIA Corp.", "شركة إنفيديا"],
  ["انفيديا", "NVDA", "NVIDIA Corp.", "شركة إنفيديا"],
  ["مايكروسوفت", "MSFT", "Microsoft Corp.", "شركة مايكروسوفت"],
  ["أمازون", "AMZN", "Amazon.com, Inc.", "شركة أمازون"],
  ["امازون", "AMZN", "Amazon.com, Inc.", "شركة أمازون"],
  ["جوجل", "GOOGL", "Alphabet Inc.", "شركة جوجل (Alphabet)"],
  ["غوغل", "GOOGL", "Alphabet Inc.", "شركة جوجل (Alphabet)"],
  ["ميتا", "META", "Meta Platforms", "شركة ميتا"],
  ["أرامكو", "2222.SR", "Saudi Aramco", "أرامكو السعودية"],
  ["ارامكو", "2222.SR", "Saudi Aramco", "أرامكو السعودية"],
  ["الراجحي", "1120.SR", "Al Rajhi Bank", "مصرف الراجحي"],
  ["سابك", "2010.SR", "SABIC", "سابك"],
  ["نتفليكس", "NFLX", "Netflix, Inc.", "شركة نتفليكس"],
  ["إنتل", "INTC", "Intel Corp.", "شركة إنتل"],
  ["كوين بيس", "COIN", "Coinbase Global", "شركة كوين بيس"],
  ["ستراتيجي", "MSTR", "Strategy (MicroStrategy)", "شركة مايكروستراتيجي"],
  ["الذهب", "GC=F", "Gold Futures (XAU)", "عقود الذهب"],
  ["الفضة", "SI=F", "Silver Futures (XAG)", "عقود الفضة"],
  ["النفط", "CL=F", "Crude Oil WTI", "النفط الخام WTI"],
  ["برنت", "BZ=F", "Brent Crude Oil", "نفط برنت"],
  ["ناسداك", "^IXIC", "NASDAQ Composite", "مؤشر ناسداك"],
  ["داو جونز", "^DJI", "Dow Jones Industrial", "مؤشر داو جونز"],
  ["إس آند بي", "SPY", "S&P 500 ETF (SPY)", "صندوق S&P 500"],
];

function withTimeout(init = {}, ms = 6500) {
  if (typeof AbortSignal !== "undefined" && typeof AbortSignal.timeout === "function") {
    return { ...init, signal: AbortSignal.timeout(ms) };
  }
  return init;
}

function fmtPrice(num) {
  const n = Number(num);
  if (!Number.isFinite(n) || n <= 0) return null;
  if (n >= 100) return n.toFixed(2);
  if (n >= 1) return n.toFixed(2);
  if (n >= 0.01) return n.toFixed(4);
  return n.toFixed(6);
}

function fmtPct(num) {
  const n = Number(num);
  if (!Number.isFinite(n)) return "0.00";
  return `${n > 0 ? "+" : ""}${n.toFixed(2)}`;
}

async function fetchYahooQuote(symbol) {
  try {
    const resp = await fetch(
      `https://query1.finance.yahoo.com/v8/finance/chart/${encodeURIComponent(symbol)}?range=5d&interval=1d`,
      withTimeout({ headers: FETCH_HEADERS, cf: { cacheTtl: 90, cacheEverything: true } })
    );
    if (!resp.ok) return null;
    const data = await resp.json();
    const res = data?.chart?.result?.[0];
    const meta = res?.meta;
    const price = Number(meta?.regularMarketPrice);
    if (!Number.isFinite(price) || price <= 0) return null;
    const prev = Number(meta?.chartPreviousClose || meta?.previousClose || price);
    const chg = prev > 0 ? ((price - prev) / prev) * 100 : 0;
    return {
      symbol: String(meta?.symbol || symbol).toUpperCase(),
      name_en: String(meta?.shortName || meta?.longName || symbol),
      currency: String(meta?.currency || "USD"),
      exchange: String(meta?.exchangeName || ""),
      price: fmtPrice(price),
      change_24h_pct: fmtPct(chg),
    };
  } catch {
    return null;
  }
}

async function searchGlobalStocks(query) {
  const qClean = String(query || "").trim();
  const candidates = [];
  const seen = new Set();

  // 1. Arabic company & index aliases first
  for (const [arKey, sym, nameEn, nameAr] of ARABIC_STOCK_ALIASES) {
    if (qClean.includes(arKey) && !seen.has(sym)) {
      seen.add(sym);
      candidates.push({ symbol: sym, name_en: nameEn, name_ar: nameAr, exchange: "Global" });
    }
  }

  // 2. Yahoo Finance global symbol/company search (stocks, ETFs, indices, commodities)
  try {
    const resp = await fetch(
      `https://query1.finance.yahoo.com/v1/finance/search?q=${encodeURIComponent(qClean)}&quotesCount=8&newsCount=0`,
      withTimeout({ headers: FETCH_HEADERS, cf: { cacheTtl: 180, cacheEverything: true } })
    );
    if (resp.ok) {
      const json = await resp.json();
      const quotes = Array.isArray(json?.quotes) ? json.quotes : [];
      for (const q of quotes) {
        const sym = String(q?.symbol || "").trim().toUpperCase();
        const qt = String(q?.quoteType || "").toUpperCase();
        if (!sym || seen.has(sym)) continue;
        if (!["EQUITY", "ETF", "INDEX", "FUTURE", "COMMODITY"].includes(qt)) continue;
        seen.add(sym);
        candidates.push({
          symbol: sym,
          name_en: String(q?.shortname || q?.longname || sym),
          name_ar: String(q?.shortname || q?.longname || sym),
          exchange: String(q?.exchDisp || q?.exchange || ""),
          quote_type: qt.toLowerCase(),
        });
        if (candidates.length >= 6) break;
      }
    }
  } catch {
    // Keep alias candidates if Yahoo search throttles
  }

  // Hydrate top 5 with live prices concurrently
  const enriched = await Promise.all(
    candidates.slice(0, 5).map(async (item) => {
      const live = await fetchYahooQuote(item.symbol);
      return {
        id: `STK_${item.symbol.replace(/[^A-Z0-9._-]/g, "")}`,
        symbol: item.symbol,
        name_en: live?.name_en || item.name_en,
        name_ar: item.name_ar || live?.name_en || item.symbol,
        exchange: live?.exchange || item.exchange || "Stock",
        currency: live?.currency || "USD",
        price: live?.price || null,
        change_24h_pct: live?.change_24h_pct || "0.00",
        category: "stock",
      };
    })
  );

  return enriched.filter((x) => x.price !== null || candidates.length <= 2);
}

async function fetchAssetNewsHeadlines(symbol, name, category = "stock") {
  const topic =
    category === "stock"
      ? `${name || symbol} ${symbol} stock earnings company news`
      : category === "metal"
      ? `${name || symbol} gold silver precious metals price news`
      : `${name || symbol} ${symbol} market price news`;
  const rssUrl = `https://news.google.com/rss/search?q=${encodeURIComponent(topic)}&hl=en-US&gl=US&ceid=US:en`;
  try {
    const resp = await fetch(
      rssUrl,
      withTimeout({
        headers: { "User-Agent": "RatePathIntel/2.0", Accept: "application/rss+xml, text/xml" },
        cf: { cacheTtl: 180, cacheEverything: true },
      })
    );
    if (!resp.ok) return [];
    const xml = await resp.text();
    const items = [];
    const itemRe = /<item>([\s\S]*?)<\/item>/gi;
    let m;
    while ((m = itemRe.exec(xml)) !== null && items.length < 4) {
      const block = m[1];
      const titleM = block.match(/<title>(?:<!\[CDATA\[)?([\s\S]*?)(?:\]\]>)?<\/title>/i);
      const linkM = block.match(/<link>([\s\S]*?)<\/link>/i);
      const pubM = block.match(/<pubDate>([\s\S]*?)<\/pubDate>/i);
      const srcM = block.match(/<source[^>]*>([\s\S]*?)<\/source>/i);
      const title = (titleM?.[1] || "").replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim();
      const url = (linkM?.[1] || "").trim();
      if (title.length >= 12 && /^https?:\/\//i.test(url)) {
        items.push({
          title,
          url,
          source: (srcM?.[1] || "News Wire").replace(/<[^>]+>/g, "").trim(),
          published_at: (pubM?.[1] || "").trim(),
        });
      }
    }
    return items;
  } catch {
    return [];
  }
}

export async function onRequestOptions() {
  return new Response(null, { status: 204, headers: CORS_HEADERS });
}

export async function onRequestGet(context) {
  const url = new URL(context.request.url);
  const rawQuery = (url.searchParams.get("q") || "").replace(/[\u0000-\u001F\u007F<>]/g, "").trim();
  const rawSymbol = (url.searchParams.get("symbol") || "").replace(/[^A-Za-z0-9.=^_-]/g, "").trim().toUpperCase();
  const category = (url.searchParams.get("category") || "stock").toLowerCase();
  const nameHint = (url.searchParams.get("name") || "").replace(/[\u0000-\u001F\u007F<>]/g, "").trim();

  if (rawSymbol) {
    const [quote, headlines] = await Promise.all([
      category === "stock" || category === "metal" ? fetchYahooQuote(rawSymbol) : Promise.resolve(null),
      fetchAssetNewsHeadlines(rawSymbol, nameHint || rawSymbol, category),
    ]);
    return new Response(
      JSON.stringify({
        symbol: rawSymbol,
        category,
        quote,
        headlines,
        informational_only: true,
      }),
      { status: 200, headers: CORS_HEADERS }
    );
  }

  if (rawQuery.length < 2 || rawQuery.length > 64) {
    return new Response(
      JSON.stringify({ query: rawQuery, results: [], error: "Provide ?q= (2-64 chars) or ?symbol=" }),
      { status: 400, headers: { ...CORS_HEADERS, "Cache-Control": "no-store" } }
    );
  }

  const results = await searchGlobalStocks(rawQuery);
  return new Response(
    JSON.stringify({
      query: rawQuery,
      source: "yahoo_stocks",
      results,
    }),
    { status: 200, headers: CORS_HEADERS }
  );
}
