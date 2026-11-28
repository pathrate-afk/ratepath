/**
 * RatePath (ريت باث) — Local Production Server (Static Built `frontend/` + Real `functions/api/*.js`)
 *
 * Mirrors Cloudflare Pages production cleanly with ZERO mocks:
 * - Serves built static files from `frontend/` with real `_headers` security headers.
 * - Executes real Cloudflare Pages Functions (`functions/api/*.js`) against live upstream APIs.
 * - Respects real `Cache-Control: s-maxage=...` headers returned by functions (edge cache parity +
 *   in-flight request coalescing for stress testing without mutating or mocking data).
 */

import fs from "node:fs";
import http from "node:http";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const FRONTEND_DIR = path.join(ROOT, "frontend");
const FUNCTIONS_API_DIR = path.join(ROOT, "functions", "api");
const PORT = Number(process.env.PORT || 8788);
const HOST = process.env.HOST || "127.0.0.1";

const MIME_TYPES = {
  ".html": "text/html; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".js": "application/javascript; charset=utf-8",
  ".mjs": "application/javascript; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".xml": "application/xml; charset=utf-8",
  ".txt": "text/plain; charset=utf-8",
  ".svg": "image/svg+xml; charset=utf-8",
  ".png": "image/png",
  ".jpg": "image/jpeg",
  ".jpeg": "image/jpeg",
  ".webp": "image/webp",
  ".ico": "image/x-icon",
  ".woff2": "font/woff2",
};

const SECURITY_HEADERS = {
  "X-Content-Type-Options": "nosniff",
  "Referrer-Policy": "strict-origin-when-cross-origin",
  "Strict-Transport-Security": "max-age=31536000; includeSubDomains; preload",
  "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=()",
  "Cross-Origin-Opener-Policy": "same-origin-allow-popups",
  "X-Permitted-Cross-Domain-Policies": "none",
  "Content-Security-Policy":
    "default-src 'self'; script-src 'self' https://telegram.org https://static.cloudflareinsights.com; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com data:; img-src 'self' data: blob: https://s2.coinmarketcap.com https://assets.coingecko.com https://coin-images.coingecko.com https://cdn.dexscreener.com https://dd.dexscreener.com; connect-src 'self' https://raw.githubusercontent.com https://*.supabase.co https://cloudflareinsights.com; frame-src https://ad.a-ads.com; frame-ancestors 'self' https://web.telegram.org https://*.telegram.org; object-src 'none'; base-uri 'self'; form-action 'self';",
};

function loadDotEnv() {
  const env = { ...process.env };
  const envPath = path.join(ROOT, ".env");
  if (!fs.existsSync(envPath)) return env;
  const lines = fs.readFileSync(envPath, "utf-8").split(/\r?\n/);
  for (const raw of lines) {
    const line = raw.trim();
    if (!line || line.startsWith("#") || !line.includes("=")) continue;
    const eq = line.indexOf("=");
    const k = line.slice(0, eq).trim();
    let v = line.slice(eq + 1).trim();
    if (v.length >= 2 && v[0] === v[v.length - 1] && (v[0] === '"' || v[0] === "'")) {
      v = v.slice(1, -1);
    }
    if (k && !(k in env)) env[k] = v;
  }
  return env;
}

const RUNTIME_ENV = loadDotEnv();
const apiModules = new Map();
const edgeCache = new Map(); // key -> { status, headers, bodyBuf, expiresAt }
const inFlight = new Map(); // key -> Promise<{ status, headers, bodyBuf }>

async function loadApiModules() {
  if (!fs.existsSync(FUNCTIONS_API_DIR)) return;
  for (const entry of fs.readdirSync(FUNCTIONS_API_DIR, { withFileTypes: true })) {
    if (!entry.isFile() || !entry.name.endsWith(".js")) continue;
    const routeName = entry.name.slice(0, -3);
    const mod = await import(pathToFileURL(path.join(FUNCTIONS_API_DIR, entry.name)).href);
    apiModules.set(routeName, mod);
  }
}

function resolveStaticFile(pathname) {
  let clean = decodeURIComponent(pathname);
  if (clean === "/" || clean === "/ar" || clean === "/ar/" || clean === "/en" || clean === "/en/") {
    return path.join(FRONTEND_DIR, "index.html");
  }
  if (clean === "/live_feed.json") return path.join(FRONTEND_DIR, "public", "live_feed.json");
  if (clean === "/catalog_feed.json") return path.join(FRONTEND_DIR, "public", "catalog_feed.json");

  const rel = clean.replace(/^\/+/, "");
  const resolved = path.resolve(FRONTEND_DIR, rel);
  if (!resolved.startsWith(FRONTEND_DIR)) return null;

  if (fs.existsSync(resolved) && fs.statSync(resolved).isFile()) {
    return resolved;
  }
  const idxHtml = path.join(resolved, "index.html");
  if (fs.existsSync(idxHtml) && fs.statSync(idxHtml).isFile()) {
    return idxHtml;
  }
  return null;
}

function readNodeBody(req) {
  return new Promise((resolve, reject) => {
    const chunks = [];
    req.on("data", (c) => chunks.push(c));
    req.on("end", () => resolve(Buffer.concat(chunks)));
    req.on("error", reject);
  });
}

function parseSMaxAge(cacheControl) {
  const s = String(cacheControl || "");
  if (/no-store|private/i.test(s)) return 0;
  const m = s.match(/s-maxage=(\d+)/i) || s.match(/max-age=(\d+)/i);
  return m ? Number(m[1]) : 0;
}

