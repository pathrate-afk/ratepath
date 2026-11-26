/**
 * RatePath (ريت باث) — Cloudflare Pages Function: `/api/cmc-search?q=<query>`
 *
 * Server-side CORS-free CoinMarketCap long-tail cryptocurrency search proxy.
 * - Cached at Cloudflare edge for 120 seconds (`Cache-Control: public, max-age=120, s-maxage=120`).
 * - Supports optional `CMC_PRO_API_KEY` env binding with seamless public CoinMarketCap v3
 *   and CoinGecko fallback so long-tail token searches outside the Top 300 always succeed.
 */

const CORS_HEADERS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, OPTIONS",
  "Access-Control-Allow-Headers": "Content-Type",
  "Content-Type": "application/json; charset=utf-8",
  "X-Content-Type-Options": "nosniff",
  "X-Robots-Tag": "noindex",
};

const CACHE_CONTROL = "public, max-age=120, s-maxage=120";
const CMC_LOGO = (id) => `https://s2.coinmarketcap.com/static/img/coins/64x64/${id}.png`;

function withTimeout(init = {}, ms = 8000) {
  if (typeof AbortSignal !== "undefined" && typeof AbortSignal.timeout === "function") {
    return { ...init, signal: AbortSignal.timeout(ms) };
  }
  return init;
}

function formatUsdPrice(num) {
  const n = Number(num);
  if (!Number.isFinite(n) || n <= 0) return null;
  if (n >= 1) return n.toFixed(2);
  if (n >= 0.01) return n.toFixed(4);
  if (n >= 0.0001) return n.toFixed(6);
  return n.toFixed(8);
}

function formatPct(num) {
  const n = Number(num);
  if (!Number.isFinite(n)) return "0.00";
  return `${n > 0 ? "+" : ""}${n.toFixed(2)}`;
}

function jsonResponse(body, status = 200, cacheHeader = CACHE_CONTROL) {
  return new Response(JSON.stringify(body), {
    status,
    headers: {
      ...CORS_HEADERS,
      "Cache-Control": cacheHeader,
    },
  });
}

async function searchCoinMarketCap(query, env) {
  const qLower = query.toLowerCase();
  const qUpper = query.toUpperCase();
  const proKey = (env?.CMC_PRO_API_KEY || "").trim();
  const hasProKey = proKey && proKey.toLowerCase() !== "placeholder";

  // 1. Query CMC public active currency map to resolve long-tail CMC IDs
  const mapResp = await fetch(
    "https://api.coinmarketcap.com/data-api/v3/map/all?listing_status=active&start=1&limit=4000",
    withTimeout({
      headers: {
        Accept: "application/json",
        "User-Agent": "RatePath-Edge/2.0",
      },
      cf: { cacheTtl: 600, cacheEverything: true },
    })
  );
  if (!mapResp.ok) throw new Error(`CMC map HTTP ${mapResp.status}`);
  const mapJson = await mapResp.json();
  const allCoins = mapJson?.data?.cryptoCurrencyMap || [];
  if (!Array.isArray(allCoins) || allCoins.length === 0) {
    throw new Error("CMC map empty");
  }

  // Score matches: exact symbol > symbol prefix > name prefix > substring
  const scored = [];
  for (const c of allCoins) {
    const sym = String(c.symbol || "");
    const name = String(c.name || "");
    const symU = sym.toUpperCase();
    const nameL = name.toLowerCase();
    let score = 0;
    if (symU === qUpper) score = 100;
    else if (nameL === qLower) score = 95;
    else if (symU.startsWith(qUpper)) score = 75;
    else if (nameL.startsWith(qLower)) score = 60;
    else if (symU.includes(qUpper) || nameL.includes(qLower)) score = 35;

    if (score > 0) {
      const rank = Number(c.rank) || 99999;
      scored.push({ c, score, rank });
    }
  }
  scored.sort((a, b) => b.score - a.score || a.rank - b.rank);
  const topMatches = scored.slice(0, 15).map((x) => x.c);
  if (topMatches.length === 0) return [];

  const idList = topMatches.map((c) => c.id).join(",");

  // 2. Fetch live USD quotes for matched CMC IDs (Pro API if configured, else Public v3)
  let quotesById = {};
  if (hasProKey) {
    const qResp = await fetch(
      `https://pro-api.coinmarketcap.com/v2/cryptocurrency/quotes/latest?id=${encodeURIComponent(idList)}&convert=USD`,
      {
        headers: {
          "X-CMC_PRO_API_KEY": proKey,
          Accept: "application/json",
        },
        cf: { cacheTtl: 120, cacheEverything: true },
      }
    );
    if (qResp.ok) {
      const qJson = await qResp.json();
      const dataObj = qJson?.data || {};
      for (const [cid, coin] of Object.entries(dataObj)) {
        const usd = coin?.quote?.USD;
        if (usd?.price != null) {
          quotesById[String(cid)] = {
            price: usd.price,
            pct24h: usd.percent_change_24h,
            rank: coin.cmc_rank,
          };
        }
      }
    }
  }

  if (Object.keys(quotesById).length === 0) {
    const qResp = await fetch(
      `https://api.coinmarketcap.com/data-api/v3/cryptocurrency/quotes/latest?id=${encodeURIComponent(idList)}`,
      {
        headers: {
          Accept: "application/json",
          "User-Agent": "RatePath-Edge/2.0",
        },
        cf: { cacheTtl: 120, cacheEverything: true },
      }
    );
    if (qResp.ok) {
      const qJson = await qResp.json();
      const list = qJson?.data || [];
      if (Array.isArray(list)) {
        for (const coin of list) {
          const usd = (coin?.quotes || []).find((q) => q?.name === "USD") || coin?.quotes?.[0];
          if (usd?.price != null) {
            quotesById[String(coin.id)] = {
              price: usd.price,
              pct24h: usd.percentChange24h,
              rank: coin.cmcRank,
            };
          }
        }
      }
    }
  }

  const results = [];
  for (const coin of topMatches) {
    const quote = quotesById[String(coin.id)];
    const priceStr = quote ? formatUsdPrice(quote.price) : null;
    if (!priceStr) continue;
    const sym = String(coin.symbol || "").toUpperCase();
    const rank = Number(quote.rank || coin.rank || 9999);
    const id =
      (sym === "BTC" || sym === "ETH" || sym === "SOL") && rank <= 25
        ? sym
        : `CMC_${coin.id}`;
    results.push({
      id,
      cmc_id: Number(coin.id),
      symbol: sym,
      name: String(coin.name || sym),
      rank,
      price_usd: priceStr,
      change_24h_pct: formatPct(quote.pct24h),
      logo_url: CMC_LOGO(coin.id),
      category: "crypto",
    });
  }
  return results;
}

