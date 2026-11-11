/** Verkettete Sortierung: die zuvor gewählte Sortierung wird Untersortierung
 *  („erst Name, dann Regulation Mark" → Regulation Mark, darin nach Name).
 *  Kodiert als „name,-set" (Minus = umgekehrt). */
export type SortStep = { key: string; reverse: boolean };

export const MAX_THEN = 2;

export function encodeThen(steps: SortStep[]): string {
  return steps.map((s) => (s.reverse ? "-" : "") + s.key).join(",");
}

export function decodeThen(text: string | null | undefined, allowed?: readonly string[]): SortStep[] {
  if (!text) return [];
  return text.split(",").map((p) => p.trim()).filter(Boolean)
    .map((p) => ({ key: p.replace(/^-/, ""), reverse: p.startsWith("-") }))
    .filter((s) => !allowed || allowed.includes(s.key))
    .slice(0, MAX_THEN);
}

/** Untersortierungen nach dem Wechsel von prev auf next. resetKey (Binder-Reihenfolge) beginnt neu. */
export function nextThen(prev: SortStep, prevThen: SortStep[], next: string, resetKey?: string): SortStep[] {
  if (next === prev.key) return prevThen;
  if (resetKey && (next === resetKey || prev.key === resetKey)) return [];
  return [prev, ...prevThen].filter((s) => s.key !== next).slice(0, MAX_THEN);
}
