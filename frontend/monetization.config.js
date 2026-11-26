/**
 * RatePath (ريت باث) — Centralized Cloud Endpoints, Geo-Filtered Monetization & 4-Channel Telegram Config
 *
 * Edit ONLY this file to activate your personal referral URLs, A-Ads unit ID,
 * NOWPayments $10/mo USDT VIP checkout, and EN/AR Telegram channels.
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

// Affiliate monetization toggle — flip to `true` as soon as you paste your personal referral links below.
// Geo-compliance router (`frontend/js/affiliates.js`) automatically hides any partner blocked in the visitor's country.
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

  // 1. Geo-Filtered Financial & Crypto Affiliates (Paste YOUR real partner link; leave "" to hide)
  // Payouts marked [USDT] pay commissions directly to your USDT wallet without a bank account.
  affiliates: {
    // Crypto Exchanges — MENA / Asia / LATAM / Turkey [Direct USDT Payout]
    bybit: "",       // https://partner.bybit.com/b/YOUR_CODE  (Blocked in US/UK/CA/SY)
    okx: "",         // https://www.okx.com/join/YOUR_CODE     (Blocked in US/UK/CA/SY)
    bitget: "",      // https://partner.bitget.com/bg/YOUR_CODE (Blocked in US/UK/CA/SY)
    binance: "",     // https://accounts.binance.com/register?ref=YOUR_CODE (Blocked in US/UK/CA/SY)

    // Gold (XAU/USD) & Forex Trading Broker [Direct USDT TRC20 Payout]
    exness: "",      // https://one.exness-track.com/a/YOUR_CODE (MENA/Asia/LATAM/TR; blocked in US/EU/UK/SY)

    // Regulated US / UK / Canada / EU Exchanges (Auto-shown ONLY to US/UK/CA/EU visitors)
    kraken: "",      // https://kraken.pxf.io/YOUR_CODE
    coinbase: "",    // https://advanced.coinbase.com/join/YOUR_CODE

    // Global Charting & Cold Wallet (Works worldwide including US/EU/MENA; TradingView works in SY too)
    tradingview: "", // https://www.tradingview.com/?aff_id=YOUR_ID
    ledger: "",      // https://shop.ledger.com/?r=YOUR_CODE    [Direct BTC/USDT Payout]

    // International Fiat Remittances (Auto-hidden in restricted countries like SY/LB/IQ)
    wise: "",        // https://wise.com/invite/u/YOUR_CODE
    remitly: "",     // https://remit.ly/YOUR_CODE
  },

  // 2. Privacy-Preserving Display Ad Slot (A-Ads / Anonymous Ads — pays directly in BTC/Crypto!)
  advertising: {
    // Drop your numeric A-Ads unit ID here (e.g. "2390123") to render the live iframe automatically
    aAdsUnitId: "",
    aAdsSize: "728x90",
    aAdsPartnerUrl: "https://aads.com/?partner=ratepath",
  },

  // 3. Business VIP Channel Subscription — $10/month paid in USDT directly to owner's NOWPayments wallet
  vipPayments: {
    checkoutApiUrl: "/api/create-vip-checkout",
    // Optional static NOWPayments Payment Link fallback if API key is not configured yet
    nowPaymentsVipUrl: "",
    vipMonthlyPriceUsd: "10",
    defaultPayCurrency: "usdttrc20",
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
