/**
 * RatePath (ريت باث) — Geo-Compliant Affiliate Monetization Router
 *
 * Automatically filters Crypto Exchanges, Gold/Forex Brokers, Charting Tools,
 * Hardware Wallets, and Remittance apps by the visitor's real jurisdiction
 * (from Cloudflare Edge `/api/geo`) so visitors NEVER encounter blocked apps
 * (e.g. Binance/Bybit blocked in US/CA/UK/SY; Wise/Coinbase blocked in SY).
 *
 * Prioritizes USDT direct-to-wallet paying partners (Bybit, OKX, Bitget, Binance, Exness, Ledger).
 */

const EU_EEA_COUNTRIES = new Set([
  "AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR", "DE", "GR", "HU",
  "IE", "IT", "LV", "LT", "LU", "MT", "NL", "PL", "PT", "RO", "SK", "SI", "ES",
  "SE", "NO", "IS", "LI", "CH",
]);

const SANCTIONED_RESTRICTED = new Set(["SY", "IR", "KP", "CU"]);

/**
 * Jurisdiction compliance rules for every financial affiliate partner.
 * - `blocked`: ISO-2 countries where the partner is restricted/unavailable -> NEVER shown.
 * - `preferred`: Countries where the partner has top conversion & regulatory fit.
 * - `payout`: How the site owner receives affiliate commissions (USDT / BTC / Bank).
 */
