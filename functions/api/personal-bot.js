/**
 * RatePath (ريت باث) — Cloudflare Pages Function: `/api/personal-bot`
 *
 * Authenticated Personal AI Tracker Bot Profile & Watchlist Manager:
 * - Strictly verifies the caller's Supabase Auth Bearer JWT (`GET /auth/v1/user`).
 * - Computes the subscriber's true active plan tier server-side from `public.vip_subscriptions`
 *   (cannot be spoofed from browser localStorage/requests):
 *     * `free`: up to 3 tracked assets (currencies, gold, crypto, stocks)
 *     * `monthly_business_vip` ($10/mo USDT): up to 10 tracked assets + Business VIP Channel
 *     * `monthly_personal_bot` ($19/mo USDT): up to 50 tracked assets + stocks/company news + VIP Channel
 * - Issues a 1-click Telegram pairing deep link (`https://t.me/RatePathTerminalBot?start=link_<TOKEN>`).
 * - Informational data & monitoring service only — no financial advice or guaranteed outcomes.
 */

const JSON_HEADERS = {
  "Content-Type": "application/json; charset=utf-8",
  "Cache-Control": "no-store",
  "X-Content-Type-Options": "nosniff",
  "X-Robots-Tag": "noindex",
};

const SUPABASE_SERVER_UA = "RatePath-Edge/2.0 (server)";
const ALLOWED_CATEGORIES = new Set(["fiat", "metal", "gold", "crypto", "stock"]);

export const PLAN_LIMITS = Object.freeze({
  free: { maxAssets: 3, priceUsd: 0, stocksAllowed: true, dailyDigest: true },
  monthly_business_vip: { maxAssets: 10, priceUsd: 10, stocksAllowed: true, dailyDigest: true },
  monthly_personal_bot: { maxAssets: 50, priceUsd: 19, stocksAllowed: true, dailyDigest: true },
});

function withTimeout(init = {}, ms = 8000) {
  if (typeof AbortSignal !== "undefined" && typeof AbortSignal.timeout === "function") {
    return { ...init, signal: AbortSignal.timeout(ms) };
  }
  return init;
}

function getSupabaseRestConfig(env) {
  const url = String(env?.SUPABASE_URL || "").trim().replace(/\/+$/, "");
  const key = String(env?.SUPABASE_KEY || "").trim();
  if (!url || !key || url.toLowerCase() === "placeholder" || key.toLowerCase() === "placeholder") {
    return null;
  }
  const origin = url.replace(/\/rest\/v1$/i, "");
  return {
    origin,
    restBase: `${origin}/rest/v1`,
    key,
    headers: {
      apikey: key,
      Authorization: `Bearer ${key}`,
      "User-Agent": SUPABASE_SERVER_UA,
      "Content-Type": "application/json",
    },
  };
}

async function verifyBearerUser(request, supaCfg, fetchImpl = fetch) {
  const authHeader = String(request.headers.get("Authorization") || "").trim();
  if (!authHeader.toLowerCase().startsWith("bearer ")) return null;
  const jwt = authHeader.slice(7).trim();
  if (jwt.length < 20) return null;

  try {
    const resp = await fetchImpl(
      `${supaCfg.origin}/auth/v1/user`,
      withTimeout({
        headers: {
          apikey: supaCfg.key,
          Authorization: `Bearer ${jwt}`,
          "User-Agent": SUPABASE_SERVER_UA,
        },
      })
    );
    if (!resp.ok) return null;
    const user = await resp.json();
    if (!user?.id) return null;
    return { id: String(user.id), email: String(user.email || "").trim().toLowerCase() };
  } catch {
    return null;
  }
}

async function resolveActivePlanTier(user, telegramChatId, supaCfg, fetchImpl = fetch) {
  const queries = [];
  if (user.email) {
    queries.push(`user_email=eq.${encodeURIComponent(user.email)}`);
  }
  if (telegramChatId) {
    queries.push(`telegram_user_id=eq.${encodeURIComponent(telegramChatId)}`);
  }
  if (queries.length === 0) return "free";

  let highest = "free";
  for (const filter of queries) {
    try {
      const resp = await fetchImpl(
        `${supaCfg.restBase}/vip_subscriptions?${filter}&payment_status=in.(finished,confirmed,active)&select=plan_tier,expires_at&order=created_at.desc&limit=5`,
        withTimeout({ headers: supaCfg.headers })
      );
      if (!resp.ok) continue;
      const rows = await resp.json();
      if (!Array.isArray(rows)) continue;
      for (const row of rows) {
        const exp = row?.expires_at;
        if (exp && Date.parse(exp) < Date.now()) continue;
        const tier = String(row?.plan_tier || "");
        if (tier === "monthly_personal_bot") return "monthly_personal_bot";
        if (tier === "monthly_business_vip" || tier === "monthly") highest = "monthly_business_vip";
      }
    } catch {
      // Ignore transient query errors
    }
  }
  return highest;
}