async function searchCoinGeckoFallback(query) {
  const sResp = await fetch(
    `https://api.coingecko.com/api/v3/search?query=${encodeURIComponent(query)}`,
    {
      headers: { Accept: "application/json", "User-Agent": "RatePath-Edge/2.0" },
      cf: { cacheTtl: 120, cacheEverything: true },
    }
  );
  if (!sResp.ok) throw new Error(`CoinGecko search HTTP ${sResp.status}`);
  const sJson = await sResp.json();
  const topCoins = (sJson?.coins || []).slice(0, 12);
  if (topCoins.length === 0) return [];

  const cgIds = topCoins.map((c) => c.id).join(",");
  const pResp = await fetch(
    `https://api.coingecko.com/api/v3/simple/price?ids=${encodeURIComponent(cgIds)}&vs_currencies=usd&include_24hr_change=true`,
    {
      headers: { Accept: "application/json", "User-Agent": "RatePath-Edge/2.0" },
      cf: { cacheTtl: 120, cacheEverything: true },
    }
  );
  if (!pResp.ok) throw new Error(`CoinGecko price HTTP ${pResp.status}`);
  const prices = await pResp.json();

  const results = [];
  for (const c of topCoins) {
    const p = prices?.[c.id];
    const priceStr = p ? formatUsdPrice(p.usd) : null;
    if (!priceStr) continue;
    const sym = String(c.symbol || "").toUpperCase();
    results.push({
      id: `CG_${String(c.id).replace(/[^a-zA-Z0-9_-]/g, "").toUpperCase()}`,
      cmc_id: null,
      symbol: sym,
      name: String(c.name || sym),
      rank: Number(c.market_cap_rank || 9999),
      price_usd: priceStr,
      change_24h_pct: formatPct(p.usd_24h_change),
      logo_url: String(c.large || c.thumb || ""),
      category: "crypto",
    });
  }
  return results;
}

export async function onRequestOptions() {
  return new Response(null, { status: 204, headers: CORS_HEADERS });
}

export async function onRequestGet(context) {
  const url = new URL(context.request.url);
  const rawQuery = (url.searchParams.get("q") || "").replace(/[\u0000-\u001F\u007F<>]/g, "").trim();
  if (rawQuery.length < 2 || rawQuery.length > 64) {
    return jsonResponse(
      { query: rawQuery, source: "validation", results: [], error: "Query must be 2-64 chars." },
      400,
      "no-store"
    );
  }

  try {
    const cmcResults = await searchCoinMarketCap(rawQuery, context.env);
    if (cmcResults.length > 0) {
      return jsonResponse({ query: rawQuery, source: "coinmarketcap", results: cmcResults }, 200);
    }
  } catch {
    // Fall through seamlessly to CoinGecko fallback
  }

  try {
    const cgResults = await searchCoinGeckoFallback(rawQuery);
    return jsonResponse({ query: rawQuery, source: "coingecko_fallback", results: cgResults }, 200);
  } catch {
    return jsonResponse(
      { query: rawQuery, source: "unavailable", results: [], error: "Upstream search temporarily unavailable." },
      502,
      "no-store"
    );
  }
}