export const PARTNER_GEO_RULES = Object.freeze({
  // --- Crypto Exchanges (Direct USDT Wallet Payouts) ---
  bybit: {
    id: "bybit",
    name: "Bybit",
    categories: ["crypto", "gold", "bots"],
    payout: "USDT",
    blocked: new Set(["US", "GB", "CA", "FR", "NL", "BE", "SG", "HK", "CN", ...SANCTIONED_RESTRICTED]),
    priority: 100,
    badgeEn: "Crypto & Gold Perps",
    badgeAr: "تداول كريبتو وذهب",
    ctaEn: "Trade on Bybit",
    ctaAr: "تداول عبر Bybit",
    botBadgeEn: "Grid & DCA Trading Bots",
    botBadgeAr: "بوتات التداول الآلي Grid و DCA",
    botCtaEn: "Explore Bybit Bots",
    botCtaAr: "بوتات Bybit الآلية",
  },
  okx: {
    id: "okx",
    name: "OKX",
    categories: ["crypto", "gold", "bots"],
    payout: "USDT",
    blocked: new Set(["US", "CA", "GB", "MY", "IN", ...SANCTIONED_RESTRICTED]),
    priority: 82,
    badgeEn: "Licensed Crypto Desk",
    badgeAr: "منصة مرخّصة (دبي/عالمي)",
    ctaEn: "Trade on OKX",
    ctaAr: "افتح حساب OKX",
    botBadgeEn: "Signal & Grid Bots",
    botBadgeAr: "بوتات الإشارات والتداول",
    botCtaEn: "OKX Trading Bots",
    botCtaAr: "بوتات OKX",
  },
  mexc: {
    id: "mexc",
    name: "MEXC",
    categories: ["crypto", "bots"],
    payout: "USDT",
    blocked: new Set(["US", "CA", "GB", "SG", "CN", ...SANCTIONED_RESTRICTED]),
    priority: 80,
    badgeEn: "0% Maker Spot & Altcoins",
    badgeAr: "عمولة منخفضة وآلاف العملات",
    ctaEn: "Trade on MEXC",
    ctaAr: "تداول في MEXC",
    botBadgeEn: "Low-Fee Spot & Futures Liquidity",
    botBadgeAr: "سيولة عالية وعقود دائمة برسوم منخفضة",
    botCtaEn: "Explore MEXC",
    botCtaAr: "افتح حساب MEXC",
  },
  bitget: {
    id: "bitget",
    name: "Bitget",
    categories: ["crypto", "gold", "bots"],
    payout: "USDT",
    blocked: new Set(["US", "CA", "GB", "SG", "HK", ...SANCTIONED_RESTRICTED]),
    priority: 90,
    badgeEn: "Copy Trading & Spot",
    badgeAr: "تداول ونسخ صفقات",
    ctaEn: "Join Bitget",
    ctaAr: "تداول في Bitget",
    botBadgeEn: "AI Grid & Copy Bots",
    botBadgeAr: "بوتات الذكاء الاصطناعي ونسخ الصفقات",
    botCtaEn: "Explore Bitget Bots",
    botCtaAr: "بوتات Bitget الذكية",
  },
  binance: {
    id: "binance",
    name: "Binance",
    categories: ["crypto", "gold"],
    payout: "USDT",
    blocked: new Set(["US", "CA", "GB", "NL", "DE", "BE", ...SANCTIONED_RESTRICTED]),
    priority: 88,
    badgeEn: "#1 Global Liquidity",
    badgeAr: "أكبر سيولة عالمية",
    ctaEn: "Open Binance",
    ctaAr: "افتح Binance",
  },
  kraken: {
    id: "kraken",
    name: "Kraken",
    categories: ["crypto", "gold"],
    payout: "USD/Crypto",
    blocked: new Set(["CN", "RU", ...SANCTIONED_RESTRICTED]),
    priority: 84,
    badgeEn: "US / UK / EU Regulated",
    badgeAr: "مرخّصة لأمريكا وأوروبا",
    ctaEn: "Trade on Kraken",
    ctaAr: "تداول في Kraken",
  },
  coinbase: {
    id: "coinbase",
    name: "Coinbase",
    categories: ["crypto"],
    payout: "USD/USDC",
    blocked: new Set(["CN", "RU", "TR", ...SANCTIONED_RESTRICTED]),
    priority: 82,
    badgeEn: "US / UK Public Exchange",
    badgeAr: "بورصة مدرجة بأمريكا",
    ctaEn: "Open Coinbase",
    ctaAr: "افتح Coinbase",
  },
  // --- Gold (XAU/USD) & Forex Stock/Commodity Broker (Direct USDT TRC20 Payout!) ---
  exness: {
    id: "exness",
    name: "Exness",
    categories: ["gold", "fiat", "bots"],
    payout: "USDT",
    blocked: new Set(["US", "CA", "GB", "AU", "JP", ...EU_EEA_COUNTRIES, ...SANCTIONED_RESTRICTED]),
    priority: 98,
    badgeEn: "Gold XAU & FX Zero Spread",
    badgeAr: "تداول الذهب XAU والعملات",
    ctaEn: "Trade Gold on Exness",
    ctaAr: "تداول الذهب في Exness",
    botBadgeEn: "MT5 Automated Gold & FX Strategies",
    botBadgeAr: "منصة MT5 الآلية للذهب والعملات",
    botCtaEn: "Exness MT5 Desk",
    botCtaAr: "منصة Exness الآلية",
  },
  // --- Global Charts & Smart Alerts (Works almost everywhere, including US/EU/MENA/SY) ---
  tradingview: {
    id: "tradingview",
    name: "TradingView",
    categories: ["gold", "crypto", "fiat", "bots"],
    payout: "USD/Crypto",
    blocked: new Set(["KP", "IR"]),
    priority: 86,
    badgeEn: "Pro Market Charts",
    badgeAr: "الرسوم البيانية الاحترافية",
    ctaEn: "Charts on TradingView",
    ctaAr: "تحليلات TradingView",
    botBadgeEn: "Stocks, Gold, FX & Crypto Smart Alerts",
    botBadgeAr: "تنبيهات ذكية للأسهم والذهب والعملات",
    botCtaEn: "TradingView Alerts",
    botCtaAr: "تنبيهات TradingView",
  },
  // --- Dedicated Auto-Trading Bot Affiliates (activate by pasting link in monetization.config.js) ---
  pionex: {
    id: "pionex",
    name: "Pionex",
    categories: ["bots", "crypto"],
    payout: "USDT",
    blocked: new Set(["US", "CN", ...SANCTIONED_RESTRICTED]),
    priority: 92,
    badgeEn: "Built-In Crypto Bots",
    badgeAr: "منصة بوتات تداول مدمجة",
    ctaEn: "Try Pionex Bots",
    ctaAr: "جرّب بوتات Pionex",
    botBadgeEn: "16 Built-In Automated Grid/DCA Bots",
    botBadgeAr: "16 بوت تداول آلي مدمج (دفع USDT)",
    botCtaEn: "Launch Pionex Bots",
    botCtaAr: "بوتات Pionex الآلية",
  },
  threecommas: {
    id: "threecommas",
    name: "3Commas",
    categories: ["bots"],
    payout: "USD/Crypto",
    blocked: new Set([...SANCTIONED_RESTRICTED]),
    priority: 88,
    badgeEn: "Multi-Exchange Bot Hub",
    badgeAr: "إدارة بوتات متعددة المنصات",
    ctaEn: "Explore 3Commas",
    ctaAr: "منصة 3Commas",
    botBadgeEn: "Automated DCA, Grid & Signal Bots",
    botBadgeAr: "تأجير وإدارة بوتات التداول الآلي",
    botCtaEn: "Try 3Commas Bots",
    botCtaAr: "بوتات 3Commas",
  },
  cryptohopper: {
    id: "cryptohopper",
    name: "Cryptohopper",
    categories: ["bots"],
    payout: "USD",
    blocked: new Set([...SANCTIONED_RESTRICTED]),
    priority: 84,
    badgeEn: "Cloud AI Trading Bots",
    badgeAr: "بوتات تداول سحابية",
    ctaEn: "Explore Cryptohopper",
    ctaAr: "منصة Cryptohopper",
    botBadgeEn: "24/7 Cloud Strategy & Alert Bots",
    botBadgeAr: "بوتات متابعة وتنفيذ سحابية 24/7",
    botCtaEn: "Try Cryptohopper",
    botCtaAr: "بوتات Cryptohopper",
  },
  ledger: {
    id: "ledger",
    name: "Ledger",
    categories: ["crypto"],
    payout: "BTC/USDT",
    blocked: new Set([...SANCTIONED_RESTRICTED]),
    priority: 70,
    badgeEn: "Cold Wallet Security",
    badgeAr: "محفظة باردة لحفظ أصولك",
    ctaEn: "Secure with Ledger",
    ctaAr: "احمِ عملاتك بـ Ledger",
  },
  // --- Cross-Border Fiat Transfers ---
  wise: {
    id: "wise",
    name: "Wise",
    categories: ["fiat"],
    payout: "Bank/USD",
    blocked: new Set(["LB", "IQ", "YE", "LY", "SD", "AF", ...SANCTIONED_RESTRICTED]),
    priority: 92,
    badgeEn: "Real Mid-Market FX",
    badgeAr: "تحويل دولي بسعر السوق",
    ctaEn: "Send via Wise",
    ctaAr: "حوّل عبر Wise",
  },
  remitly: {
    id: "remitly",
    name: "Remitly",
    categories: ["fiat"],
    payout: "USD",
    blocked: new Set(["LB", "IQ", "YE", "LY", "SD", "AF", ...SANCTIONED_RESTRICTED]),
    priority: 85,
    badgeEn: "Fast Family Transfers",
    badgeAr: "تحويلات دولية سريعة",
    ctaEn: "Try Remitly",
    ctaAr: "خدمة Remitly",
  },
});

