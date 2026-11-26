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

// Native Arabic display names for popular world currencies added via online search
const ARABIC_FIAT_NAMES = {
  RUB: "روبل روسي",
  BYN: "روبل بيلاروسي",
  PKR: "روبية باكستانية",
  INR: "روبية هندية",
  IDR: "روبية إندونيسية",
  LKR: "روبية سريلانكية",
  NPR: "روبية نيبالية",
  CNY: "يوان صيني",
  JPY: "ين ياباني",
  KRW: "وون كوري جنوبي",
  MXN: "بيزو مكسيكي",
  ARS: "بيزو أرجنتيني",
  CLP: "بيزو تشيلي",
  COP: "بيزو كولومبي",
  PHP: "بيزو فلبيني",
  BRL: "ريال برازيلي",
  YER: "ريال يمني",
  IRR: "ريال إيراني",
  SEK: "كرونة سويدية",
  NOK: "كرونة نرويجية",
  DKK: "كرونة دنماركية",
  CZK: "كرونة تشيكية",
  ISK: "كرونة آيسلندية",
  PLN: "زلوتي بولندي",
  HUF: "فورنت مجري",
  RON: "ليو روماني",
  BGN: "ليف بلغاري",
  RSD: "دينار صربي",
  UAH: "هريفنيا أوكرانية",
  GEL: "لاري جورجي",
  AZN: "مانات أذربيجاني",
  KZT: "تينغي كازاخستاني",
  UZS: "سوم أوزبكي",
  MYR: "رينغيت ماليزي",
  THB: "بات تايلاندي",
  SGD: "دولار سنغافوري",
  HKD: "دولار هونغ كونغ",
  TWD: "دولار تايواني",
  NZD: "دولار نيوزيلندي",
  VND: "دونغ فيتنامي",
  BDT: "تاكا بنغلاديشي",
  AFN: "أفغاني",
  ZAR: "راند جنوب أفريقي",
  NGN: "نيرة نيجيرية",
  KES: "شلن كيني",
  SDG: "جنيه سوداني",
  MAD: "درهم مغربي",
  DZD: "دينار جزائري",
  TND: "دينار تونسي",
  LYD: "دينار ليبي",
  IQD: "دينار عراقي",
  JOD: "دينار أردني",
  KWD: "دينار كويتي",
  BHD: "دينار بحريني",
  OMR: "ريال عماني",
  QAR: "ريال قطري",
  SAR: "ريال سعودي",
  AED: "درهم إماراتي",
  EGP: "جنيه مصري",
  LBP: "ليرة لبنانية",
  TRY: "ليرة تركية",
  VES: "بوليفار فنزويلي",
};

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
  const arabicPriority = new Map();
  for (const [arKey, codes] of ARABIC_FIAT_KEYWORDS) {
    if (query.includes(arKey)) {
      codes.forEach((c, idx) => {
        const prio = 92 - Math.min(idx, 10);
        if (!arabicPriority.has(c) || arabicPriority.get(c) < prio) {
          arabicPriority.set(c, prio);
        }
      });
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

  const namesMap = namesResp?.ok ? await namesResp.json().catch(() => ({})) : {};
  if (!ratesResp?.ok) return { results: [], fiatCodes: new Set(), namesMap };
  const ratesJson = await ratesResp.json();
  const erRates = ratesJson?.rates || {};
  const fiatCodes = new Set();

  const matched = [];
  for (const [codeRaw, rateVal] of Object.entries(erRates)) {
    const code = String(codeRaw || "").toUpperCase();
    if (/^[A-Z]{3}$/.test(code)) fiatCodes.add(code);
    // Never return official bank SYP here (SYP comes strictly from SP-Today)
    if (!/^[A-Z]{3}$/.test(code) || code === "SYP" || code === "XAU" || code === "XAG") continue;
    const nameEn = String(namesMap[code.toLowerCase()] || code);
    const nameAr = ARABIC_FIAT_NAMES[code] || nameEn;
    const nameLow = nameEn.toLowerCase();

    let score = 0;
    if (code === qUpper) score = 100;
    else if (arabicPriority.has(code)) score = arabicPriority.get(code);
    else if (nameAr.includes(query)) score = 91;
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
          name_ar: nameAr,
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
  return { results: matched.slice(0, 8).map((x) => x.item), fiatCodes, namesMap };
}

/**
 * Search ANY cryptocurrency worldwide using edge-friendly public APIs:
 * 1. CoinMarketCap Pro (when CMC_PRO_API_KEY is configured)
 * 2. KuCoin Live Ticker (ultra-fast, live 24h %, works reliably on Cloudflare Edge)
 * 3. CoinGecko Search + Price
 * 4. DexScreener Global Search (100,000+ coins/tokens)
 * 5. jsDelivr Currency CDN Crypto Fallback (strictly excluding fiat ISO codes)
 */
async function searchCryptoOnline(query, env, fiatCodes = new Set(), namesMap = {}) {
  const qUpper = query.toUpperCase().replace(/[^A-Z0-9]/g, "");
  const proKey = (env?.CMC_PRO_API_KEY || "").trim();
  const hasProKey = proKey && proKey.toLowerCase() !== "placeholder";
  const isPureArabic = !qUpper;
  if (isPureArabic) return { source: "none", results: [] };

  // 1. Optional CMC Pro API
  if (hasProKey && qUpper.length >= 2 && qUpper.length <= 12 && !fiatCodes.has(qUpper)) {
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

  // 2. KuCoin Live Ticker (exact ticker match with live 24h change %, fast & zero CF IP blocks)
  if (qUpper.length >= 2 && qUpper.length <= 10 && !fiatCodes.has(qUpper)) {
    try {
      const kcResp = await fetch(
        `https://api.kucoin.com/api/v1/market/stats?symbol=${encodeURIComponent(qUpper)}-USDT`,
        withTimeout({
          headers: { Accept: "application/json", "User-Agent": "Mozilla/5.0" },
          cf: { cacheTtl: 60, cacheEverything: true },
        }, 5000)
      );
      if (kcResp.ok) {
        const kcJson = await kcResp.json();
        const d = kcJson?.data;
        const priceStr = d?.last ? formatUsdPrice(d.last) : null;
        if (priceStr) {
          const changePct = d?.changeRate != null ? Number(d.changeRate) * 100 : 0;
          const friendlyName = String(namesMap[qUpper.toLowerCase()] || qUpper);
          return {
            source: "kucoin",
            results: [
              {
                id: `KC_${qUpper}`,
                cmc_id: null,
                symbol: qUpper,
                name: friendlyName,
                rank: null,
                price_usd: priceStr,
                change_24h_pct: formatPct(changePct),
                logo_url: "",
                category: "crypto",
              },
            ],
          };
        }
      }
    } catch {
      // Continue to CoinGecko / DexScreener
    }
  }

  // 3. CoinGecko Search + Price (returns clean canonical coins by ticker OR full name)
  if (!fiatCodes.has(qUpper)) {
    try {
      const sResp = await fetch(
        `https://api.coingecko.com/api/v3/search?query=${encodeURIComponent(query)}`,
        withTimeout({
          headers: { Accept: "application/json", "User-Agent": "Mozilla/5.0" },
          cf: { cacheTtl: 120, cacheEverything: true },
        }, 5000)
      );
      if (sResp.ok) {
        const sJson = await sResp.json();
        const topCoins = (sJson?.coins || []).slice(0, 8);
        if (topCoins.length > 0) {
          const cgIds = topCoins.map((c) => c.id).join(",");
          const pResp = await fetch(
            `https://api.coingecko.com/api/v3/simple/price?ids=${encodeURIComponent(cgIds)}&vs_currencies=usd&include_24hr_change=true`,
            withTimeout({
              headers: { Accept: "application/json", "User-Agent": "Mozilla/5.0" },
              cf: { cacheTtl: 120, cacheEverything: true },
            }, 5000)
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
  }

  // 4. DexScreener Global Token Search (100,000+ coins/tokens)
  if (!fiatCodes.has(qUpper)) {
    try {
      const dResp = await fetch(
        `https://api.dexscreener.com/latest/dex/search?q=${encodeURIComponent(query)}`,
        withTimeout({
          headers: { Accept: "application/json", "User-Agent": "Mozilla/5.0" },
          cf: { cacheTtl: 90, cacheEverything: true },
        }, 5000)
      );
      if (dResp.ok) {
        const dJson = await dResp.json();
        const pairs = Array.isArray(dJson?.pairs) ? dJson.pairs : [];
        pairs.sort((a, b) => Number(b?.liquidity?.usd || 0) - Number(a?.liquidity?.usd || 0));
        const seenSym = new Set();
        const results = [];
        for (const pair of pairs) {
          const tok = pair?.baseToken;
          const sym = String(tok?.symbol || "").toUpperCase().replace(/[^A-Z0-9]/g, "");
          const priceStr = formatUsdPrice(pair?.priceUsd);
          if (!sym || !priceStr || seenSym.has(sym) || fiatCodes.has(sym)) continue;
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
  }

  // 5. jsDelivr Currency CDN Crypto Fallback (strictly excluding fiat ISO codes)
  if (qUpper.length >= 2 && qUpper.length <= 10 && !fiatCodes.has(qUpper)) {
    try {
      const cdnResp = await fetch(
        `https://cdn.jsdelivr.net/npm/@fawazahmed0/currency-api@latest/v1/currencies/${encodeURIComponent(qUpper.toLowerCase())}.json`,
        withTimeout({ cf: { cacheTtl: 180, cacheEverything: true } }, 5000)
      );
      if (cdnResp.ok) {
        const cdnData = await cdnResp.json();
        const usdVal = cdnData?.[qUpper.toLowerCase()]?.usd;
        const priceStr = formatUsdPrice(usdVal);
        if (priceStr) {
          const friendlyName = String(namesMap[qUpper.toLowerCase()] || qUpper);
          return {
            source: "jsdelivr_crypto",
            results: [
              {
                id: `CDN_${qUpper}`,
                cmc_id: null,
                symbol: qUpper,
                name: friendlyName,
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

  const fiatOutcome = await searchWorldFiatOnline(rawQuery).catch(() => ({
    results: [],
    fiatCodes: new Set(),
    namesMap: {},
  }));
  const fiatResults = fiatOutcome.results || [];
  const cryptoOutcome = await searchCryptoOnline(
    rawQuery,
    context.env,
    fiatOutcome.fiatCodes,
    fiatOutcome.namesMap
  ).catch(() => ({ source: "none", results: [] }));

  const combined = [...fiatResults, ...(cryptoOutcome.results || [])];
  const sourceLabel =
    fiatResults.length > 0 && cryptoOutcome.results.length > 0
      ? `world_fx+${cryptoOutcome.source}`
      : fiatResults.length > 0
      ? "world_fx"
      : cryptoOutcome.source;

  return jsonResponse({ query: rawQuery, source: sourceLabel, results: combined }, 200);
}
