import type { Card } from "@/lib/api";
import type { SortStep } from "@/lib/sortChain";

/** Sortierung der Rasteransicht einer Sammlung („manual" = Binder-Reihenfolge). */
export type GridSort = "manual" | "name" | "pokedex" | "set" | "value" | "regulation" | "newest";
export const GRID_SORTS: GridSort[] = ["manual", "name", "pokedex", "set", "value", "regulation", "newest"];

const collator = new Intl.Collator(undefined, { numeric: true, sensitivity: "base" });
const nameOf = (c: Card, lang: string) => (lang === "EN" && c.englischer_name ? c.englischer_name : c.kartenname) ?? "";
const num = (v: string | null | undefined) => (v == null || v === "" ? null : Number(v));
const setKey = (c: Card) => (c.set_id || c.set_edition) ? `${c.set_id || c.set_edition}\u0000${c.karten_nr ?? ""}` : null;
const markOf = (c: Card) => (c.regulation_mark && /^[A-Z]$/.test(c.regulation_mark) ? c.regulation_mark : null);
const newerFirst = <T,>(x: T, y: T) => (x < y ? 1 : x > y ? -1 : 0);

type Cmp = (a: Card, b: Card) => number;

/** Nach Schlüssel (dir = -1 kehrt um); fehlende Werte immer ans Ende; Gleichstand = 0. */
function by<T>(get: (c: Card) => T | null | undefined, cmp: (a: T, b: T) => number, dir: number): Cmp {
  return (a, b) => {
    const x = get(a), y = get(b);
    if (x == null && y == null) return 0;
    if (x == null) return 1;
    if (y == null) return -1;
    return dir * cmp(x, y);
  };
}

function keyCmp(sort: Exclude<GridSort, "manual">, dir: number, lang: string): Cmp {
  switch (sort) {
    case "name": return (a, b) => dir * collator.compare(nameOf(a, lang), nameOf(b, lang));
    case "pokedex": return by((c) => c.pokedex_nr, (x, y) => x - y, dir);
    case "set": return by(setKey, (x, y) => collator.compare(x, y), dir);
    case "value": return by((c) => num(c.wert_eur), (x, y) => y - x, dir);
    case "regulation": return by(markOf, newerFirst, dir);
    case "newest": return by((c) => c.hinzugefuegt_am, newerFirst, dir);
  }
}

// Gleichstand ohne Untersortierung: gleiches Zeichen → Set & Nummer, sonst → Name
const DEFAULT_THEN: Record<Exclude<GridSort, "manual">, Exclude<GridSort, "manual">[]> = {
  name: [], pokedex: ["name"], set: ["name"], value: ["name"], regulation: ["set", "name"], newest: ["name"],
};

/** reverse kehrt die Hauptsortierung um (auch die Binder-Reihenfolge); then = Untersortierungen
 *  in Reihenfolge (zuvor gewählte Sortierungen). Karten ohne Wert bleiben hinten. */
export function sortCards<C extends Card>(cards: C[], sort: GridSort, lang: string, reverse = false, then: SortStep[] = []): C[] {
  const dir = reverse ? -1 : 1;
  if (sort === "manual") return reverse ? [...cards].reverse() : cards;
  const steps: Cmp[] = [
    keyCmp(sort, dir, lang),
    ...then.filter((s): s is SortStep & { key: Exclude<GridSort, "manual"> } => s.key !== "manual" && GRID_SORTS.includes(s.key as GridSort))
      .map((s) => keyCmp(s.key, s.reverse ? -1 : 1, lang)),
    ...DEFAULT_THEN[sort].map((k) => keyCmp(k, dir, lang)),
  ];
  // Array.sort ist stabil: bei völligem Gleichstand bleibt die Binder-Reihenfolge
  return [...cards].sort((a, b) => {
    for (const cmp of steps) {
      const r = cmp(a, b);
      if (r) return r;
    }
    return 0;
  });
}
