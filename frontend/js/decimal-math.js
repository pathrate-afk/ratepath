/**
 * RatePath (ريت باث) — Pure BigInt 18-Decimal Fixed-Point Cross-Asset Engine
 * Eliminates IEEE-754 floating-point precision drift across Fiat, Gold, and Crypto conversions.
 */

export const DECIMALS = 18n;
export const SCALE = 10n ** DECIMALS;
const HALF_TEN = 5n;

/**
 * Parse a decimal string (e.g. "3572.09" or "1,000.50") into an 18-decimal scaled BigInt.
 * Returns null if input is empty or non-numeric.
 */
export function parseDecimalToScaled(rawInput) {
  if (rawInput === null || rawInput === undefined) return null;
  const cleaned = String(rawInput)
    .replace(/,/g, "")
    .replace(/[\u0660-\u0669]/g, (d) => String(d.charCodeAt(0) - 0x0660))
    .replace(/\u066B/g, ".")
    .trim();

  if (!cleaned || !/^-?\d+(\.\d+)?$/.test(cleaned)) {
    return null;
  }

  const isNegative = cleaned.startsWith("-");
  const unsigned = isNegative ? cleaned.slice(1) : cleaned;
  const [intPart, fracPart = ""] = unsigned.split(".");

  const paddedFrac = (fracPart + "0".repeat(19)).slice(0, 19);
  const mainFrac = paddedFrac.slice(0, 18);
  const guardDigit = BigInt(paddedFrac[18] || "0");

  let scaled = BigInt(intPart || "0") * SCALE + BigInt(mainFrac || "0");
  if (guardDigit >= HALF_TEN) {
    scaled += 1n;
  }
  return isNegative ? -scaled : scaled;
}

/**
 * Format an 18-decimal scaled BigInt into a localized tabular decimal string with exact rounding.
 */
export function formatScaled(scaledValue, displayDecimals = 2, useGrouping = true) {
  if (typeof scaledValue !== "bigint") return "0.00";

  const isNeg = scaledValue < 0n;
  const absVal = isNeg ? -scaledValue : scaledValue;

  // Dynamic precision for tiny crypto amounts (e.g., 1 TRY -> 0.00000032 BTC)
  let targetDecimals = displayDecimals;
  if (absVal > 0n && absVal < SCALE / 100n && displayDecimals < 6) {
    targetDecimals = 6;
  }
  if (absVal > 0n && absVal < SCALE / 10000n && targetDecimals < 8) {
    targetDecimals = 8;
  }

  const cutDigits = 18 - targetDecimals;
  const divisor = 10n ** BigInt(cutDigits);
  const halfDivisor = divisor / 2n;

  const roundedUnits = (absVal + halfDivisor) / divisor;
  const unitScale = 10n ** BigInt(targetDecimals);
  const intUnits = roundedUnits / unitScale;
  const fracUnits = roundedUnits % unitScale;

  let intStr = intUnits.toString();
  if (useGrouping) {
    intStr = intStr.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  }

  const fracStr =
    targetDecimals > 0 ? "." + fracUnits.toString().padStart(targetDecimals, "0") : "";

  return (isNeg && roundedUnits > 0n ? "-" : "") + intStr + fracStr;
}

/**
 * Build unified cross-asset catalog merging `live_feed.json` (core pricing + news bias),
 * `catalog_feed.json` (155+ world/Arab FX, SP-Today SYP, Silver, Top-300 CoinMarketCap),
 * and any visitor-added long-tail custom tokens (`customAssets`).
 * Every asset stores exact `usdPerUnitScaled` and `unitsPerUsdScaled` in 18-decimal BigInt.
 */
