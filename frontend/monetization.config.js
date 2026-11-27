/**
 * RatePath (ريت باث) — Centralized Cloud Endpoints, Feature Flags, Geo-Filtered Monetization & Telegram Config
 *
 * Edit ONLY this file to manage your Supabase public key, NOWPayments USDT checkout links,
 * affiliate referral URLs, A-Ads unit ID, section feature flags, and EN/AR Telegram channels.
 */

// ---------------------------------------------------------------------------
// 1. Live Serverless Data Feeds (GitHub `live-data` branch + Cloudflare Pages fallback)
// ---------------------------------------------------------------------------
export const LIVE_FEED_URL =
  "https://raw.githubusercontent.com/pathrate-afk/ratepath/live-data/live_feed.json";

export const CATALOG_FEED_URL =
  "https://raw.githubusercontent.com/pathrate-afk/ratepath/live-data/catalog_feed.json";

export const CMC_SEARCH_API_URL = "/api/cmc-search";

// ---------------------------------------------------------------------------
// 2. Public Supabase Cloud Sync (Optional — Cloud Sync UI hides cleanly if left blank)
// SECURITY: Paste ONLY your public anon / publishable client key below (NEVER a privileged backend secret key).
// ---------------------------------------------------------------------------
export const SUPABASE_URL = "https://kyhljlkituvhyiawjtqr.supabase.co"; // Paste your Supabase Project URL (or "" to hide Cloud Sync)
export const SUPABASE_ANON_KEY = "sb_publishable_WtLIN9IYG46IfC67plySDg_AStEV44u"; // Paste your Supabase public anon/publishable key (or "" to hide Cloud Sync)
export const SUPABASE_PUBLIC_ANON_KEY = SUPABASE_ANON_KEY; // Backward-compatible alias

// ---------------------------------------------------------------------------
// 3. Section Feature Flags (Auto-hides any section with 0 live rows for maximum conversion)
// ---------------------------------------------------------------------------
export const FEATURE_FLAGS = {
  showFundingSpreads: true,
  showQuantRadar: true,
  showLiveNews: true,
  showBreakingBar: true,
  showPersonalBot: true,
  showPartnersShelf: true,
  showCloudSync: true,        // Automatically hidden if SUPABASE_URL or SUPABASE_ANON_KEY is empty
  autoHideEmptySections: true, // Automatically hides sections if live upstream returns 0 rows
};

// Affiliate monetization toggle — Geo-compliance router (`frontend/js/affiliates.js`) automatically
// hides any partner restricted in the visitor's jurisdiction.
export const AFFILIATES_ENABLED = true;

// Public Canonical Site URL
export const PUBLIC_SITE_URL = "https://ratepathlive.com";

export const MONETIZATION_CONFIG = {
  AFFILIATES_ENABLED,
  FEATURE_FLAGS,
  LIVE_FEED_URL,
  CATALOG_FEED_URL,
  CMC_SEARCH_API_URL,
  SUPABASE_URL,
  SUPABASE_ANON_KEY,
  SUPABASE_PUBLIC_ANON_KEY,
  PUBLIC_SITE_URL,
  cloudFeedUrl: LIVE_FEED_URL,

  // -------------------------------------------------------------------------
  // 4. Geo-Filtered Crypto, Gold/FX & Bot Affiliate Partners
  // Replace global fallback URLs below with your personal affiliate referral links to earn USDT commissions.
  // -------------------------------------------------------------------------
  affiliates: {
    // Active Verified Referral Links [Direct USDT Payout]
    bybit: "https://www.bybit.com/invite?ref=N8PZMKN&medium=referral&utm_campaign=evergreen&share_to=link",
    bitget: "https://share.bitget.com/u/N9R5T1J8",
    exness: "https://one.toexness.com/a/c_zmvyur7qg4",
    tradingview: "https://www.tradingview.com/?aff_id=1176056",

    // Global Fallback Partners (Clickable now — paste your personal referral codes when approved)
    okx: "https://www.okx.com/join",          // Paste your OKX referral link here: https://www.okx.com/join/YOUR_CODE
    mexc: "https://www.mexc.com/register",    // Paste your MEXC referral link here: https://www.mexc.com/register?inviteCode=YOUR_CODE

    // Optional Extra Exchanges & Wallets (Leave "" to keep hidden until you paste your referral link)
    binance: "",      // https://accounts.binance.com/register?ref=YOUR_CODE
    kraken: "",       // https://kraken.pxf.io/YOUR_CODE        (Auto-shown ONLY in US/UK/CA/EU)
    coinbase: "",     // https://advanced.coinbase.com/join/YOUR_CODE (Auto-shown ONLY in US/UK)
    ledger: "",       // https://shop.ledger.com/?r=YOUR_CODE
    pionex: "",       // https://www.pionex.com/en/signUp?r=YOUR_CODE
    threecommas: "",  // https://3commas.io/?c=YOUR_CODE
    cryptohopper: "", // https://www.cryptohopper.com/?atid=YOUR_CODE
    wise: "",         // https://wise.com/invite/u/YOUR_CODE
    remitly: "",      // https://remit.ly/YOUR_CODE
  },

  // -------------------------------------------------------------------------
  // 5. Privacy-Preserving Display Ad Slot (A-Ads — auto-hidden until numeric ID is pasted)
  // -------------------------------------------------------------------------
  advertising: {
    aAdsUnitId: "", // Paste numeric A-Ads unit ID (e.g. "2390123") to activate banner
    aAdsSize: "728x90",
    aAdsPartnerUrl: "https://aads.com/?partner=ratepath",
  },

  // -------------------------------------------------------------------------
  // 6. Direct USDT Subscriptions (NOWPayments API + Optional Static Payment Links)
  // -------------------------------------------------------------------------
  vipPayments: {
    checkoutApiUrl: "/api/create-vip-checkout",
    // Paste your direct NOWPayments Payment Links below as instant fallback if API key is paused:
    nowPaymentsVipUrl: "",         // $10/mo Business VIP + Bot: https://nowpayments.io/payment/?iid=YOUR_10_USD_LINK
    nowPaymentsPersonalBotUrl: "", // $19/mo Personal Bot Pro:   https://nowpayments.io/payment/?iid=YOUR_19_USD_LINK
    vipMonthlyPriceUsd: "10",
    personalBotMonthlyPriceUsd: "19",
    defaultPayCurrency: "usdttrc20",
  },

  // -------------------------------------------------------------------------
  // 7. Official Bilingual Telegram Ecosystem Links (Auto-bound by EN / AR)
  // -------------------------------------------------------------------------
  telegram: {
    publicChannelEn: "https://t.me/RatePathLive",
    vipChannelEn: "https://t.me/RatePathTerminalBot?start=vip_en",
    publicChannelAr: "https://t.me/RatePath_AR",
    vipChannelAr: "https://t.me/RatePathTerminalBot?start=vip_ar",
    botUrl: "https://t.me/RatePathTerminalBot",
    miniAppUrl: `${PUBLIC_SITE_URL}/`,
    publicChannelUrl: "https://t.me/RatePathLive",
    vipChannelUrl: "https://t.me/RatePathTerminalBot?start=vip_en",
  },
};

export default MONETIZATION_CONFIG;
