// Anzeige-Namen für gespeicherte Auswahlwerte. Gespeichert bleibt der deutsche
// Wert (Server-Enums, Filter, Backups); die englische Oberfläche zeigt ihn
// übersetzt. Englische Werte (Mint, Holo, Rare …) stehen hier nicht.

const EN_VALUE: Record<string, string> = {
  // Wunschlisten-Priorität
  Hoch: "High",
  Mittel: "Medium",
  Niedrig: "Low",
  // Folierungs-Muster
  Sterne: "Stars",
  Energie: "Energy",
  Sonstiges: "Other",   // auch Sealed-Typ
  // Sealed-Zustand
  Versiegelt: "Sealed",
  "Geöffnet": "Opened",
  "Beschädigt": "Damaged",
};

export function valueLabel(value: string | null | undefined, lang: string): string {
  if (value == null || value === "") return "";
  return lang === "EN" ? EN_VALUE[value] ?? value : value;
}
