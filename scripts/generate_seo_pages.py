"""Programmatic SEO Engine for RatePath (ريت باث).

Generates 10 high-intent bilingual (Arabic + English) static landing pages under
`frontend/<slug>/index.html`, plus an auto-updated `frontend/sitemap.xml` and
`frontend/robots.txt`.

Every generated page:
- Pre-renders real initial prices from `frontend/public/live_feed.json` & `catalog_feed.json`
  so Googlebot, Bingbot, and first-paint visitors see live values immediately.
- Hydrates continuously via `/js/seo-pair-page.js` (100% CSP-compliant, zero inline JS).
- Includes a pre-selected Instant Converter, direct Telegram alert CTA (`@RatePathTerminalBot`),
  unique `<title>`, `<meta description>`, canonical URL, hreflang tags, OpenGraph/Twitter tags,
  and structured JSON-LD (`FinancialProduct` + `FAQPage`).
- Strictly avoids the raw Unicode Syria flag emoji (uses custom revolution flag SVG + `🟢 ل.س`).
"""

from __future__ import annotations

import html
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Any

WORKSPACE_ROOT: Path = Path(__file__).resolve().parent.parent
FRONTEND_DIR: Path = WORKSPACE_ROOT / "frontend"
LIVE_FEED_PATH: Path = FRONTEND_DIR / "public" / "live_feed.json"
CATALOG_FEED_PATH: Path = FRONTEND_DIR / "public" / "catalog_feed.json"
SITE_ORIGIN: str = "https://ratepathlive.com"
BOT_USERNAME: str = "RatePathTerminalBot"
RAW_SYRIA_FLAG: str = "\U0001F1F8\U0001F1FE"


@dataclass(frozen=True)
class SeoPageSpec:
    slug: str
    base: str
    quote: str
    mode: str  # "pair" | "gold" | "silver"
    title_ar: str
    title_en: str
    h1_ar: str
    h1_en: str
    subtitle_ar: str
    subtitle_en: str
    desc_ar: str
    desc_en: str
    keywords: str
    faqs: tuple[tuple[str, str, str, str], ...]  # (q_ar, a_ar, q_en, a_en)


