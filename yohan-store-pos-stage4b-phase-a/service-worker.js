/* ===================================================================
   Yohan Store POS - application service worker (Stage 4B, Phase A)

   WHAT IT DOES
   - Saves the app's own files (page, styles, scripts, icons) and the public Firebase
     JavaScript library files, so the screen can open again with no internet after one
     successful online visit.
   - Does NOT save any data. Sales, costs, products, user profiles, logins and every other
     Firebase answer are never touched by this file (see "What is allowed" below).
   - Does NOT make sales, restocks or consumption work offline. That is a later phase.

   ONE WORKER, NOT TWO
   A browser allows only ONE service worker per scope (folder). The notification worker
   (firebase-messaging-sw.js) and this worker both need this folder, so this worker loads
   the notification worker's code with importScripts. firebase-messaging-sw.js is not
   edited at all: its push, low-stock alert and notification-click code runs inside this
   worker exactly as before.
   If that file cannot be loaded, this worker deliberately FAILS to install, so a broken
   update can never silently replace a working one and switch notifications off.
   =================================================================== */

importScripts("firebase-messaging-sw.js");

// Change CACHE_VERSION whenever you add or remove files in CORE_FILES / OPTIONAL_FILES.
// Older "yohan-store-pos-*" caches are deleted when the new version activates.
const CACHE_PREFIX = "yohan-store-pos-";
const CACHE_VERSION = "v1";
const CACHE_NAME = CACHE_PREFIX + CACHE_VERSION;

const BASE = self.registration.scope;                 // the folder this worker controls
const at = (path) => new URL(path, BASE).href;

// Must be available for the worker to install (the app cannot run without them).
const CORE_FILES = ["./", "index.html", "style.css", "app.js", "pwa.js", "manifest.json"];
// Nice to have; a missing one never stops the install.
const OPTIONAL_FILES = ["icon-192.png", "icon-512.png", "icon-maskable-512.png", "apple-touch-icon.png"];

const FIREBASE_LIBRARY_ROOT = "https://www.gstatic.com/firebasejs/";
const NETWORK_WAIT_MS = 4000;   // on a very slow connection, show the saved copy after this long

// -------------------------------------------------------------------
// What is allowed in the cache (an allow-list, not a block-list):
//   1. The exact files named in CORE_FILES / OPTIONAL_FILES (same site only)
//   2. Public Firebase library code from https://www.gstatic.com/firebasejs/
// Anything else (Firestore, Firebase Auth, token / installation / messaging calls, any other
// website or API) is never intercepted: this worker does not call respondWith for it, so the
// browser handles it normally and nothing is stored.
// -------------------------------------------------------------------
const ALLOWED_APP_URLS = new Set([...CORE_FILES, ...OPTIONAL_FILES].map(at));
const INDEX_URL = at("index.html");
const ROOT_URL = at("./");

