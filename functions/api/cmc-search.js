/**
 * RatePath (ريت باث) — Cloudflare Pages Function: `/api/cmc-search?q=<query>`
 *
 * Lightweight, edge-CPU-safe CORS-free cryptocurrency search proxy (<2ms CPU, <50KB JSON).
 * - Cached at Cloudflare edge for 120 seconds (`Cache-Control: public, max-age=120, s-maxage=120`).
 * - Tier 1: Optional `CMC_PRO_API_KEY` symbol quote -> CoinMarketCap lightweight `data-api/v3` search
 * - Tier 2: CoinGecko lightweight `/api/v3/search` + `/api/v3/simple/price` (~5KB payload, ultra-fast)
 * - Tier 3: Binance Vision public `/api/v3/ticker/24hr` direct symbol lookup fallback
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

function withTimeout(init = {}, ms = 7000) {
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

async function searchCoinMarketCapLightweight(query, env) {
  const qUpper = query.toUpperCase().replace(/[^A-Z0-9]/g, "");
  const proKey = (env?.CMC_PRO_API_KEY || "").trim();
  const hasProKey = proKey && proKey.toLowerCase() !== "placeholder";

  // Pro API direct symbol lookup (tiny payload)
  if (hasProKey && qUpper.length >= 2 && qUpper.length <= 12) {
    const qResp = await fetch(
      `https://pro-api.coinmarketcap.com/v2/cryptocurrency/quotes/latest?symbol=${encodeURIComponent(qUpper)}&convert=USD`,
      withTimeout({
        headers: { "X-CMC_PRO_API_KEY": proKey, Accept: "application/json" },
        cf: { cacheTtl: 120, cacheEverything: true },
      })
    );
    if (qResp.ok) {
      const qJson = await qResp.json();
      const entries = qJson?.data?.[qUpper] || [];
      const results = [];
      for (const coin of entries.slice(0, 6)) {
        const usd = coin?.quote?.USD;
        const priceStr = usd?.price != null ? formatUsdPrice(usd.price) : null;
        if (!priceStr) continue;
        const sym = String(coin.symbol || qUpper).toUpperCase();
        const rank = Number(coin.cmc_rank || 9999);
        results.push({
          id: (sym === "BTC" || sym === "ETH" || sym === "SOL") && rank <= 25 ? sym : `CMC_${coin.id}`,
          cmc_id: Number(coin.id),
          symbol: sym,
          name: String(coin.name || sym),
          rank,
          price_usd: priceStr,
          change_24h_pct: formatPct(usd.percent_change_24h),
          logo_url: CMC_LOGO(coin.id),
          category: "crypto",
        });
      }
      if (results.length > 0) return results;
    }
  }

  // Lightweight CMC public symbol quote lookup (small payload, zero 4000-coin map blowup)
  if (qUpper.length >= 2 && qUpper.length <= 12) {
    const qResp = await fetch(
      `https://api.coinmarketcap.com/data-api/v3/cryptocurrency/quotes/latest?symbol=${encodeURIComponent(qUpper)}`,
      withTimeout({
        headers: { Accept: "application/json", "User-Agent": "RatePath-Edge/2.0" },
        cf: { cacheTtl: 120, cacheEverything: true },
      })
    );
    if (qResp.ok) {
      const qJson = await qResp.json();
      const list = Array.isArray(qJson?.data) ? qJson.data : [];
      const results = [];
      for (const coin of list.slice(0, 8)) {
        const usd = (coin?.quotes || []).find((q) => q?.name === "USD") || coin?.quotes?.[0];
        const priceStr = usd?.price != null ? formatUsdPrice(usd.price) : null;
        if (!priceStr) continue;
        const sym = String(coin.symbol || qUpper).toUpperCase();
        const rank = Number(coin.cmcRank || 9999);
        results.push({
          id: (sym === "BTC" || sym === "ETH" || sym === "SOL") && rank <= 25 ? sym : `CMC_${coin.id}`,
          cmc_id: Number(coin.id),
          symbol: sym,
          name: String(coin.name || sym),
          rank,
          price_usd: priceStr,
          change_24h_pct: formatPct(usd.percentChange24h),
          logo_url: CMC_LOGO(coin.id),
          category: "crypto",
        });
      }
      if (results.length > 0) return results;
    }
  }

  return [];
}

async function searchCoinGeckoFallback(query) {
  const sResp = await fetch(
    `https://api.coingecko.com/api/v3/search?query=${encodeURIComponent(query)}`,
    withTimeout({
      headers: { Accept: "application/json", "User-Agent": "RatePath-Edge/2.0" },
      cf: { cacheTtl: 120, cacheEverything: true },
    })
  );
  if (!sResp.ok) throw new Error(`CoinGecko search HTTP ${sResp.status}`);
  const sJson = await sResp.json();
  const topCoins = (sJson?.coins || []).slice(0, 10);
  if (topCoins.length === 0) return [];

  const cgIds = topCoins.map((c) => c.id).join(",");
  const pResp = await fetch(
    `https://api.coingecko.com/api/v3/simple/price?ids=${encodeURIComponent(cgIds)}&vs_currencies=usd&include_24hr_change=true`,
    withTimeout({
      headers: { Accept: "application/json", "User-Agent": "RatePath-Edge/2.0" },
      cf: { cacheTtl: 120, cacheEverything: true },
    })
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

async function searchBinanceDirectFallback(query) {
  const sym = query.toUpperCase().replace(/[^A-Z0-9]/g, "");
  if (sym.length < 2 || sym.length > 10) return [];
  const resp = await fetch(
    `https://data-api.binance.vision/api/v3/ticker/24hr?symbol=${encodeURIComponent(sym)}USDT`,
    withTimeout({
      headers: { Accept: "application/json", "User-Agent": "RatePath-Edge/2.0" },
      cf: { cacheTtl: 60, cacheEverything: true },
    })
  );
  if (!resp.ok) return [];
  const data = await resp.json();
  const priceStr = formatUsdPrice(data?.lastPrice);
  if (!priceStr) return [];
  return [
    {
      id: sym === "BTC" || sym === "ETH" || sym === "SOL" ? sym : `BN_${sym}`,
      cmc_id: null,
      symbol: sym,
      name: sym,
      rank: 999,
      price_usd: priceStr,
      change_24h_pct: formatPct(data?.priceChangePercent),
      logo_url: "",
      category: "crypto",
    },
  ];
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
    const cmcResults = await searchCoinMarketCapLightweight(rawQuery, context.env);
    if (cmcResults.length > 0) {
      return jsonResponse({ query: rawQuery, source: "coinmarketcap", results: cmcResults }, 200);
    }
  } catch {
    // Fall through to CoinGecko
  }

  try {
    const cgResults = await searchCoinGeckoFallback(rawQuery);
    if (cgResults.length > 0) {
      return jsonResponse({ query: rawQuery, source: "coingecko_fallback", results: cgResults }, 200);
    }
  } catch {
    // Fall through to Binance Vision
  }

  try {
    const bnResults = await searchBinanceDirectFallback(rawQuery);
    return jsonResponse({ query: rawQuery, source: "binance_fallback", results: bnResults }, 200);
  } catch {
    return jsonResponse(
      { query: rawQuery, source: "unavailable", results: [], error: "Upstream search temporarily unavailable." },
      502,
      "no-store"
    );
  }
}