SEO_PAGES: tuple[SeoPageSpec, ...] = (
    SeoPageSpec(
        slug="usd-to-syp",
        base="USD",
        quote="SYP",
        mode="pair",
        title_ar="سعر الدولار مقابل الليرة السورية اليوم + محوّل فوري | ريت باث",
        title_en="USD to SYP Live Rate Today & Instant Converter | RatePath",
        h1_ar="سعر الدولار مقابل الليرة السورية اليوم (USD/SYP)",
        h1_en="USD to SYP Exchange Rate Today",
        subtitle_ar="تحديث مباشر لسعر صرف الدولار مقابل الليرة السورية مع محوّل فوري وتنبيهات تيليجرام مجانية.",
        subtitle_en="Real-time US Dollar to Syrian Pound rate with instant calculator and free Telegram breakout alerts.",
        desc_ar="تابع سعر الدولار مقابل الليرة السورية اليوم لحظة بلحظة مع محوّل عملات فوري وتنبيهات سعر مجانية عبر تيليجرام على ريت باث.",
        desc_en="Live USD to SYP exchange rate today with instant currency converter and free Telegram price alerts on RatePath.",
        keywords="سعر الدولار مقابل الليرة السورية اليوم, الدولار في سوريا, محول العملات, USD to SYP, Syrian Pound rate today",
        faqs=(
            (
                "كم سعر الدولار مقابل الليرة السورية اليوم؟",
                "يعرض ريت باث السعر المباشر للدولار الأمريكي مقابل الليرة السورية (USD/SYP) بتحديث تلقائي على مدار الساعة عبر بطاقة السعر والمحوّل الفوري أعلاه.",
                "What is the USD to SYP exchange rate today?",
                "RatePath displays the live US Dollar to Syrian Pound (USD/SYP) rate updated continuously in the live rate card and instant converter above.",
            ),
            (
                "كيف أفعل تنبيه سعر الدولار مقابل الليرة السورية على تيليجرام؟",
                "اضغط على زر تفعيل التنبيه المجاني لفتح @RatePathTerminalBot، اختر الليرة السورية SYP وحدد السعر المستهدف ليصلك إشعار لحظي عند اختراقه.",
                "How can I get free Telegram alerts for USD/SYP?",
                "Tap the Free Telegram Alert button to open @RatePathTerminalBot, add SYP to your free watchlist, and set your exact target price.",
            ),
            (
                "هل يمكن تحويل أي مبلغ من الدولار إلى الليرة السورية والعكس؟",
                "نعم، المحوّل الفوري في الصفحة يدعم التحويل بالاتجاهين (USD ↔ SYP) بدقة حسابية فورية بدون إعلانات مزعجة.",
                "Can I convert any amount between USD and SYP?",
                "Yes, the built-in instant converter supports bidirectional USD ↔ SYP conversions with quick preset amounts and zero pop-ups.",
            ),
        ),
    ),
    SeoPageSpec(
        slug="usd-to-try",
        base="USD",
        quote="TRY",
        mode="pair",
        title_ar="سعر الدولار مقابل الليرة التركية اليوم + محوّل فوري | ريت باث",
        title_en="USD to TRY Live Rate Today & Instant Converter | RatePath",
        h1_ar="سعر الدولار مقابل الليرة التركية اليوم (USD/TRY)",
        h1_en="USD to TRY Exchange Rate Today",
        subtitle_ar="سعر صرف الدولار مقابل الليرة التركية لحظياً مع حاسبة تحويل فورية وتنبيهات اختراق السعر.",
        subtitle_en="Live US Dollar to Turkish Lira exchange rate with instant calculator and free Telegram alerts.",
        desc_ar="اعرف سعر الدولار مقابل الليرة التركية اليوم USD/TRY مباشرة مع محوّل فوري وجدول تحويل سريع وتنبيهات تيليجرام مجانية.",
        desc_en="Check live USD to TRY exchange rate today with instant Turkish Lira converter and free Telegram price alerts.",
        keywords="سعر الدولار مقابل الليرة التركية اليوم, الدولار في تركيا, USD to TRY, Turkish Lira converter",
        faqs=(
            (
                "كم سعر صرف الدولار مقابل الليرة التركية الآن؟",
                "يتم سحب سعر صرف الدولار مقابل الليرة التركية (USD/TRY) تلقائياً وتحديثه كل دقيقة في البطاقة الرئيسية والمحوّل أعلاه.",
                "What is the current USD to TRY rate?",
                "USD/TRY is fetched from live FX feeds and updated automatically every minute in the rate card and calculator above.",
            ),
            (
                "كيف أحصل على تنبيه عند صعود أو هبوط الليرة التركية؟",
                "عبر بوت @RatePathTerminalBot المجاني يمكنك إضافة TRY وتحديد سعر مستهدف ليصلك إشعار فوري لحظة وصول السعر.",
                "How do I set a USD/TRY breakout alert?",
                "Open @RatePathTerminalBot, select Turkish Lira (TRY) on your free watchlist, and set a custom target price or +/-2% alert.",
            ),
            (
                "هل يظهر جدول تحويل لمبالغ 10 و100 و1000 دولار بالليرة التركية؟",
                "نعم، الجدول السريع أسفل المحوّل يحسب تلقائياً قيمة 1 و10 و100 و1,000 دولار بالليرة التركية حسب آخر سعر مباشر.",
                "Does this page show quick USD to TRY conversion amounts?",
                "Yes, the live conversion table below calculates 1, 10, 100, and 1,000 USD in Turkish Lira automatically.",
            ),
        ),
    ),
    SeoPageSpec(
        slug="usd-to-egp",
        base="USD",
        quote="EGP",
        mode="pair",
        title_ar="سعر الدولار مقابل الجنيه المصري اليوم + محوّل فوري | ريت باث",
        title_en="USD to EGP Live Rate Today & Instant Converter | RatePath",
        h1_ar="سعر الدولار مقابل الجنيه المصري اليوم (USD/EGP)",
        h1_en="USD to EGP Exchange Rate Today",
        subtitle_ar="تابع سعر الدولار في مصر اليوم مقابل الجنيه المصري مع حاسبة تحويل فورية وتنبيهات تيليجرام.",
        subtitle_en="Live US Dollar to Egyptian Pound rate today with instant converter and free Telegram price alerts.",
        desc_ar="سعر الدولار مقابل الجنيه المصري اليوم USD/EGP بتحديث مباشر، حاسبة تحويل فورية بالاتجاهين وتنبيهات أسعار مجانية على تيليجرام.",
        desc_en="Live USD to EGP exchange rate today with instant Egyptian Pound calculator and free Telegram alerts on RatePath.",
        keywords="سعر الدولار مقابل الجنيه المصري اليوم, سعر الدولار في مصر, USD to EGP, Egyptian Pound rate",
        faqs=(
            (
                "ما هو سعر الدولار مقابل الجنيه المصري اليوم؟",
                "يعرض ريت باث سعر الدولار مقابل الجنيه المصري (USD/EGP) بتحديث فوري مستمر مع إمكانية التحويل المباشر لأي مبلغ.",
                "What is today's USD to EGP exchange rate?",
                "RatePath tracks USD/EGP in real time with automatic updates and an instant two-way converter.",
            ),
            (
                "هل تنبيهات سعر الدولار مقابل الجنيه المصري مجانية؟",
                "نعم، يمكنك متابعة حتى 3 أصول مجاناً وتفعيل تنبيهات فورية عبر @RatePathTerminalBot بدون أي رسوم.",
                "Are USD/EGP Telegram alerts free?",
                "Yes, the Free plan on @RatePathTerminalBot lets you track 3 assets with instant breakout alerts at zero cost.",
            ),
            (
                "هل أسعار ريت باث تُعد نصيحة استثمارية؟",
                "لا، ريت باث منصة معلوماتية لمتابعة البيانات وأسعار الصرف اللحظية فقط ولا تقدم نصائح استثمارية.",
                "Is RatePath financial advice?",
                "No. RatePath provides informational market data and price alerts only — never investment advice.",
            ),
        ),
    ),
    SeoPageSpec(
        slug="gold-price-today",
        base="XAU_OZ",
        quote="USD",
        mode="gold",
        title_ar="سعر الذهب اليوم عيار 24 و21 والأونصة بالدولار والعملات العربية | ريت باث",
        title_en="Gold Price Today (24K, 21K, 18K & XAU/OZ Live) | RatePath",
        h1_ar="سعر الذهب اليوم — عيار 24 و21 و18 والأونصة (XAU)",
        h1_en="Gold Price Today — 24K, 21K, 18K & Spot Ounce (XAU)",
        subtitle_ar="أسعار جرام الذهب الفوري عيار 24 و21 و18 والأونصة العالمية بالدولار والليرة السورية والتركية والجنيه المصري والريال والدرهم.",
        subtitle_en="Live spot Gold ounce (XAU) plus 24K, 21K, and 18K gram prices converted live into USD, SYP, TRY, EGP, SAR, AED & EUR.",
        desc_ar="تابع سعر الذهب اليوم عالمياً ومحلياً: أونصة الذهب XAU وجرام عيار 24 وعيار 21 وعيار 18 بالدولار والليرة السورية والتركية والمصري مع تنبيهات تيليجرام.",
        desc_en="Check live Gold price today per ounce (XAU) and per gram (24K, 21K, 18K) in USD, SYP, TRY, EGP, SAR & AED with free Telegram alerts.",
        keywords="سعر الذهب اليوم, سعر جرام الذهب عيار 21, سعر الذهب عيار 24, سعر اونصة الذهب, gold price today, XAU USD",
        faqs=(
            (
                "كيف يُحسب سعر جرام الذهب عيار 24 وعيار 21 من سعر الأونصة؟",
                "تساوي الأونصة التروي 31.1034768 جرام ذهب خالص (عيار 24)، بينما يُحسب عيار 21 بضرب سعر عيار 24 في 21/24 (87.5%).",
                "How are 24K and 21K gold gram prices calculated from XAU/OZ?",
                "One troy ounce equals 31.1034768 grams of pure 24K gold. 21K gold is 21/24 (87.5%) of the 24K gram price.",
            ),
            (
                "هل يعرض ريت باث سعر الذهب بالليرة السورية والتركية والجنيه المصري؟",
                "نعم، جدول المعادن المباشر في هذه الصفحة يحوّل الأونصة وعيار 24 وعيار 21 وعيار 18 لحظياً إلى USD وSYP وTRY وEGP وSAR وAED.",
                "Does RatePath convert gold into Arabic currencies automatically?",
                "Yes, the live metals matrix below converts XAU Ounce, 24K, 21K, and 18K grams live into USD, SYP, TRY, EGP, SAR, and AED.",
            ),
            (
                "كيف أضبط تنبيه فوري عند وصول الذهب لسعر محدد؟",
                "اضغط على زر التنبيه المجاني لفتح @RatePathTerminalBot، اختر الذهب XAU واكتب السعر المستهدف لتلقي تنبيه فوري عند الاختراق.",
                "How do I set a live Gold (XAU) price alert?",
                "Tap the Telegram Alert button to open @RatePathTerminalBot, add Gold (XAU), and enter your target price level.",
            ),
        ),
    ),
    SeoPageSpec(
        slug="btc-to-usdt",
        base="BTC",
        quote="USDT",
        mode="pair",
        title_ar="سعر البيتكوين اليوم BTC/USDT + حاسبة الكريبتو | ريت باث",
        title_en="BTC to USDT Live Price Today & Crypto Calculator | RatePath",
        h1_ar="سعر البيتكوين اليوم مقابل التيثر (BTC/USDT)",
        h1_en="Bitcoin Price Today (BTC to USDT)",
        subtitle_ar="سعر البيتكوين المباشر مقابل USDT مع حاسبة كريبتو فورية وتنبيهات اختراق على تيليجرام.",
        subtitle_en="Live Bitcoin (BTC) to Tether (USDT) spot price with instant calculator and Telegram breakout alerts.",
        desc_ar="اعرف سعر البيتكوين اليوم BTC/USDT مباشرة مع حاسبة تحويل فورية وتنبيهات كسر الأسعار المجانية عبر @RatePathTerminalBot.",
        desc_en="Real-time BTC to USDT price today with instant crypto converter and free Telegram breakout alerts on RatePath.",
        keywords="سعر البيتكوين اليوم, BTC to USDT, Bitcoin price today, حاسبة البيتكوين, crypto converter",
        faqs=(
            (
                "كم سعر البيتكوين اليوم مقابل USDT؟",
                "يعرض ريت باث السعر الفوري للبيتكوين (BTC/USDT) المحدّث تلقائياً من أسواق السيولة العالمية مع نسبة التغير خلال 24 ساعة.",
                "What is the live BTC to USDT price right now?",
                "RatePath streams live BTC/USDT spot pricing with 24-hour change indicators and an instant converter above.",
            ),
            (
                "هل يمكن تفعيل تنبيه فوري عند اختراق البيتكوين لمستوى معين؟",
                "نعم، افتح @RatePathTerminalBot وأضف BTC وحدد مستوى الصعود أو الهبوط ليصلك تنبيه لحظي بمجرد وصول السعر.",
                "Can I set a Bitcoin breakout alert on Telegram?",
                "Yes — open @RatePathTerminalBot, select Bitcoin (BTC), and arm a one-tap +/-2% or custom target price alert.",
            ),
            (
                "هل تدعم حاسبة BTC/USDT التحويل العكسي من USDT إلى BTC؟",
                "نعم، اضغط زر التبديل (⇄) لمعرفة كم بيتكوين تحصل عليه مقابل أي مبلغ بالـ USDT فوراً.",
                "Can I convert USDT back to BTC?",
                "Yes, tap the swap button (⇄) inside the calculator to convert any USDT amount into BTC instantaneously.",
            ),
        ),
    ),
    SeoPageSpec(
        slug="usdt-to-try",
        base="USDT",
        quote="TRY",
        mode="pair",
        title_ar="سعر التيثر USDT مقابل الليرة التركية اليوم + محوّل فوري | ريت باث",
        title_en="USDT to TRY Live Rate Today & Converter | RatePath",
        h1_ar="سعر التيثر USDT مقابل الليرة التركية اليوم (USDT/TRY)",
        h1_en="USDT to TRY Live Rate Today",
        subtitle_ar="احسب قيمة USDT بالليرة التركية لحظياً مع جدول تحويل سريع وتنبيهات تيليجرام مجانية.",
        subtitle_en="Real-time Tether (USDT) to Turkish Lira (TRY) live converter and free Telegram price alerts.",
        desc_ar="سعر USDT مقابل الليرة التركية اليوم بتحديث مباشر، محوّل فوري لـ 10 و100 و1000 USDT بالليرة التركية وتنبيهات أسعار مجانية.",
        desc_en="Live USDT to TRY exchange rate today with instant Tether to Turkish Lira calculator and free Telegram alerts.",
        keywords="سعر USDT مقابل الليرة التركية, USDT to TRY, Tether Turkish Lira, تحويل usdt الى ليرة تركية",
        faqs=(
            (
                "كم يساوي 1 USDT بالليرة التركية اليوم؟",
                "يعرض المحوّل أعلاه سعر 1 USDT بالليرة التركية (TRY) بتحديث تلقائي لحظي بالاعتماد على سعر التيثر وسعر الليرة التركية.",
                "How much is 1 USDT in Turkish Lira today?",
                "The live rate card above calculates 1 USDT in TRY continuously using real-time Tether and FX feeds.",
            ),
            (
                "هل يمكن ضبط تنبيه لزوج USDT/TRY على تيليجرام؟",
                "نعم، اضغط زر التنبيه المجاني في الصفحة لتفعيل تنبيهات فورية على @RatePathTerminalBot.",
                "Can I get free Telegram alerts for USDT/TRY?",
                "Yes, tap the Telegram Alert button to track TRY and USDT for free inside @RatePathTerminalBot.",
            ),
        ),
    ),
    SeoPageSpec(
        slug="usdt-to-syp",
        base="USDT",
        quote="SYP",
        mode="pair",
        title_ar="سعر التيثر USDT مقابل الليرة السورية اليوم + محوّل فوري | ريت باث",
        title_en="USDT to SYP Live Rate Today & Converter | RatePath",
        h1_ar="سعر التيثر USDT مقابل الليرة السورية اليوم (USDT/SYP)",
        h1_en="USDT to SYP Live Rate Today",
        subtitle_ar="حوّل USDT إلى الليرة السورية والعكس لحظياً مع تنبيهات فورية عبر تيليجرام.",
        subtitle_en="Convert Tether (USDT) to Syrian Pound (SYP) live with instant calculator and free Telegram alerts.",
        desc_ar="تابع سعر USDT مقابل الليرة السورية اليوم بتحديث مستمر مع محوّل فوري وتنبيهات تيليجرام مجانية عبر ريت باث.",
        desc_en="Live USDT to SYP rate today with instant bidirectional converter and free Telegram price alerts on RatePath.",
        keywords="سعر USDT مقابل الليرة السورية, USDT to SYP, تحويل usdt الى ليرة سورية, Tether Syrian Pound",
        faqs=(
            (
                "كم يساوي 1 USDT بالليرة السورية اليوم؟",
                "تظهر القيمة اللحظية لـ 1 USDT مقابل الليرة السورية (SYP) في البطاقة الرئيسية مع حاسبة تحويل فورية لأي مبلغ.",
                "What is 1 USDT in Syrian Pounds today?",
                "The live card above shows the real-time USDT to SYP rate with instant conversion for any amount.",
            ),
            (
                "كيف أتابع تغيرات سعر USDT/SYP تلقائياً؟",
                "فعّل التنبيه المجاني عبر @RatePathTerminalBot لتصلك إشعارات لحظية عند تجاوز السعر المستهدف.",
                "How can I track USDT/SYP changes automatically?",
                "Start @RatePathTerminalBot and add SYP to your free watchlist to get instant breakout notifications.",
            ),
        ),
    ),
    SeoPageSpec(
        slug="eth-to-usdt",
        base="ETH",
        quote="USDT",
        mode="pair",
        title_ar="سعر الإيثيريوم ETH مقابل USDT اليوم + حاسبة فورية | ريت باث",
        title_en="ETH to USDT Live Price Today & Calculator | RatePath",
        h1_ar="سعر الإيثيريوم اليوم مقابل التيثر (ETH/USDT)",
        h1_en="Ethereum Price Today (ETH to USDT)",
        subtitle_ar="سعر الإيثيريوم المباشر مقابل USDT مع حاسبة كريبتو دقيقة وتنبيهات كسر الأسعار.",
        subtitle_en="Live Ethereum (ETH) to USDT spot price with instant converter and free Telegram alerts.",
        desc_ar="اعرف سعر الإيثيريوم اليوم ETH/USDT لحظة بلحظة مع حاسبة تحويل فورية وتنبيهات مجانية على تيليجرام عبر ريت باث.",
        desc_en="Check live ETH to USDT price today with instant Ethereum calculator and free Telegram breakout alerts.",
        keywords="سعر الايثيريوم اليوم, ETH to USDT, Ethereum price today, حاسبة الايثيريوم",
        faqs=(
            (
                "ما هو سعر الإيثيريوم مقابل USDT الآن؟",
                "يعرض ريت باث السعر الفوري المباشر لعملة الإيثيريوم (ETH/USDT) مع تحديث تلقائي وحاسبة بالاتجاهين.",
                "What is the live ETH to USDT price?",
                "RatePath updates the ETH/USDT spot rate automatically with 24h performance and an instant calculator.",
            ),
            (
                "كيف أستقبل تنبيهات اختراق سعر الإيثيريوم مجاناً؟",
                "افتح @RatePathTerminalBot وأضف ETH إلى قائمتك المجانية وحدد السعر المستهدف.",
                "How do I set a free Ethereum breakout alert?",
                "Open @RatePathTerminalBot, add ETH to your watchlist, and enter your target price level.",
            ),
        ),
    ),
    SeoPageSpec(
        slug="eur-to-usd",
        base="EUR",
        quote="USD",
        mode="pair",
        title_ar="سعر اليورو مقابل الدولار اليوم EUR/USD + محوّل فوري | ريت باث",
        title_en="EUR to USD Live Exchange Rate Today & Converter | RatePath",
        h1_ar="سعر اليورو مقابل الدولار اليوم (EUR/USD)",
        h1_en="EUR to USD Exchange Rate Today",
        subtitle_ar="سعر صرف اليورو مقابل الدولار الأمريكي لحظياً مع محوّل فوري وتنبيهات تيليجرام مجانية.",
        subtitle_en="Real-time Euro to US Dollar (EUR/USD) exchange rate with instant converter and free Telegram alerts.",
        desc_ar="تابع سعر اليورو مقابل الدولار اليوم EUR/USD مباشرة مع محوّل عملات فوري وتنبيهات سعر مجانية على تيليجرام عبر ريت باث.",
        desc_en="Live EUR to USD exchange rate today with instant Euro to Dollar converter and free Telegram price alerts.",
        keywords="سعر اليورو مقابل الدولار اليوم, EUR to USD, Euro Dollar converter, محول اليورو للدولار",
        faqs=(
            (
                "كم سعر صرف اليورو مقابل الدولار اليوم؟",
                "يعرض ريت باث سعر صرف زوج اليورو/دولار (EUR/USD) بتحديث مستمر مع محوّل فوري يحسب أي مبلغ بالاتجاهين.",
                "What is the EUR to USD exchange rate today?",
                "RatePath displays the live EUR/USD rate refreshed continuously alongside a bidirectional converter.",
            ),
            (
                "هل يمكن الحصول على تنبيهات فورية لزوج EUR/USD على تيليجرام؟",
                "نعم، اضغط على زر التنبيه المجاني لفتح @RatePathTerminalBot وإضافة اليورو EUR لقائمة متابعتك.",
                "Can I receive instant EUR/USD Telegram alerts?",
                "Yes — open @RatePathTerminalBot, select Euro (EUR), and arm an instant breakout or drop alert.",
            ),
        ),
    ),
    SeoPageSpec(
        slug="silver-price-today",
        base="XAG_OZ",
        quote="USD",
        mode="silver",
        title_ar="سعر الفضة اليوم عيار 999 والأونصة بالدولار والعملات العربية | ريت باث",
        title_en="Silver Price Today (XAG/OZ & Gram 999 Live) | RatePath",
        h1_ar="سعر الفضة اليوم — الأونصة (XAG) وجرام عيار 999",
        h1_en="Silver Price Today — Spot Ounce (XAG) & 999 Gram",
        subtitle_ar="تابع سعر أونصة الفضة الفورية وجرام الفضة النقي عيار 999 بالدولار والعملات العربية مع محوّل مباشر.",
        subtitle_en="Live spot Silver ounce (XAG) and 999 pure gram prices converted live into USD, SYP, TRY, EGP, SAR & AED.",
        desc_ar="سعر الفضة اليوم عالمياً: سعر أونصة الفضة XAG وجرام الفضة عيار 999 بالدولار والليرة السورية والتركية والجنيه المصري مع تنبيهات تيليجرام مجانية.",
        desc_en="Check live Silver price today per ounce (XAG) and per 999 gram in USD, SYP, TRY, EGP, SAR & AED with free Telegram alerts.",
        keywords="سعر الفضة اليوم, سعر جرام الفضة 999, سعر اونصة الفضة, silver price today, XAG USD",
        faqs=(
            (
                "كم سعر جرام الفضة عيار 999 اليوم؟",
                "يُحسب جرام الفضة النقي عيار 999 بقسمة سعر أونصة الفضة الفورية (XAG) على 31.1034768، ويظهر محدثاً تلقائياً بالجدول أدناه.",
                "How is the 999 silver gram price calculated?",
                "Pure 999 silver per gram equals the spot XAG troy ounce price divided by 31.1034768, updated automatically below.",
            ),
            (
                "هل يمكن متابعة سعر الفضة عبر تنبيهات تيليجرام؟",
                "نعم، أضف الفضة (XAG) مجاناً عبر @RatePathTerminalBot لتلقي إشعار فوري عند اختراق السعر الذي تحدده.",
                "Can I track Silver (XAG) alerts on Telegram?",
                "Yes, add Silver (XAG) on @RatePathTerminalBot to receive instant breakout or drop notifications.",
            ),
        ),
    ),
)


