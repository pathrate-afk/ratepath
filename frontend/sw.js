/* RatePath (ريت باث) Offline-First Service Worker (Bypassed on localhost) */
const CACHE_VERSION = "ratepath-pwa-v6";
const FEED_CACHE_NAME = "ratepath-live-feed-v2";

const IS_LOCALHOST =
  self.location.hostname === "localhost" ||
  self.location.hostname === "127.0.0.1" ||
  self.location.hostname === "[::1]";

const APP_SHELL_ASSETS = [
  "./",
  "./index.html",
  "./logo.png",
  "./favicon.ico",
  "./manifest.json",
  "./monetization.config.js",
  "./styles/terminal.css",
  "./js/decimal-math.js",
  "./js/syria-flag.js",
  "./js/i18n.js",
  "./js/quote-card.js",
  "./js/app.js",
  "./icons/logo-mark.png",
  "./icons/favicon-32x32.png",
  "./icons/apple-touch-icon.png",
  "./icons/icon-192x192.png",
  "./icons/icon-512x512.png",
  "./public/live_feed.json"
];

self.addEventListener("install", (event) => {
  if (IS_LOCALHOST) {
    self.skipWaiting();
    return;
  }
  event.waitUntil(
    caches.open(CACHE_VERSION).then((cache) => cache.addAll(APP_SHELL_ASSETS))
  );
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(
        keys
          .filter((key) => IS_LOCALHOST || (key !== CACHE_VERSION && key !== FEED_CACHE_NAME))
          .map((key) => caches.delete(key))
      )
    )
  );
  self.clients.claim();
});

self.addEventListener("fetch", (event) => {
  // Always bypass Service Worker caching on localhost for instant local development
  if (IS_LOCALHOST) return;

  const { request } = event;
  if (request.method !== "GET") return;

  const url = new URL(request.url);

  // Network-first with guaranteed offline cache fallback for live_feed.json
  if (url.pathname.endsWith("/live_feed.json")) {
    event.respondWith(
      fetch(request)
        .then((networkResp) => {
          if (networkResp && networkResp.ok) {
            const clone = networkResp.clone();
            caches.open(FEED_CACHE_NAME).then((cache) => cache.put(request, clone));
          }
          return networkResp;
        })
        .catch(async () => {
          const feedCache = await caches.open(FEED_CACHE_NAME);
          const cachedFeed = await feedCache.match(request);
          if (cachedFeed) return cachedFeed;
          const shellCache = await caches.open(CACHE_VERSION);
          return shellCache.match("./public/live_feed.json");
        })
    );
    return;
  }

  // Network-first with offline cache fallback for same-origin App Shell & monetization.config.js
  if (url.origin === self.location.origin) {
    event.respondWith(
      fetch(request)
        .then((resp) => {
          if (resp && resp.ok) {
            const clone = resp.clone();
            caches.open(CACHE_VERSION).then((c) => c.put(request, clone));
          }
          return resp;
        })
        .catch(async () => {
          const cached = await caches.match(request);
          if (cached) return cached;
          return caches.match("./index.html");
        })
    );
  }
});
