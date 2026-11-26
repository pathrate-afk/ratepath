/**
 * RatePath (ريت باث) — Cloudflare Pages Function: `/api/cmc-search?q=<query>`
 *
 * Universal World Currency (170+ Fiat) + Global Crypto Search Engine on Cloudflare Edge:
 * - Zero IP blocks on Cloudflare Workers: combines jsDelivr Currency CDN (`@fawazahmed0/currency-api`),
 *   `open.er-api.com`, DexScreener global token search, CoinMarketCap & CoinGecko.
 * - Searches by ISO code (`JOD`, `RUB`, `PKR`, `VES`, `NGN`...), English name (`dinar`, `ruble`, `rupee`...),
 *   Arabic currency keywords (`دينار`, `ريال`, `درهم`, `روبل`, `روبية`, `بيزو`, `ين`, `يوان`, `فرنك`...),
 *   or ANY crypto ticker/name (`XRP`, `KAS`, `PEPE`, `BONK`, `SUI`, `PI`, etc.).
 * - Edge cached for 120s (`Cache-Control: public, max-age=120, s-maxage=120`).
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

// Arabic -> ISO fiat search helper so visitors searching in Arabic find ANY world currency immediately
const ARABIC_FIAT_KEYWORDS = [
  ["دينار", ["JOD", "KWD", "BHD", "IQD", "DZD", "TND", "LYD", "RSD"]],
  ["ريال", ["SAR", "QAR", "OMR", "YER", "IRR", "BRL"]],
  ["درهم", ["AED", "MAD"]],
  ["جنيه", ["EGP", "GBP", "SDG", "LBP"]],
  ["ليرة", ["TRY", "LBP"]],
  ["دولار", ["USD", "CAD", "AUD", "NZD", "SGD", "HKD", "TWD"]],
  ["يورو", ["EUR"]],
  ["فرنك", ["CHF", "XOF", "XAF"]],
  ["ين", ["JPY"]],
  ["يوان", ["CNY"]],
  ["روبل", ["RUB", "BYN"]],
  ["روبية", ["INR", "PKR", "IDR", "LKR", "NPR"]],
  ["بيزو", ["MXN", "ARS", "CLP", "COP", "PHP"]],
  ["كرونة", ["SEK", "NOK", "DKK", "CZK", "ISK"]],
  ["وون", ["KRW"]],
  ["رينغيت", ["MYR"]],
  ["بات", ["THB"]],
  ["راند", ["ZAR"]],
  ["زلوتي", ["PLN"]],
  ["شيكل", ["ILS"]],
  ["لاري", ["GEL"]],
  ["مانات", ["AZN", "TMT"]],
  ["تينغي", ["KZT"]],
  ["سوم", ["UZS", "KGS"]],
  ["نيرة", ["NGN"]],
  ["شلن", ["KES", "UGX", "TZS"]],
  ["تاكا", ["BDT"]],
  ["دونغ", ["VND"]],
  ["أفغاني", ["AFN"]],
  ["بوليفار", ["VES"]],
  ["فورنت", ["HUF"]],
  ["ليف", ["BGN"]],
  ["ليو", ["RON"]],
];

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

function formatFiatRate(num) {
  const n = Number(num);
  if (!Number.isFinite(n) || n <= 0) return null;
  if (n >= 100) return n.toFixed(2);
  return n.toFixed(4);
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

/**
 * Search ALL 170+ world fiat currencies via jsDelivr Currency API + open.er-api.com (never blocks Cloudflare Edge).
 * Note: Strictly excludes `SYP` from bank feeds (SYP is sourced exclusively from SP-Today).
 */