def _dec(val: Any) -> Decimal | None:
    try:
        d = Decimal(str(val).strip())
        return d if d > 0 else None
    except (InvalidOperation, ValueError, TypeError):
        return None


def _fmt_dec(d: Decimal | None) -> str:
    if d is None:
        return "—"
    if d < Decimal("0.01"):
        q = d.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)
        return f"{q:,.6f}"
    if d < Decimal("1"):
        q = d.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
        return f"{q:,.4f}"
    q = d.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return f"{q:,.2f}"


def _usd_per_unit(code: str, feed: dict[str, Any], catalog: dict[str, Any]) -> Decimal | None:
    sym = code.upper()
    if sym in ("USD", "USDT"):
        return Decimal("1")
    gold_prices = (feed.get("gold") or {}).get("prices") or {}
    cat_metals = catalog.get("metals") or {}
    if sym in ("XAU", "XAU_OZ"):
        return _dec(gold_prices.get("XAU_OZ")) or _dec((cat_metals.get("XAU_OZ") or {}).get("price_usd")) or Decimal("3653.50")
    if sym == "GRAM_24K":
        return _dec(gold_prices.get("GRAM_24K")) or _dec((cat_metals.get("GRAM_24K") or {}).get("price_usd")) or Decimal("117.46")
    if sym == "GRAM_21K":
        return _dec(gold_prices.get("GRAM_21K")) or _dec((cat_metals.get("GRAM_21K") or {}).get("price_usd")) or Decimal("102.78")
    if sym == "GRAM_18K":
        return _dec(gold_prices.get("GRAM_18K")) or _dec((cat_metals.get("GRAM_18K") or {}).get("price_usd")) or Decimal("88.10")
    if sym in ("XAG", "XAG_OZ"):
        return _dec(gold_prices.get("XAG_OZ")) or _dec((cat_metals.get("XAG_OZ") or {}).get("price_usd")) or Decimal("43.75")
    if sym == "XAG_GRAM":
        return _dec(gold_prices.get("XAG_GRAM")) or _dec((cat_metals.get("XAG_GRAM") or {}).get("price_usd")) or Decimal("1.41")

    crypto_live = ((feed.get("crypto") or {}).get("assets") or {}).get(sym) or {}
    if _dec(crypto_live.get("spot_price")):
        return _dec(crypto_live.get("spot_price"))
    for item in catalog.get("crypto") or []:
        if str(item.get("symbol") or "").upper() == sym:
            p = _dec(item.get("price_usd"))
            if p:
                return p

    fiat_live = ((feed.get("fiat") or {}).get("rates") or {}).get(sym) or {}
    u_live = _dec(fiat_live.get("units_per_usd"))
    if u_live:
        return Decimal("1") / u_live
    fiat_cat = (catalog.get("fiat") or {}).get(sym) or {}
    u_cat = _dec(fiat_cat.get("units_per_usd"))
    if u_cat:
        return Decimal("1") / u_cat
    return None


