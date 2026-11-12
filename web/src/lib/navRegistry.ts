// Zentrale Navigations-Registry — die EINE Quelle aller Navigationsziele (DRY,
// CONTEXT.md Grundsatz 1). Burger-Menü, BottomNav und die BottomNav-Konfig in
// den Einstellungen lesen ausschließlich aus dieser Liste. Neue Seiten werden
// nur hier eingetragen, nie in den einzelnen Nav-Komponenten dupliziert.
import type { LucideIcon } from "lucide-react";
import {
  LayoutGrid,
  Library,
  BookOpen,
  ScanLine,
  FileClock,
  Star,
  BarChart3,
  Package,
  Settings,
  ShieldCheck,
  Users,
  Layers,
} from "lucide-react";

// Stabile Schlüssel für localStorage — bewusst getrennt vom Pfad, damit ein
// späterer Pfad-Umbau die gespeicherte BottomNav-Konfig nicht entwertet.
export type NavKey =
  | "pokedex"
  | "collections"
  | "decks"
  | "catalog"
  | "scan"
  | "scandrafts"
  | "wishlist"
  | "stats"
  | "friends"
  | "sealed"
  | "settings"
  | "admin";

// i18n-Keys der Labels (alle string-wertig in web/src/lib/i18n.tsx) — als Union
// getippt, damit t[labelKey] ohne Cast wieder ein string ist.
export type NavLabelKey =
  | "nav_collection"
  | "nav_collections"
  | "nav_decks"
  | "nav_catalog"
  | "nav_scan_short"
  | "nav_scan_drafts"
  | "nav_scan_drafts_short"
  | "nav_wishlist"
  | "nav_statistics"
  | "nav_friends"
  | "nav_sealed"
  | "nav_settings"
  | "nav_admin";

export type NavTarget = {
  key: NavKey;
  href: string;
  Icon: LucideIcon;
  labelKey: NavLabelKey;
  /** Kürzere Beschriftung für die untere Leiste (sonst labelKey) */
  shortLabelKey?: NavLabelKey;
  // exact: aktiver Zustand nur bei exakter Pfadgleichheit (nötig für "/", das
  // sonst jeden Pfad als Präfix matchen würde).
  exact?: boolean;
  // nur für Admins sichtbar (Menü, BottomNav und deren Konfiguration)
  adminOnly?: boolean;
};

export const NAV_TARGETS: NavTarget[] = [
  // Sammlungen zuerst: sie sind die Startseite ("/" leitet dorthin um)
  { key: "collections", href: "/collections", Icon: Library, labelKey: "nav_collections" },
  { key: "pokedex", href: "/pokedex", Icon: LayoutGrid, labelKey: "nav_collection" },
  { key: "catalog", href: "/catalog", Icon: BookOpen, labelKey: "nav_catalog" },
  { key: "decks", href: "/decks", Icon: Layers, labelKey: "nav_decks" },
  { key: "scan", href: "/scan", Icon: ScanLine, labelKey: "nav_scan_short" },
  // Offene Scans (Prüfseite als Entwurf) — mit Zähler in Menü, Leiste und Kopfzeile
  { key: "scandrafts", href: "/scan/drafts", Icon: FileClock, labelKey: "nav_scan_drafts", shortLabelKey: "nav_scan_drafts_short" },
  { key: "wishlist", href: "/wishlist", Icon: Star, labelKey: "nav_wishlist" },
  { key: "stats", href: "/stats", Icon: BarChart3, labelKey: "nav_statistics" },
  { key: "friends", href: "/friends", Icon: Users, labelKey: "nav_friends" },
  // Sealed-Seite baut Issue #35 parallel — Link jetzt schon aufnehmen, auch wenn
  // die Route bis zum Merge von #35 noch 404t.
  { key: "sealed", href: "/sealed", Icon: Package, labelKey: "nav_sealed" },
  { key: "settings", href: "/settings", Icon: Settings, labelKey: "nav_settings" },
  { key: "admin", href: "/admin", Icon: ShieldCheck, labelKey: "nav_admin", adminOnly: true },
];

/** Ziele, die dieser Benutzer sehen darf (Admin-Ziele nur für Admins). */
export function visibleNavTargets(isAdmin: boolean): NavTarget[] {
  return NAV_TARGETS.filter((target) => !target.adminOnly || isAdmin);
}

// Kanonische Reihenfolge der Schlüssel — die BottomNav zeigt gewählte Ziele
// immer in dieser Reihenfolge (deterministisch, unabhängig von der Klickfolge).
export const NAV_KEYS_IN_ORDER: NavKey[] = NAV_TARGETS.map((t) => t.key);

export function isNavKey(x: unknown): x is NavKey {
  return typeof x === "string" && NAV_KEYS_IN_ORDER.includes(x as NavKey);
}

// Aktiver Zustand eines Ziels für den aktuellen Pfad — eine Routine für Burger
// und BottomNav (DRY), damit beide identisch highlighten.
export const DRAFTS_PATH = "/scan/drafts";

/**
 * Scan-Ziel mit Kontext: merkt sich die aktuelle Seite (from=, dorthin führt
 * „Zurück" nach dem Scan) und wählt von einer Sammlungsseite aus diese
 * Sammlung als Ziel vor (collection=<id>).
 */
export function scanHref(pathname: string): string {
  const q = new URLSearchParams();
  const m = pathname.match(/^\/collections\/(\d+)(?:\/|$)/);
  if (m) q.set("collection", m[1]);
  const d = pathname.match(/^\/decks\/(\d+)(?:\/|$)/);
  if (d) q.set("deck", d[1]);
  if (pathname !== "/" && pathname !== "/collections" && (!pathname.startsWith("/scan") || pathname.startsWith(DRAFTS_PATH))) {
    q.set("from", pathname);
  }
  const s = q.toString();
  return s ? `/scan?${s}` : "/scan";
}

/** Rücksprungziel aus ?from= — nur app-interne Pfade (kein //host, kein Schema). */
export function safeReturnPath(from: string | null): string | null {
  if (!from || !from.startsWith("/") || from.startsWith("//") || from.includes("\\")) return null;
  if (from === DRAFTS_PATH) return from;          // fortgesetzter Entwurf → zurück zur Liste
  if (from.startsWith("/scan") || from === "/login" || from.startsWith("/auth/")) return null;
  return from;
}

/** Scan-Seite: einen offenen Entwurf fortsetzen, danach zurück zur Liste. */
export function resumeDraftHref(id: number): string {
  return `/scan?draft=${id}&from=${encodeURIComponent(DRAFTS_PATH)}`;
}

/** Link eines Navigationsziels für die aktuelle Seite (Scan nimmt die Sammlung mit). */
export function navHref(target: NavTarget, pathname: string): string {
  return target.key === "scan" ? scanHref(pathname) : target.href;
}

function matches(pathname: string, target: NavTarget): boolean {
  if (target.exact) return pathname === target.href;
  return pathname === target.href || pathname.startsWith(target.href + "/");
}

// Das genaueste Ziel gewinnt: /scan/drafts ist „Offene Scans", nicht „Scan"
export function isNavActive(pathname: string, target: NavTarget): boolean {
  if (!matches(pathname, target)) return false;
  return !NAV_TARGETS.some((o) => o !== target && o.href.length > target.href.length && matches(pathname, o));
}
