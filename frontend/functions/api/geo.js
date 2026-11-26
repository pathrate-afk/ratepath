/**
 * RatePath (ريت باث) — Cloudflare Pages Function: `GET /api/geo`
 *
 * Zero-cost, zero-cookie visitor country detection via Cloudflare Edge (`request.cf.country`).
 * Enables geo-compliant affiliate routing so visitors NEVER see exchanges or payment apps
 * blocked in their jurisdiction (e.g. US, UK, CA, EU, MENA, TR, SY).
 */

const GEO_HEADERS = {
  "Content-Type": "application/json; charset=utf-8",
  "Cache-Control": "private, max-age=3600",
  "X-Content-Type-Options": "nosniff",
  "X-Robots-Tag": "noindex",
};

export async function onRequestGet(context) {
  const cf = context?.request?.cf || {};
  const rawCountry = String(cf.country || "").trim().toUpperCase();
  const country = /^[A-Z]{2}$/.test(rawCountry) ? rawCountry : "GLOBAL";
  const continent = String(cf.continent || "").trim().toUpperCase() || null;

  return new Response(
    JSON.stringify({
      ok: true,
      country,
      continent,
    }),
    { status: 200, headers: GEO_HEADERS }
  );
}