def _pair_rate(base: str, quote: str, feed: dict[str, Any], catalog: dict[str, Any]) -> Decimal | None:
    u_from = _usd_per_unit(base, feed, catalog)
    u_to = _usd_per_unit(quote, feed, catalog)
    if not u_from or not u_to:
        return None
    return u_from / u_to


def _display_code(code: str) -> str:
    sym = code.upper()
    if sym == "XAU_OZ":
        return "XAU (oz)"
    if sym == "XAG_OZ":
        return "XAG (oz)"
    return sym


def _build_metals_table_html(spec: SeoPageSpec, feed: dict[str, Any], catalog: dict[str, Any]) -> str:
    if spec.mode not in ("gold", "silver"):
        return ""
    units = (
        [
            ("XAU_OZ", "أونصة الذهب (XAU)", "Gold Ounce (XAU)"),
            ("GRAM_24K", "جرام ذهب عيار 24", "Gold 24K Gram"),
            ("GRAM_21K", "جرام ذهب عيار 21", "Gold 21K Gram"),
            ("GRAM_18K", "جرام ذهب عيار 18", "Gold 18K Gram"),
        ]
        if spec.mode == "gold"
        else [
            ("XAG_OZ", "أونصة الفضة (XAG)", "Silver Ounce (XAG)"),
            ("XAG_GRAM", "جرام الفضة عيار 999", "Silver 999 Gram"),
        ]
    )
    ccys = ("USD", "SYP", "TRY", "EGP", "SAR", "AED")
    rows_html = []
    for unit_code, lbl_ar, lbl_en in units:
        cells = []
        for ccy in ccys:
            val = _pair_rate(unit_code, ccy, feed, catalog)
            cells.append(
                f'<td class="px-2 py-2.5 font-mono text-xs text-slate-100 whitespace-nowrap" dir="ltr" '
                f'data-metal-unit="{unit_code}" data-metal-ccy="{ccy}">{_fmt_dec(val)} {ccy}</td>'
            )
        rows_html.append(
            f'<tr class="border-t border-slate-800/80 hover:bg-slate-800/40">'
            f'<td class="px-2.5 py-2.5 font-semibold text-xs sm:text-sm text-amber-300 whitespace-nowrap" data-ar="{html.escape(lbl_ar)}" data-en="{html.escape(lbl_en)}">{html.escape(lbl_ar)}</td>'
            f'{"".join(cells)}'
            f"</tr>"
        )

    heading_ar = "جدول أسعار الذهب المباشرة بالعملات العربية والعالمية" if spec.mode == "gold" else "جدول أسعار الفضة المباشرة بالعملات العربية والعالمية"
    heading_en = "Live Gold Prices in USD & Regional Currencies" if spec.mode == "gold" else "Live Silver Prices in USD & Regional Currencies"

    return f"""
    <section class="mt-6 rounded-2xl bg-slate-900/85 border border-slate-800 p-4 sm:p-6">
      <h2 class="text-base sm:text-lg font-bold text-white mb-4" data-ar="{html.escape(heading_ar)}" data-en="{html.escape(heading_en)}">{html.escape(heading_ar)}</h2>
      <div class="overflow-x-auto">
        <table class="w-full text-start border-collapse">
          <thead>
            <tr class="text-[11px] text-slate-400 uppercase border-b border-slate-800">
              <th class="px-2.5 py-2 text-start" data-ar="العيار / الوحدة" data-en="Unit / Karat">العيار / الوحدة</th>
              <th class="px-2 py-2 text-start" dir="ltr">USD</th>
              <th class="px-2 py-2 text-start" dir="ltr">🟢 SYP</th>
              <th class="px-2 py-2 text-start" dir="ltr">TRY</th>
              <th class="px-2 py-2 text-start" dir="ltr">EGP</th>
              <th class="px-2 py-2 text-start" dir="ltr">SAR</th>
              <th class="px-2 py-2 text-start" dir="ltr">AED</th>
            </tr>
          </thead>
          <tbody>
            {"".join(rows_html)}
          </tbody>
        </table>
      </div>
    </section>
"""


