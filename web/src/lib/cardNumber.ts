// Karten-Nr. als zwei Felder: Nummer und Gesamtzahl des Sets („117" und „132").
// Gespeichert bleibt beides zusammen („117/132"), so wie es auf der Karte steht —
// Suche, Sortierung, Export und der Abgleich lesen die Nummer vor dem „/".

/** „117/132" → ["117", "132"]; „TG05/TG30" → ["TG05", "TG30"]; „117" → ["117", ""]. */
export function splitCardNr(value: unknown): [string, string] {
  const s = String(value ?? "").trim();
  const i = s.indexOf("/");
  return i < 0 ? [s, ""] : [s.slice(0, i).trim(), s.slice(i + 1).trim()];
}

/** ["117", "132"] → „117/132"; ohne Gesamtzahl nur die Nummer. Ohne Nummer bleibt die
 *  Gesamtzahl beim Tippen stehen („/132"); gespeichert wird das als keine Nummer. */
export function joinCardNr(number: string, total: string): string {
  const n = number.trim();
  const t = total.trim();
  if (!n) return t ? `/${t}` : "";
  return t ? `${n}/${t}` : n;
}

// Ein Teil der Nummer (vor oder nach dem „/"): Ziffern und Buchstaben, dazu was auf
// Karten vorkommt — Black-Star-Promos „SWSH050", „HGSS01", „SVP 001", japanische
// Promos „001/SV-P", Unown „!" und „?". Andere Zeichen sind Tippfehler.
const PART = /^[A-Za-z0-9!?](?:[A-Za-z0-9 .!?-]{0,14}[A-Za-z0-9!?])?$/;

/** Sieht wie eine aufgedruckte Kartennummer aus? „041", „041/109", „TG05/TG30", „SWSH050". */
export function isCardNr(nr: string): boolean {
  const parts = nr.trim().split("/");
  if (parts.length > 2) return false;
  return parts.every((p, i) => PART.test(p.trim()) || (i === 1 && p.trim() === ""));
}

export const isPromoSet = (set: { name?: string | null; name_en?: string | null }) =>
  [set.name, set.name_en].some((n) => !!n && n.toLowerCase().includes("promo"));

/** Gesamtzahl, die das Set aufdruckt (für Vorschlag/Platzhalter), z. B. „132".
 *  Promo-Sets drucken keine Gesamtzahl. */
export function setTotal(set: { name?: string | null; name_en?: string | null; card_count_official?: number | null; max_card_nr?: number | null } | null | undefined): string {
  if (!set || isPromoSet(set)) return "";
  const n = set.card_count_official ?? set.max_card_nr ?? null;
  return n ? String(n).padStart(3, "0") : "";
}
