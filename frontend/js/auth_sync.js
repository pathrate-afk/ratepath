/**
 * RatePath (ريت باث) — Zero-Dependency Supabase Email OTP / Magic Link Auth & Cloud Sync
 *
 * Security Invariants:
 * - Strictly refuses privileged keys (`sb_secret_*` or JWT `"role": "service_role"`).
 * - Syncs `layout_config` into `public.user_preferences` (`user_id`, `email`, `layout_config`, `updated_at`)
 *   protected by Supabase Row-Level Security (`auth.uid() = user_id`).
 */

import {
  FEATURE_FLAGS,
  MONETIZATION_CONFIG,
  SUPABASE_ANON_KEY,
  SUPABASE_PUBLIC_ANON_KEY,
  SUPABASE_URL,
} from "../monetization.config.js";

const SESSION_STORAGE_KEY = "ratepath_supabase_session_v1";

function getSupabaseUrl() {
  return String(SUPABASE_URL || MONETIZATION_CONFIG?.SUPABASE_URL || "")
    .trim()
    .replace(/\/+$/, "");
}

function getSupabaseAnonKey() {
  return String(
    SUPABASE_ANON_KEY ||
      SUPABASE_PUBLIC_ANON_KEY ||
      MONETIZATION_CONFIG?.SUPABASE_ANON_KEY ||
      MONETIZATION_CONFIG?.SUPABASE_PUBLIC_ANON_KEY ||
      ""
  ).trim();
}

/**
 * Verify that the configured frontend key is strictly a safe public anon/publishable key.
 * NEVER permits `sb_secret_*` or `service_role` JWTs in browser requests.
 */
export function isSafePublicSupabaseKey(key = getSupabaseAnonKey()) {
  const cleaned = String(key || "").trim();
  if (!cleaned) return false;
  const low = cleaned.toLowerCase();
  if (
    low === "placeholder" ||
    low === "supabase_public_anon_key" ||
    low.startsWith("placeholder_") ||
    low.startsWith("your_") ||
    low.startsWith("sb_secret_")
  ) {
    return false;
  }
  if (cleaned.startsWith("sb_publishable_")) {
    return cleaned.length > 20;
  }
  const parts = cleaned.split(".");
  if (parts.length === 3) {
    try {
      const b64 = parts[1].replace(/-/g, "+").replace(/_/g, "/");
      const padded = b64 + "=".repeat((4 - (b64.length % 4)) % 4);
      const claims = JSON.parse(atob(padded));
      return claims?.role === "anon";
    } catch {
      return false;
    }
  }
  return false;
}

export function isCloudSyncConfigured() {
  if (FEATURE_FLAGS?.showCloudSync === false || MONETIZATION_CONFIG?.FEATURE_FLAGS?.showCloudSync === false) {
    return false;
  }
  const url = getSupabaseUrl();
  return /^https:\/\/[a-z0-9-]+\.supabase\.co$/i.test(url) && isSafePublicSupabaseKey();
}

