"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import toast from "react-hot-toast";
import { API_BASE, PictureCandidates, catalogApi } from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import { spiegelUrl } from "@/lib/utils";
import CornerEditor, { CornerSuggestion } from "@/components/CornerEditor";
import { cropToCardPhoto, CropTransform, loadImg } from "@/lib/cardCrop";

const WHOLE: number[][] = [[0, 0], [1, 0], [1, 1], [0, 1]];
const CATALOG_WIDTH = 734;     // Breite eines zugeschnittenen Katalogbilds (wie pokemontcg.io hires)

/** Großes Bild für den Vorschlag verkleinern (die Ecken sind normiert, die Auflösung zählt nicht). */
async function shrink(blob: Blob, maxSide = 1600): Promise<Blob> {
  const url = URL.createObjectURL(blob);
  try {
    const img = await loadImg(url);
    const k = Math.min(1, maxSide / Math.max(img.width, img.height));
    if (k >= 1 && blob.size < 4_000_000) return blob;
    const c = document.createElement("canvas");
    c.width = Math.round(img.width * k);
    c.height = Math.round(img.height * k);
    c.getContext("2d")!.drawImage(img, 0, 0, c.width, c.height);
    return await new Promise<Blob>((res) => c.toBlob((b) => res(b ?? blob), "image/jpeg", 0.9));
  } finally {
    URL.revokeObjectURL(url);
  }
}

