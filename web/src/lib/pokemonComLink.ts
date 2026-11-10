// Karte, für die gerade auf pokemon.com gesucht wird — damit „Teilen →
// PokéCollect" (Android, /share) weiß, wohin der geteilte Link gehört.
const KEY = "pc_pokemon_com_card";
const MAX_AGE_MS = 2 * 60 * 60 * 1000;

export function pokemonComSearchUrl(name: string): string {
  return `https://www.pokemon.com/us/pokemon-tcg/pokemon-cards?cardName=${encodeURIComponent(name.trim()).replace(/%20/g, "+")}`;
}

export function rememberLinkTarget(cardId: number): void {
  try { localStorage.setItem(KEY, JSON.stringify({ id: cardId, at: Date.now() })); } catch { /* egal */ }
}

export function pendingLinkTarget(): number | null {
  try {
    const v = JSON.parse(localStorage.getItem(KEY) ?? "null") as { id: number; at: number } | null;
    return v && Date.now() - v.at < MAX_AGE_MS ? v.id : null;
  } catch {
    return null;
  }
}

export function clearLinkTarget(): void {
  try { localStorage.removeItem(KEY); } catch { /* egal */ }
}

/** Sieht der Text nach einem Kartenlink/-bild von pokemon.com aus? */
export function looksLikePokemonComLink(text: string): boolean {
  return /pokemon\.com\/[^\s]*\/(series?\/[^\s/]+\/[^\s/]+|cards\/web\/)/i.test(text);
}
