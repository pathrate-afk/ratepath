/**
 * RatePath (ريت باث) — Cloudflare Pages Function: `GET /api/strategist-signals`
 *
 * Live Quantitative + Macro + Derivatives Probabilistic Signal Radar & Real 30-Day Track Record:
 * - Computes EMA(20), EMA(50), RSI(14), Funding Rate (OKX public), and Macro DXY/US10Y pressure
 *   for Gold (XAU), Silver (XAG), Euro (EUR), British Pound (GBP), BTC, ETH, and SOL.
 * - Applies Selective Abstention (`[-35, +35]` -> `neutral`) to avoid noisy guesswork.
 * - Queries real 30-day evaluated predictions from Supabase `public.market_memory` (NEVER fabricates accuracy).
 * - Informational data analytics only — not investment advice or guaranteed outcomes.
 */

const CORS_HEADERS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, OPTIONS",
  "Access-Control-Allow-Headers": "Content-Type",
  "Content-Type": "application/json; charset=utf-8",
  "Cache-Control": "public, max-age=300, s-maxage=300",
  "X-Content-Type-Options": "nosniff",
  "X-Robots-Tag": "noindex",
};

const ABSTENTION_BAND = 35;
const SUPABASE_SERVER_UA = "RatePath-Edge/2.0 (server)";
const FETCH_HEADERS = {
  Accept: "application/json",
  "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) RatePathQuant/2.0",
};

function withTimeout(init = {}, ms = 6500) {
  if (typeof AbortSignal !== "undefined" && typeof AbortSignal.timeout === "function") {
    return { ...init, signal: AbortSignal.timeout(ms) };
  }
  return init;
}

export function computeEma(values, period) {
  if (!Array.isArray(values) || values.length < period || period <= 0) return null;
  const k = 2 / (period + 1);
  let ema = values.slice(0, period).reduce((acc, v) => acc + v, 0) / period;
  for (let i = period; i < values.length; i++) {
    ema = values[i] * k + ema * (1 - k);
  }
  return ema;
}

export function computeRsi(values, period = 14) {
  if (!Array.isArray(values) || values.length <= period) return null;
  let gainSum = 0;
  let lossSum = 0;
  for (let i = 1; i <= period; i++) {
    const d = values[i] - values[i - 1];
    if (d > 0) gainSum += d;
    else lossSum += -d;
  }
  let avgGain = gainSum / period;
  let avgLoss = lossSum / period;
  for (let i = period + 1; i < values.length; i++) {
    const d = values[i] - values[i - 1];
    const g = d > 0 ? d : 0;
    const l = d < 0 ? -d : 0;
    avgGain = (avgGain * (period - 1) + g) / period;
    avgLoss = (avgLoss * (period - 1) + l) / period;
  }
  if (avgLoss === 0) return avgGain > 0 ? 100 : 50;
  const rs = avgGain / avgLoss;
  return 100 - 100 / (1 + rs);
}

