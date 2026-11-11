/** Wie eine Sortierung gerade läuft, in Worten (für den Umkehren-Knopf). */
export type SortDir = "az" | "za" | "asc" | "desc" | "high" | "low" | "new" | "old";

// Grundrichtung je Sortierschlüssel – Sammlungs-Raster, Kartenliste und Katalog
const BASE: Record<string, SortDir> = {
  manual: "asc", name: "az", pokedex: "asc", pokedex_nr: "asc", dex: "asc", set: "asc",
  value: "high", wert: "low", regulation: "new", newest: "new", hinzugefuegt_am: "old",
};
const FLIP: Record<SortDir, SortDir> = { az: "za", za: "az", asc: "desc", desc: "asc", high: "low", low: "high", new: "old", old: "new" };

export function sortDir(sort: string | undefined, reverse: boolean | undefined): SortDir {
  const base = BASE[sort ?? ""] ?? "asc";
  return reverse ? FLIP[base] : base;
}