async function searchWorldFiatOnline(query) {
  const qLower = query.toLowerCase();
  const qUpper = query.toUpperCase();
  const arabicCodes = new Set();
  for (const [arKey, codes] of ARABIC_FIAT_KEYWORDS) {
    if (query.includes(arKey)) {
      for (const c of codes) arabicCodes.add(c);
    }
  }

  const [namesResp, ratesResp] = await Promise.all([
    fetch("https://cdn.jsdelivr.net/npm/@fawazahmed0/currency-api@latest/v1/currencies.json", {
      cf: { cacheTtl: 3600, cacheEverything: true },
    }).catch(() => null),
    fetch("https://open.er-api.com/v6/latest/USD", {
      cf: { cacheTtl: 600, cacheEverything: true },
    }).catch(() => null),
  ]);

  if (!ratesResp?.ok) return [];
  const ratesJson = await ratesResp.json();
  const erRates = ratesJson?.rates || {};
  const namesMap = namesResp?.ok ? await namesResp.json().catch(() => ({})) : {};

  const matched = [];
  for (const [codeRaw, rateVal] of Object.entries(erRates)) {
    const code = String(codeRaw || "").toUpperCase();
    // Never return official bank SYP here (SYP comes strictly from SP-Today)
    if (!/^[A-Z]{3}$/.test(code) || code === "SYP" || code === "XAU" || code === "XAG") continue;
    const nameEn = String(namesMap[code.toLowerCase()] || code);
    const nameLow = nameEn.toLowerCase();

    let score = 0;
    if (code === qUpper) score = 100;
    else if (arabicCodes.has(code)) score = 90;
    else if (nameLow === qLower) score = 95;
    else if (code.startsWith(qUpper) && qUpper.length >= 2) score = 80;
    else if (nameLow.includes(qLower)) score = 60;

    if (score > 0) {
      const rateStr = formatFiatRate(rateVal);
      if (!rateStr) continue;
      matched.push({
        score,
        item: {
          id: code,
          cmc_id: null,
          code,
          symbol: code,
          name: nameEn,
          name_en: nameEn,
          name_ar: nameEn,
          rank: null,
          units_per_usd: rateStr,
          price_usd: null,
          change_24h_pct: "0.00",
          logo_url: "",
          category: "fiat",
        },
      });
    }
  }

  matched.sort((a, b) => b.score - a.score || a.item.code.localeCompare(b.item.code));
  return matched.slice(0, 8).map((x) => x.item);
}

/**
 * Search ANY cryptocurrency worldwide using edge-friendly public APIs:
 * 1. CoinMarketCap Pro/Public (if not rate-limited)
 * 2. CoinGecko Search + Price
 * 3. DexScreener Global Search (100,000+ coins/tokens, reliable on Cloudflare Workers)
 */