export function getStoredSession() {
  try {
    const raw = localStorage.getItem(SESSION_STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    if (!parsed?.access_token || !parsed?.user?.id) return null;
    return parsed;
  } catch {
    return null;
  }
}

export function saveStoredSession(session) {
  try {
    if (!session) {
      localStorage.removeItem(SESSION_STORAGE_KEY);
      return;
    }
    localStorage.setItem(SESSION_STORAGE_KEY, JSON.stringify(session));
  } catch {
    // Ignore storage failures
  }
}

export function clearStoredSession() {
  saveStoredSession(null);
}

function buildAuthHeaders(accessToken = null) {
  const anonKey = getSupabaseAnonKey();
  const headers = {
    apikey: anonKey,
    "Content-Type": "application/json",
  };
  if (accessToken) {
    headers.Authorization = `Bearer ${accessToken}`;
  } else if (!anonKey.startsWith("sb_publishable_")) {
    headers.Authorization = `Bearer ${anonKey}`;
  }
  return headers;
}

async function fetchUserProfile(accessToken) {
  const resp = await fetch(`${getSupabaseUrl()}/auth/v1/user`, {
    headers: buildAuthHeaders(accessToken),
  });
  if (!resp.ok) return null;
  return resp.json();
}

/**
 * Consume Magic Link tokens returned in URL hash (`#access_token=...&refresh_token=...`).
 */
export async function consumeMagicLinkRedirect() {
  if (!isCloudSyncConfigured()) return null;
  const hash = String(window.location.hash || "");
  if (!hash.includes("access_token=")) return null;

  const params = new URLSearchParams(hash.replace(/^#/, ""));
  const accessToken = params.get("access_token");
  const refreshToken = params.get("refresh_token") || "";
  const expiresIn = Number(params.get("expires_in") || "3600");
  if (!accessToken) return null;

  try {
    const user = await fetchUserProfile(accessToken);
    if (!user?.id) return null;
    const session = {
      access_token: accessToken,
      refresh_token: refreshToken,
      expires_at: Math.floor(Date.now() / 1000) + expiresIn,
      user: { id: user.id, email: user.email || "" },
    };
    saveStoredSession(session);
    // Clean tokens from browser address bar immediately
    if (window.history?.replaceState) {
      window.history.replaceState({}, "", window.location.pathname + window.location.search);
    }
    return session;
  } catch {
    return null;
  }
}

export async function ensureFreshSession() {
  if (!isCloudSyncConfigured()) return null;

  const magicSession = await consumeMagicLinkRedirect();
  if (magicSession) return magicSession;

  const current = getStoredSession();
  if (!current) return null;

  const nowSec = Math.floor(Date.now() / 1000);
  if (current.expires_at && current.expires_at - nowSec > 90) {
    return current;
  }

  if (!current.refresh_token) {
    clearStoredSession();
    return null;
  }

  try {
    const resp = await fetch(`${getSupabaseUrl()}/auth/v1/token?grant_type=refresh_token`, {
      method: "POST",
      headers: buildAuthHeaders(),
      body: JSON.stringify({ refresh_token: current.refresh_token }),
    });
    if (!resp.ok) {
      clearStoredSession();
      return null;
    }
    const data = await resp.json();
    if (!data?.access_token || !data?.user?.id) {
      clearStoredSession();
      return null;
    }
    const refreshed = {
      access_token: data.access_token,
      refresh_token: data.refresh_token || current.refresh_token,
      expires_at: Math.floor(Date.now() / 1000) + Number(data.expires_in || 3600),
      user: { id: data.user.id, email: data.user.email || current.user.email || "" },
    };
    saveStoredSession(refreshed);
    return refreshed;
  } catch {
    return current;
  }
}

/**
 * Send Supabase Email OTP / Magic Link (`signInWithOtp` REST equivalent).
 */
export async function sendEmailOtp(email) {
  if (!isCloudSyncConfigured()) {
    return { ok: false, error: "supabase_not_configured" };
  }
  const cleanEmail = String(email || "").trim().toLowerCase();
  if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(cleanEmail)) {
    return { ok: false, error: "invalid_email" };
  }

  try {
    const redirectTo = `${window.location.origin}${window.location.pathname}`;
    const resp = await fetch(
      `${getSupabaseUrl()}/auth/v1/otp?redirect_to=${encodeURIComponent(redirectTo)}`,
      {
        method: "POST",
        headers: buildAuthHeaders(),
        body: JSON.stringify({
          email: cleanEmail,
          create_user: true,
        }),
      }
    );
    if (!resp.ok) {
      const errBody = await resp.json().catch(() => ({}));
      return {
        ok: false,
        error: errBody?.msg || errBody?.error_description || `HTTP ${resp.status}`,
      };
    }
    return { ok: true, email: cleanEmail };
  } catch (err) {
    return { ok: false, error: String(err?.message || "network_error") };
  }
}

/**
 * Verify 6-digit Email OTP code returned by Supabase Auth.
 */
export async function verifyEmailOtp(email, token) {
  if (!isCloudSyncConfigured()) {
    return { ok: false, error: "supabase_not_configured" };
  }
  const cleanEmail = String(email || "").trim().toLowerCase();
  const cleanCode = String(token || "").replace(/\s+/g, "");
  if (cleanCode.length < 6) {
    return { ok: false, error: "invalid_otp" };
  }

  for (const verifyType of ["email", "magiclink", "signup"]) {
    try {
      const resp = await fetch(`${getSupabaseUrl()}/auth/v1/verify`, {
        method: "POST",
        headers: buildAuthHeaders(),
        body: JSON.stringify({
          email: cleanEmail,
          token: cleanCode,
          type: verifyType,
        }),
      });
      if (!resp.ok) continue;
      const data = await resp.json();
      if (data?.access_token && data?.user?.id) {
        const session = {
          access_token: data.access_token,
          refresh_token: data.refresh_token || "",
          expires_at: Math.floor(Date.now() / 1000) + Number(data.expires_in || 3600),
          user: { id: data.user.id, email: data.user.email || cleanEmail },
        };
        saveStoredSession(session);
        return { ok: true, session };
      }
    } catch {
      // Try next verification type
    }
  }
  return { ok: false, error: "verification_failed" };
}

export async function signOutCloudSync() {
  const current = getStoredSession();
  clearStoredSession();
  if (!current?.access_token || !isCloudSyncConfigured()) return;
  try {
    await fetch(`${getSupabaseUrl()}/auth/v1/logout`, {
      method: "POST",
      headers: buildAuthHeaders(current.access_token),
    });
  } catch {
    // Session already cleared locally
  }
}

/**
 * Fetch authenticated user's `layout_config` from `public.user_preferences`.
 */
export async function fetchCloudLayout(session) {
  if (!session?.access_token || !session?.user?.id || !isCloudSyncConfigured()) {
    return null;
  }
  try {
    const uid = encodeURIComponent(session.user.id);
    const resp = await fetch(
      `${getSupabaseUrl()}/rest/v1/user_preferences?user_id=eq.${uid}&select=user_id,email,layout_config,updated_at`,
      {
        headers: buildAuthHeaders(session.access_token),
      }
    );
    if (!resp.ok) return null;
    const rows = await resp.json();
    if (Array.isArray(rows) && rows.length > 0 && rows[0]?.layout_config) {
      return rows[0].layout_config;
    }
    return null;
  } catch {
    return null;
  }
}

/**
 * Upsert authenticated user's `layout_config` into `public.user_preferences` on `user_id`.
 */
export async function upsertCloudLayout(session, layoutConfig) {
  if (!session?.access_token || !session?.user?.id || !isCloudSyncConfigured()) {
    return { ok: false, reason: "not_authenticated" };
  }
  try {
    const resp = await fetch(
      `${getSupabaseUrl()}/rest/v1/user_preferences?on_conflict=user_id`,
      {
        method: "POST",
        headers: {
          ...buildAuthHeaders(session.access_token),
          Prefer: "resolution=merge-duplicates,return=representation",
        },
        body: JSON.stringify({
          user_id: session.user.id,
          email: session.user.email || null,
          layout_config: layoutConfig,
          updated_at: new Date().toISOString(),
        }),
      }
    );
    if (!resp.ok) {
      return { ok: false, status: resp.status };
    }
    return { ok: true };
  } catch (err) {
    return { ok: false, error: String(err?.message || "network_error") };
  }
}
