// Nach „Ecken anpassen" wird die Karte mit dem neuen Zuschnitt neu abgeglichen. Ein
// unsicherer neuer Abgleich ersetzt keine sicher erkannte (oder gewählte) Karte: beim
// Live-Scan stammt der Zuschnitt aus dem Seitenbild (klein), erkannt wurde die Karte aber
// aus vielen Bildern und der Nahaufnahme (Brians Video: Antique Armor Fossil 90 % → nach
// dem Anpassen Antique Helix Fossil 25 %). Seine Treffer kommen dann zu den Alternativen.
// Die Folierung gehört zum Exemplar in der Hand: sie bleibt, wenn es sie von der neuen Karte gibt.
// Reine Rechnung ohne DOM, damit sie sich für sich prüfen lässt.
import type { ScanAlternative, ScanCandidate } from "./api";

type Before = Pick<ScanCandidate, "match" | "needs_choice" | "alternatives" | "suggested"> & { chosenId?: string | null };

/** Der neue Abgleich ist schwächer als der bisherige: die bisherige Karte bleibt. */
export function rematchIsWeak(old: Before | undefined, n: ScanCandidate | undefined): boolean {
  const before = old?.match?.tcgdex_card_id;
  if (!n || !before) return false;
  const wasSure = !old.needs_choice || !!old.chosenId;
  return wasSure && n.match?.tcgdex_card_id !== before && (!!n.needs_choice || !n.match?.tcgdex_card_id);
}

/** Bisherige Alternativen + die Treffer des neuen Abgleichs (sein bester zuerst), ohne Doppelte. */
export function rematchAlternatives(old: Before, n: ScanCandidate): ScanAlternative[] {
  const have = new Set<string>([old.match?.tcgdex_card_id ?? "", ...(old.alternatives ?? []).map((a) => a.tcgdex_card_id)]);
  const m = n.match;
  const top: ScanAlternative[] = m?.tcgdex_card_id
    ? [{ tcgdex_card_id: m.tcgdex_card_id, name: m.name, set_name: m.set_name, local_id: m.local_id, image_url: m.image_url, art_inliers: 0 }]
    : [];
  const out = [...(old.alternatives ?? [])];
  for (const a of [...top, ...(n.alternatives ?? [])]) {
    if (!have.has(a.tcgdex_card_id)) { have.add(a.tcgdex_card_id); out.push(a); }
  }
  return out;
}

/** Die Vorschläge des neuen Treffers, mit der bisherigen Folierung, wenn es sie von ihm gibt. */
export function rematchSuggested(old: Before, n: ScanCandidate): Record<string, unknown> {
  const finish = String((old.suggested as Record<string, unknown> | undefined)?.folierung ?? "");
  const keep = !!finish && finish !== "Normal" && (!n.foil_options?.length || n.foil_options.includes(finish));
  return keep ? { ...n.suggested, folierung: finish } : n.suggested;
}