export function scoreAssetQuant({ asset, closes, fundingPct = null, dxyChgPct = null, us10yChgPct = null }) {
  if (!Array.isArray(closes) || closes.length < 20) {
    return {
      asset,
      score: 0,
      bias: "neutral",
      confidence: 40,
      price: closes?.[closes.length - 1] ?? null,
      change_24h_pct: null,
      ema20: null,
      ema50: null,
      rsi14: null,
      funding_8h_pct: fundingPct,
      reason_en: "Insufficient candle depth — holding neutral.",
      reason_ar: "بيانات الشموع غير كافية حالياً — القراءة محايدة.",
    };
  }

  const latest = closes[closes.length - 1];
  const prev = closes[closes.length - 2] || latest;
  const chg24 = prev > 0 ? ((latest - prev) / prev) * 100 : 0;
  const ema20 = computeEma(closes, 20);
  const ema50 = computeEma(closes, Math.min(50, closes.length));
  const rsi14 = computeRsi(closes, 14);

  let score = 0;
  const factorsEn = [];
  const factorsAr = [];

  // 1. EMA Trend (+/-40)
  if (ema20 && ema50 && ema50 > 0) {
    const spreadPct = ((ema20 - ema50) / ema50) * 100;
    if (latest > ema20 && ema20 > ema50) {
      score += spreadPct >= 0.3 ? 40 : 25;
      factorsEn.push("Price above EMA20/50 uptrend");
      factorsAr.push("السعر فوق متوسطي EMA20/50 (زخم صاعد)");
    } else if (latest < ema20 && ema20 < ema50) {
      score -= spreadPct <= -0.3 ? 40 : 25;
      factorsEn.push("Price below EMA20/50 downtrend");
      factorsAr.push("السعر تحت متوسطي EMA20/50 (ضغط هابط)");
    } else if (latest > ema20) {
      score += 12;
      factorsEn.push("Mild rebound above EMA20");
      factorsAr.push("ارتداد متوسط فوق EMA20");
    } else {
      score -= 12;
      factorsEn.push("Mild pullback below EMA20");
      factorsAr.push("تراجع محدود تحت EMA20");
    }
  }

  // 2. RSI Momentum (+/-25)
  if (rsi14 !== null) {
    if (rsi14 >= 55 && rsi14 <= 69) {
      score += 22;
      factorsEn.push(`Healthy RSI (${rsi14.toFixed(1)})`);
      factorsAr.push(`قوة زخم متوازنة RSI (${rsi14.toFixed(1)})`);
    } else if (rsi14 >= 31 && rsi14 <= 45) {
      score -= 22;
      factorsEn.push(`Weak RSI (${rsi14.toFixed(1)})`);
      factorsAr.push(`ضعف زخم RSI (${rsi14.toFixed(1)})`);
    } else if (rsi14 > 76) {
      score -= 15;
      factorsEn.push(`Overbought RSI (${rsi14.toFixed(1)})`);
      factorsAr.push(`تشبع شرائي RSI (${rsi14.toFixed(1)})`);
    } else if (rsi14 < 24) {
      score += 15;
      factorsEn.push(`Oversold RSI (${rsi14.toFixed(1)})`);
      factorsAr.push(`تشبع بيعي RSI (${rsi14.toFixed(1)})`);
    }
  }

  // 3. Crypto Derivatives Funding (+/-35)
  if ((asset === "BTC" || asset === "ETH" || asset === "SOL") && fundingPct !== null) {
    if (fundingPct >= 0.045) {
      score -= 30;
      factorsEn.push(`Crowded long funding (${fundingPct.toFixed(3)}%)`);
      factorsAr.push(`ازدحام مراكز شراء بتمويل مرتفع (${fundingPct.toFixed(3)}%)`);
    } else if (fundingPct <= -0.015) {
      score += 30;
      factorsEn.push(`Negative funding squeeze setup (${fundingPct.toFixed(3)}%)`);
      factorsAr.push(`تمويل سلبي يدعم ارتداداً محتملاً (${fundingPct.toFixed(3)}%)`);
    } else if (fundingPct >= 0.005 && fundingPct <= 0.025) {
      score += 10;
      factorsEn.push(`Balanced funding (${fundingPct.toFixed(3)}%)`);
      factorsAr.push(`معدل تمويل صحي (${fundingPct.toFixed(3)}%)`);
    }
  }

  // 4. Macro DXY & US10Y for Gold, Silver, Euro, Pound (+/-35)
  if (asset === "XAU" || asset === "XAG" || asset === "EUR" || asset === "GBP") {
    if (dxyChgPct !== null) {
      if (dxyChgPct <= -0.3) {
        score += 22;
        factorsEn.push(`Weaker DXY (${dxyChgPct.toFixed(2)}%)`);
        factorsAr.push(`تراجع مؤشر الدولار DXY (${dxyChgPct.toFixed(2)}%)`);
      } else if (dxyChgPct >= 0.3) {
        score -= 22;
        factorsEn.push(`Stronger DXY (+${dxyChgPct.toFixed(2)}%)`);
        factorsAr.push(`قوة مؤشر الدولار DXY (+${dxyChgPct.toFixed(2)}%)`);
      }
    }
    if ((asset === "XAU" || asset === "XAG") && us10yChgPct !== null) {
      if (us10yChgPct <= -1.0) {
        score += 13;
        factorsEn.push("Falling US10Y yields");
        factorsAr.push("تراجع عوائد السندات الأمريكية");
      } else if (us10yChgPct >= 1.0) {
        score -= 13;
        factorsEn.push("Rising US10Y yields");
        factorsAr.push("ارتفاع عوائد السندات الأمريكية");
      }
    }
  }

  score = Math.max(-100, Math.min(100, Math.round(score)));
  const bias = score >= ABSTENTION_BAND ? "bullish" : score <= -ABSTENTION_BAND ? "bearish" : "neutral";
  const confidence = Math.min(90, Math.round(45 + Math.abs(score) * 0.45));

  return {
    asset,
    score,
    bias,
    confidence,
    price: Number(latest.toFixed(latest >= 100 ? 2 : 4)),
    change_24h_pct: Number(chg24.toFixed(2)),
    ema20: ema20 !== null ? Number(ema20.toFixed(ema20 >= 100 ? 2 : 4)) : null,
    ema50: ema50 !== null ? Number(ema50.toFixed(ema50 >= 100 ? 2 : 4)) : null,
    rsi14: rsi14 !== null ? Number(rsi14.toFixed(1)) : null,
    funding_8h_pct: fundingPct !== null ? Number(fundingPct.toFixed(4)) : null,
    reason_en:
      factorsEn.slice(0, 2).join(" • ") ||
      "Signals inside neutral deadband (selective abstention).",
    reason_ar:
      factorsAr.slice(0, 2).join(" • ") ||
      "الأرقام ضمن النطاق المحايد (انتظار تأكيد أوضح).",
  };
}