async function handleApiRequest(req, res, urlObj) {
  const routeName = urlObj.pathname.slice("/api/".length).replace(/\/+$/, "");
  const mod = apiModules.get(routeName);
  if (!mod) {
    res.writeHead(404, { "Content-Type": "application/json; charset=utf-8", "X-Content-Type-Options": "nosniff" });
    res.end(JSON.stringify({ ok: false, error: "api_route_not_found" }));
    return;
  }

  const method = String(req.method || "GET").toUpperCase();
  const methodFnMap = {
    GET: mod.onRequestGet,
    POST: mod.onRequestPost,
    PUT: mod.onRequestPut,
    PATCH: mod.onRequestPatch,
    DELETE: mod.onRequestDelete,
    OPTIONS: mod.onRequestOptions,
  };
  const handler = methodFnMap[method] || mod.onRequest;
  if (typeof handler !== "function") {
    res.writeHead(405, { "Content-Type": "application/json; charset=utf-8" });
    res.end(JSON.stringify({ ok: false, error: "method_not_allowed" }));
    return;
  }

  const cacheKey = method === "GET" ? `${urlObj.pathname}${urlObj.search}` : null;
  if (cacheKey) {
    const hit = edgeCache.get(cacheKey);
    if (hit && hit.expiresAt > Date.now()) {
      res.writeHead(hit.status, hit.headers);
      res.end(hit.bodyBuf);
      return;
    }
    if (inFlight.has(cacheKey)) {
      const shared = await inFlight.get(cacheKey);
      res.writeHead(shared.status, shared.headers);
      res.end(shared.bodyBuf);
      return;
    }
  }

  const execPromise = (async () => {
    const bodyBuf = method === "GET" || method === "HEAD" ? undefined : await readNodeBody(req);
    const headers = new Headers();
    for (const [k, v] of Object.entries(req.headers)) {
      if (v === undefined) continue;
      headers.set(k, Array.isArray(v) ? v.join(", ") : String(v));
    }
    const webReq = new Request(urlObj.toString(), {
      method,
      headers,
      body: bodyBuf && bodyBuf.length > 0 ? bodyBuf : undefined,
    });
    // Attach Cloudflare-compatible `cf` property (defaults to GLOBAL on localhost)
    Object.defineProperty(webReq, "cf", {
      value: { country: "GLOBAL", continent: null },
      enumerable: true,
    });

    const webResp = await handler({
      request: webReq,
      env: RUNTIME_ENV,
      params: {},
      waitUntil: () => {},
    });
    const respBuf = Buffer.from(await webResp.arrayBuffer());
    const outHeaders = {};
    webResp.headers.forEach((v, k) => {
      outHeaders[k] = v;
    });
    const ttlSec = method === "GET" && webResp.status === 200 ? parseSMaxAge(outHeaders["cache-control"]) : 0;
    if (cacheKey && ttlSec > 0) {
      edgeCache.set(cacheKey, {
        status: webResp.status,
        headers: outHeaders,
        bodyBuf: respBuf,
        expiresAt: Date.now() + ttlSec * 1000,
      });
    }
    return { status: webResp.status, headers: outHeaders, bodyBuf: respBuf };
  })();

  if (cacheKey) inFlight.set(cacheKey, execPromise);
  try {
    const result = await execPromise;
    res.writeHead(result.status, result.headers);
    res.end(result.bodyBuf);
  } catch (err) {
    res.writeHead(500, { "Content-Type": "application/json; charset=utf-8" });
    res.end(JSON.stringify({ ok: false, error: err?.message || "internal_server_error" }));
  } finally {
    if (cacheKey) inFlight.delete(cacheKey);
  }
}

async function start() {
  await loadApiModules();
  const server = http.createServer(async (req, res) => {
    const urlObj = new URL(req.url || "/", `http://${HOST}:${PORT}`);
    if (urlObj.pathname.startsWith("/api/")) {
      await handleApiRequest(req, res, urlObj);
      return;
    }

    const filePath = resolveStaticFile(urlObj.pathname);
    if (!filePath) {
      const notFoundPath = path.join(FRONTEND_DIR, "404.html");
      const html404 = fs.existsSync(notFoundPath) ? fs.readFileSync(notFoundPath) : Buffer.from("404 Not Found");
      res.writeHead(404, {
        ...SECURITY_HEADERS,
        "Content-Type": "text/html; charset=utf-8",
        "X-Robots-Tag": "noindex",
      });
      res.end(html404);
      return;
    }

    const ext = path.extname(filePath).toLowerCase();
    const contentType = MIME_TYPES[ext] || "application/octet-stream";
    const data = fs.readFileSync(filePath);
    const headers = {
      ...SECURITY_HEADERS,
      "Content-Type": contentType,
      "Content-Length": String(data.length),
      "Cache-Control": ext === ".html" || ext === ".js" || ext === ".css" ? "public, max-age=0, must-revalidate" : "public, max-age=3600",
    };
    if (urlObj.pathname.endsWith("live_feed.json") || urlObj.pathname.endsWith("catalog_feed.json")) {
      headers["Access-Control-Allow-Origin"] = "*";
    }
    res.writeHead(200, headers);
    res.end(data);
  });

  server.keepAliveTimeout = 15000;
  server.listen(PORT, HOST, () => {
    console.log(`[RatePath Prod Server] Listening at http://${HOST}:${PORT} (${apiModules.size} real API functions loaded)`);
  });
}

start().catch((err) => {
  console.error("[RatePath Prod Server] Startup failed:", err);
  process.exit(1);
});