async function searchCryptoOnline(query, env) {
  const qUpper = query.toUpperCase().replace(/[^A-Z0-9]/g, "");
  const proKey = (env?.CMC_PRO_API_KEY || "").trim();
  const hasProKey = proKey && proKey.toLowerCase() !== "placeholder";

  // 1. Optional CMC Pro API
  if (hasProKey && qUpper.length >= 2 && qUpper.length <= 12) {
    try {
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
        if (results.length > 0) return { source: "coinmarketcap", results };
      }
    } catch {
      // Continue to next provider
    }
  }

  // 2. CoinGecko Search + Price (returns clean canonical CMC/CG coins)
  try {
    const sResp = await fetch(
      `https://api.coingecko.com/api/v3/search?query=${encodeURIComponent(query)}`,
      withTimeout({
        headers: { Accept: "application/json", "User-Agent": "RatePath-Edge/2.0" },
        cf: { cacheTtl: 120, cacheEverything: true },
      })
    );
    if (sResp.ok) {
      const sJson = await sResp.json();
      const topCoins = (sJson?.coins || []).slice(0, 8);
      if (topCoins.length > 0) {
        const cgIds = topCoins.map((c) => c.id).join(",");
        const pResp = await fetch(
          `https://api.coingecko.com/api/v3/simple/price?ids=${encodeURIComponent(cgIds)}&vs_currencies=usd&include_24hr_change=true`,
          withTimeout({
            headers: { Accept: "application/json", "User-Agent": "RatePath-Edge/2.0" },
            cf: { cacheTtl: 120, cacheEverything: true },
          })
        );
        if (pResp.ok) {
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
          if (results.length > 0) return { source: "coingecko", results };
        }
      }
    }
  } catch {
    // Continue to DexScreener
  }

  // 3. DexScreener Global Token Search (always fast & accessible from Cloudflare Edge!)
  try {
    const dResp = await fetch(
      `https://api.dexscreener.com/latest/dex/search?q=${encodeURIComponent(query)}`,
      withTimeout({
        headers: { Accept: "application/json", "User-Agent": "RatePath-Edge/2.0" },
        cf: { cacheTtl: 90, cacheEverything: true },
      })
    );
    if (dResp.ok) {
      const dJson = await dResp.json();
      const pairs = Array.isArray(dJson?.pairs) ? dJson.pairs : [];
      // Sort by USD liquidity descending to prefer real canonical tokens over micro-clones
      pairs.sort((a, b) => Number(b?.liquidity?.usd || 0) - Number(a?.liquidity?.usd || 0));
      const seenSym = new Set();
      const results = [];
      for (const pair of pairs) {
        const tok = pair?.baseToken;
        const sym = String(tok?.symbol || "").toUpperCase().replace(/[^A-Z0-9]/g, "");
        const priceStr = formatUsdPrice(pair?.priceUsd);
        if (!sym || !priceStr || seenSym.has(sym)) continue;
        seenSym.add(sym);
        results.push({
          id: `DEX_${sym}`,
          cmc_id: null,
          symbol: sym,
          name: String(tok?.name || sym),
          rank: null,
          price_usd: priceStr,
          change_24h_pct: formatPct(pair?.priceChange?.h24),
          logo_url: String(pair?.info?.imageUrl || ""),
          category: "crypto",
        });
        if (results.length >= 6) break;
      }
      if (results.length > 0) return { source: "dexscreener", results };
    }
  } catch {
    // Continue to jsDelivr crypto fallback
  }

  // 4. jsDelivr Currency CDN Crypto Fallback (always online on Cloudflare Edge)
  if (qUpper.length >= 2 && qUpper.length <= 10) {
    try {
      const cdnResp = await fetch(
        `https://cdn.jsdelivr.net/npm/@fawazahmed0/currency-api@latest/v1/currencies/${encodeURIComponent(qUpper.toLowerCase())}.json`,
        withTimeout({ cf: { cacheTtl: 180, cacheEverything: true } })
      );
      if (cdnResp.ok) {
        const cdnData = await cdnResp.json();
        const usdVal = cdnData?.[qUpper.toLowerCase()]?.usd;
        const priceStr = formatUsdPrice(usdVal);
        if (priceStr) {
          return {
            source: "jsdelivr_crypto",
            results: [
              {
                id: `CDN_${qUpper}`,
                cmc_id: null,
                symbol: qUpper,
                name: qUpper,
                rank: null,
                price_usd: priceStr,
                change_24h_pct: "0.00",
                logo_url: "",
                category: "crypto",
              },
            ],
          };
        }
      }
    } catch {
      // Fall through cleanly
    }
  }

  return { source: "none", results: [] };
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

  const [fiatResults, cryptoOutcome] = await Promise.all([
    searchWorldFiatOnline(rawQuery).catch(() => []),
    searchCryptoOnline(rawQuery, context.env).catch(() => ({ source: "none", results: [] })),
  ]);

  const combined = [...fiatResults, ...(cryptoOutcome.results || [])];
  const sourceLabel =
    fiatResults.length > 0 && cryptoOutcome.results.length > 0
      ? `world_fx+${cryptoOutcome.source}`
      : fiatResults.length > 0
      ? "world_fx"
      : cryptoOutcome.source;

  return jsonResponse({ query: rawQuery, source: sourceLabel, results: combined }, 200);
}
