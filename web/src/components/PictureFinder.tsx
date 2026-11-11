"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import toast from "react-hot-toast";
import { API_BASE, PictureCandidates, catalogApi } from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import { spiegelUrl } from "@/lib/utils";

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
  const fileRef = useRef<HTMLInputElement>(null);
  const [failed, setFailed] = useState<Set<string>>(new Set());   // Vorschläge, die nicht laden

  useEffect(() => {
    let alive = true;
    catalogApi.pictures(cardId).then((r) => { if (alive) setData(r.data); })
      .catch(() => { if (alive) setData(null); })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [cardId]);

  const takeBlob = useCallback((blob: Blob | null | undefined) => {
    if (!blob || !blob.type.startsWith("image/")) return false;
    setPasted((prev) => { if (prev) URL.revokeObjectURL(prev.url); return { blob, url: URL.createObjectURL(blob) }; });
    return true;
  }, []);

  // Strg+V überall im Dialog
  useEffect(() => {
    const onPaste = (e: ClipboardEvent) => {
      const item = Array.from(e.clipboardData?.items ?? []).find((i) => i.type.startsWith("image/"));
      if (item && takeBlob(item.getAsFile())) e.preventDefault();
    };
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
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
            {pasted && (
              <button type="button" disabled={busy} onClick={() => void applyPasted()} data-testid="picture-use-pasted"
                className="rounded bg-green-700 px-3 py-1 text-white hover:bg-green-600 disabled:opacity-50">✓ {t.pic_use}</button>
            )}
          </div>
          <input ref={fileRef} type="file" accept="image/*" className="hidden"
            onChange={(e) => { takeBlob(e.target.files?.[0]); e.target.value = ""; }} />
        </div>
      </div>
    </div>
  );
}
