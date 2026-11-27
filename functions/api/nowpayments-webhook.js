/**
 * RatePath (ريت باث) — Cloudflare Pages Function: `POST /api/nowpayments-webhook`
 *
 * Official NOWPayments IPN Webhook Handler:
 * 1. Verifies `x-nowpayments-sig` HMAC-SHA512 signature over recursively sorted JSON keys
 *    using `env.NOWPAYMENTS_IPN_SECRET` (constant-time compare).
 * 2. Parses `order_id` metadata (`{telegram_user_id}_{plan}_{timestamp}` OR legacy `RPVIP-/RPBOT-`).
 * 3. On `finished` / `confirmed` payment status:
 *    - Upgrades the user's tier (`monthly_business_vip` $10/mo -> 10 assets, `monthly_personal_bot` $19/mo -> 50 assets)
 *      and sets `expires_at = now + 30 days` across Supabase (`vip_subscriptions`, `user_tracker_profiles`,
 *      `telegram_bot_users`) AND Upstash Redis (`ratepath:tg_user:{telegram_user_id}`).
 *    - Generates a single-use (`member_limit: 1`, 24h expiry) Telegram VIP invite link
 *      (using `TELEGRAM_VIP_CHANNEL_AR`/`TELEGRAM_VIP_CHANNEL_EN` or `VIP_CHANNEL_ID`).
 *    - Delivers instant confirmation + private VIP invite link directly to the user on Telegram
 *      and notifies `ADMIN_CHAT_ID`.
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
const TG_USER_REDIS_PREFIX = "ratepath:tg_user:";
const TG_USERS_INDEX_REDIS_KEY = "ratepath:tg_users:index";

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

export function parseOrderIdMetadata(orderId, priceAmount = 10) {
  const raw = String(orderId || "").trim();
  const structured = raw.match(
    /^(\d{5,20})_(vip10|pro19|monthly_business_vip|monthly_personal_bot)_(\d+)$/i
  );
  if (structured) {
    const tgId = structured[1];
    const planTok = structured[2].toLowerCase();
    const tier =
      planTok === "pro19" || planTok === "monthly_personal_bot"
        ? "monthly_personal_bot"
        : "monthly_business_vip";
    return { telegramUserId: tgId, planTier: tier };
  }
  const fallbackTier =
    raw.startsWith("RPBOT-") || Number(priceAmount) >= 19
      ? "monthly_personal_bot"
      : "monthly_business_vip";
  return { telegramUserId: null, planTier: fallbackTier };
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

async function upstashCmd(env, cmdArray, fetchImpl = fetch) {
  const url = String(env?.UPSTASH_REDIS_REST_URL || "").trim().replace(/\/+$/, "");
  const token = String(env?.UPSTASH_REDIS_REST_TOKEN || "").trim();
  if (!url || !token || url.toLowerCase() === "placeholder" || token.toLowerCase() === "placeholder") {
    return null;
  }
  try {
    const resp = await fetchImpl(url, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${token}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify(cmdArray),
    });
    if (!resp.ok) return null;
    const data = await resp.json();
    return data?.result ?? null;
  } catch {
    return null;
  }
}

async function createSingleUseVipInviteLink(channelId, botToken, fetchImpl = fetch) {
  if (!channelId || !botToken || String(channelId).toLowerCase() === "placeholder") return null;
  const expireDate = Math.floor(Date.now() / 1000) + 86400; // 24-hour single-use invite
  try {
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
  } catch {
    return null;
  }
}

async function sendTelegramMessage(chatId, text, botToken, fetchImpl = fetch, replyMarkup = null) {
  if (!chatId || !botToken) return false;
  try {
    const payload = {
      chat_id: chatId,
      text,
      parse_mode: "HTML",
      disable_web_page_preview: true,
    };
    if (replyMarkup) payload.reply_markup = replyMarkup;
    const resp = await fetchImpl(`https://api.telegram.org/bot${botToken}/sendMessage`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
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

  const parsedMeta = parseOrderIdMetadata(orderId, priceAmount);

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

  // Idempotency guard: if already fulfilled for this order, skip duplicate fulfillment
  if (
    existingRow &&
    COMPLETED_STATUSES.has(String(existingRow.payment_status || "")) &&
    (existingRow.invite_link_issued || existingRow.expires_at)
  ) {
    return { handled: true, status: "already_fulfilled", order_id: orderId };
  }

  const isPaid = COMPLETED_STATUSES.has(paymentStatus);
  let inviteLink = existingRow?.invite_link_issued || null;
  let expiresAt = existingRow?.expires_at || null;

  const botToken = String(env?.TELEGRAM_BOT_TOKEN || "").trim();
  const tgUserId =
    String(existingRow?.telegram_user_id || parsedMeta.telegramUserId || "").trim() || null;

  // Detect preferred language from Redis profile if not stored on subscription row
  let cachedRedisProfile = null;
  if (tgUserId) {
    const rawCached = await upstashCmd(env, ["GET", `${TG_USER_REDIS_PREFIX}${tgUserId}`], fetchImpl);
    if (typeof rawCached === "string" && rawCached.trim()) {
      try {
        cachedRedisProfile = JSON.parse(rawCached);
      } catch {
        cachedRedisProfile = null;
      }
    }
  }

  const lang =
    existingRow?.lang === "en" || cachedRedisProfile?.lang === "en" ? "en" : "ar";
  const resolvedTier = existingRow?.plan_tier || parsedMeta.planTier;

  if (isPaid) {
    expiresAt = new Date(Date.now() + SUBSCRIPTION_DAYS * 86400 * 1000).toISOString();
    const targetVipChannel =
      (lang === "en" ? env?.TELEGRAM_VIP_CHANNEL_EN : env?.TELEGRAM_VIP_CHANNEL_AR) ||
      env?.VIP_CHANNEL_ID ||
      env?.TELEGRAM_VIP_CHANNEL_AR ||
      env?.TELEGRAM_VIP_CHANNEL_EN;
    if (botToken && targetVipChannel) {
      inviteLink = await createSingleUseVipInviteLink(targetVipChannel, botToken, fetchImpl);
    }
  }

  if (hasSupa) {
    const patchBody = {
      payment_status: paymentStatus || "waiting",
      payment_id: paymentId || existingRow?.payment_id || null,
      telegram_user_id: tgUserId,
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

      // Sync active paid tier into user_tracker_profiles & telegram_bot_users when paid
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
        if (tgUserId) {
          await fetchImpl(
            `${restBase}/user_tracker_profiles?telegram_chat_id=eq.${encodeURIComponent(tgUserId)}`,
            {
              method: "PATCH",
              headers: { ...supaHeaders, Prefer: "return=minimal" },
              body: JSON.stringify({ plan_tier: resolvedTier }),
            }
          ).catch(() => {});

          await fetchImpl(`${restBase}/telegram_bot_users?on_conflict=telegram_user_id`, {
            method: "POST",
            headers: { ...supaHeaders, Prefer: "resolution=merge-duplicates,return=minimal" },
            body: JSON.stringify({
              telegram_user_id: tgUserId,
              lang,
              plan_tier: resolvedTier,
              expires_at: expiresAt,
              expiry_reminded_at: null,
              alerts_paused: false,
            }),
          }).catch(() => {});
        }
      }
    } catch {
      // Logged best-effort
    }
  }

  // Sync instant paid tier upgrade into Upstash Redis hot profile (<50ms unlock in bot!)
  if (isPaid && tgUserId) {
    const nextRedisProfile = {
      ...(cachedRedisProfile && typeof cachedRedisProfile === "object" ? cachedRedisProfile : {}),
      telegram_user_id: tgUserId,
      lang,
      plan_tier: resolvedTier,
      expires_at: expiresAt,
      alerts_paused: false,
      watchlist: Array.isArray(cachedRedisProfile?.watchlist) ? cachedRedisProfile.watchlist : [],
      updated_at: new Date().toISOString(),
    };
    await upstashCmd(
      env,
      ["SET", `${TG_USER_REDIS_PREFIX}${tgUserId}`, JSON.stringify(nextRedisProfile)],
      fetchImpl
    );
    await upstashCmd(env, ["SADD", TG_USERS_INDEX_REDIS_KEY, tgUserId], fetchImpl);
  }

  if (isPaid && botToken) {
    const isPersonalBot = resolvedTier === "monthly_personal_bot";
    const maxSlots = isPersonalBot ? 50 : 10;
    if (tgUserId) {
      const inviteBlockAr = inviteLink
        ? `\n\n🔑 رابط الدخول الخاص لقناة VIP (للاستخدام مرة واحدة خلال 24 ساعة):\n${inviteLink}`
        : "";
      const inviteBlockEn = inviteLink
        ? `\n\n🔑 Your private single-use VIP channel invite link (valid 24h):\n${inviteLink}`
        : "";
      const userMsg =
        lang === "ar"
          ? `✅ <b>تم تفعيل اشتراكك بنجاح!</b>\n\n` +
            `💎 الباقة: <b>${isPersonalBot ? "البوت الشخصي الذكي Pro + VIP (19$ USDT)" : "Business VIP + البوت الشخصي (10$ USDT)"}</b>\n` +
            `📊 الحد النشط الآن: <b>${maxSlots} أصول</b> مع أولوية التنبيهات الفورية لمدة 30 يوماً.` +
            `${inviteBlockAr}\n\n` +
            `🤖 اضغط الزر أدناه لإضافة أصولك الآن أو أرسل /watchlist.\n\n` +
            `⚖️ <i>خدمة برمجية لمتابعة البيانات وتنبيهات الأسعار فقط وليست نصيحة استثمارية.</i>`
          : `✅ <b>Your Subscription Is Now Active!</b>\n\n` +
            `💎 Plan: <b>${isPersonalBot ? "Personal Bot Pro + VIP ($19 USDT)" : "Business VIP + Personal Bot ($10 USDT)"}</b>\n` +
            `📊 Active Limit: <b>${maxSlots} assets</b> with priority breakout alerts for 30 days.` +
            `${inviteBlockEn}\n\n` +
            `🤖 Tap below to add your assets now or send /watchlist.\n\n` +
            `⚖️ <i>Informational data &amp; price-alert service only — not investment advice.</i>`;
      const markup = {
        inline_keyboard: [
          [
            {
              text: lang === "ar" ? "➕ إضافة أصل الآن" : "➕ Add Asset Now",
              callback_data: "cb:add_menu",
            },
            {
              text: lang === "ar" ? "📊 قائمة متابعتي" : "📊 My Watchlist",
              callback_data: "cb:watchlist",
            },
          ],
        ],
      };
      await sendTelegramMessage(tgUserId, userMsg, botToken, fetchImpl, markup);
    }

    const adminId = String(env?.ADMIN_CHAT_ID || "").trim();
    if (adminId && adminId.toLowerCase() !== "placeholder") {
      const adminMsg =
        `💰 <b>New RatePath USDT Payment Confirmed!</b>\n` +
        `• Plan: <code>${resolvedTier}</code>\n` +
        `• User TG ID: <code>${tgUserId || "web_only"}</code>\n` +
        `• Order: <code>${orderId}</code>\n` +
        `• Amount: <code>$${priceAmount} USD</code> (${String(ipnPayload?.pay_currency || "USDT").toUpperCase()})\n` +
        `• Expires: <code>${String(expiresAt || "").slice(0, 10)}</code>\n` +
        `• Invite Issued: ${inviteLink ? "✅ Yes" : "⚠️ Manual follow-up"}`;
      await sendTelegramMessage(adminId, adminMsg, botToken, fetchImpl);
    }
  }

  return {
    handled: true,
    status: isPaid ? "fulfilled" : paymentStatus,
    order_id: orderId,
    telegram_user_id: tgUserId,
    plan_tier: resolvedTier,
    expires_at: expiresAt,
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
