"use client";
import toast from "react-hot-toast";
import { API_BASE, Card, cardApi } from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import { imageUrl } from "@/lib/utils";
import CornerEditor from "@/components/CornerEditor";

// Play!-Pokémon-Stempel im Kartenfoto markieren (vier Ecken, Kartenkoordinaten)
// — Trainingsdaten für einen Stempel-Detektor (Scan-Datensatz: stamp.yaml).
// Kartenseite und Admin-Liste „Play!-Stempel".
export const DEFAULT_STAMP: number[][] = [[0.42, 0.43], [0.58, 0.43], [0.58, 0.51], [0.42, 0.51]];

export default function StampEditor({ cardId, photo, version, initialQuad, onSaved, onClose }: {
  cardId: number;
  photo: string;            // eigenes Foto (Pfad unter /images)
  version?: string | null;  // Cache-Bust
  initialQuad?: number[][] | null;
  onSaved: (card: Card) => void;
  onClose: () => void;
}) {
  const { t } = useI18n();
  const src = imageUrl(photo, API_BASE, version ?? undefined);
  if (!src) return null;
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
      onCancel={onClose}
      onApply={(quad) => void save(quad)}
      extra={initialQuad ? (
        <button type="button" onClick={() => void save(null)} data-testid="stamp-remove"
          className="text-red-400 hover:text-red-300 text-sm">{t.stamp_remove}</button>
      ) : undefined}
    />
  );
}
