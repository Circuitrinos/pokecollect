// Debug-Protokoll für die Entwicklung: was die App gerade tut (Live-Scan Bild für
// Bild, Fotos, Antworten des Bild-Abgleichs) — gesammelt und alle zwei Sekunden
// ans Server-Log geschickt (api.log, Logger „web.debug"), dazu in die Konsole.
// Nur, wenn es in diesem Browser eingeschaltet ist: Einstellungen → Werkzeuge,
// oder eine Adresse mit ?debug=1 (?debug=0 schaltet aus).

import { APP_VERSION } from "./version";

const KEY = "pc_debug";
const FLUSH_MS = 2000;
const BATCH = 50;
const MAX_QUEUE = 300;

type Event = { t: number; event: string; data?: Record<string, unknown> };

let on: boolean | null = null;
const queue: Event[] = [];
let timer: ReturnType<typeof setTimeout> | null = null;
let flushing = false;
const t0 = Date.now();
const session = Math.random().toString(36).slice(2, 8);   // ein Seitenaufruf = eine Kennung im Log

export function debugEnabled(): boolean {
  if (on === null) {
    try { on = typeof window !== "undefined" && localStorage.getItem(KEY) === "1"; } catch { on = false; }
  }
  return on;
}

export function setDebug(value: boolean) {
  on = value;
  try {
    if (value) localStorage.setItem(KEY, "1"); else localStorage.removeItem(KEY);
  } catch { /* privat */ }
  if (value) dlog("debug.on", { ua: navigator.userAgent.slice(0, 160), screen: `${screen.width}x${screen.height}` });
}

/** ?debug=1 / ?debug=0 in der Adresse schaltet um (praktisch am Handy). */
export function debugFromUrl() {
  if (typeof window === "undefined") return;
  const v = new URLSearchParams(window.location.search).get("debug");
  if (v === "1" || v === "0") setDebug(v === "1");
}

// Zahlen kürzen, damit eine Zeile lesbar bleibt
function tidy(v: unknown, depth = 0): unknown {
  if (typeof v === "number") return Number.isInteger(v) ? v : Math.round(v * 1000) / 1000;
  if (depth > 4) return "…";
  if (Array.isArray(v)) return v.slice(0, 40).map((x) => tidy(x, depth + 1));
  if (v && typeof v === "object") {
    return Object.fromEntries(Object.entries(v as Record<string, unknown>).slice(0, 40).map(([k, x]) => [k, tidy(x, depth + 1)]));
  }
  return typeof v === "string" && v.length > 300 ? `${v.slice(0, 300)}…` : v;
}

export function dlog(event: string, data?: Record<string, unknown>) {
  if (!debugEnabled()) return;
  const e: Event = { t: Date.now() - t0, event, data: data ? (tidy(data) as Record<string, unknown>) : undefined };
  console.debug(`[pc ${(e.t / 1000).toFixed(1)}s] ${event}`, e.data ?? "");
  queue.push(e);
  if (queue.length > MAX_QUEUE) queue.splice(0, queue.length - MAX_QUEUE);
  if (!timer) timer = setTimeout(() => void flush(), FLUSH_MS);
}

async function flush() {
  timer = null;
  if (flushing) return;
  flushing = true;
  try {
    while (queue.length) {
      const events = queue.splice(0, BATCH);
      try {
        const { api } = await import("./api");   // spät geladen: api.ts protokolliert selbst hierher
        await api.post("/client-log/debug", {
          events, session, version: APP_VERSION,
          url: (window.location.pathname + window.location.search).slice(0, 500),
        });
      } catch {
        break;   // Server nicht erreichbar: diese Ereignisse verwerfen, nicht stauen
      }
    }
  } finally {
    flushing = false;
  }
}
