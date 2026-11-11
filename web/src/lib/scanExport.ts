// Warum ein Kartenfoto nicht in den Scan-Trainingsdatensatz (Admin-Export)
// kommt — dieselbe Regel wie backend/app/services/scan_export.py.
import type { Card } from "@/lib/api";

export type ScanExportProblem = "no_card" | "no_original";

export function scanExportProblems(card: Pick<Card, "bild_karte_pfad" | "bild_original_pfad" | "tcgdex_card_id"> & { bild_scan_pfad?: string | null }): ScanExportProblem[] {
  if (!(card.bild_scan_pfad || card.bild_karte_pfad)) return [];
  const out: ScanExportProblem[] = [];
  if (!card.tcgdex_card_id) out.push("no_card");
  if (!card.bild_original_pfad) out.push("no_original");
  return out;
}
