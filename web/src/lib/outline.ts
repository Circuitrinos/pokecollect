import type { CornerSuggestion } from "@/components/CornerEditor";
import type { OutlineResult } from "@/lib/api";

type Labels = {
  pic_crop_matched: (ref: string) => string;
  pic_crop_card: string;
  pic_crop_frame: string;
  pic_crop_found: string;
};

/**
 * matcher-/outline-Ergebnis → Vorschlag im Eck-Editor. Am Artwork ausgerichtet
 * oder Kanten, die wie ein Kartenrahmen aussehen = verlässlich (grün); nur an
 * einen typischen Rahmen angelegt oder nur Kanten = bitte prüfen (gelb).
 */
export function outlineSuggestion(r: OutlineResult, t: Labels): CornerSuggestion | null {
  if (!r.quad) return null;
  if (r.method === "reference")
    return { quad: r.quad, ok: true, label: t.pic_crop_matched(r.reference_label ?? r.reference ?? "") };
  if (r.frame_ok) return { quad: r.quad, ok: true, label: t.pic_crop_card };
  return { quad: r.quad, ok: false, label: r.method === "frame" ? t.pic_crop_frame : t.pic_crop_found };
}