// Bild finden (Admin): Vorschläge aus offenen Quellen (TCGdex in anderen
// Sprachen, pokemontcg.io, pokemon.com) — oder ein Bild aus der Websuche, das
// der Nutzer kopiert und hier einfügt (der Server ruft keine fremden Seiten ab).
// Das Bild gilt für die Katalogkarte und eigene Karten ohne Bild.
export default function PictureFinder({ cardId, name, onClose, onApplied }: {
  cardId: string;
  name: string;
  onClose: () => void;
  onApplied: (imageUrl: string | null) => void;
}) {
  const { t } = useI18n();
  const [data, setData] = useState<PictureCandidates | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [pasted, setPasted] = useState<{ blob: Blob; url: string } | null>(null);
  // Zuschneiden: Original des eingefügten Bilds (für erneutes Zuschneiden) und offener Eck-Editor
  const originalRef = useRef<Blob | null>(null);
  const [cropUrl, setCropUrl] = useState<string | null>(null);
  const cropOpenRef = useRef(false);
  const smallRef = useRef<Blob | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const [failed, setFailed] = useState<Set<string>>(new Set());   // Vorschläge, die nicht laden

  useEffect(() => {
    let alive = true;
    catalogApi.pictures(cardId).then((r) => { if (alive) setData(r.data); })
      .catch(() => { if (alive) setData(null); })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [cardId]);

  const openCrop = useCallback((blob: Blob) => {
    cropOpenRef.current = true;
    setCropUrl((prev) => { if (prev) URL.revokeObjectURL(prev); return URL.createObjectURL(blob); });
  }, []);
  const closeCrop = () => {
    cropOpenRef.current = false;
    setCropUrl((prev) => { if (prev) URL.revokeObjectURL(prev); return null; });
  };
  const takeBlob = useCallback((blob: Blob | null | undefined) => {
    if (!blob || !blob.type.startsWith("image/")) return false;
    originalRef.current = blob;
    smallRef.current = null;
    setPasted((prev) => { if (prev) URL.revokeObjectURL(prev.url); return { blob, url: URL.createObjectURL(blob) }; });
    openCrop(blob);          // gleich zuschneiden — der Vorschlag findet die Kartenkanten
    return true;
  }, [openCrop]);

  // Eck-Editor: grobe Ecken → Kartenkanten vom Bild-Abgleich
  const suggestCrop = async (quad: number[][]): Promise<CornerSuggestion | null> => {
    const orig = originalRef.current;
    if (!orig) return null;
    smallRef.current ??= await shrink(orig);
    const r = await catalogApi.pictureOutline(cardId, smallRef.current, quad);
    if (!r.data.quad) return null;
    // Artwork eines gleichnamigen Drucks oder Kanten, die wie ein Kartenrahmen aussehen = verlässlich;
    // nur an den Rahmen angelegt oder nur Kanten = bitte prüfen
    if (r.data.method === "reference")
      return { quad: r.data.quad, ok: true, label: t.pic_crop_matched(r.data.reference_label ?? r.data.reference ?? "") };
    if (r.data.frame_ok) return { quad: r.data.quad, ok: true, label: t.pic_crop_card };
    return { quad: r.data.quad, ok: false, label: r.data.method === "frame" ? t.pic_crop_frame : t.pic_crop_found };
  };
  const applyCrop = async (quad: number[][], transform: CropTransform) => {
    const orig = originalRef.current;
    closeCrop();
    if (!orig) return;
    try {
      const file = await cropToCardPhoto(orig, { quad, ...transform, width: CATALOG_WIDTH });
      setPasted((prev) => { if (prev) URL.revokeObjectURL(prev.url); return { blob: file, url: URL.createObjectURL(file) }; });
    } catch {
      toast.error(t.pic_failed);
    }
  };

  // Strg+V überall im Dialog
  useEffect(() => {
    const onPaste = (e: ClipboardEvent) => {
      if (cropOpenRef.current) return;
      const item = Array.from(e.clipboardData?.items ?? []).find((i) => i.type.startsWith("image/"));
      if (item && takeBlob(item.getAsFile())) e.preventDefault();
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      if (cropOpenRef.current) { cropOpenRef.current = false; setCropUrl((prev) => { if (prev) URL.revokeObjectURL(prev); return null; }); return; }
      onClose();
    };
    window.addEventListener("paste", onPaste);
    window.addEventListener("keydown", onKey);
    return () => { window.removeEventListener("paste", onPaste); window.removeEventListener("keydown", onKey); };
  }, [takeBlob, onClose]);
  useEffect(() => () => { if (pasted) URL.revokeObjectURL(pasted.url); }, [pasted]);

  const readClipboard = async () => {
    try {
      for (const item of await navigator.clipboard.read()) {
        const type = item.types.find((ty) => ty.startsWith("image/"));
        if (type && takeBlob(await item.getType(type))) return;
      }
      toast(t.pic_clipboard_empty, { icon: "📋" });
    } catch {
      toast(t.pic_clipboard_empty, { icon: "📋" });
    }
  };

  const finish = (r: { image_url: string | null; cards_updated: number }) => {
    toast.success(t.pic_saved(r.cards_updated));
    onApplied(r.image_url);
  };
  const applyUrl = async (url: string) => {
    setBusy(true);
    try { finish((await catalogApi.usePicture(cardId, url)).data); }
    catch { toast.error(t.pic_failed); }
    finally { setBusy(false); }
  };
  const applyPasted = async () => {
    if (!pasted) return;
    setBusy(true);
    try { finish((await catalogApi.uploadPicture(cardId, pasted.blob)).data); }
    catch { toast.error(t.pic_failed); }
    finally { setBusy(false); }
  };

  const q = encodeURIComponent(data?.query ?? `${name} pokemon card`);
  const visible = (data?.candidates ?? []).filter((c) => !failed.has(c.url));
  return (
    <div className="fixed inset-0 z-[60] flex items-center justify-center bg-black/80 p-4" onClick={(e) => { e.stopPropagation(); onClose(); }}>
      <div role="dialog" aria-modal="true" aria-label={t.pic_title(name)} data-testid="picture-finder"
        className="max-h-[90vh] w-full max-w-2xl overflow-y-auto rounded-lg bg-pokemon-card p-4" onClick={(e) => e.stopPropagation()}>
        <div className="mb-3 flex items-start justify-between gap-2">
          <h2 className="text-lg font-bold text-white">{t.pic_title(name)}</h2>
          <button type="button" onClick={onClose} className="text-xl leading-none text-gray-400 hover:text-white">✕</button>
        </div>

        <h3 className="mb-1 text-sm text-gray-300">{t.pic_suggestions}</h3>
        {loading ? <p className="text-sm text-gray-500">{t.pic_loading}</p>
          : visible.length === 0 ? <p className="text-sm text-gray-500">{t.pic_none}</p> : (
          <div className="grid grid-cols-3 gap-2 sm:grid-cols-4">
            {visible.map((c) => (
              <button key={c.url} type="button" disabled={busy} onClick={() => void applyUrl(c.url)} data-testid="picture-candidate"
                title={`${c.label} · ${t.pic_use}`}
                className="rounded border border-gray-700 p-1 text-left hover:border-pokemon-yellow disabled:opacity-50">
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img src={spiegelUrl(c.spiegel, API_BASE) ?? c.url} alt="" loading="lazy" className="aspect-[63/88] w-full rounded object-contain"
                  onError={() => setFailed((f) => new Set(f).add(c.url))} />
                <div className="mt-0.5 truncate text-[10px] text-gray-400">{c.label}</div>
              </button>
            ))}
          </div>
        )}

        <h3 className="mb-1 mt-4 text-sm text-gray-300">{t.pic_web}</h3>
        <div className="flex flex-wrap gap-2">
          <a href={`https://www.google.com/search?tbm=isch&q=${q}`} target="_blank" rel="noopener noreferrer"
            className="rounded bg-gray-700 px-3 py-1.5 text-sm text-white hover:bg-gray-600">Google Images ↗</a>
          <a href={`https://www.bing.com/images/search?q=${q}`} target="_blank" rel="noopener noreferrer"
            className="rounded bg-gray-700 px-3 py-1.5 text-sm text-white hover:bg-gray-600">Bing Images ↗</a>
        </div>
        <p className="mt-1 text-xs text-gray-500">{t.pic_web_hint}</p>

        <div
          onDragOver={(e) => e.preventDefault()}
          onDrop={(e) => { e.preventDefault(); takeBlob(e.dataTransfer.files?.[0]); }}
          className="mt-3 flex flex-col items-center gap-2 rounded border border-dashed border-gray-600 p-3 text-center text-xs text-gray-400">
          {pasted ? (
            // eslint-disable-next-line @next/next/no-img-element
            <img src={pasted.url} alt="" className="max-h-64 rounded object-contain" data-testid="picture-pasted" />
          ) : <span>{t.pic_drop}</span>}
          <div className="flex flex-wrap justify-center gap-2">
            <button type="button" onClick={() => void readClipboard()} className="rounded bg-gray-700 px-3 py-1 text-white hover:bg-gray-600">📋 {t.pic_paste}</button>
            <button type="button" onClick={() => fileRef.current?.click()} className="rounded bg-gray-700 px-3 py-1 text-white hover:bg-gray-600">📁 {t.pic_upload}</button>
            {pasted && originalRef.current && (
              <button type="button" onClick={() => originalRef.current && openCrop(originalRef.current)} data-testid="picture-crop"
                className="rounded bg-gray-700 px-3 py-1 text-white hover:bg-gray-600">✂ {t.pic_crop}</button>
            )}
            {pasted && (
              <button type="button" disabled={busy} onClick={() => void applyPasted()} data-testid="picture-use-pasted"
                className="rounded bg-green-700 px-3 py-1 text-white hover:bg-green-600 disabled:opacity-50">✓ {t.pic_use}</button>
            )}
          </div>
          <input ref={fileRef} type="file" accept="image/*" className="hidden"
            onChange={(e) => { takeBlob(e.target.files?.[0]); e.target.value = ""; }} />
        </div>
      </div>
      {cropUrl && (
        <div onClick={(e) => e.stopPropagation()}>
          <CornerEditor imageUrl={cropUrl} initialQuad={WHOLE} title={t.pic_crop_title} hint={t.pic_crop_hint}
            onCancel={closeCrop} onApply={(quad, transform) => void applyCrop(quad, transform)}
            suggest={suggestCrop} suggestOnOpen />
        </div>
      )}
    </div>
  );
}
