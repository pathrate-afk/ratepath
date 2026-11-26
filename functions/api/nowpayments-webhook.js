/**
 * RatePath (ريت باث) — Cloudflare Pages Function: `POST /api/nowpayments-webhook`
 *
 * Official NOWPayments IPN Webhook Handler:
 * 1. Verifies `x-nowpayments-sig` HMAC-SHA512 signature over recursively sorted JSON keys
 *    using `env.NOWPAYMENTS_IPN_SECRET` (constant-time compare).
 * 2. On `finished` / `confirmed` ($10/mo USDT payment):
 *    - Generates a single-use (`member_limit: 1`, 24h expiry) Telegram VIP invite link
 *      for `TELEGRAM_VIP_CHANNEL_AR` or `TELEGRAM_VIP_CHANNEL_EN`.
 *    - Updates `public.vip_subscriptions` (`payment_status='finished'`, `expires_at=+30 days`).
 *    - Delivers the private VIP invite link directly to the subscriber on Telegram (if ID known)
 *      and sends an instant revenue confirmation to `ADMIN_CHAT_ID`.
 */

const JSON_HEADERS = {
  "Content-Type": "application/json; charset=utf-8",
  "Cache-Control": "no-store",
  "X-Content-Type-Options": "nosniff",
  "X-Robots-Tag": "noindex",
};
const SUPABASE_SERVER_UA = "RatePath-Edge/2.0 (server)";
const COMPLETED_STATUSES = new Set(["finished", "confirmed"]);
const SUBSCRIPTION_DAYS = 30;

export function sortObjectKeysDeep(val) {
  if (Array.isArray(val)) return val.map(sortObjectKeysDeep);
  if (val && typeof val === "object") {
    const sorted = {};
    for (const key of Object.keys(val).sort()) {
      sorted[key] = sortObjectKeysDeep(val[key]);
    }
    return sorted;
  }
  return val;
}

function timingSafeEqualHex(a, b) {
  const s1 = String(a || "").trim().toLowerCase();
  const s2 = String(b || "").trim().toLowerCase();
  if (!s1 || !s2 || s1.length !== s2.length) return false;
  let diff = 0;
  for (let i = 0; i < s1.length; i++) {
    diff |= s1.charCodeAt(i) ^ s2.charCodeAt(i);
  }
  return diff === 0;
}

export async function verifyNowPaymentsSignature(payloadObj, sigHeader, ipnSecret) {
  const secret = String(ipnSecret || "").trim();
  const provided = String(sigHeader || "").trim().toLowerCase();
  if (!secret || secret.toLowerCase() === "placeholder" || !provided) return false;

  const canonicalJson = JSON.stringify(sortObjectKeysDeep(payloadObj));
  const enc = new TextEncoder();
  const cryptoKey = await crypto.subtle.importKey(
    "raw",
    enc.encode(secret),
    { name: "HMAC", hash: "SHA-512" },
    false,
    ["sign"]
  );
  const sigBuf = await crypto.subtle.sign("HMAC", cryptoKey, enc.encode(canonicalJson));
  const computedHex = Array.from(new Uint8Array(sigBuf))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
  return timingSafeEqualHex(computedHex, provided);
}

async function createSingleUseVipInviteLink(channelId, botToken, fetchImpl = fetch) {
  if (!channelId || !botToken) return null;
  const expireDate = Math.floor(Date.now() / 1000) + 86400; // 24-hour single-use invite
  const resp = await fetchImpl(`https://api.telegram.org/bot${botToken}/createChatInviteLink`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      chat_id: channelId,
      member_limit: 1,
      expire_date: expireDate,
      name: `RatePath VIP USDT ${new Date().toISOString().slice(0, 10)}`,
    }),
  });
  if (!resp.ok) return null;
  const data = await resp.json();
  return data?.ok ? String(data?.result?.invite_link || "") || null : null;
}

async function sendTelegramMessage(chatId, text, botToken, fetchImpl = fetch) {
  if (!chatId || !botToken) return false;
  try {
    const resp = await fetchImpl(`https://api.telegram.org/bot${botToken}/sendMessage`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        chat_id: chatId,
        text,
        parse_mode: "HTML",
        disable_web_page_preview: true,
      }),
    });
    return resp.ok;
  } catch {
    return false;
  }
}

