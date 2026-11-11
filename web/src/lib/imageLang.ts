// Sprachfassung eines TCGdex-Kartenbilds (wie backend/app/services/image_lang.py):
// westliche Karten liegen je Sprache unter /<sprache>/… mit derselben Nummer.

export const IMAGE_LANGS = ["EN", "DE", "FR", "ES", "IT", "PT"] as const;
const CODE: Record<string, string> = { EN: "en", DE: "de", FR: "fr", ES: "es", IT: "it", PT: "pt" };
const BACK: Record<string, string> = Object.fromEntries(Object.entries(CODE).map(([k, v]) => [v, k]));
const RX = /^(https:\/\/assets\.tcgdex\.net\/)([a-z]{2}(?:-[a-z]{2})?)(\/.+)$/i;

/** App-Sprachcode des Bilds ("DE") oder null, wenn es kein westliches TCGdex-Bild ist. */
export function imageLangOf(url: string | null | undefined): string | null {
  const m = RX.exec(url ?? "");
  return m ? BACK[m[2].toLowerCase()] ?? null : null;
}

/** Dasselbe Bild in einer anderen Sprache; null, wenn das nicht geht. */
export function withImageLang(url: string | null | undefined, lang: string): string | null {
  const m = RX.exec(url ?? "");
  const code = CODE[lang.toUpperCase()];
  if (!m || !code || !BACK[m[2].toLowerCase()]) return null;
  return `${m[1]}${code}${m[3]}`;
}