export function buildAssetCatalog(feed, catalogFeed = null, customAssets = {}) {
  const fiatRates = feed?.fiat?.rates || {};
  const goldPrices = feed?.gold?.prices || {};
  const cryptoAssets = feed?.crypto?.assets || {};

  const extFiat = catalogFeed?.fiat || {};
  const extMetals = catalogFeed?.metals || {};
  const extCryptoList = Array.isArray(catalogFeed?.crypto) ? catalogFeed.crypto : [];
  const extCryptoById = Object.fromEntries(extCryptoList.map((c) => [c.id, c]));

  // Aggregate 24h consensus bias across news items for live directional badges
  const biasScore = { USD: 0, EUR: 0, GBP: 0, XAU: 0, BTC: 0, ETH: 0, SOL: 0 };
  for (const item of feed?.news || []) {
    const biases = item?.assets_bias_24h || {};
    for (const [k, dir] of Object.entries(biases)) {
      if (k in biasScore) {
        if (dir === "bullish") biasScore[k] += 1;
        else if (dir === "bearish") biasScore[k] -= 1;
      }
    }
  }

  const resolveSignal = (score) =>
    score > 0 ? "bullish" : score < 0 ? "bearish" : "neutral";

  const signalFromPct = (pctStr, fallback = "neutral") => {
    const raw = String(pctStr || "").trim();
    if (!raw || raw === "0.00") return fallback;
    if (raw.startsWith("-")) return "bearish";
    if (raw.startsWith("+") || Number(raw) > 0) return "bullish";
    return fallback;
  };

  const makeFiatEntry = (code, fallbackSymbol, decimals = 2, biasKey = null) => {
    const catMeta = extFiat[code] || {};
    if (code === "USD") {
      return {
        id: "USD",
        code: "USD",
        category: "fiat",
        symbol: "$",
        nameEn: catMeta.name_en || "US Dollar",
        nameAr: catMeta.name_ar || "الدولار الأمريكي",
        decimals: 2,
        pairLabel: "USD/USD",
        spotDisplay: "1.0000",
        unitsPerUsdScaled: SCALE,
        usdPerUnitScaled: SCALE,
        signal: resolveSignal(biasScore.USD),
        sourceTag: null,
        logoUrl: "",
        change24hPct: null,
      };
    }

    // Prefer SP-Today official SYP market rate from catalog_feed when available
    const useCatalogForSyp = code === "SYP" && catMeta.units_per_usd && catMeta.source === "sp-today";
    const entry = useCatalogForSyp ? catMeta : fiatRates[code] || catMeta;
    const unitsStr = entry?.units_per_usd || "1";
    const unitsScaled = parseDecimalToScaled(unitsStr) || SCALE;
    const usdPerUnitScaled = (SCALE * SCALE + unitsScaled / 2n) / unitsScaled;

    return {
      id: code,
      code,
      category: "fiat",
      symbol: catMeta.symbol || fallbackSymbol || code,
      nameEn: catMeta.name_en || code,
      nameAr: catMeta.name_ar || code,
      decimals,
      pairLabel: entry?.pair_label || `USD/${code}`,
      spotDisplay: entry?.display_rate || unitsStr,
      unitsPerUsdScaled: unitsScaled,
      usdPerUnitScaled,
      signal: biasKey ? resolveSignal(biasScore[biasKey]) : "neutral",
      sourceTag: entry?.source || null,
      buyRate: entry?.buy_rate || null,
      sellRate: entry?.sell_rate || null,
      logoUrl: "",
      change24hPct: null,
    };
  };

  const makeMetalEntry = (id, shortCode, unitTag, symbol = "Au", fallbackNameEn = null, fallbackNameAr = null) => {
    const extM = extMetals[id] || {};
    const rawUsd = goldPrices[id] || extM.price_usd || "1";
    const usdScaled = parseDecimalToScaled(rawUsd) || SCALE;
    return {
      id,
      code: shortCode,
      unitTag,
      category: "gold",
      symbol,
      nameEn: extM.name_en || fallbackNameEn || shortCode,
      nameAr: extM.name_ar || fallbackNameAr || shortCode,
      decimals: id === "XAG_GRAM" ? 4 : 2,
      pairLabel: `${shortCode}/USD`,
      spotDisplay: rawUsd,
      unitsPerUsdScaled: (SCALE * SCALE + usdScaled / 2n) / usdScaled,
      usdPerUnitScaled: usdScaled,
      signal: resolveSignal(biasScore.XAU),
      logoUrl: "",
      change24hPct: null,
    };
  };

  const makeCryptoEntry = (code, symbol, decimals) => {
    const asset = cryptoAssets[code] || {};
    const cmcMeta = extCryptoById[code] || {};
    const rawUsd = asset.spot_price || cmcMeta.price_usd || "1";
    const usdScaled = parseDecimalToScaled(rawUsd) || SCALE;
    const chg24 = cmcMeta.change_24h_pct || null;
    return {
      id: code,
      code,
      category: "crypto",
      symbol,
      nameEn: cmcMeta.name || code,
      nameAr: cmcMeta.name || code,
      decimals,
      pairLabel: `${code}/USD`,
      spotDisplay: rawUsd,
      spotSource: asset.spot_source || "binance_vision",
      funding: asset.funding || null,
      cmcId: cmcMeta.cmc_id ?? null,
      cmcRank: cmcMeta.rank ?? null,
      logoUrl: cmcMeta.logo_url || "",
      change24hPct: chg24,
      unitsPerUsdScaled: (SCALE * SCALE + usdScaled / 2n) / usdScaled,
      usdPerUnitScaled: usdScaled,
      signal: biasScore[code] ? resolveSignal(biasScore[code]) : signalFromPct(chg24, "neutral"),
    };
  };

  const makeExtendedCryptoEntry = (coin) => {
    const rawUsd = String(coin.price_usd || "1");
    const usdScaled = parseDecimalToScaled(rawUsd) || SCALE;
    const displayDec =
      usdScaled >= SCALE * 1000n ? 6 : usdScaled >= SCALE ? 4 : 2;
    const chg24 = coin.change_24h_pct || "0.00";
    return {
      id: coin.id,
      code: coin.symbol,
      category: "crypto",
      symbol: coin.symbol,
      nameEn: coin.name || coin.symbol,
      nameAr: coin.name || coin.symbol,
      decimals: displayDec,
      pairLabel: `${coin.symbol}/USD`,
      spotDisplay: rawUsd,
      spotSource: "coinmarketcap",
      funding: null,
      cmcId: coin.cmc_id ?? null,
      cmcRank: coin.rank ?? null,
      logoUrl: coin.logo_url || "",
      change24hPct: chg24,
      unitsPerUsdScaled: (SCALE * SCALE + usdScaled / 2n) / usdScaled,
      usdPerUnitScaled: usdScaled,
      signal: signalFromPct(chg24, "neutral"),
    };
  };

  // 1. Core 15 built-in assets first
  const coreAssets = [
    makeFiatEntry("USD", "$", 2, "USD"),
    makeFiatEntry("EUR", "€", 2, "EUR"),
    makeFiatEntry("GBP", "£", 2, "GBP"),
    makeFiatEntry("SAR", "﷼", 2, null),
    makeFiatEntry("AED", "د.إ", 2, null),
    makeFiatEntry("EGP", "E£", 2, null),
    makeFiatEntry("TRY", "₺", 2, null),
    ...(fiatRates.SYP || extFiat.SYP ? [makeFiatEntry("SYP", "ل.س", 2, null)] : []),
    makeMetalEntry("XAU_OZ", "XAU", "oz", "Au", "Gold Troy Ounce", "أونصة الذهب الفوري"),
    makeMetalEntry("GRAM_24K", "24K", "g", "Au", "Gold 24K Gram", "جرام الذهب عيار 24"),
    makeMetalEntry("GRAM_21K", "21K", "g", "Au", "Gold 21K Gram", "جرام الذهب عيار 21"),
    makeMetalEntry("GRAM_18K", "18K", "g", "Au", "Gold 18K Gram", "جرام الذهب عيار 18"),
    makeCryptoEntry("BTC", "₿", 6),
    makeCryptoEntry("ETH", "Ξ", 5),
    makeCryptoEntry("SOL", "◎", 4),
  ];

  const byId = Object.fromEntries(coreAssets.map((item) => [item.id, item]));
  const allSearchable = [...coreAssets];

  // 2. Extended Metals: Silver (XAG_OZ & XAG_GRAM)
  for (const [metalId, shortCode, unitTag, nameEn, nameAr] of [
    ["XAG_OZ", "XAG", "oz", "Silver Troy Ounce", "أونصة الفضة الفوري"],
    ["XAG_GRAM", "Ag/g", "g", "Silver Gram (999)", "جرام الفضة عيار 999"],
  ]) {
    if (extMetals[metalId] && !byId[metalId]) {
      const m = makeMetalEntry(metalId, shortCode, unitTag, "Ag", nameEn, nameAr);
      byId[metalId] = m;
      allSearchable.push(m);
    }
  }

  // 3. Extended 155+ World & Arab Currencies from `catalog_feed.json`
  for (const code of Object.keys(extFiat)) {
    if (byId[code]) continue;
    const f = makeFiatEntry(code, extFiat[code]?.symbol || code, 2, null);
    byId[code] = f;
    allSearchable.push(f);
  }

  // 4. Extended Top-300 CoinMarketCap Cryptocurrencies from `catalog_feed.json`
  for (const coin of extCryptoList) {
    if (!coin?.id || byId[coin.id]) continue;
    const c = makeExtendedCryptoEntry(coin);
    byId[coin.id] = c;
    allSearchable.push(c);
  }

  // 5. Visitor-added long-tail custom assets (e.g., discovered via `/api/cmc-search`)
  for (const [cid, desc] of Object.entries(customAssets || {})) {
    if (!desc || byId[cid]) continue;
    if (desc.category === "fiat" && desc.units_per_usd) {
      const unitsScaled = parseDecimalToScaled(desc.units_per_usd) || SCALE;
      const usdPerUnitScaled = (SCALE * SCALE + unitsScaled / 2n) / unitsScaled;
      const customFiat = {
        id: cid,
        code: desc.code || cid,
        category: "fiat",
        symbol: desc.symbol || cid,
        nameEn: desc.name_en || desc.name || cid,
        nameAr: desc.name_ar || desc.name || cid,
        decimals: 2,
        pairLabel: `USD/${desc.code || cid}`,
        spotDisplay: desc.units_per_usd,
        unitsPerUsdScaled: unitsScaled,
        usdPerUnitScaled,
        signal: "neutral",
        logoUrl: "",
        change24hPct: null,
      };
      byId[cid] = customFiat;
      allSearchable.push(customFiat);
    } else if (desc.price_usd) {
      const customCrypto = makeExtendedCryptoEntry({ ...desc, id: cid });
      byId[cid] = customCrypto;
      allSearchable.push(customCrypto);
    }
  }

  return { ordered: coreAssets, allSearchable, byId };
}