async function fetchCryptoCloses(sym) {
  // Primary: Yahoo Finance crypto daily candles (100% reliable on Cloudflare Edge)
  const yahooRes = await fetchYahooCloses(`${sym}-USD`);
  if (yahooRes.closes.length >= 20) return yahooRes.closes;

  // Fallback: KuCoin 4h candles
  try {
    const resp = await fetch(
      `https://api.kucoin.com/api/v1/market/candles?type=4hour&symbol=${encodeURIComponent(sym)}-USDT`,
      withTimeout({ headers: FETCH_HEADERS, cf: { cacheTtl: 180, cacheEverything: true } })
    );
    if (!resp.ok) return [];
    const data = await resp.json();
    const rows = Array.isArray(data?.data) ? data.data.slice(0, 65).reverse() : [];
    return rows.map((r) => Number(r[2])).filter((n) => Number.isFinite(n) && n > 0);
  } catch {
    return [];
  }
}

async function fetchOkxFundingPct(sym) {
  try {
    const resp = await fetch(
      `https://www.okx.com/api/v5/public/funding-rate?instId=${encodeURIComponent(sym)}-USDT-SWAP`,
      withTimeout({ headers: FETCH_HEADERS, cf: { cacheTtl: 180, cacheEverything: true } })
    );
    if (!resp.ok) return null;
    const data = await resp.json();
    const raw = Number(data?.data?.[0]?.fundingRate);
    return Number.isFinite(raw) ? raw * 100 : null;
  } catch {
    return null;
  }
}

async function fetchYahooCloses(ticker) {
  try {
    const resp = await fetch(
      `https://query1.finance.yahoo.com/v8/finance/chart/${encodeURIComponent(ticker)}?range=3mo&interval=1d`,
      withTimeout({ headers: FETCH_HEADERS, cf: { cacheTtl: 300, cacheEverything: true } })
    );
    if (!resp.ok) return { closes: [], chgPct: null };
    const data = await resp.json();
    const rawCloses = data?.chart?.result?.[0]?.indicators?.quote?.[0]?.close || [];
    const closes = rawCloses.map(Number).filter((n) => Number.isFinite(n) && n > 0).slice(-65);
    if (closes.length < 2) return { closes, chgPct: null };
    const chgPct = ((closes[closes.length - 1] - closes[closes.length - 2]) / closes[closes.length - 2]) * 100;
    return { closes, chgPct };
  } catch {
    return { closes: [], chgPct: null };
  }
}

async function fetchReal30DayTrackRecord(env) {
  const supaUrl = String(env?.SUPABASE_URL || "").trim().replace(/\/+$/, "");
  const supaKey = String(env?.SUPABASE_KEY || "").trim();
  if (!supaUrl || !supaKey || supaUrl.toLowerCase() === "placeholder" || supaKey.toLowerCase() === "placeholder") {
    return { graded_count_30d: 0, avg_accuracy_pct: null, pending_count: 0, recent: [] };
  }
  const restBase = supaUrl.endsWith("/rest/v1") ? supaUrl : `${supaUrl}/rest/v1`;
  const sinceIso = new Date(Date.now() - 30 * 86400 * 1000).toISOString();
  const headers = {
    apikey: supaKey,
    Authorization: `Bearer ${supaKey}`,
    "User-Agent": SUPABASE_SERVER_UA,
  };
  try {
    const [gradedResp, pendingResp] = await Promise.all([
      fetch(
        `${restBase}/market_memory?record_type=eq.evaluated_lesson&evaluation_status=eq.graded&evaluated_at=gte.${encodeURIComponent(sinceIso)}&select=event_key,accuracy_score,evaluated_at,headline_en,headline_ar&order=evaluated_at.desc&limit=100`,
        withTimeout({ headers })
      ),
      fetch(
        `${restBase}/market_memory?record_type=eq.live_prediction&evaluation_status=eq.pending&select=id&limit=100`,
        withTimeout({ headers })
      ),
    ]);
    const gradedRows = gradedResp.ok ? await gradedResp.json() : [];
    const pendingRows = pendingResp.ok ? await pendingResp.json() : [];
    const validScores = (Array.isArray(gradedRows) ? gradedRows : [])
      .map((r) => Number(r.accuracy_score))
      .filter((n) => Number.isFinite(n) && n >= 0 && n <= 100);
    const avg =
      validScores.length > 0
        ? Number((validScores.reduce((a, b) => a + b, 0) / validScores.length).toFixed(1))
        : null;
    return {
      graded_count_30d: validScores.length,
      avg_accuracy_pct: avg,
      pending_count: Array.isArray(pendingRows) ? pendingRows.length : 0,
      recent: (Array.isArray(gradedRows) ? gradedRows : []).slice(0, 3).map((r) => ({
        evaluated_at: r.evaluated_at,
        accuracy_score: r.accuracy_score,
        headline_en: r.headline_en,
        headline_ar: r.headline_ar,
      })),
    };
  } catch {
    return { graded_count_30d: 0, avg_accuracy_pct: null, pending_count: 0, recent: [] };
  }
}