def _render_seo_page_html(
    spec: SeoPageSpec,
    feed: dict[str, Any],
    catalog: dict[str, Any],
    generated_date: str,
) -> str:
    canonical_url = f"{SITE_ORIGIN}/{spec.slug}"
    rate = _pair_rate(spec.base, spec.quote, feed, catalog)
    inv_rate = (Decimal("1") / rate) if rate else None
    rate_str = _fmt_dec(rate)
    inv_str = _fmt_dec(inv_rate)
    base_label = _display_code(spec.base)
    quote_label = _display_code(spec.quote)
    bot_cta_url = f"https://t.me/{BOT_USERNAME}?start=seo_{spec.slug.replace('-', '_')}"
    terminal_asset = "XAU" if spec.base == "XAU_OZ" else ("XAG" if spec.base == "XAG_OZ" else spec.base)

    # Structured JSON-LD: FinancialProduct + FAQPage
    faq_entities = []
    for q_ar, a_ar, q_en, a_en in spec.faqs:
        faq_entities.append(
            {
                "@type": "Question",
                "name": q_ar,
                "acceptedAnswer": {"@type": "Answer", "text": a_ar},
            }
        )
        faq_entities.append(
            {
                "@type": "Question",
                "name": q_en,
                "acceptedAnswer": {"@type": "Answer", "text": a_en},
            }
        )

    json_ld_obj = {
        "@context": "https://schema.org",
        "@graph": [
            {
                "@type": "FinancialProduct",
                "name": f"{spec.title_ar} | {spec.title_en}",
                "description": f"{spec.desc_ar} {spec.desc_en}",
                "url": canonical_url,
                "provider": {
                    "@type": "Organization",
                    "name": "RatePath | ريت باث",
                    "url": f"{SITE_ORIGIN}/",
                },
                "feesAndCommissionsSpecification": "Free live market data and instant converter; optional Telegram Pro alert plans.",
                "offers": {
                    "@type": "Offer",
                    "price": str(rate.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)) if rate else "0",
                    "priceCurrency": spec.quote if len(spec.quote) == 3 else "USD",
                    "availability": "https://schema.org/InStock",
                },
            },
            {
                "@type": "FAQPage",
                "mainEntity": faq_entities,
            },
        ],
    }
    json_ld_str = json.dumps(json_ld_obj, ensure_ascii=False, indent=2)

    quick_rows = []
    for qty in (1, 10, 50, 100, 500, 1000):
        q_val = (Decimal(qty) * rate) if rate else None
        quick_rows.append(
            f'<tr class="border-t border-slate-800/70" data-quick-qty="{qty}">'
            f'<td class="px-3 py-2 font-mono text-sm text-slate-300" dir="ltr" data-quick-in>{qty:,} {base_label}</td>'
            f'<td class="px-3 py-2 font-mono text-sm font-bold text-amber-300" dir="ltr" data-quick-out>{_fmt_dec(q_val)} {quote_label}</td>'
            f"</tr>"
        )

    faqs_html = []
    for faq_idx, (q_ar, a_ar, q_en, a_en) in enumerate(spec.faqs):
        open_attr = " open" if faq_idx == 0 else ""
        faqs_html.append(
            f"""
        <details class="group rounded-xl bg-slate-900/70 border border-slate-800 p-4"{open_attr}>
          <summary class="cursor-pointer font-bold text-slate-100 text-sm sm:text-base list-none flex items-center justify-between gap-2">
            <span data-ar="{html.escape(q_ar)}" data-en="{html.escape(q_en)}">{html.escape(q_ar)}</span>
            <span class="text-amber-400 text-xs">▾</span>
          </summary>
          <p class="mt-2 text-xs sm:text-sm text-slate-300 leading-relaxed" data-ar="{html.escape(a_ar)}" data-en="{html.escape(a_en)}">{html.escape(a_ar)}</p>
        </details>"""
        )

    related_links = []
    for other in SEO_PAGES:
        active_cls = (
            "border-amber-400/70 bg-amber-400/10 text-amber-200"
            if other.slug == spec.slug
            else "border-slate-800 bg-slate-900/70 text-slate-200 hover:border-amber-400/40"
        )
        related_links.append(
            f'<a href="/{other.slug}" class="px-3 py-2 rounded-xl border text-xs sm:text-sm font-semibold transition {active_cls}" '
            f'data-ar="{html.escape(other.h1_ar)}" data-en="{html.escape(other.h1_en)}">{html.escape(other.h1_ar)}</a>'
        )

    syp_flag_slot = (
        '<span data-syria-flag-slot class="inline-flex items-center"></span>'
        if "SYP" in (spec.base, spec.quote)
        else ""
    )

    page_html = f"""<!DOCTYPE html>
<html lang="ar" dir="rtl" class="dark">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover" />
  <title>{html.escape(spec.title_ar)}</title>
  <meta name="description" content="{html.escape(spec.desc_ar)}" />
  <meta name="keywords" content="{html.escape(spec.keywords)}" />
  <meta name="theme-color" content="#070B14" />

  <link rel="canonical" href="{canonical_url}" />
  <link rel="alternate" hreflang="ar" href="{canonical_url}?lang=ar" />
  <link rel="alternate" hreflang="en" href="{canonical_url}?lang=en" />
  <link rel="alternate" hreflang="x-default" href="{canonical_url}" />

  <meta property="og:type" content="website" />
  <meta property="og:url" content="{canonical_url}" />
  <meta property="og:site_name" content="RatePath | ريت باث" />
  <meta property="og:title" content="{html.escape(spec.title_ar)}" />
  <meta property="og:description" content="{html.escape(spec.desc_ar)}" />
  <meta property="og:image" content="{SITE_ORIGIN}/icons/logo-full.jpg" />
  <meta name="twitter:card" content="summary_large_image" />
  <meta name="twitter:title" content="{html.escape(spec.title_en)}" />
  <meta name="twitter:description" content="{html.escape(spec.desc_en)}" />
  <meta name="twitter:image" content="{SITE_ORIGIN}/icons/logo-full.jpg" />

  <link rel="icon" href="/favicon.ico" sizes="any" />
  <link rel="icon" type="image/png" sizes="32x32" href="/icons/favicon-32x32.png" />
  <link rel="apple-touch-icon" sizes="180x180" href="/icons/apple-touch-icon.png" />

  <link rel="preconnect" href="https://fonts.googleapis.com" />
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
  <link id="rp-google-fonts" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans+Arabic:wght@400;500;600;700&amp;family=Inter:wght@400;500;600;700;800&amp;family=JetBrains+Mono:wght@500;600;700;800&amp;display=swap" rel="stylesheet" media="print" />
  <noscript><link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans+Arabic:wght@400;500;600;700&amp;family=Inter:wght@400;500;600;700;800&amp;family=JetBrains+Mono:wght@500;600;700;800&amp;display=swap" rel="stylesheet" /></noscript>

  <link rel="stylesheet" href="/styles/tailwind.min.css?v=23" />
  <link rel="stylesheet" href="/styles/terminal.css?v=23" />

  <script type="application/ld+json">
{json_ld_str}
  </script>
</head>
<body class="bg-[#070B14] text-slate-100 min-h-screen antialiased" data-seo-slug="{spec.slug}" data-base="{spec.base}" data-quote="{spec.quote}" data-mode="{spec.mode}" data-title-ar="{html.escape(spec.title_ar)}" data-title-en="{html.escape(spec.title_en)}">
  <header class="sticky top-0 z-40 bg-[#070B14]/95 backdrop-blur-md border-b border-slate-800/90">
    <div class="rp-seo-wrap px-4 h-16 flex items-center justify-between gap-2 sm:gap-3">
      <a href="/" class="flex items-center gap-2 sm:gap-2.5 min-w-0">
        <img src="/icons/logo-mark-80.webp" alt="RatePath | ريت باث" width="36" height="36" class="w-8 h-8 sm:w-9 sm:h-9 rounded-xl ring-1 ring-amber-400/45 object-cover shrink-0" />
        <div class="min-w-0">
          <div class="flex items-center gap-1.5 sm:gap-2">
            <span class="text-sm sm:text-base font-extrabold text-white whitespace-nowrap">RatePath</span>
            <span class="hidden sm:inline text-xs font-bold text-amber-400 whitespace-nowrap">• ريت باث</span>
          </div>
          <p class="hidden sm:block text-[11px] text-slate-400 truncate" data-ar="تيرمينال أسعار الصرف والذهب والكريبتو" data-en="Live FX, Gold &amp; Crypto Terminal">تيرمينال أسعار الصرف والذهب والكريبتو</p>
        </div>
      </a>
      <div class="flex items-center gap-2 shrink-0">
        <span id="seo-utc-clock" dir="ltr" class="inline-flex items-center font-mono text-[11px] sm:text-xs text-emerald-300 bg-slate-900 border border-slate-800 px-2.5 py-1 rounded-lg whitespace-nowrap">--:--:-- UTC</span>
        <button id="seo-lang-toggle" type="button" class="px-3 py-1.5 rounded-lg text-xs font-bold bg-slate-900 border border-slate-700 hover:border-amber-400/60 text-slate-100 whitespace-nowrap">🇬🇧 English</button>
      </div>
    </div>
  </header>

  <main class="rp-seo-wrap px-4 py-6 sm:py-8">
    <!-- Hero Live Rate Card -->
    <section class="rounded-2xl bg-gradient-to-br from-slate-900 via-[#0C1426] to-slate-950 border border-amber-400/35 p-5 sm:p-7 shadow-xl shadow-black/40">
      <div class="flex flex-wrap items-center justify-between gap-2 mb-3">
        <span class="inline-flex items-center gap-2 px-3 py-1 rounded-full text-xs font-semibold bg-emerald-500/15 text-emerald-300 border border-emerald-500/35">
          <span class="w-2 h-2 rounded-full bg-emerald-400 animate-pulse"></span>
          <span id="seo-updated-label" data-ar="تحديث مباشر الآن" data-en="Updated live just now">تحديث مباشر الآن</span>
        </span>
        <span id="seo-change-badge" dir="ltr" hidden></span>
      </div>

      <h1 class="text-xl sm:text-3xl font-extrabold text-white tracking-tight flex items-center gap-2.5 flex-wrap">
        {syp_flag_slot}
        <span data-ar="{html.escape(spec.h1_ar)}" data-en="{html.escape(spec.h1_en)}">{html.escape(spec.h1_ar)}</span>
      </h1>
      <p class="mt-2 text-sm text-slate-300" data-ar="{html.escape(spec.subtitle_ar)}" data-en="{html.escape(spec.subtitle_en)}">{html.escape(spec.subtitle_ar)}</p>

      <div class="mt-5 p-4 sm:p-5 rounded-xl bg-slate-950/75 border border-slate-800 flex flex-col sm:flex-row sm:items-end sm:justify-between gap-4">
        <div dir="ltr" class="text-start">
          <div class="text-xs uppercase tracking-wider text-slate-400 font-semibold">1 {base_label} =</div>
          <div id="seo-hero-rate" class="mt-1 text-3xl sm:text-5xl font-extrabold font-mono text-amber-300 tabular-nums">{rate_str} {quote_label}</div>
          <div id="seo-inverse-rate" class="mt-1 text-xs font-mono text-slate-400 tabular-nums">1 {quote_label} = {inv_str} {base_label}</div>
        </div>
        <div class="flex flex-col sm:items-end gap-2">
          <a href="{bot_cta_url}" target="_blank" rel="noopener noreferrer"
             class="inline-flex items-center justify-center gap-2 px-4 py-3 rounded-xl bg-amber-400 hover:bg-amber-300 text-slate-950 font-extrabold text-xs sm:text-sm shadow-lg shadow-amber-500/20 transition">
            <span data-ar="🔔 فعّل تنبيه السعر مجاناً على تيليجرام" data-en="🔔 Get Free Instant Telegram Alert">🔔 فعّل تنبيه السعر مجاناً على تيليجرام</span>
            <bdi dir="ltr" class="font-mono">(@RatePathTerminalBot)</bdi>
          </a>
          <a href="/?asset={terminal_asset}"
             class="inline-flex items-center justify-center gap-1.5 px-3.5 py-2 rounded-xl bg-slate-900 hover:bg-slate-800 border border-slate-700 text-slate-200 font-semibold text-xs transition"
             data-ar="🌐 فتح التيرمينال الكامل ومخططات الأداء"
             data-en="🌐 Open Full Terminal &amp; Multi-Asset Charts">
            🌐 فتح التيرمينال الكامل ومخططات الأداء
          </a>
        </div>
      </div>
    </section>

    <!-- Instant Pre-Selected Converter -->
    <section class="mt-6 rp-seo-grid gap-6">
      <div class="rp-seo-main rounded-2xl bg-slate-900/85 border border-slate-800 p-5">
        <div class="flex items-center justify-between gap-2 mb-4">
          <h2 class="text-base sm:text-lg font-bold text-white" data-ar="المحوّل الفوري ({base_label} ↔ {quote_label})" data-en="Instant Converter ({base_label} ↔ {quote_label})">المحوّل الفوري ({base_label} ↔ {quote_label})</h2>
          <button id="conv-swap" type="button" class="px-3 py-1.5 rounded-lg bg-slate-800 hover:bg-slate-700 border border-slate-700 text-xs font-bold text-amber-300">⇄ <span data-ar="عكس" data-en="Swap">عكس</span></button>
        </div>

        <label for="conv-amount" class="block text-xs text-slate-400 mb-1.5" data-ar="أدخل المبلغ للتحويل الفوري" data-en="Enter amount to convert instantly">أدخل المبلغ للتحويل الفوري</label>
        <div class="flex items-center gap-2" dir="ltr">
          <input id="conv-amount" type="number" inputmode="decimal" step="any" min="0" value="1"
                 class="w-full px-4 py-3 rounded-xl bg-slate-950 border border-slate-700 focus:border-amber-400 focus:outline-none font-mono text-lg font-bold text-white" />
          <span id="conv-from-code" class="px-3.5 py-3 rounded-xl bg-slate-800 border border-slate-700 font-mono text-sm font-extrabold text-amber-300 shrink-0">{base_label}</span>
        </div>

        <div class="mt-3 flex flex-wrap gap-2" dir="ltr">
          <button type="button" data-chip-amount="1" class="px-3 py-1 rounded-lg bg-slate-800 hover:bg-slate-700 text-xs font-mono text-slate-200">1</button>
          <button type="button" data-chip-amount="10" class="px-3 py-1 rounded-lg bg-slate-800 hover:bg-slate-700 text-xs font-mono text-slate-200">10</button>
          <button type="button" data-chip-amount="100" class="px-3 py-1 rounded-lg bg-slate-800 hover:bg-slate-700 text-xs font-mono text-slate-200">100</button>
          <button type="button" data-chip-amount="500" class="px-3 py-1 rounded-lg bg-slate-800 hover:bg-slate-700 text-xs font-mono text-slate-200">500</button>
          <button type="button" data-chip-amount="1000" class="px-3 py-1 rounded-lg bg-slate-800 hover:bg-slate-700 text-xs font-mono text-slate-200">1,000</button>
        </div>

        <div class="mt-4 p-4 rounded-xl bg-slate-950/90 border border-amber-400/30">
          <div class="flex items-center justify-between text-xs text-slate-400 mb-1">
            <span data-ar="النتيجة الفورية" data-en="Instant Result">النتيجة الفورية</span>
            <span id="conv-to-code" class="font-mono font-bold text-amber-300" dir="ltr">{quote_label}</span>
          </div>
          <div id="conv-output" dir="ltr" class="text-2xl sm:text-3xl font-extrabold font-mono text-emerald-300 tabular-nums">{rate_str} {quote_label}</div>
          <div id="conv-unit-rate" dir="ltr" class="mt-1 text-xs font-mono text-slate-400 tabular-nums">1 {base_label} = {rate_str} {quote_label}</div>
        </div>
      </div>

      <!-- Quick Reference Conversion Table -->
      <div class="rp-seo-side rounded-2xl bg-slate-900/85 border border-slate-800 p-5">
        <h2 class="text-base font-bold text-white mb-3" data-ar="جدول التحويل السريع" data-en="Quick Conversion Table">جدول التحويل السريع</h2>
        <table class="w-full text-start border-collapse">
          <thead>
            <tr class="text-xs text-slate-400 border-b border-slate-800">
              <th id="quick-th-from" class="px-3 py-2 text-start" dir="ltr">{base_label}</th>
              <th id="quick-th-to" class="px-3 py-2 text-start" dir="ltr">{quote_label}</th>
            </tr>
          </thead>
          <tbody>
            {"".join(quick_rows)}
          </tbody>
        </table>
      </div>
    </section>

    {_build_metals_table_html(spec, feed, catalog)}

    <!-- FAQ Section (Matching JSON-LD FAQPage) -->
    <section class="mt-8">
      <h2 class="text-lg font-extrabold text-white mb-3" data-ar="الأسئلة الشائعة" data-en="Frequently Asked Questions">الأسئلة الشائعة</h2>
      <div class="space-y-3">
        {"".join(faqs_html)}
      </div>
    </section>

    <!-- Cross-Links to All High-Intent SEO Pages -->
    <section class="mt-8 rounded-2xl bg-slate-900/75 border border-slate-800 p-5">
      <h2 class="text-sm sm:text-base font-bold text-white mb-3" data-ar="أسعار وحاسبات مباشرة أخرى على ريت باث" data-en="More Live Converters &amp; Rates on RatePath">أسعار وحاسبات مباشرة أخرى على ريت باث</h2>
      <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2.5">
        {"".join(related_links)}
      </div>
    </section>
  </main>

  <footer class="rp-seo-wrap px-4 py-8 mt-6 border-t border-slate-800/80 text-xs text-slate-400 flex flex-col sm:flex-row items-start sm:items-center justify-between gap-3">
    <p data-ar="⚖️ ريت باث (RatePath) يقدم بيانات سوقية وأدوات تحويل وتنبيهات معلوماتية فقط — ليست نصيحة استثمارية أو مالية." data-en="⚖️ RatePath provides live market data, conversion tools &amp; informational price alerts only — not financial advice.">⚖️ ريت باث (RatePath) يقدم بيانات سوقية وأدوات تحويل وتنبيهات معلوماتية فقط — ليست نصيحة استثمارية أو مالية.</p>
    <p class="font-mono shrink-0" dir="ltr">ratepathlive.com • {generated_date}</p>
  </footer>

  <script type="module" src="/js/seo-pair-page.js?v=22"></script>
</body>
</html>
"""
    assert RAW_SYRIA_FLAG not in page_html, f"Raw Syria flag leaked in {spec.slug}"
    return page_html


