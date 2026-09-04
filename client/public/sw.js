// Caches the app shell so the PWA opens instantly and survives a brief
// network blip. Conversation data is NEVER cached -- /api is always network,
// because the server is the single source of truth.

const CACHE = "shell-v2";

// Only the entry document and the unhashed assets are named here. The bundle's
// JS and CSS carry content hashes in their filenames, so there is nothing
// stable to precache: they are cached on first fetch by the handler below and
// a new build simply requests new URLs.
const SHELL = ["/", "/index.html", "/manifest.webmanifest", "/icon.svg"];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE).then((cache) => cache.addAll(SHELL)).then(() => self.skipWaiting()),
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim()),
  );
});

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);

  // `/openrouter/callback/...` is the OpenRouter sign-in landing on the way
  // back. It is a one-shot page the server renders, its URL carries the
  // single-use handle for that sign-in, and it is never requested twice --
  // three reasons not to put it in a cache that outlives the tab.
  if (
    url.pathname.startsWith("/api") ||
    url.pathname.startsWith("/openrouter/") ||
    event.request.method !== "GET"
  ) {
    return; // straight to the network, no caching, no interception
  }

  event.respondWith(
    // Network-first so a deployed change lands without a hard reload; the
    // cache is the offline fallback, not the primary source.
    fetch(event.request)
      .then((response) => {
        const copy = response.clone();
        caches.open(CACHE).then((cache) => cache.put(event.request, copy));
        return response;
      })
      .catch(() =>
        caches
          .match(event.request)
          .then((hit) => hit || caches.match("/index.html")),
      ),
  );
});