export async function onRequestOptions() {
  return new Response(null, { status: 204, headers: CORS_HEADERS });
}

export async function onRequestGet(context) {
  const [
    btcCloses,
    ethCloses,
    solCloses,
    btcFunding,
    ethFunding,
    solFunding,
    xau,
    xag,
    eur,
    gbp,
    dxy,
    us10y,
    trackRecord,
  ] = await Promise.all([
    fetchCryptoCloses("BTC"),
    fetchCryptoCloses("ETH"),
    fetchCryptoCloses("SOL"),
    fetchOkxFundingPct("BTC"),
    fetchOkxFundingPct("ETH"),
    fetchOkxFundingPct("SOL"),
    fetchYahooCloses("GC=F"),
    fetchYahooCloses("SI=F"),
    fetchYahooCloses("EURUSD=X"),
    fetchYahooCloses("GBPUSD=X"),
    fetchYahooCloses("DX-Y.NYB"),
    fetchYahooCloses("^TNX"),
    fetchReal30DayTrackRecord(context.env),
  ]);

  const dxyChg = dxy.chgPct;
  const us10yChg = us10y.chgPct;

  const signals = [
    scoreAssetQuant({ asset: "XAU", closes: xau.closes, dxyChgPct: dxyChg, us10yChgPct: us10yChg }),
    scoreAssetQuant({ asset: "XAG", closes: xag.closes, dxyChgPct: dxyChg, us10yChgPct: us10yChg }),
    scoreAssetQuant({ asset: "EUR", closes: eur.closes, dxyChgPct: dxyChg }),
    scoreAssetQuant({ asset: "GBP", closes: gbp.closes, dxyChgPct: dxyChg }),
    scoreAssetQuant({ asset: "BTC", closes: btcCloses, fundingPct: btcFunding }),
    scoreAssetQuant({ asset: "ETH", closes: ethCloses, fundingPct: ethFunding }),
    scoreAssetQuant({ asset: "SOL", closes: solCloses, fundingPct: solFunding }),
  ];

  return new Response(
    JSON.stringify({
      generated_at: new Date().toISOString(),
      model: "RatePath Quant + Macro + Derivatives Ensemble v2 (Selective Abstention +/-35)",
      macro_context: {
        dxy_price: dxy.closes.length ? Number(dxy.closes[dxy.closes.length - 1].toFixed(2)) : null,
        dxy_24h_pct: dxyChg !== null ? Number(dxyChg.toFixed(2)) : null,
        us10y_yield: us10y.closes.length ? Number(us10y.closes[us10y.closes.length - 1].toFixed(2)) : null,
        us10y_24h_pct: us10yChg !== null ? Number(us10yChg.toFixed(2)) : null,
      },
      signals,
      track_record_30d: trackRecord,
      legal_notice: {
        en: "Informational data & statistical analytics only — NOT financial advice or guaranteed outcomes. Market numbers change continuously and no model predicts the future with certainty.",
        ar: "تحليل رقمي وإحصائي للمعلومات والبيانات فقط — ليس نصيحة استثمارية ولا ضماناً لصعود أو هبوط أي أصل؛ الأسواق تتحرك بلغة الأرقام المتغيرة ولا أحد يعلم الغيب.",
      },
    }),
    { status: 200, headers: CORS_HEADERS }
  );
}
