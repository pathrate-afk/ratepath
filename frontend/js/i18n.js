/**
 * RatePath (ريت باث) — Instant Bilingual (EN LTR / AR RTL) Dictionary & JSON-LD SEO Engine
 */

import { PUBLIC_SITE_URL } from "../monetization.config.js";

export const TRANSLATIONS = {
  en: {
    brandName: "RatePath",
    brandSub: "Institutional Cross-Asset & Macro Terminal",
    liveHealthy: "LIVE FEED",
    offlineMode: "OFFLINE CACHE",
    wakeLockOff: "Keep Screen On",
    wakeLockOn: "Screen Awake",
    wakeLockUnsupported: "Wake-Lock N/A",
    shareCardBtn: "Quote Card PNG",
    vipAlertsBtn: "VIP Alerts",
    refreshTitle: "Sync live market feed",
    converterTitle: "Single-Input Cross-Asset Converter",
    converterSubtitle: "Type once — instant BigInt Decimal conversion across 14 Fiat, Gold & Crypto benchmarks",
    amountLabel: "Amount",
    baseAssetLabel: "Base Asset",
    usdValuePrefix: "Institutional USD Equivalent:",
    tapToSetBase: "Click any card to set as active base",
    sectionFiat: "Fiat Currencies (USD Cross-Rates)",
    sectionGold: "Spot & Karat Gold (31.1034768g / oz)",
    sectionCrypto: "Digital Assets (Spot Liquidity)",
    fundingTitle: "Perpetual Funding Spreads (8h Carry)",
    fundingSubtitle: "Live cross-exchange arbitrage across Binance & Bybit linear contracts",
    colAsset: "Asset",
    colPrimary: "Primary (8h)",
    colSecondary: "Secondary (8h)",
    colSpreadBps: "Spread",
    colAnnYield: "Ann. Carry",
    colSignal: "Strategy",
    newsTitle: "Auditor-Verified Macro Intelligence",
    newsSubtitle: "Dual-Pass AI thesis, priced-in exhaustion warnings & 24h asset biases",
    auditorVerified: "Auditor Verified",
    confidenceLabel: "Confidence",
    sourceLinkLabel: "Verified Source",
    macroThesisLabel: "24h Institutional Thesis",
    pricedInActive: "Priced-In Risk",
    pricedInFresh: "Fresh Catalyst",
    biasBullish: "Bullish",
    biasBearish: "Bearish",
    biasNeutral: "Neutral",
    adSlotBadge: "SPONSORED • A-ADS NETWORK",
    adSlotHeadline: "Zero-Tracking Institutional Crypto & FinTech Advertising Slot (A-Ads 728×90)",
    adSlotCta: "Advertise on RatePath",
    // Contextual Affiliate CTAs
    affFiatBadge: "FX TRANSFER PARTNER",
    affFiatTitle: "Sending EUR, GBP, SAR, AED, EGP or TRY internationally?",
    affFiatDesc: "Cut hidden bank FX markups up to 3.8% using Wise & Remitly mid-market rails.",
    affFiatBtnPrimary: "Compare on Wise",
    affFiatBtnSecondary: "Remitly Express",
    affCryptoBadge: "LIQUIDITY & GOLD EXECUTION",
    affCryptoTitle: "Execute Spot BTC, ETH, SOL & Tokenized Gold (PAXG) with Tight Spreads",
    affCryptoDesc: "Access deep institutional order books and capture 8h perpetual funding carry on Bybit & Binance.",
    affCryptoBtnPrimary: "Trade on Bybit",
    affCryptoBtnSecondary: "Open Binance",
    // Mobile Bottom Navigation
    navConverter: "Convert",
    navRates: "Rates",
    navFunding: "Funding",
    navNews: "Macro",
    navVip: "VIP Alerts",
    // Quote Card Modal
    cardModalTitle: "RatePath Watermarked Quote Card (PNG)",
    cardModalSubtitle: "High-resolution 1200×630 institutional snapshot ready for Telegram, X & WhatsApp",
    downloadPngBtn: "Download PNG",
    sharePngBtn: "Share Snapshot",
    closeBtn: "Close",
    // VIP Price Alert Modal
    vipModalTitle: "VIP Telegram Price & Spread Alerts",
    vipModalSubtitle: "Instant push alerts on XAU Gold breakouts, EGP/TRY shifts & >1.5 bps funding arbitrage",
    alertAssetLabel: "Target Market",
    alertConditionLabel: "Trigger Condition",
    condAbove: "Crosses Above (≥)",
    condBelow: "Drops Below (≤)",
    alertPriceLabel: "Target Price (USD / Rate)",
    alertCreateBtn: "Activate Local + VIP Alert",
    activeAlertsHeading: "Active Watchlist Triggers",
    noActiveAlerts: "No custom triggers armed yet. Add a target above or join the live VIP channel.",
    joinVipChannelBtn: "Join RatePath VIP Channel (EN)",
    joinPublicChannelBtn: "Public Macro Channel (EN)",
    nowPaymentsVipBtn: "Unlock VIP via NOWPayments ($19/mo)",
    openTelegramBotBtn: "Launch @RatePathBot",
    deleteAlertLabel: "Remove",
    // Asset Localized Names
    assets: {
      USD: "US Dollar",
      EUR: "Euro",
      GBP: "British Pound",
      SAR: "Saudi Riyal",
      AED: "UAE Dirham",
      EGP: "Egyptian Pound",
      TRY: "Turkish Lira",
      XAU_OZ: "Gold Troy Ounce",
      GRAM_24K: "Gold 24K Gram",
      GRAM_21K: "Gold 21K Gram",
      GRAM_18K: "Gold 18K Gram",
      BTC: "Bitcoin",
      ETH: "Ethereum",
      SOL: "Solana",
    },
  },
  ar: {
    brandName: "ريت باث",
    brandSub: "المنصة المؤسسية للعملات والذهب والأصول الرقمية",
    liveHealthy: "بث مباشر",
    offlineMode: "وضع عدم الاتصال",
    wakeLockOff: "تثبيت إضاءة الشاشة",
    wakeLockOn: "الشاشة نشطة",
    wakeLockUnsupported: "غير مدعوم",
    shareCardBtn: "بطاقة الأسعار PNG",
    vipAlertsBtn: "تنبيهات VIP",
    refreshTitle: "تحديث الأسعار الحية",
    converterTitle: "المحول الفوري الموحد عبر فئات الأصول",
    converterSubtitle: "أدخل مبلغاً واحداً للتحويل الدقيق فوراً عبر 14 عملة وعيارات الذهب والعملات الرقمية",
    amountLabel: "المبلغ",
    baseAssetLabel: "الأصل الأساسي",
    usdValuePrefix: "المعادل المؤسسي بالدولار الأمريكي:",
    tapToSetBase: "اضغط على أي بطاقة لجعلها عملة الأساس",
    sectionFiat: "العملات النقدية الرئيسية والإقليمية",
    sectionGold: "الذهب الفوري وعيارات الجرام (الأونصة 31.1034768 جم)",
    sectionCrypto: "الأصول الرقمية الفورية",
    fundingTitle: "فروق عقود التمويل الدائمة (كل 8 ساعات)",
    fundingSubtitle: "رصد فوري لفرص المراجحة بين منصتي Binance و Bybit",
    colAsset: "الأصل",
    colPrimary: "المنصة الأولى",
    colSecondary: "المنصة الثانية",
    colSpreadBps: "الفرق (bps)",
    colAnnYield: "العائد السنوي",
    colSignal: "الإشارة",
    newsTitle: "التحليلات الاقتصادية الكلية الموثقة",
    newsSubtitle: "تحليل مزدوج التدقيق مع تحذيرات التسعير المسبق وتوقعات 24 ساعة",
    auditorVerified: "مدقق وموثق",
    confidenceLabel: "درجة الثقة",
    sourceLinkLabel: "المصدر الأصلي",
    macroThesisLabel: "الأطروحة المؤسسية لـ 24 ساعة",
    pricedInActive: "تحذير تسعير مسبق",
    pricedInFresh: "محفز سعري جديد",
    biasBullish: "صعودي",
    biasBearish: "هبوطي",
    biasNeutral: "محايد",
    adSlotBadge: "إعلان ممول • شبكة A-ADS",
    adSlotHeadline: "مساحة إعلانية مالية تحترم الخصوصية بدون ملفات تتبع (A-Ads 728×90)",
    adSlotCta: "أعلن عبر ريت باث",
    // Contextual Affiliate CTAs
    affFiatBadge: "شريك التحويلات الدولية",
    affFiatTitle: "هل تحوّل الدولار أو اليورو أو الريال أو الدرهم أو الجنيه دولياً؟",
    affFiatDesc: "وفّر حتى 3.8% من هوامش البنوك المخفية عبر التحويل بالسعر الحقيقي مع Wise و Remitly.",
    affFiatBtnPrimary: "حوّل عبر Wise",
    affFiatBtnSecondary: "خدمة Remitly",
    affCryptoBadge: "تنفيذ الذهب والسيولة الرقمية",
    affCryptoTitle: "تداول البيتكوين والإيثيريوم والذهب المرمّز بأقل الفروق السعرية",
    affCryptoDesc: "سيولة مؤسسية عميقة واستفادة من عوائد فروق التمويل عبر Bybit و Binance.",
    affCryptoBtnPrimary: "تداول في Bybit",
    affCryptoBtnSecondary: "افتح Binance",
    // Mobile Bottom Navigation
    navConverter: "المحول",
    navRates: "الأسعار",
    navFunding: "التمويل",
    navNews: "الأخبار",
    navVip: "تنبيهات VIP",
    // Quote Card Modal
    cardModalTitle: "بطاقة ريت باث الموثقة للأسعار (PNG)",
    cardModalSubtitle: "صورة عالية الدقة 1200×630 مع العلامة المائية جاهزة للمشاركة على تيليجرام وإكس وواتساب",
    downloadPngBtn: "تحميل البطاقة PNG",
    sharePngBtn: "مشاركة فورية",
    closeBtn: "إغلاق",
    // VIP Price Alert Modal
    vipModalTitle: "تنبيهات الأسعار وفروق التمويل عبر تيليجرام VIP",
    vipModalSubtitle: "إشعارات فورية عند اختراق الذهب أو تحرك العملات وفروق عوائد العقود الدائمة",
    alertAssetLabel: "الأصل المستهدف",
    alertConditionLabel: "شرط التنبيه",
    condAbove: "يتجاوز صعوداً (≥)",
    condBelow: "يكسر هبوطاً (≤)",
    alertPriceLabel: "السعر المستهدف",
    alertCreateBtn: "تفعيل التنبيه المحلي + VIP",
    activeAlertsHeading: "التنبيهات النشطة حالياً",
    noActiveAlerts: "لا توجد تنبيهات مخصصة حالياً. أضف مستوى سعري أعلاه أو انضم لقناة VIP.",
    joinVipChannelBtn: "انضم لقناة ريت باث VIP (عربي)",
    joinPublicChannelBtn: "القناة العربية العامة للأخبار",
    nowPaymentsVipBtn: "اشتراك VIP عبر NOWPayments (19$/شهرياً)",
    openTelegramBotBtn: "تشغيل بوت تيليجرام",
    deleteAlertLabel: "حذف",
    // Asset Localized Names
    assets: {
      USD: "الدولار الأمريكي",
      EUR: "اليورو الأوروبي",
      GBP: "الجنيه الإسترليني",
      SAR: "الريال السعودي",
      AED: "الدرهم الإماراتي",
      EGP: "الجنيه المصري",
      TRY: "الليرة التركية",
      XAU_OZ: "أونصة الذهب الفوري",
      GRAM_24K: "جرام الذهب عيار 24",
      GRAM_21K: "جرام الذهب عيار 21",
      GRAM_18K: "جرام الذهب عيار 18",
      BTC: "بيتكوين",
      ETH: "إيثيريوم",
      SOL: "سولانا",
    },
  },
};