export async function processNowPaymentsIpn(ipnPayload, env, fetchImpl = fetch) {
  const orderId = String(ipnPayload?.order_id || "").trim();
  const paymentStatus = String(ipnPayload?.payment_status || "").trim().toLowerCase();
  const paymentId = ipnPayload?.payment_id != null ? String(ipnPayload.payment_id) : null;
  const priceAmount = Number(ipnPayload?.price_amount || 10);

  if (!orderId) {
    return { handled: false, reason: "missing_order_id" };
  }

  const supaUrl = String(env?.SUPABASE_URL || "").trim().replace(/\/+$/, "");
  const supaKey = String(env?.SUPABASE_KEY || "").trim();
  const hasSupa =
    Boolean(supaUrl && supaKey) &&
    supaUrl.toLowerCase() !== "placeholder" &&
    supaKey.toLowerCase() !== "placeholder";
  const restBase = supaUrl.endsWith("/rest/v1") ? supaUrl : `${supaUrl}/rest/v1`;
  const supaHeaders = {
    apikey: supaKey,
    Authorization: `Bearer ${supaKey}`,
    "User-Agent": SUPABASE_SERVER_UA,
    "Content-Type": "application/json",
  };

  let existingRow = null;
  if (hasSupa) {
    try {
      const rGet = await fetchImpl(
        `${restBase}/vip_subscriptions?order_id=eq.${encodeURIComponent(orderId)}&select=*&limit=1`,
        { headers: supaHeaders }
      );
      if (rGet.ok) {
        const rows = await rGet.json();
        if (Array.isArray(rows) && rows.length > 0) existingRow = rows[0];
      }
    } catch {
      // Continue safely
    }
  }

  // Idempotency guard: if invite link already issued for this order, do not issue duplicate
  if (existingRow?.invite_link_issued && COMPLETED_STATUSES.has(String(existingRow.payment_status || ""))) {
    return { handled: true, status: "already_fulfilled", order_id: orderId };
  }

  const isPaid = COMPLETED_STATUSES.has(paymentStatus);
  let inviteLink = existingRow?.invite_link_issued || null;
  let expiresAt = existingRow?.expires_at || null;

  const botToken = String(env?.TELEGRAM_BOT_TOKEN || "").trim();
  const lang = existingRow?.lang === "en" ? "en" : "ar";
  const resolvedTier =
    existingRow?.plan_tier ||
    (orderId.startsWith("RPBOT-") || priceAmount >= 19
      ? "monthly_personal_bot"
      : "monthly_business_vip");

  if (isPaid) {
    expiresAt = new Date(Date.now() + SUBSCRIPTION_DAYS * 86400 * 1000).toISOString();
    const targetVipChannel =
      lang === "en" ? env?.TELEGRAM_VIP_CHANNEL_EN : env?.TELEGRAM_VIP_CHANNEL_AR;
    if (botToken && targetVipChannel) {
      inviteLink = await createSingleUseVipInviteLink(targetVipChannel, botToken, fetchImpl);
    }
  }

  if (hasSupa) {
    const patchBody = {
      payment_status: paymentStatus || "waiting",
      payment_id: paymentId || existingRow?.payment_id || null,
      invite_link_issued: inviteLink,
      expires_at: expiresAt,
    };
    try {
      if (existingRow) {
        await fetchImpl(
          `${restBase}/vip_subscriptions?order_id=eq.${encodeURIComponent(orderId)}`,
          {
            method: "PATCH",
            headers: { ...supaHeaders, Prefer: "return=minimal" },
            body: JSON.stringify(patchBody),
          }
        );
      } else {
        await fetchImpl(`${restBase}/vip_subscriptions`, {
          method: "POST",
          headers: { ...supaHeaders, Prefer: "return=minimal" },
          body: JSON.stringify({
            order_id: orderId,
            amount_usd: Number.isFinite(priceAmount) ? priceAmount : 10,
            plan_tier: resolvedTier,
            lang,
            ...patchBody,
          }),
        });
      }

      // Sync active paid tier into user_tracker_profiles when paid
      if (isPaid) {
        if (existingRow?.user_email) {
          await fetchImpl(
            `${restBase}/user_tracker_profiles?email=eq.${encodeURIComponent(existingRow.user_email)}`,
            {
              method: "PATCH",
              headers: { ...supaHeaders, Prefer: "return=minimal" },
              body: JSON.stringify({ plan_tier: resolvedTier }),
            }
          ).catch(() => {});
        }
        if (existingRow?.telegram_user_id) {
          await fetchImpl(
            `${restBase}/user_tracker_profiles?telegram_chat_id=eq.${encodeURIComponent(existingRow.telegram_user_id)}`,
            {
              method: "PATCH",
              headers: { ...supaHeaders, Prefer: "return=minimal" },
              body: JSON.stringify({ plan_tier: resolvedTier }),
            }
          ).catch(() => {});
        }
      }
    } catch {
      // Logged best-effort
    }
  }

  if (isPaid && botToken) {
    const tgUserId = existingRow?.telegram_user_id;
    const isPersonalBot = resolvedTier === "monthly_personal_bot";
    if (tgUserId && inviteLink) {
      const userMsg =
        lang === "ar"
          ? `✅ <b>تم تفعيل اشتراكك في ${isPersonalBot ? "البوت الشخصي الذكي Pro + قناة VIP (19$ USDT)" : "قناة ريت باث لرجال الأعمال VIP (10$ USDT)"}!</b>\n\n🔑 رابط الدخول الخاص بك (للاستخدام مرة واحدة خلال 24 ساعة):\n${inviteLink}\n\n🤖 يمكنك الآن إدارة قائمتك الخاصة للعملات والذهب والأسهم من https://ratepathlive.com أو كتابة /watchlist هنا.\n\nℹ️ <i>تنويه: الخدمة معلوماتية وتقنية لمتابعة الأرقام والأخبار فقط وليست توصية استثمارية.</i>`
          : `✅ <b>Your RatePath ${isPersonalBot ? "Personal AI Tracker Bot Pro + VIP ($19 USDT)" : "Business VIP ($10 USDT)"} Subscription is Active!</b>\n\n🔑 Your private single-use invite link (valid 24h):\n${inviteLink}\n\n🤖 Manage your custom currencies, metals &amp; stocks watchlist at https://ratepathlive.com or send /watchlist here.\n\nℹ️ <i>Informational market monitoring service only — not investment advice.</i>`;
      await sendTelegramMessage(tgUserId, userMsg, botToken, fetchImpl);
    }

    const adminId = String(env?.ADMIN_CHAT_ID || "").trim();
    if (adminId && adminId.toLowerCase() !== "placeholder") {
      const adminMsg =
        `💰 <b>New RatePath USDT Payment Confirmed!</b>\n` +
        `• Plan: <code>${resolvedTier}</code>\n` +
        `• Order: <code>${orderId}</code>\n` +
        `• Amount: <code>$${priceAmount} USD</code> (${String(ipnPayload?.pay_currency || "USDT").toUpperCase()})\n` +
        `• Lang: <code>${lang.toUpperCase()}</code>\n` +
        `• Invite Issued: ${inviteLink ? "✅ Yes" : "⚠️ Manual follow-up"}`;
      await sendTelegramMessage(adminId, adminMsg, botToken, fetchImpl);
    }
  }

  return {
    handled: true,
    status: isPaid ? "fulfilled" : paymentStatus,
    order_id: orderId,
    invite_issued: Boolean(inviteLink),
  };
}

export async function onRequestPost(context) {
  const { request, env } = context;
  let payload;
  try {
    payload = await request.json();
  } catch {
    return new Response(JSON.stringify({ ok: false, error: "invalid_json" }), {
      status: 400,
      headers: JSON_HEADERS,
    });
  }

  const sig = request.headers.get("x-nowpayments-sig");
  const valid = await verifyNowPaymentsSignature(payload, sig, env?.NOWPAYMENTS_IPN_SECRET);
  if (!valid) {
    return new Response(JSON.stringify({ ok: false, error: "invalid_ipn_signature" }), {
      status: 401,
      headers: JSON_HEADERS,
    });
  }

  try {
    const result = await processNowPaymentsIpn(payload, env, fetch);
    return new Response(JSON.stringify({ ok: true, ...result }), {
      status: 200,
      headers: JSON_HEADERS,
    });
  } catch {
    return new Response(JSON.stringify({ ok: false, error: "internal_error" }), {
      status: 200,
      headers: JSON_HEADERS,
    });
  }
}
