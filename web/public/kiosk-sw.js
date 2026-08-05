// Kiosk service worker (S7, doc 01 §5).
//
// Its one job: make the app *shell* load with no network, so a kiosk that was
// running when the uplink died — or one that reboots during an outage — still
// paints the intake UI instead of the browser's dinosaur.
//
// It deliberately does NOT cache data. The trees, the token blocks and the
// queued intakes live in IndexedDB (see _lib/offline/db.ts), which is the right
// place for them: it is queryable, transactional, and the walker reads it
// directly. A service worker caching /kiosk/bundle would be a second, dumber
// copy of that data with its own staleness — the ETag on the bundle already
// handles revalidation. So this caches HTML/JS/CSS/fonts only.
//
// Strategy:
//   navigation (the HTML shell)  network-first, falling back to the cache
//   hashed static assets         cache-first — their URL changes when they do
//   /kiosk/* API calls           never touched — the app's own fetch handles
//                                those and falls back to the local walker
//
// **Why navigation is network-first.** It used to be cache-first "updated in the
// background", with a comment asking the next person to bump the cache name on
// every deploy. Nobody ever did, and the first kiosk load after a release served
// the *previous* shell — which is how a deploy that had landed correctly still
// showed the old intake screens, once, convincingly, to the person checking
// whether the deploy had worked.
//
// A shell is one small HTML request and this box is on the hospital LAN, so
// asking the network first costs nothing worth counting, and the cache still
// answers the moment a fetch fails — which is the actual offline requirement
// (doc 01 §5), not "answer from cache even when the network is fine". The
// hashed `/_next/` assets stay cache-first because their filenames change
// whenever their contents do, so they can never be stale.

const CACHE = "kiosk-shell-v2";

// Precache the entry point. Next's hashed chunks are cached lazily as they are
// first requested (their names are not known at build time here).
const PRECACHE = ["/kiosk"];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE).then((cache) => cache.addAll(PRECACHE)).then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

function isApi(url) {
  // Anything the app talks to for data. Never served from this cache — a stale
  // clinical tree or a cached "healthy" from a dead API is the one thing the
  // shell cache must not do. The app's fetch owns these and fails over locally.
  return (
    url.pathname.startsWith("/kiosk/bundle") ||
    url.pathname.startsWith("/kiosk/blocks") ||
    url.pathname.startsWith("/kiosk/sync") ||
    url.pathname.startsWith("/kiosk/start") ||
    url.pathname.startsWith("/health") ||
    /\/kiosk\/[^/]+\/(next|answer|finish|confirm)$/.test(url.pathname)
  );
}

function isShellAsset(request, url) {
  if (request.mode === "navigate") return true;
  if (url.pathname.startsWith("/_next/")) return true; // JS, CSS, chunks
  if (url.pathname.startsWith("/fonts/")) return true;
  return ["style", "script", "font", "image"].includes(request.destination);
}

self.addEventListener("fetch", (event) => {
  const { request } = event;
  if (request.method !== "GET") return;

  const url = new URL(request.url);
  if (isApi(url)) return; // let the app handle it
  if (!isShellAsset(request, url)) return;

  const store = (response) => {
    if (response && response.ok) {
      const copy = response.clone();
      caches.open(CACHE).then((cache) => cache.put(request, copy));
    }
    return response;
  };

  // The shell: network first, cache only when the network cannot answer. A
  // deployed change is visible on the very next load, and an offline kiosk
  // still paints.
  if (request.mode === "navigate") {
    event.respondWith(
      fetch(request)
        .then(store)
        .catch(() => caches.match(request).then((cached) => cached || caches.match("/kiosk")))
    );
    return;
  }

  // Everything else here is content-addressed by filename, so a cached copy can
  // only be the right one.
  event.respondWith(
    caches.match(request).then((cached) => cached || fetch(request).then(store))
  );
});