const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const withoutQuery = (href) => href.split(/[?#]/)[0];

// Only plain, successful, non-redirected answers are ever stored.
function isStorable(response) {
  return Boolean(response) &&
    response.status === 200 &&
    (response.type === "basic" || response.type === "cors") &&
    !response.redirected;
}

async function fetchAndStore(cache, href, library = false) {
  const request = new Request(href, library ? { mode: "cors", credentials: "omit" } : { cache: "reload" });
  const response = await fetch(request);
  if (!isStorable(response)) throw new Error("Could not save " + href + " (HTTP " + response.status + ")");
  await cache.put(href, response.clone());
  return response;
}

// Firebase's library files import each other. Save the whole chain so the app can start offline.
async function saveLibraryTree(cache, href, seen = new Set()) {
  if (seen.has(href) || !href.startsWith(FIREBASE_LIBRARY_ROOT)) return;
  seen.add(href);

  const stored = await cache.match(href);
  const response = stored || await fetchAndStore(cache, href, true);
  const code = await response.clone().text();

  const found = new Set();
  for (const match of code.matchAll(/(?:from|import)\s*\(?\s*["']([^"']+\.js)["']/g)) {
    try { found.add(new URL(match[1], href).href); } catch (error) { /* ignore */ }
  }
  await Promise.all([...found].map((child) => saveLibraryTree(cache, child, seen).catch(() => {})));
}

self.addEventListener("install", (event) => {
  event.waitUntil((async () => {
    const cache = await caches.open(CACHE_NAME);

    await Promise.all(CORE_FILES.map((file) => fetchAndStore(cache, at(file))));
    await Promise.all(OPTIONAL_FILES.map((file) => fetchAndStore(cache, at(file)).catch(() => {})));

    // Find the Firebase library files app.js uses, so this list never needs editing by hand.
    const appCode = await (await cache.match(at("app.js"))).text();
    const libraries = new Set(
      [...appCode.matchAll(/https:\/\/www\.gstatic\.com\/firebasejs\/[^"'\s]+\.js/g)].map((m) => m[0])
    );
    const seen = new Set();
    await Promise.all([...libraries].map((href) => saveLibraryTree(cache, href, seen).catch(() => {})));

    await self.skipWaiting();
  })());
});

self.addEventListener("activate", (event) => {
  event.waitUntil((async () => {
    // Delete only OUR old caches. Caches with any other name are left alone.
    const names = await caches.keys();
    await Promise.all(names
      .filter((name) => name.startsWith(CACHE_PREFIX) && name !== CACHE_NAME)
      .map((name) => caches.delete(name)));
    await self.clients.claim();
  })());
});

// Online: always fetch the newest file (and refresh the saved copy).
// Offline, or too slow: use the saved copy.
async function networkFirst(request, key) {
  const cache = await caches.open(CACHE_NAME);

  const fromNetwork = fetch(request).then(async (response) => {
    if (isStorable(response)) await cache.put(key, response.clone());
    return response;
  });
  fromNetwork.catch(() => {});   // never leave an unhandled error behind

  const saved = await cache.match(key, { ignoreVary: true });
  if (!saved) return fromNetwork.catch(() => offlineAnswer(request));

  try {
    return await Promise.race([fromNetwork, wait(NETWORK_WAIT_MS).then(() => saved)]);
  } catch (error) {
    return saved;   // network failed: use the saved copy
  }
}

function offlineAnswer(request) {
  if (request.mode === "navigate") {
    return new Response(
      "<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>" +
      "<title>Yohan Store POS</title><body style='font-family:Arial,sans-serif;padding:32px;text-align:center'>" +
      "<h2>You are offline</h2><p>Yohan Store POS has not been saved on this device yet. " +
      "Connect to the internet and open the app once.</p></body>",
      { status: 503, headers: { "Content-Type": "text/html; charset=utf-8" } }
    );
  }
  return new Response("", { status: 503, statusText: "Offline" });
}

// Public library code never changes for a given version URL, so the saved copy is used first.
async function libraryAnswer(request) {
  const cache = await caches.open(CACHE_NAME);
  const saved = await cache.match(request.url);
  if (saved) return saved;

  try {
    const response = await fetch(request);
    if (isStorable(response)) await cache.put(request.url, response.clone());
    return response;
  } catch (error) {
    return offlineAnswer(request);
  }
}

self.addEventListener("fetch", (event) => {
  const request = event.request;
  if (request.method !== "GET") return;               // saving data is never handled here

  const url = new URL(request.url);
  const clean = withoutQuery(request.url);

  if (url.origin === "https://www.gstatic.com" && url.pathname.startsWith("/firebasejs/")) {
    event.respondWith(libraryAnswer(request));
    return;
  }

  if (url.origin !== self.location.origin) return;    // every other website: untouched

  // Opening the app (typing the address, tapping the icon, refreshing).
  if (request.mode === "navigate" && (clean === ROOT_URL || clean === INDEX_URL)) {
    event.respondWith(networkFirst(request, clean));
    return;
  }

  // The app's own files, by exact address only.
  if (ALLOWED_APP_URLS.has(clean)) {
    event.respondWith(networkFirst(request, clean));
  }
});