def generate_sitemap_xml(generated_date: str) -> str:
    entries = [
        f"""  <url>
    <loc>{SITE_ORIGIN}/</loc>
    <lastmod>{generated_date}</lastmod>
    <xhtml:link rel="alternate" hreflang="en" href="{SITE_ORIGIN}/?lang=en" />
    <xhtml:link rel="alternate" hreflang="ar" href="{SITE_ORIGIN}/?lang=ar" />
    <xhtml:link rel="alternate" hreflang="x-default" href="{SITE_ORIGIN}/" />
    <changefreq>hourly</changefreq>
    <priority>1.0</priority>
  </url>""",
        f"""  <url>
    <loc>{SITE_ORIGIN}/ar</loc>
    <lastmod>{generated_date}</lastmod>
    <xhtml:link rel="alternate" hreflang="en" href="{SITE_ORIGIN}/en" />
    <xhtml:link rel="alternate" hreflang="ar" href="{SITE_ORIGIN}/ar" />
    <xhtml:link rel="alternate" hreflang="x-default" href="{SITE_ORIGIN}/" />
    <changefreq>hourly</changefreq>
    <priority>1.0</priority>
  </url>""",
        f"""  <url>
    <loc>{SITE_ORIGIN}/en</loc>
    <lastmod>{generated_date}</lastmod>
    <xhtml:link rel="alternate" hreflang="en" href="{SITE_ORIGIN}/en" />
    <xhtml:link rel="alternate" hreflang="ar" href="{SITE_ORIGIN}/ar" />
    <xhtml:link rel="alternate" hreflang="x-default" href="{SITE_ORIGIN}/" />
    <changefreq>hourly</changefreq>
    <priority>0.9</priority>
  </url>""",
    ]
    for spec in SEO_PAGES:
        url = f"{SITE_ORIGIN}/{spec.slug}"
        entries.append(
            f"""  <url>
    <loc>{url}</loc>
    <lastmod>{generated_date}</lastmod>
    <xhtml:link rel="alternate" hreflang="ar" href="{url}?lang=ar" />
    <xhtml:link rel="alternate" hreflang="en" href="{url}?lang=en" />
    <xhtml:link rel="alternate" hreflang="x-default" href="{url}" />
    <changefreq>hourly</changefreq>
    <priority>0.9</priority>
  </url>"""
        )

    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"\n'
        '        xmlns:xhtml="http://www.w3.org/1999/xhtml">\n'
        + "\n".join(entries)
        + "\n</urlset>\n"
    )


