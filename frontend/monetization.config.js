/**
 * RatePath (ريت باث) — Centralized Cloud Endpoints, Monetization & 4-Channel Telegram Config
 *
 * Edit ONLY this file to activate your live Hugging Face Spaces feed URL, public Cloudflare domain,
 * affiliate links, A-Ads unit ID, NOWPayments VIP checkout link, and EN/AR Telegram channels.
 */

// Live Serverless Feed URLs (GitHub Actions `live-data` orphan branch with CORS)
export const LIVE_FEED_URL =
  "https://raw.githubusercontent.com/pathrate-afk/ratepath/live-data/live_feed.json";

export const CATALOG_FEED_URL =
  "https://raw.githubusercontent.com/pathrate-afk/ratepath/live-data/catalog_feed.json";

// On-Demand CoinMarketCap Long-Tail Search Proxy (Cloudflare Pages Function)
export const CMC_SEARCH_API_URL = "/api/cmc-search";

// Public Supabase Auth & Preferences Sync Config
// SECURITY: ONLY place a public anon / publishable client key here (never a privileged backend secret key).
export const SUPABASE_URL = "https://kyhljlkituvhyiawjtqr.supabase.co";
export const SUPABASE_PUBLIC_ANON_KEY = "sb_publishable_WtLIN9IYG46IfC67plySDg_AStEV44u";

// Affiliate monetization toggle — strictly FALSE until verified partner accounts are activated
export const AFFILIATES_ENABLED = false;

// Public Canonical Site URL (Cloudflare Pages / Custom Domain)
export const PUBLIC_SITE_URL = "https://ratepath.io";

export const MONETIZATION_CONFIG = {
  AFFILIATES_ENABLED,
  LIVE_FEED_URL,
  CATALOG_FEED_URL,
  CMC_SEARCH_API_URL,
  SUPABASE_URL,
  SUPABASE_PUBLIC_ANON_KEY,
  PUBLIC_SITE_URL,
  cloudFeedUrl: LIVE_FEED_URL,

  // 1. Contextual Converter Affiliate Links
  affiliates: {
    // Shown when Fiat (USD, EUR, GBP, SAR, AED, EGP, TRY) is the active base asset
    wise: "https://wise.com/?utm_source=ratepath&utm_medium=converter_cta",
    remitly: "https://www.remitly.com/?utm_source=ratepath&utm_medium=converter_cta",

    // Shown when Gold (XAU_OZ, 24K, 21K, 18K) or Crypto (BTC, ETH, SOL) is the active base asset
    bybit: "https://www.bybit.com/?utm_source=ratepath&utm_medium=converter_cta",
    binance: "https://www.binance.com/?utm_source=ratepath&utm_medium=converter_cta",
  },

  // 2. Privacy-Preserving Display Ad Slot (A-Ads / Anonymous Ads)
  advertising: {
    // Drop your numeric A-Ads unit ID here (e.g. "2390123") to render the live iframe automatically
    aAdsUnitId: "",
    aAdsSize: "728x90",
    aAdsPartnerUrl: "https://aads.com/?partner=ratepath",
  },

  // 3. VIP Subscription Crypto Checkout (NOWPayments)
  vipPayments: {
    // Leave as "" until your real NOWPayments invoice/checkout link exists -> the button auto-hides.
    nowPaymentsVipUrl: "",
    vipMonthlyPriceUsd: "19",
  },

  // 4. Official 4-Channel Language-Separated Telegram Ecosystem Links
  // English UI automatically binds `_EN` channels; Arabic UI automatically binds `_AR` channels.
  telegram: {
    // Pure English Channels (bound when UI is EN / LTR) — verified live usernames
    publicChannelEn: "https://t.me/RatePathLive",
    // VIP channels are private (no public username) -> route visitors through the official bot
    vipChannelEn: "https://t.me/RatePathTerminalBot?start=vip_en",

    // Pure Arabic Channels (bound when UI is AR / RTL) — verified live usernames
    publicChannelAr: "https://t.me/RatePath_AR",
    vipChannelAr: "https://t.me/RatePathTerminalBot?start=vip_ar",

    // Shared Official Bot & Mini App Base URL (verified via Telegram getMe)
    botUrl: "https://t.me/RatePathTerminalBot",
    miniAppUrl: `${PUBLIC_SITE_URL}/`,

    // Backward-compatible fallbacks
    publicChannelUrl: "https://t.me/RatePathLive",
    vipChannelUrl: "https://t.me/RatePathTerminalBot?start=vip_en",
  },
};

export default MONETIZATION_CONFIG;