export function isPartnerConfiguredUrl(url) {
  const s = String(url || "").trim();
  if (!/^https:\/\//i.test(s)) return false;
  if (/placeholder|your_ref|change_me/i.test(s)) return false;
  return true;
}

/**
 * Select up to `maxCount` geo-compliant, active affiliate partners for the visitor's country & asset category.
 * Guarantees a visitor in US/UK/CA/EU/SY NEVER sees a geo-blocked platform.
 */
export function selectGeoCompliantPartners({
  countryCode = "GLOBAL",
  category = "crypto",
  affiliatesConfig = {},
  affiliatesEnabled = false,
  maxCount = 2,
} = {}) {
  if (!affiliatesEnabled) return [];

  const cc = String(countryCode || "GLOBAL").trim().toUpperCase();
  const cat =
    category === "fiat" || category === "gold" || category === "bots" ? category : "crypto";

  const eligible = [];
  for (const [key, rule] of Object.entries(PARTNER_GEO_RULES)) {
    if (!rule.categories.includes(cat)) continue;
    if (rule.blocked.has(cc)) continue;

    const rawEntry = affiliatesConfig?.[key];
    const url = typeof rawEntry === "string" ? rawEntry : rawEntry?.url;
    const explicitlyDisabled = typeof rawEntry === "object" && rawEntry?.enabled === false;
    if (explicitlyDisabled || !isPartnerConfiguredUrl(url)) continue;

    // Boost US/UK/EU-regulated partners when visitor is in US/UK/CA/EU
    let score = rule.priority;
    if ((cc === "US" || cc === "GB" || cc === "CA" || EU_EEA_COUNTRIES.has(cc)) && (key === "kraken" || key === "coinbase" || key === "wise")) {
      score += 15;
    }
    if (cat === "gold" && key === "exness") {
      score += 12;
    }

    eligible.push({
      ...rule,
      url: String(url).trim(),
      score,
    });
  }

  eligible.sort((a, b) => b.score - a.score);
  return eligible.slice(0, maxCount);
}

/**
 * Fetch visitor ISO-2 country code from Cloudflare Edge `/api/geo` (cached in sessionStorage).
 */
export async function detectVisitorCountry() {
  try {
    const cached = sessionStorage.getItem("ratepath_geo_country");
    if (cached && /^[A-Z]{2}$/.test(cached)) return cached;
  } catch {
    // Ignore storage errors
  }

  try {
    const resp = await fetch("/api/geo", { cache: "default" });
    if (resp.ok) {
      const data = await resp.json();
      const cc = String(data?.country || "GLOBAL").toUpperCase();
      if (/^[A-Z]{2}$/.test(cc)) {
        try {
          sessionStorage.setItem("ratepath_geo_country", cc);
        } catch {
          // Ignore
        }
        return cc;
      }
    }
  } catch {
    // Local dev or offline fallback
  }
  return "GLOBAL";
}
