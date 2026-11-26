/**
 * RatePath (ريت باث) — Cloudflare Pages Function: `POST /api/create-vip-checkout`
 *
 * Creates a live $10.00 USD / month USDT invoice via NOWPayments (`POST /v1/invoice`)
 * settled directly into the owner's configured NOWPayments payout USDT wallet,
 * and logs a `waiting` subscription record in Supabase `public.vip_subscriptions`.
 *
 * Required Cloudflare Pages Secrets:
 * - `NOWPAYMENTS_API_KEY` (from NOWPayments Dashboard -> Store Settings -> API Keys)
 * - `SUPABASE_URL`, `SUPABASE_KEY` (service/secret key for server-side subscription logging)
 * - `PUBLIC_SITE_URL` (e.g. https://yourdomain.com)
 */

const VIP_MONTHLY_PRICE_USD = 10;
const ALLOWED_USDT_NETWORKS = new Set([
  "usdttrc20", // USDT TRC-20 (TRON — default, lowest fee & widest MENA/global usage)
  "usdtbsc",   // USDT BEP-20 (BNB Smart Chain)
  "usdton",    // USDT TON (Telegram native network)
  "usdtarb",   // USDT Arbitrum
  "usdtmatic", // USDT Polygon
]);

const JSON_HEADERS = {
  "Content-Type": "application/json; charset=utf-8",
  "Cache-Control": "no-store",
  "X-Content-Type-Options": "nosniff",
  "X-Robots-Tag": "noindex",
};
const SUPABASE_SERVER_UA = "RatePath-Edge/2.0 (server)";

function withTimeout(init = {}, ms = 10000) {
  if (typeof AbortSignal !== "undefined" && typeof AbortSignal.timeout === "function") {
    return { ...init, signal: AbortSignal.timeout(ms) };
  }
  return init;
}

export function normalizePayCurrency(raw) {
  const cleaned = String(raw || "usdttrc20").trim().toLowerCase();
  return ALLOWED_USDT_NETWORKS.has(cleaned) ? cleaned : "usdttrc20";
}

export function generateOrderId() {
  const rand = crypto.randomUUID().replace(/-/g, "").slice(0, 12).toUpperCase();
  return `RPVIP-${Date.now().toString(36).toUpperCase()}-${rand}`;
}

export async function createVipCheckoutSession(body, env, fetchImpl = fetch) {
  const apiKey = String(env?.NOWPAYMENTS_API_KEY || "").trim();
  if (!apiKey || apiKey.toLowerCase() === "placeholder") {
    return {
      status: 503,
      payload: {
        ok: false,
        error: "nowpayments_not_configured",
        message: "NOWPayments API key is not configured on Cloudflare Pages yet.",
      },
    };
  }

  const lang = String(body?.lang || "ar").toLowerCase() === "en" ? "en" : "ar";
  const payCurrency = normalizePayCurrency(body?.pay_currency);
  const rawEmail = String(body?.email || "").trim().toLowerCase();
  const userEmail = /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(rawEmail) ? rawEmail : null;
  const rawTgId = String(body?.telegram_user_id || "").trim().replace(/^@/, "");
  const telegramUserId = /^\d{5,20}$/.test(rawTgId) ? rawTgId : null;

  const siteBase = String(env?.PUBLIC_SITE_URL || "https://ratepath.io")
    .trim()
    .replace(/\/+$/, "");
  const orderId = generateOrderId();

  const invoiceReq = {
    price_amount: VIP_MONTHLY_PRICE_USD,
    price_currency: "usd",
    pay_currency: payCurrency,
    order_id: orderId,
    order_description:
      lang === "ar"
        ? "اشتراك قناة ريت باث لرجال الأعمال VIP (30 يوماً — USDT)"
        : "RatePath Business VIP Channel Subscription (30 Days — USDT)",
    ipn_callback_url: `${siteBase}/api/nowpayments-webhook`,
    success_url: `${siteBase}/?lang=${lang}&vip_checkout=success&order=${encodeURIComponent(orderId)}`,
    cancel_url: `${siteBase}/?lang=${lang}&vip_checkout=cancelled`,
    is_fixed_rate: true,
    is_fee_paid_by_user: true,
  };

  const npResp = await fetchImpl(
    "https://api.nowpayments.io/v1/invoice",
    withTimeout({
      method: "POST",
      headers: {
        "x-api-key": apiKey,
        "Content-Type": "application/json",
      },
      body: JSON.stringify(invoiceReq),
    })
  );

  if (!npResp.ok) {
    return {
      status: 502,
      payload: {
        ok: false,
        error: "nowpayments_upstream_error",
      },
    };
  }

  const npData = await npResp.json();
  const invoiceUrl = String(npData?.invoice_url || "").trim();
  const paymentId = npData?.id != null ? String(npData.id) : null;
  if (!invoiceUrl.startsWith("https://")) {
    return {
      status: 502,
      payload: { ok: false, error: "invalid_invoice_url" },
    };
  }

  // Persist pending order into Supabase `public.vip_subscriptions` (non-blocking if offline)
  const supaUrl = String(env?.SUPABASE_URL || "").trim().replace(/\/+$/, "");
  const supaKey = String(env?.SUPABASE_KEY || "").trim();
  if (
    supaUrl &&
    supaKey &&
    supaUrl.toLowerCase() !== "placeholder" &&
    supaKey.toLowerCase() !== "placeholder"
  ) {
    const restBase = supaUrl.endsWith("/rest/v1") ? supaUrl : `${supaUrl}/rest/v1`;
    try {
      await fetchImpl(
        `${restBase}/vip_subscriptions`,
        withTimeout({
          method: "POST",
          headers: {
            apikey: supaKey,
            Authorization: `Bearer ${supaKey}`,
            "User-Agent": SUPABASE_SERVER_UA,
            "Content-Type": "application/json",
            Prefer: "return=minimal",
          },
          body: JSON.stringify({
            order_id: orderId,
            payment_id: paymentId,
            user_email: userEmail,
            telegram_user_id: telegramUserId,
            plan_tier: "monthly_business_vip",
            lang,
            amount_usd: VIP_MONTHLY_PRICE_USD,
            payment_status: "waiting",
          }),
        })
      );
    } catch {
      // Invoice already issued; webhook will reconcile order_id
    }
  }

  return {
    status: 200,
    payload: {
      ok: true,
      order_id: orderId,
      invoice_url: invoiceUrl,
      amount_usd: VIP_MONTHLY_PRICE_USD,
      pay_currency: payCurrency,
    },
  };
}

export async function onRequestPost(context) {
  let body;
  try {
    body = await context.request.json();
  } catch {
    return new Response(JSON.stringify({ ok: false, error: "invalid_json" }), {
      status: 400,
      headers: JSON_HEADERS,
    });
  }

  try {
    const res = await createVipCheckoutSession(body, context.env, fetch);
    return new Response(JSON.stringify(res.payload), {
      status: res.status,
      headers: JSON_HEADERS,
    });
  } catch {
    return new Response(JSON.stringify({ ok: false, error: "internal_error" }), {
      status: 500,
      headers: JSON_HEADERS,
    });
  }
}
