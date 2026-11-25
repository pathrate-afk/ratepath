"""External catalog data sources (CoinMarketCap, 155+ World FX, Silver & SP-Today SYP)."""

from backend.sources.coinmarketcap import (
    CMCCatalogResult,
    build_catalog_feed_payload,
    fetch_cmc_top_cryptos,
    fetch_global_155_fiat_rates,
    fetch_silver_spot,
    fetch_sp_today_syp_rate,
    generate_and_publish_catalog_feed,
    parse_sp_today_usd_syp_html,
    publish_catalog_feed_atomic,
)

__all__ = [
    "CMCCatalogResult",
    "build_catalog_feed_payload",
    "fetch_cmc_top_cryptos",
    "fetch_global_155_fiat_rates",
    "fetch_silver_spot",
    "fetch_sp_today_syp_rate",
    "generate_and_publish_catalog_feed",
    "parse_sp_today_usd_syp_html",
    "publish_catalog_feed_atomic",
]