/**
 * Convert a single input amount from `sourceId` across the active workspace assets (or entire catalog).
 */
export function convertAllAssets(rawAmountStr, sourceId, catalog, targetIds = null) {
  const sourceAsset = catalog.byId[sourceId] || catalog.byId.USD;
  const parsedAmount = parseDecimalToScaled(rawAmountStr);
  const safeAmount = parsedAmount !== null && parsedAmount >= 0n ? parsedAmount : 0n;

  // Total value in USD (18-decimal scaled)
  const totalUsdScaled = (safeAmount * sourceAsset.usdPerUnitScaled + SCALE / 2n) / SCALE;

  const targets = Array.isArray(targetIds)
    ? targetIds.map((id) => catalog.byId[id]).filter(Boolean)
    : catalog.ordered;

  const conversions = {};
  const convertOne = (target) => {
    let targetScaled;
    if (target.id === sourceAsset.id) {
      targetScaled = safeAmount;
    } else if (sourceAsset.id === "USD" && target.category === "fiat") {
      targetScaled = (safeAmount * target.unitsPerUsdScaled + SCALE / 2n) / SCALE;
    } else if (target.id === "USD") {
      targetScaled = totalUsdScaled;
    } else {
      targetScaled =
        (totalUsdScaled * SCALE + target.usdPerUnitScaled / 2n) / target.usdPerUnitScaled;
    }

    conversions[target.id] = {
      scaled: targetScaled,
      formatted: formatScaled(targetScaled, target.decimals, true),
      rawPlain: formatScaled(targetScaled, target.decimals, false),
    };
  };

  convertOne(sourceAsset);
  for (const target of targets) {
    if (!conversions[target.id]) {
      convertOne(target);
    }
  }

  return {
    sourceAsset,
    amountScaled: safeAmount,
    usdEquivalentFormatted: formatScaled(totalUsdScaled, 2, true),
    conversions,
  };
}

/**
 * Compute exact tick-over-tick % change badges between current feed and cached previous snapshot.
 */
export function computeTickDeltas(currentCatalog, previousCatalog) {
  const deltas = {};
  for (const item of currentCatalog.ordered) {
    const prev = previousCatalog?.byId?.[item.id];
    if (!prev || prev.usdPerUnitScaled <= 0n) {
      deltas[item.id] = { pctStr: "0.00%", direction: item.signal };
      continue;
    }
    const diff = item.usdPerUnitScaled - prev.usdPerUnitScaled;
    if (diff === 0n) {
      deltas[item.id] = { pctStr: "0.00%", direction: item.signal };
      continue;
    }
    // pctScaled = (diff * 100 * SCALE) / prev
    const pctScaled = (diff * 100n * SCALE) / prev.usdPerUnitScaled;
    const sign = pctScaled > 0n ? "+" : "";
    deltas[item.id] = {
      pctStr: `${sign}${formatScaled(pctScaled, 2, false)}%`,
      direction: pctScaled > 0n ? "bullish" : "bearish",
    };
  }
  return deltas;
}
