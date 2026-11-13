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

/** Gesamtzahl, die das Set aufdruckt (für Vorschlag/Platzhalter), z. B. „132". */
export function setTotal(set: { card_count_official?: number | null; max_card_nr?: number | null } | null | undefined): string {
  const n = set?.card_count_official ?? set?.max_card_nr ?? null;
  return n ? String(n).padStart(3, "0") : "";
}
