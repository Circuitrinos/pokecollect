/*
 * PokéCollect Service Worker – konservativ.
 *  - Navigationen: network-first, Fallback auf gecachte App-Shell (Offline-Lesen)
 *  - API (/api/…): network-first — IMMER die aktuellen Daten; der Cache dient
 *    nur als Offline-Fallback. (Vorher stale-while-revalidate: nach Löschen/
 *    Ändern zeigte die Liste bis zum nächsten Laden den alten Stand.)
 *  - Statische Assets/Bilder: stale-while-revalidate (Dateinamen sind gehasht
 *    bzw. tragen eine UUID — ändern sich nicht unter gleicher URL)
 *  - Nicht-GET (POST/PUT/DELETE) wird nie angefasst
 * v2: alter Cache (mit veralteten API-Antworten) wird beim Aktivieren gelöscht.
 */
const CACHE = "pokecollect-v2";
const SHELL = ["/", "/manifest.webmanifest", "/icon-192.png", "/icon-512.png"];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

function store(req, res) {
  if (res && res.status === 200 && (res.type === "basic" || res.type === "cors")) {
    const copy = res.clone();
    caches.open(CACHE).then((c) => c.put(req, copy)).catch(() => {});
  }
  return res;
}

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  // Anmeldung (inkl. SSO-Weiterleitungen) und Downloads nie über den SW
  if (url.pathname.startsWith("/api/v1/auth/") || url.pathname.startsWith("/api/v1/data/")) return;

  // Seiten-Navigationen: aktuelles Netz bevorzugen, offline aus Cache
  if (req.mode === "navigate") {
    event.respondWith(
      fetch(req)
        .then((res) => store(req, res))
        .catch(() => caches.match(req).then((m) => m || caches.match("/")))
    );
    return;
  }

  // API: Netz zuerst, Cache nur offline
  if (url.pathname.startsWith("/api/")) {
    event.respondWith(
      fetch(req)
        .then((res) => store(req, res))
        .catch(() => caches.match(req).then((m) => m || Response.error()))
    );
    return;
  }

  // Übrige GETs (Assets, Bilder): stale-while-revalidate
  event.respondWith(
    caches.match(req).then((cached) => {
      const network = fetch(req)
        .then((res) => store(req, res))
        .catch(() => cached);
      return cached || network;
    })
  );
});
