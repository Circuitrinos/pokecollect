"use client";
import { useRef, useState } from "react";
import toast from "react-hot-toast";
import { API_BASE, Card, cardApi } from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import { imageUrl } from "@/lib/utils";
import CornerEditor, { CornerSuggestion } from "@/components/CornerEditor";

// Play!-Pokémon-Stempel im Kartenfoto markieren (vier Ecken, Kartenkoordinaten)
// — Trainingsdaten für einen Stempel-Detektor (Scan-Datensatz: stamp.yaml).
// Kartenseite und Admin-Liste „Play!-Stempel". Mit gelernter Vorlage schlägt der
// Bild-Abgleich den Stempel vor: beim Öffnen überall, nach dem Ziehen um die Ecken.
// Der Stempel sitzt unten rechts (manchmal links) am Bildfenster.
export const DEFAULT_STAMP: number[][] = [[0.79, 0.375], [0.96, 0.375], [0.96, 0.47], [0.79, 0.47]];

export default function StampEditor({ cardId, photo, version, initialQuad, onSaved, onClose }: {
  cardId: number;
  photo: string;            // eigenes Foto (Pfad unter /images)
  version?: string | null;  // Cache-Bust
  initialQuad?: number[][] | null;
  onSaved: (card: Card) => void;
  onClose: () => void;
}) {
  const { t } = useI18n();
  const [noTemplate, setNoTemplate] = useState(false);
  const askedRef = useRef(false);
  const src = imageUrl(photo, API_BASE, version ?? undefined);
  if (!src) return null;
  // erster Vorschlag ohne Hinweis (beide Stellen), danach um die gezogenen Ecken
  const suggest = async (quad: number[][]): Promise<CornerSuggestion | null> => {
    const hint = askedRef.current || initialQuad ? quad : null;
    askedRef.current = true;
    const r = await cardApi.stampSuggest(cardId, hint);
    setNoTemplate(!r.data.template);
    if (!r.data.quad) return null;
    const pct = Math.round((r.data.score ?? 0) * 100);
    return { quad: r.data.quad, ok: r.data.ok, label: r.data.ok ? t.stamp_suggest_found(pct) : t.stamp_suggest_maybe(pct) };
  };
  const save = async (quad: number[][] | null) => {
    try {
      const r = await cardApi.setStamp(cardId, quad);
      toast.success(quad ? t.stamp_saved : t.stamp_removed);
      onSaved(r.data);
      onClose();
    } catch {
      toast.error(t.form_save_error);
    }
  };
  return (
    <CornerEditor
      imageUrl={src}
      initialQuad={initialQuad ?? DEFAULT_STAMP}
      title={t.stamp_title}
      hint={t.stamp_hint}
      orientable={false}
      suggest={suggest}
      suggestOnOpen={!initialQuad}
      suggestTexts={{ hint: t.stamp_suggest_hint, busy: t.stamp_suggest_busy,
        none: noTemplate ? t.stamp_suggest_no_template : t.stamp_suggest_none }}
      onCancel={onClose}
      onApply={(quad) => void save(quad)}
      extra={initialQuad ? (
        <button type="button" onClick={() => void save(null)} data-testid="stamp-remove"
          className="text-red-400 hover:text-red-300 text-sm">{t.stamp_remove}</button>
      ) : undefined}
    />
  );
}
