// Service Worker, je Build neu ausgeliefert: die Build-Kennung steckt im
// Cache-Namen. Nach einem Update ist die Datei damit eine andere → der Browser
// installiert sie, und beim Aktivieren werden alle alten Caches gelöscht
// (vorher blieb sw.js unverändert und alte Seiten-Antworten im Cache).
export const dynamic = "force-dynamic";

const BUILD = process.env.NEXT_PUBLIC_BUILD_STAMP ?? "dev";

const SOURCE = `/*
 * PokéCollect Service Worker (Build ${BUILD})
 *  - Seiten, Seitendaten (Next.js-RSC) und API: Netz zuerst — nach einem Update
 *    kommt sofort die neue Fassung; der Cache dient nur offline.
 *  - Cache zuerst nur für Dateien, die sich unter derselben Adresse nie ändern:
 *    /_next/static (Name enthält einen Hash), eigene Fotos und Bild-Spiegel,
 *    App-Symbole.
 *  - Nicht-GET (POST/PUT/DELETE), Anmeldung und Downloads fasst er nie an.
 */
const CACHE = "pokecollect-${BUILD}";
const SHELL = ["/collections", "/manifest.webmanifest", "/icon-192.png", "/icon-512.png"];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE).then((c) => c.addAll(SHELL)).catch(() => {}).then(() => self.skipWaiting())
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
  // Ersetzter (alter) Worker legt nichts mehr ab — sonst entstünde sein
  // gerade gelöschter Cache durch eine späte Antwort neu.
  if (self.serviceWorker && self.serviceWorker.state === "redundant") return res;
  if (res && res.status === 200 && (res.type === "basic" || res.type === "cors")) {
    const copy = res.clone();
    caches.open(CACHE).then((c) => c.put(req, copy)).catch(() => {});
  }
  return res;
}

function networkFirst(event, fallback) {
  event.respondWith(
    fetch(event.request)
      .then((res) => store(event.request, res))
      .catch(() => caches.match(event.request).then((m) => m || fallback()))
  );
}

const IMMUTABLE = /^\\/(_next\\/static\\/|images\\/|bild-spiegel\\/|icon-)/;

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.pathname.startsWith("/api/v1/auth/") || url.pathname.startsWith("/api/v1/data/")) return;
  if (url.pathname === "/sw.js" || url.pathname === "/version.json") return;

  if (req.mode === "navigate") {
    return networkFirst(event, () => caches.match("/collections").then((m) => m || Response.error()));
  }
  if (!IMMUTABLE.test(url.pathname)) {
    return networkFirst(event, () => Response.error());
  }
  // Unveränderliche Dateien: Cache zuerst, im Hintergrund auffrischen
  event.respondWith(
    caches.match(req).then((cached) => {
      const network = fetch(req).then((res) => store(req, res)).catch(() => cached);
      return cached || network;
    })
  );
});
`;

export function GET() {
  return new Response(SOURCE, {
    headers: {
      "Content-Type": "application/javascript; charset=utf-8",
      "Cache-Control": "no-cache, no-store, must-revalidate",
    },
  });
}