/**
 * Inject/Update structured JSON-LD FinancialService + ExchangeRateSpecification + NewsArticle SEO Schema.
 */
export function updateSeoJsonLd(feed, lang = "en") {
  const scriptEl = document.getElementById("seo-jsonld");
  if (!scriptEl || !feed) return;

  const fiatRates = feed?.fiat?.rates || {};
  const goldPrices = feed?.gold?.prices || {};
  const cryptoAssets = feed?.crypto?.assets || {};

  const exchangeSpecs = Object.entries(fiatRates).map(([code, data]) => ({
    "@type": "ExchangeRateSpecification",
    currency: code,
    currentExchangeRate: {
      "@type": "UnitPriceSpecification",
      price: data.units_per_usd,
      priceCurrency: "USD",
    },
  }));

  exchangeSpecs.push({
    "@type": "ExchangeRateSpecification",
    currency: "XAU",
    currentExchangeRate: {
      "@type": "UnitPriceSpecification",
      price: goldPrices.XAU_OZ || "0",
      priceCurrency: "USD",
    },
  });

  for (const [sym, data] of Object.entries(cryptoAssets)) {
    exchangeSpecs.push({
      "@type": "ExchangeRateSpecification",
      currency: sym,
      currentExchangeRate: {
        "@type": "UnitPriceSpecification",
        price: data.spot_price,
        priceCurrency: "USD",
      },
    });
  }

  const newsArticles = (feed?.news || []).map((n) => ({
    "@type": "NewsArticle",
    headline: lang === "ar" ? n.ar?.title : n.en?.title,
    description: lang === "ar" ? n.ar?.summary : n.en?.summary,
    datePublished: n.published_at,
    inLanguage: lang === "ar" ? "ar" : "en",
    url: n.source_url,
    publisher: {
      "@type": "Organization",
      name: n.source_name,
    },
  }));

  const siteBase = String(PUBLIC_SITE_URL || "https://ratepath.io").replace(/\/+$/, "");
  const schemaGraph = {
    "@context": "https://schema.org",
    "@graph": [
      {
        "@type": "FinancialService",
        name: "RatePath | ريت باث",
        alternateName: "ريت باث",
        url: `${siteBase}/`,
        logo: `${siteBase}/logo.png`,
        image: `${siteBase}/icons/logo-full.jpg`,
        description: TRANSLATIONS[lang]?.brandSub || TRANSLATIONS.en.brandSub,
        areaServed: "Global",
        availableLanguage: ["en", "ar"],
        hasOfferCatalog: {
          "@type": "OfferCatalog",
          name: "Live Cross-Asset Reference Rates",
          itemListElement: exchangeSpecs,
        },
      },
      {
        "@type": "ItemList",
        name: lang === "ar" ? "تحليلات ريت باث الاقتصادية الموثقة" : "RatePath Verified Macro Intelligence",
        itemListElement: newsArticles,
      },
    ],
  };

  scriptEl.textContent = JSON.stringify(schemaGraph, null, 2);
}