function generateLinkToken() {
  return crypto.randomUUID().replace(/-/g, "").slice(0, 18);
}

export function sanitizeWatchlist(rawList, maxItems = 3) {
  if (!Array.isArray(rawList)) return [];
  const clean = [];
  const seen = new Set();
  for (const item of rawList) {
    if (!item || typeof item !== "object") continue;
    const symbol = String(item.symbol || "")
      .replace(/[^A-Za-z0-9.=^_-]/g, "")
      .trim()
      .toUpperCase()
      .slice(0, 20);
    if (!symbol || seen.has(symbol)) continue;
    seen.add(symbol);

    const rawCat = String(item.category || "crypto").trim().toLowerCase();
    const category = ALLOWED_CATEGORIES.has(rawCat) ? (rawCat === "gold" ? "metal" : rawCat) : "crypto";

    const parseOptNum = (v) => {
      if (v === null || v === undefined || String(v).trim() === "") return null;
      const n = Number(String(v).replace(/,/g, "").trim());
      return Number.isFinite(n) && n > 0 ? String(v).replace(/,/g, "").trim().slice(0, 20) : null;
    };

    clean.push({
      symbol,
      category,
      name_en: String(item.name_en || item.name || symbol).replace(/[<>]/g, "").trim().slice(0, 64),
      name_ar: String(item.name_ar || item.name_en || symbol).replace(/[<>]/g, "").trim().slice(0, 64),
      alert_above: parseOptNum(item.alert_above),
      alert_below: parseOptNum(item.alert_below),
      news_alerts: item.news_alerts !== false,
      last_alerted_at: item.last_alerted_at || null,
    });
    if (clean.length >= maxItems) break;
  }
  return clean;
}

async function fetchOrCreateProfile(user, supaCfg, fetchImpl = fetch) {
  const getResp = await fetchImpl(
    `${supaCfg.restBase}/user_tracker_profiles?user_id=eq.${encodeURIComponent(user.id)}&select=*&limit=1`,
    withTimeout({ headers: supaCfg.headers })
  );
  if (getResp.ok) {
    const rows = await getResp.json();
    if (Array.isArray(rows) && rows.length > 0) {
      return rows[0];
    }
  }

  const fresh = {
    user_id: user.id,
    email: user.email || null,
    telegram_chat_id: null,
    link_token: generateLinkToken(),
    lang: "ar",
    plan_tier: "free",
    watchlist: [],
    daily_digest_enabled: true,
    digest_hour_utc: 7,
  };
  await fetchImpl(
    `${supaCfg.restBase}/user_tracker_profiles?on_conflict=user_id`,
    withTimeout({
      method: "POST",
      headers: { ...supaCfg.headers, Prefer: "resolution=merge-duplicates,return=minimal" },
      body: JSON.stringify(fresh),
    })
  );
  return fresh;
}

export async function onRequestGet(context) {
  const supaCfg = getSupabaseRestConfig(context.env);
  if (!supaCfg) {
    return new Response(JSON.stringify({ ok: false, error: "supabase_not_configured" }), {
      status: 503,
      headers: JSON_HEADERS,
    });
  }

  const user = await verifyBearerUser(context.request, supaCfg, fetch);
  if (!user) {
    return new Response(JSON.stringify({ ok: false, error: "unauthorized" }), {
      status: 401,
      headers: JSON_HEADERS,
    });
  }

  const profile = await fetchOrCreateProfile(user, supaCfg, fetch);
  const activeTier = await resolveActivePlanTier(user, profile.telegram_chat_id, supaCfg, fetch);
  let linkToken = profile.link_token;
  if (!profile.telegram_chat_id && !linkToken) {
    linkToken = generateLinkToken();
    await fetch(
      `${supaCfg.restBase}/user_tracker_profiles?user_id=eq.${encodeURIComponent(user.id)}`,
      withTimeout({
        method: "PATCH",
        headers: { ...supaCfg.headers, Prefer: "return=minimal" },
        body: JSON.stringify({ link_token: linkToken, plan_tier: activeTier }),
      })
    );
  } else if (activeTier !== profile.plan_tier) {
    await fetch(
      `${supaCfg.restBase}/user_tracker_profiles?user_id=eq.${encodeURIComponent(user.id)}`,
      withTimeout({
        method: "PATCH",
        headers: { ...supaCfg.headers, Prefer: "return=minimal" },
        body: JSON.stringify({ plan_tier: activeTier }),
      })
    );
  }

  const limits = PLAN_LIMITS[activeTier] || PLAN_LIMITS.free;
  return new Response(
    JSON.stringify({
      ok: true,
      profile: {
        email: user.email,
        plan_tier: activeTier,
        limits,
        telegram_linked: Boolean(profile.telegram_chat_id),
        telegram_link_url: linkToken
          ? `https://t.me/RatePathTerminalBot?start=link_${linkToken}`
          : "https://t.me/RatePathTerminalBot",
        lang: profile.lang || "ar",
        watchlist: Array.isArray(profile.watchlist) ? profile.watchlist : [],
        daily_digest_enabled: profile.daily_digest_enabled !== false,
        digest_hour_utc: Number.isInteger(profile.digest_hour_utc) ? profile.digest_hour_utc : 7,
      },
    }),
    { status: 200, headers: JSON_HEADERS }
  );
}