def generate_robots_txt() -> str:
    allow_lines = [f"Allow: /{spec.slug}" for spec in SEO_PAGES]
    return "\n".join(
        [
            "User-agent: *",
            "Allow: /",
            "Allow: /ar",
            "Allow: /en",
            *allow_lines,
            "Allow: /public/live_feed.json",
            "Disallow: /api/",
            "Disallow: /server.py",
            "",
            f"Sitemap: {SITE_ORIGIN}/sitemap.xml",
            "",
        ]
    )


def generate_all_seo_assets(*, frontend_dir: Path = FRONTEND_DIR) -> dict[str, Any]:
    feed = json.loads(LIVE_FEED_PATH.read_text(encoding="utf-8")) if LIVE_FEED_PATH.exists() else {}
    catalog = json.loads(CATALOG_FEED_PATH.read_text(encoding="utf-8")) if CATALOG_FEED_PATH.exists() else {}
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    generated_routes: list[str] = []
    for spec in SEO_PAGES:
        out_dir = frontend_dir / spec.slug
        out_dir.mkdir(parents=True, exist_ok=True)
        html_text = _render_seo_page_html(spec, feed, catalog, today_str)
        (out_dir / "index.html").write_text(html_text, encoding="utf-8")
        generated_routes.append(f"/{spec.slug}")

    (frontend_dir / "sitemap.xml").write_text(generate_sitemap_xml(today_str), encoding="utf-8")
    (frontend_dir / "robots.txt").write_text(generate_robots_txt(), encoding="utf-8")
    return {
        "generated_count": len(generated_routes),
        "routes": generated_routes,
        "sitemap": str(frontend_dir / "sitemap.xml"),
        "robots": str(frontend_dir / "robots.txt"),
    }


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    summary = generate_all_seo_assets()
    print(json.dumps(summary, ensure_ascii=False, indent=2))
