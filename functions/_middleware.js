/**
 * RatePath (ريت باث) — Cloudflare Pages Edge Security Middleware
 * Blocks direct access to local development helper files (e.g. `/server.py`) with HTTP 404.
 */

export async function onRequest(context) {
  const url = new URL(context.request.url);
  if (url.pathname.toLowerCase().endsWith(".py")) {
    return new Response("Not Found", {
      status: 404,
      headers: {
        "Content-Type": "text/plain; charset=utf-8",
        "X-Content-Type-Options": "nosniff",
        "X-Robots-Tag": "noindex",
      },
    });
  }
  return context.next();
}
