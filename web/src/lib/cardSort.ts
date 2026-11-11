import type { Card } from "@/lib/api";

/** Sortierung der Rasteransicht einer Sammlung („manual" = Binder-Reihenfolge). */
export type GridSort = "manual" | "name" | "pokedex" | "set" | "value" | "regulation" | "newest";
export const GRID_SORTS: GridSort[] = ["manual", "name", "pokedex", "set", "value", "regulation", "newest"];

const collator = new Intl.Collator(undefined, { numeric: true, sensitivity: "base" });
const nameOf = (c: Card, lang: string) => (lang === "EN" && c.englischer_name ? c.englischer_name : c.kartenname) ?? "";
const num = (v: string | null | undefined) => (v == null || v === "" ? null : Number(v));

/** Fehlende Werte immer ans Ende, sonst nach Schlüssel; gleichrangig → Name. */
function by<T>(get: (c: Card) => T | null | undefined, cmp: (a: T, b: T) => number, lang: string) {
  return (a: Card, b: Card) => {
    const x = get(a), y = get(b);
    if (x == null && y == null) return collator.compare(nameOf(a, lang), nameOf(b, lang));
    if (x == null) return 1;
    if (y == null) return -1;
    return cmp(x, y) || collator.compare(nameOf(a, lang), nameOf(b, lang));
  };
}

const setKey = (c: Card) => (c.set_id || c.set_edition) ? `${c.set_id || c.set_edition}\u0000${c.karten_nr ?? ""}` : null;
const markOf = (c: Card) => (c.regulation_mark && /^[A-Z]$/.test(c.regulation_mark) ? c.regulation_mark : null);

export function sortCards<C extends Card>(cards: C[], sort: GridSort, lang: string): C[] {
  if (sort === "manual") return cards;
  const list = [...cards];
  switch (sort) {
    case "name":
      return list.sort((a, b) => collator.compare(nameOf(a, lang), nameOf(b, lang)));
    case "pokedex":
      return list.sort(by((c) => c.pokedex_nr, (x, y) => x - y, lang));
    case "set":
      return list.sort(by(setKey, (x, y) => collator.compare(x, y), lang));
    case "value":
      return list.sort(by((c) => num(c.wert_eur), (x, y) => y - x, lang));
    case "regulation": {
      // Neuestes Zeichen zuerst (J, I, H …), ohne Zeichen ans Ende; gleiches Zeichen → Set & Nummer
      const bySet = by(setKey, (x, y) => collator.compare(x, y), lang);
      return list.sort((a, b) => {
        const x = markOf(a), y = markOf(b);
        if (x === y) return bySet(a, b);
        if (x == null) return 1;
        if (y == null) return -1;
        return x < y ? 1 : -1;
      });
    }
    case "newest":
      return list.sort(by((c) => c.hinzugefuegt_am, (x, y) => (x < y ? 1 : x > y ? -1 : 0), lang));
  }
}
