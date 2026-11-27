/**
 * RatePath (ريت باث) — Embedded Resilient First-Paint Fallback Snapshot
 *
 * Guarantees the converter and default workspace (USD, EUR, GBP, SAR, AED, EGP, TRY, SYP,
 * Gold XAU/24K/21K/18K, Silver XAG/Ag-g, BTC, ETH, SOL, USDT) render INSTANTLY on first visit
 * with zero empty states even before live endpoints respond or if an upstream API rate-limits.
 * Replaced immediately once live data loads from GitHub live-data or same-origin `/public/live_feed.json`.
 */

export const FALLBACK_FEED_SNAPSHOT = Object.freeze({
  meta: {
    platform: "RatePath",
    platform_ar: "ريت باث",
    schema_version: "1.0.0",
    generated_at: "2026-11-27T22:50:51Z",
    status: "cached_fallback",
  },
  fiat: {
    base: "USD",
    updated_at: "2026-11-27T22:50:51Z",
    source: "cached_snapshot",
    rates: {
      EUR: { units_per_usd: "0.9113", usd_per_unit: "1.0973", pair_label: "EUR/USD", display_rate: "1.0973" },
      GBP: { units_per_usd: "0.7630", usd_per_unit: "1.3106", pair_label: "GBP/USD", display_rate: "1.3106" },
      SAR: { units_per_usd: "3.7500", usd_per_unit: "0.2667", pair_label: "USD/SAR", display_rate: "3.7500" },
      AED: { units_per_usd: "3.6725", usd_per_unit: "0.2723", pair_label: "USD/AED", display_rate: "3.6725" },
      EGP: { units_per_usd: "54.8187", usd_per_unit: "0.0182", pair_label: "USD/EGP", display_rate: "54.8187" },
      TRY: { units_per_usd: "50.7000", usd_per_unit: "0.0197", pair_label: "USD/TRY", display_rate: "50.7000" },
      SYP: { units_per_usd: "152.5000", usd_per_unit: "0.0066", pair_label: "USD/SYP", display_rate: "152.5000", source: "sp-today" },
    },
  },
  gold: {
    currency: "USD",
    troy_ounce_grams: "31.1034768",
    updated_at: "2026-11-27T22:50:51Z",
    source: "swissquote_xau_bbo",
    screen_vs_real_spread_pct: "0.05",
    prices: {
      XAU_OZ: "3653.50",
      GRAM_24K: "117.46",
      GRAM_21K: "102.78",
      GRAM_18K: "88.10",
      XAG_OZ: "43.75",
      XAG_GRAM: "1.41",
    },
  },
  crypto: {
    quote_currency: "USD",
    updated_at: "2026-11-27T22:50:51Z",
    assets: {
      BTC: {
        symbol: "BTC",
        spot_price: "59749.90",
        spot_source: "binance_vision",
        funding: {
          primary_exchange: "okx",
          primary_rate: "0.00010000",
          secondary_exchange: "bitget",
          secondary_rate: "0.00008600",
          net_spread: "0.00001400",
          spread_bps: "0.14",
          annualized_spread_pct: "1.53",
          arbitrage_signal: "short_okx_long_bitget",
        },
      },
      ETH: {
        symbol: "ETH",
        spot_price: "1657.06",
        spot_source: "binance_vision",
        funding: {
          primary_exchange: "okx",
          primary_rate: "0.00006000",
          secondary_exchange: "bitget",
          secondary_rate: "0.00010000",
          net_spread: "-0.00004000",
          spread_bps: "0.40",
          annualized_spread_pct: "4.38",
          arbitrage_signal: "short_bitget_long_okx",
        },
      },
      SOL: {
        symbol: "SOL",
        spot_price: "72.78",
        spot_source: "binance_vision",
        funding: {
          primary_exchange: "okx",
          primary_rate: "0.00002000",
          secondary_exchange: "bitget",
          secondary_rate: "0.00010000",
          net_spread: "-0.00008000",
          spread_bps: "0.80",
          annualized_spread_pct: "8.76",
          arbitrage_signal: "short_bitget_long_okx",
        },
      },
      USDT: {
        symbol: "USDT",
        spot_price: "1.0000",
        spot_source: "coinmarketcap",
        funding: null,
      },
    },
  },
  news: [],
});

export const FALLBACK_CATALOG_SNAPSHOT = Object.freeze({
  meta: {
    fiat_count: 166,
    metals_count: 6,
    crypto_count: 300,
    generated_at: "2026-11-27T22:50:52Z",
  },
  fiat: {
    SYP: {
      units_per_usd: "152.5000",
      display_rate: "152.5000",
      pair_label: "USD/SYP",
      name_en: "Syrian Pound",
      name_ar: "الليرة السورية",
      symbol: "ل.س",
      source: "sp-today",
    },
  },
  metals: {
    XAU_OZ: { price_usd: "3653.50", name_en: "Gold Troy Ounce", name_ar: "أونصة الذهب الفوري" },
    GRAM_24K: { price_usd: "117.46", name_en: "Gold 24K Gram", name_ar: "جرام الذهب عيار 24" },
    GRAM_21K: { price_usd: "102.78", name_en: "Gold 21K Gram", name_ar: "جرام الذهب عيار 21" },
    GRAM_18K: { price_usd: "88.10", name_en: "Gold 18K Gram", name_ar: "جرام الذهب عيار 18" },
    XAG_OZ: { price_usd: "43.75", name_en: "Silver Troy Ounce", name_ar: "أونصة الفضة الفوري" },
    XAG_GRAM: { price_usd: "1.41", name_en: "Silver Gram (999)", name_ar: "جرام الفضة عيار 999" },
  },
  crypto: [
    {
      id: "USDT",
      cmc_id: 825,
      symbol: "USDT",
      name: "Tether USDt",
      rank: 3,
      price_usd: "1.0000",
      change_24h_pct: "0.00",
      logo_url: "https://s2.coinmarketcap.com/static/img/coins/64x64/825.png",
      category: "crypto",
    },
  ],
});
