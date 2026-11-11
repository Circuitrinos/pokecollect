// Aktuelle UI-Sprache außerhalb von React (API-Kopf, Zahlen-/Datumsformat).
// Der I18nProvider setzt sie bei jedem Render; Funktionen wie formatEur lesen
// sie, ohne dass jede Aufruferin `lang` durchreichen muss.

export type UiLang = "DE" | "EN";

let current: UiLang = "DE";

export function setUiLang(l: UiLang) {
  current = l;
}

export function getUiLang(): UiLang {
  return current;
}

/** Gebietsschema für Zahlen und Daten: Deutsch wie bisher (de-AT), Englisch
 * nach der Browsersprache (en-US, en-GB, en-AU …), ohne Angabe en-US. */
export function uiLocale(lang: UiLang = current): string {
  if (lang === "DE") return "de-AT";
  if (typeof navigator !== "undefined") {
    const nav = navigator.languages?.find((l) => /^en(-|$)/i.test(l)) ?? "";
    if (nav.includes("-")) return nav;
  }
  return "en-US";
}

/** Fehlermeldung des Servers, falls sie ein Text ist (422-Listen nicht). */
export function apiErrorText(e: unknown): string | null {
  const d = (e as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
  return typeof d === "string" && d.trim() ? d : null;
}