export async function onRequestPost(context) {
  const supaCfg = getSupabaseRestConfig(context.env);
  if (!supaCfg) {
    return new Response(JSON.stringify({ ok: false, error: "supabase_not_configured" }), {
      status: 503,
      headers: JSON_HEADERS,
    });
  }

  const user = await verifyBearerUser(context.request, supaCfg, fetch);
  if (!user) {
    return new Response(JSON.stringify({ ok: false, error: "unauthorized" }), {
      status: 401,
      headers: JSON_HEADERS,
    });
  }

  let body;
  try {
    body = await context.request.json();
  } catch {
    return new Response(JSON.stringify({ ok: false, error: "invalid_json" }), {
      status: 400,
      headers: JSON_HEADERS,
    });
  }

  const existing = await fetchOrCreateProfile(user, supaCfg, fetch);
  const activeTier = await resolveActivePlanTier(user, existing.telegram_chat_id, supaCfg, fetch);
  const limits = PLAN_LIMITS[activeTier] || PLAN_LIMITS.free;

  const lang = String(body?.lang || existing.lang || "ar").toLowerCase() === "en" ? "en" : "ar";
  const dailyDigest = body?.daily_digest_enabled !== undefined ? Boolean(body.daily_digest_enabled) : existing.daily_digest_enabled !== false;
  const rawHour = Number(body?.digest_hour_utc ?? existing.digest_hour_utc ?? 7);
  const digestHour = Number.isInteger(rawHour) && rawHour >= 0 && rawHour <= 23 ? rawHour : 7;

  const rawWatchlist = Array.isArray(body?.watchlist) ? body.watchlist : existing.watchlist || [];
  const truncated = rawWatchlist.length > limits.maxAssets;
  const cleanWatchlist = sanitizeWatchlist(rawWatchlist, limits.maxAssets);

  const unlinkRequested = Boolean(body?.unlink_telegram);
  const nextChatId = unlinkRequested ? null : existing.telegram_chat_id;
  const nextLinkToken =
    unlinkRequested || (!nextChatId && !existing.link_token)
      ? generateLinkToken()
      : existing.link_token;

  const updatePayload = {
    user_id: user.id,
    email: user.email || null,
    telegram_chat_id: nextChatId,
    link_token: nextLinkToken,
    lang,
    plan_tier: activeTier,
    watchlist: cleanWatchlist,
    daily_digest_enabled: dailyDigest,
    digest_hour_utc: digestHour,
  };

  const saveResp = await fetch(
    `${supaCfg.restBase}/user_tracker_profiles?on_conflict=user_id`,
    withTimeout({
      method: "POST",
      headers: { ...supaCfg.headers, Prefer: "resolution=merge-duplicates,return=minimal" },
      body: JSON.stringify(updatePayload),
    })
  );

  if (!saveResp.ok) {
    return new Response(JSON.stringify({ ok: false, error: "save_failed" }), {
      status: 502,
      headers: JSON_HEADERS,
    });
  }

  return new Response(
    JSON.stringify({
      ok: true,
      truncated_to_plan_limit: truncated,
      profile: {
        email: user.email,
        plan_tier: activeTier,
        limits,
        telegram_linked: Boolean(nextChatId),
        telegram_link_url: nextLinkToken
          ? `https://t.me/RatePathTerminalBot?start=link_${nextLinkToken}`
          : "https://t.me/RatePathTerminalBot",
        lang,
        watchlist: cleanWatchlist,
        daily_digest_enabled: dailyDigest,
        digest_hour_utc: digestHour,
      },
    }),
    { status: 200, headers: JSON_HEADERS }
  );
}
