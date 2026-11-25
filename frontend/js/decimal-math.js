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
 * Build unified 14-asset catalog strictly from `shared/contract.schema.json` live_feed.json.
 * Every asset stores exact `usdPerUnit` and (for Fiat) `unitsPerUsd` in 18-decimal BigInt.
 */
export function buildAssetCatalog(feed) {
  const fiatRates = feed?.fiat?.rates || {};
  const goldPrices = feed?.gold?.prices || {};
  const cryptoAssets = feed?.crypto?.assets || {};

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

  const makeFiatEntry = (code, symbol, decimals = 2, biasKey = null) => {
    if (code === "USD") {
      return {
        id: "USD",
        code: "USD",
        category: "fiat",
        symbol: "$",
        decimals: 2,
        pairLabel: "USD/USD",
        spotDisplay: "1.0000",
        unitsPerUsdScaled: SCALE,
        usdPerUnitScaled: SCALE,
        signal: resolveSignal(biasScore.USD),
      };
    }
    const entry = fiatRates[code];
    const unitsScaled = parseDecimalToScaled(entry?.units_per_usd || "1") || SCALE;
    // Exact inverse at 18-decimal BigInt precision: (SCALE * SCALE) / unitsPerUsd
    const usdPerUnitScaled = (SCALE * SCALE + unitsScaled / 2n) / unitsScaled;
    return {
      id: code,
      code,
      category: "fiat",
      symbol,
      decimals,
      pairLabel: entry?.pair_label || `USD/${code}`,
      spotDisplay: entry?.display_rate || "1.0000",
      unitsPerUsdScaled: unitsScaled,
      usdPerUnitScaled,
      signal: biasKey ? resolveSignal(biasScore[biasKey]) : "neutral",
    };
  };

  const makeGoldEntry = (id, shortCode, unitTag) => {
    const rawUsd = goldPrices[id] || "1";
    const usdScaled = parseDecimalToScaled(rawUsd) || SCALE;
    return {
      id,
      code: shortCode,
      unitTag,
      category: "gold",
      symbol: "Au",
      decimals: 2,
      pairLabel: `${shortCode}/USD`,
      spotDisplay: rawUsd,
      unitsPerUsdScaled: (SCALE * SCALE + usdScaled / 2n) / usdScaled,
      usdPerUnitScaled: usdScaled,
      signal: resolveSignal(biasScore.XAU),
    };
  };

  const makeCryptoEntry = (code, symbol, decimals) => {
    const asset = cryptoAssets[code] || {};
    const rawUsd = asset.spot_price || "1";
    const usdScaled = parseDecimalToScaled(rawUsd) || SCALE;
    return {
      id: code,
      code,
      category: "crypto",
      symbol,
      decimals,
      pairLabel: `${code}/USD`,
      spotDisplay: rawUsd,
      spotSource: asset.spot_source || "binance_vision",
      funding: asset.funding || null,
      unitsPerUsdScaled: (SCALE * SCALE + usdScaled / 2n) / usdScaled,
      usdPerUnitScaled: usdScaled,
      signal: resolveSignal(biasScore[code]),
    };
  };

  const ordered = [
    makeFiatEntry("USD", "$", 2, "USD"),
    makeFiatEntry("EUR", "€", 2, "EUR"),
    makeFiatEntry("GBP", "£", 2, "GBP"),
    makeFiatEntry("SAR", "﷼", 2, null),
    makeFiatEntry("AED", "د.إ", 2, null),
    makeFiatEntry("EGP", "E£", 2, null),
    makeFiatEntry("TRY", "₺", 2, null),
    makeGoldEntry("XAU_OZ", "XAU", "oz"),
    makeGoldEntry("GRAM_24K", "24K", "g"),
    makeGoldEntry("GRAM_21K", "21K", "g"),
    makeGoldEntry("GRAM_18K", "18K", "g"),
    makeCryptoEntry("BTC", "₿", 6),
    makeCryptoEntry("ETH", "Ξ", 5),
    makeCryptoEntry("SOL", "◎", 4),
  ];

  const byId = Object.fromEntries(ordered.map((item) => [item.id, item]));
  return { ordered, byId };
}

/**
 * Convert a single input amount from `sourceId` across all 14 Fiat, Gold, and Crypto assets.
 */
export function convertAllAssets(rawAmountStr, sourceId, catalog) {
  const sourceAsset = catalog.byId[sourceId] || catalog.byId.USD;
  const parsedAmount = parseDecimalToScaled(rawAmountStr);
  const safeAmount = parsedAmount !== null && parsedAmount >= 0n ? parsedAmount : 0n;

  // Total value in USD (18-decimal scaled)
  const totalUsdScaled = (safeAmount * sourceAsset.usdPerUnitScaled + SCALE / 2n) / SCALE;

  const conversions = {};
  for (const target of catalog.ordered) {
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
