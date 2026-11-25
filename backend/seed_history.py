"""Historical Macro Event Seeder for RatePath (ريت باث) `market_memory`.

Seeds verified historical macro twins with bilingual (`en`/`ar`) institutional theses,
realized 24h reactions, priced-in calibration lessons, and 768-d pgvector embeddings.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from backend.config import LOCAL_MEMORY_FALLBACK_PATH, Settings, enforce_utf8_runtime
from backend.evaluator import MarketMemoryStore, build_deterministic_embedding


def get_historical_macro_twins() -> list[dict[str, Any]]:
    """Return canonical bilingual historical macro twins for RatePath institutional memory."""
    raw_twins: list[dict[str, Any]] = [
        {
            "event_key": "hist-fomc-hawkish-hold-2023-09",
            "record_type": "historical_macro_twin",
            "event_timestamp": "2023-09-20T18:00:00Z",
            "macro_regime": "hawkish_dot_plot_repricer",
            "source_name": "Federal Reserve",
            "source_url": "https://www.federalreserve.gov/newsevents/pressreleases/monetary20230920a.htm",
            "headline_en": "Federal Reserve holds rates steady but signals higher-for-longer 2024 dot plot",
            "headline_ar": "الاحتياطي الفيدرالي يثبت الفائدة ويرسل إشارة تشددية ببقاء العوائد مرتفعة لفترة أطول",
            "summary_en": (
                "FOMC maintained the target range at 5.25%-5.50% while slashing 2024 rate-cut "
                "projections from 100 bps to 50 bps, lifting 10Y Treasury real yields."
            ),
            "summary_ar": (
                "أبقى الفيدرالي الأمريكي النطاق المستهدف للفائدة دون تغيير مع تقليص توقعات خفض الفائدة "
                "بمقدار النصف، مما دفع العوائد الحقيقية للسندات العشرية للارتفاع."
            ),
            "priced_in_warning": {
                "active": False,
                "severity": "low",
                "en": "Pause was priced in, but the 50 bps upward revision in median 2024 dots was a hawkish shock.",
                "ar": "تثبيت الفائدة كان مسعّراً مسبقاً، لكن تقليص تخفيضات الفائدة في مخطط النقاط شكّل مفاجأة تشددية.",
            },
            "assets_bias_24h": {
                "USD": "bullish",
                "EUR": "bearish",
                "GBP": "bearish",
                "XAU": "bearish",
                "BTC": "bearish",
                "ETH": "bearish",
                "SOL": "bearish",
            },
            "baseline_prices": {
                "EUR_USD": "1.0715",
                "GBP_USD": "1.2390",
                "XAU_OZ": "1945.20",
                "BTC": "27150.00",
            },
            "realized_prices_24h": {
                "EUR_USD": "1.0655",
                "GBP_USD": "1.2292",
                "XAU_OZ": "1919.80",
                "BTC": "26580.00",
            },
            "realized_changes_pct": {
                "EUR": "-0.5600",
                "GBP": "-0.7910",
                "XAU": "-1.3058",
                "BTC": "-2.0994",
            },
            "evaluation_status": "historical_anchor",
            "accuracy_score": "100.00",
            "lesson_en": (
                "Forward-guidance dot-plot revisions dominate headline rate holds; hawkish terminal "
                "rate shifts pressure Gold and BTC over the ensuing 24 hours."
            ),
            "lesson_ar": (
                "مراجعات مخطط النقاط المستقبلي تتفوق في تأثيرها على قرار التثبيت الفوري؛ رفع المسار المتوقع "
                "للفائدة يضغط هبوطياً على الذهب والبيتكوين خلال 24 ساعة."
            ),
        },
        {
            "event_key": "hist-us-cpi-upside-shock-2024-04",
            "record_type": "historical_macro_twin",
            "event_timestamp": "2024-04-10T12:30:00Z",
            "macro_regime": "sticky_core_inflation_shock",
            "source_name": "Bureau of Labor Statistics",
            "source_url": "https://www.bls.gov/cpi/",
            "headline_en": "US Core CPI beats consensus for third consecutive month at 0.4% MoM",
            "headline_ar": "التضخم الأساسي الأمريكي يتجاوز التوقعات للشهر الثالث على التوالي مسجلاً 0.4% شهرياً",
            "summary_en": (
                "Hotter-than-expected shelter and services inflation forced swap markets to price "
                "out June rate cuts, sparking an immediate surge in USDJPY and DXY."
            ),
            "summary_ar": (
                "تسببت قراءة تضخم الخدمات والإسكان الأعلى من التوقعات في استبعاد تسعير خفض الفائدة المبكر "
                "وارتفاع فوري في مؤشر الدولار وعوائد السندات."
            ),
            "priced_in_warning": {
                "active": False,
                "severity": "low",
                "en": "Upside surprise broke consensus positioning, driving clean 24h USD trend continuation.",
                "ar": "المفاجأة التضخمية الصعودية كسرت تمركزات الإجماع وأطلقت مساراً صعودياً قوياً للدولار خلال 24 ساعة.",
            },
            "assets_bias_24h": {
                "USD": "bullish",
                "EUR": "bearish",
                "GBP": "bearish",
                "XAU": "bearish",
                "BTC": "neutral",
                "ETH": "bearish",
                "SOL": "bearish",
            },
            "baseline_prices": {
                "EUR_USD": "1.0856",
                "GBP_USD": "1.2678",
                "XAU_OZ": "2352.40",
                "BTC": "69100.00",
            },
            "realized_prices_24h": {
                "EUR_USD": "1.0738",
                "GBP_USD": "1.2542",
                "XAU_OZ": "2334.10",
                "BTC": "69950.00",
            },
            "realized_changes_pct": {
                "EUR": "-1.0870",
                "GBP": "-1.0727",
                "XAU": "-0.7779",
                "BTC": "1.2301",
            },
            "evaluation_status": "historical_anchor",
            "accuracy_score": "87.50",
            "lesson_en": (
                "Core CPI beats reliably crush G10 FX vs USD over 24h, though spot ETF inflows "
                "can allow BTC to absorb initial macro dips faster than high-beta altcoins."
            ),
            "lesson_ar": (
                "مفاجآت التضخم الأساسي تضغط بقوة على اليورو والإسترليني أمام الدولار خلال 24 ساعة، بينما قد "
                "يمتص البيتكوين الصدمة أسرع بفضل التدفقات المؤسسية الفورية."
            ),
        },
        {
            "event_key": "hist-fed-priced-in-cut-2024-11",
            "record_type": "historical_macro_twin",
            "event_timestamp": "2024-11-07T19:00:00Z",
            "macro_regime": "priced_in_policy_easing",
            "source_name": "Federal Reserve",
            "source_url": "https://www.federalreserve.gov/newsevents/pressreleases/monetary20241107a.htm",
            "headline_en": "Federal Reserve delivers widely expected 25 bps rate cut to 4.50%-4.75%",
            "headline_ar": "الاحتياطي الفيدرالي يخفض الفائدة بمقدار 25 نقطة أساس وفق التوقعات المسبقة للأسواق",
            "summary_en": (
                "With fed funds futures pricing a 98% probability ahead of the meeting, cross-asset "
                "reactions muted quickly as traders faded the initial knee-jerk move."
            ),
            "summary_ar": (
                "مع تسعير العقود الآجلة لاحتمالية الخفض بنسبة 98% مسبقاً، تلاشت التفاعلات اللحظية سريعاً "
                "ودخلت الأسواق في جني أرباح متوازن."
            ),
            "priced_in_warning": {
                "active": True,
                "severity": "high",
                "en": "98% OIS implied probability ahead of release triggered classic sell-the-fact mean reversion.",
                "ar": "تسعير القرار مسبقاً بنسبة 98% في عقود المبادلة أدى إلى تفعيل قاعدة البيع عند تحقق الخبر.",
            },
            "assets_bias_24h": {
                "USD": "neutral",
                "EUR": "neutral",
                "GBP": "neutral",
                "XAU": "neutral",
                "BTC": "bullish",
                "ETH": "bullish",
                "SOL": "bullish",
            },
            "baseline_prices": {
                "EUR_USD": "1.0785",
                "GBP_USD": "1.2960",
                "XAU_OZ": "2698.00",
                "BTC": "75800.00",
            },
            "realized_prices_24h": {
                "EUR_USD": "1.0772",
                "GBP_USD": "1.2945",
                "XAU_OZ": "2689.50",
                "BTC": "76550.00",
            },
            "realized_changes_pct": {
                "EUR": "-0.1205",
                "GBP": "-0.1157",
                "XAU": "-0.3150",
                "BTC": "0.9894",
            },
            "evaluation_status": "historical_anchor",
            "accuracy_score": "92.86",
            "lesson_en": (
                "When OIS pricing exceeds 90% pre-announcement, assign neutral 24h FX/Gold bias "
                "and activate high priced-in exhaustion warnings."
            ),
            "lesson_ar": (
                "عندما يتجاوز التسعير المسبق في عقود الفائدة 90% قبل البيان، يجب اعتماد نظرة محايدة للعملات "
                "والذهب وتفعيل تحذير التسعير المسبق المرتفع."
            ),
        },
    ]

    enriched: list[dict[str, Any]] = []
    for twin in raw_twins:
        vec = build_deterministic_embedding(f"{twin['headline_en']} {twin['summary_en']}")
        enriched.append({**twin, "embedding": "[" + ",".join(vec) + "]"})
    return enriched


async def seed_market_memory(
    *,
    settings: Settings | None = None,
    local_path: Path = LOCAL_MEMORY_FALLBACK_PATH,
) -> int:
    """Upsert historical macro twins into Supabase `market_memory` or local fallback ledger."""
    enforce_utf8_runtime()
    store = MarketMemoryStore(settings=settings, local_path=local_path)
    twins = get_historical_macro_twins()
    return await store.upsert_records(twins)


if __name__ == "__main__":
    enforce_utf8_runtime()
    inserted = asyncio.run(seed_market_memory())
    print(f"[RatePath | ريت باث] Seeded {inserted} historical macro twins into market_memory.")
