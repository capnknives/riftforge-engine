/**
 * Offline shell cache for the browser HUD — static assets only.
 * Does not intercept WebSockets or non-shell fetch targets.
 */
const CACHE_BUILD = "2026-09-05c";
const CACHE_NAME = "riftforge-shell-" + CACHE_BUILD;

const SHELL_FILES = new Set([
  "index.html",
  "style.css",
  "app.js",
  "discord-invite.js",
  "manifest.json",
  "icon.svg",
  "icon-192.png",
  "icon-512.png",
]);

function shellBasename(url) {
  const path = url.pathname;
  if (path === "/" || path.endsWith("/")) {
    return "index.html";
  }
  const slash = path.lastIndexOf("/");
  return slash === -1 ? path : path.slice(slash + 1);
}

// index.html cache-busts with ?v=…; offline lookup must ignore the query.
function shellCacheRequest(url) {
  const base = shellBasename(url);
  return new Request(new URL("./" + base, url).href);
}

self.addEventListener("install", function (event) {
  event.waitUntil(
    caches.open(CACHE_NAME).then(function (cache) {
      return cache.addAll([
        "./index.html",
        "./style.css",
        "./app.js",
        "./discord-invite.js",
        "./manifest.json",
        "./icon.svg",
        "./icon-192.png",
        "./icon-512.png",
      ]);
    })
  );
  self.skipWaiting();
});

self.addEventListener("activate", function (event) {
  event.waitUntil(
    caches.keys().then(function (keys) {
      return Promise.all(
        keys
          .filter(function (key) {
            return key.indexOf("riftforge-shell-") === 0 && key !== CACHE_NAME;
          })
          .map(function (key) {
            return caches.delete(key);
          })
      );
    })
  );
  self.clients.claim();
});

self.addEventListener("fetch", function (event) {
  if (event.request.method !== "GET") {
    return;
  }
  const url = new URL(event.request.url);
  const base = shellBasename(url);
  if (!SHELL_FILES.has(base)) {
    return;
  }
  const cacheReq = shellCacheRequest(url);
  event.respondWith(
    fetch(event.request)
      .then(function (res) {
        if (!res || res.status !== 200) {
          return caches.match(cacheReq).then(function (cached) {
            return cached || res;
          });
        }
        const copy = res.clone();
        caches.open(CACHE_NAME).then(function (cache) {
          cache.put(cacheReq, copy);
        });
        return res;
      })
      .catch(function () {
        return caches.match(cacheReq);
      })
  );
});
