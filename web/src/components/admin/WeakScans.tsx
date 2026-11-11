"use client";
import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import toast from "react-hot-toast";
import { API_BASE, ScanQualityItem, ScanQualityOverview, cardApi, scanQualityApi } from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import { imageUrl, spiegelUrl } from "@/lib/utils";
import CornerEditor from "@/components/CornerEditor";
import { cropToCardPhoto, CropTransform } from "@/lib/cardCrop";

// Admin: gespeicherte Scan-Fotos, die schlecht zu ihrer Karte passen
// (backend/app/services/scan_quality.py). Vergrößern zeigt Foto und
// Katalogbild nebeneinander; „Zuschnitt korrigieren" öffnet den Eck-Editor
// hier auf dem Originalfoto (mit vorgeschlagenen Ecken) und prüft danach neu.
type Tab = "low" | "no_reference" | "dismissed";
// Unter so vielen Treffern ist die Lage der Ecken nicht messbar (matcher MIN_GOOD)
const MIN_FOR_CORNERS = 12;
const cornerErr = (inliers: number, err: number | null) => (inliers >= MIN_FOR_CORNERS ? err : null);
type Fixed = { ok: boolean; stats: string };

export default function WeakScans() {
  const { t, lang } = useI18n();
  const [tab, setTab] = useState<Tab>("low");
  const [data, setData] = useState<ScanQualityOverview | null>(null);
  const [zoom, setZoom] = useState<ScanQualityItem | null>(null);
  // Eck-Editor auf dem Original
  const [editing, setEditing] = useState<{ item: ScanQualityItem; url: string; blob: Blob } | null>(null);
  const [busyId, setBusyId] = useState<number | null>(null);
  const [fixed, setFixed] = useState<Record<number, Fixed>>({});

  const load = useCallback(() => {
    scanQualityApi.overview(tab).then((r) => setData(r.data)).catch(() => {});
  }, [tab]);
  useEffect(() => { load(); }, [load]);
  // Während der Prüfung läuft: Stand nachladen
  const running = !!data?.job.running;
  useEffect(() => {
    if (!running) return;
    const h = window.setInterval(load, 2000);
    return () => window.clearInterval(h);
  }, [running, load]);

  const run = async (full: boolean) => {
    try {
      await scanQualityApi.run(full);
      load();
    } catch {
      toast.error(t.form_save_error);
    }
  };
  const dismiss = async (id: number) => {
    try {
      await scanQualityApi.dismiss(id);
      setData((d) => d && { ...d, items: d.items.filter((i) => i.card_id !== id) });
    } catch {
      toast.error(t.form_save_error);
    }
  };

  const openEditor = async (item: ScanQualityItem) => {
    const src = imageUrl(item.original, API_BASE, item.checked_at);
    if (!src) return;
    setBusyId(item.card_id);
    try {
      const blob = await (await fetch(src)).blob();
      setEditing({ item, blob, url: URL.createObjectURL(blob) });
    } catch {
      toast.error(t.detail_upload_error);
    } finally {
      setBusyId(null);
    }
  };
  const closeEditor = () => {
    setEditing((e) => { if (e) URL.revokeObjectURL(e.url); return null; });
  };

  const applyCrop = async (quad: number[][], transform: CropTransform) => {
    if (!editing) return;
    const { item, blob } = editing;
    closeEditor();
    setBusyId(item.card_id);
    try {
      const file = await cropToCardPhoto(blob, { quad, ...transform });
      const card = (await cardApi.recrop(item.card_id, file, quad)).data;
      // neuer Dateiname: Vorschau sofort auf das neue Foto umstellen
      const photo = card.bild_scan_pfad ?? card.bild_karte_pfad ?? item.photo;
      setData((d) => d && { ...d, items: d.items.map((i) => i.card_id === item.card_id
        ? { ...i, photo, quad, checked_at: new Date().toISOString() } : i) });
      toast.success(t.fix_crop_saved);
      // gleich neu prüfen und das Ergebnis in der Liste zeigen
      try {
        const r = (await scanQualityApi.check(item.card_id)).data;
        const stats = t.weak_stats(r.inliers, cornerErr(r.inliers, r.corner_err));
        setFixed((f) => ({ ...f, [item.card_id]: { ok: r.status !== "low", stats } }));
        setData((d) => d && { ...d, items: d.items.map((i) => i.card_id === item.card_id ? {
          ...i, inliers: r.inliers, corner_err: r.corner_err, photo: r.photo ?? photo, quad,
          suggested_quad: r.suggested_quad, has_suggestion: !!r.suggested_quad, checked_at: new Date().toISOString(),
        } : i) });
      } catch {
        setFixed((f) => ({ ...f, [item.card_id]: { ok: true, stats: "" } }));
      }
    } catch {
      toast.error(t.detail_upload_error);
    } finally {
      setBusyId(null);
    }
  };

  const c = data?.counts;
  const checked = c ? c.ok + c.low + c.no_reference + c.error + c.dismissed : 0;
  const nameOf = (it: ScanQualityItem) => (lang === "EN" && it.english_name) || it.name;
  return (
    <div className="space-y-3" data-testid="weak-scans">
      <p className="text-gray-400 text-xs">{t.weak_hint}</p>
      {c && <p className="text-gray-300 text-sm">{t.weak_counts(checked, c.photos, c.low, c.no_reference, c.unchecked)}</p>}
      <div className="flex flex-wrap items-center gap-2">
        <button type="button" onClick={() => void run(false)} disabled={running}
          className="bg-blue-700 text-white text-sm px-3 py-1.5 rounded hover:bg-blue-600 disabled:opacity-50">
          {t.weak_run}
        </button>
        <button type="button" onClick={() => void run(true)} disabled={running}
          className="bg-gray-700 text-white text-sm px-3 py-1.5 rounded hover:bg-gray-600 disabled:opacity-50">
          {t.weak_run_all}
        </button>
        {running && data && <span className="text-xs text-amber-300">{t.weak_running(data.job.done, data.job.total)}</span>}
      </div>
      {!running && data?.job.error && <p className="text-xs text-red-400">{t.weak_error(data.job.error)}</p>}

      <div className="flex gap-1 rounded-lg bg-gray-800/70 p-1 text-xs" role="tablist">
        {([["low", t.weak_tab_low], ["no_reference", t.weak_tab_noref], ["dismissed", t.weak_tab_dismissed]] as const).map(([k, label]) => (
          <button key={k} type="button" role="tab" aria-selected={tab === k} onClick={() => setTab(k)}
            className={`flex-1 rounded-md px-2 py-1 ${tab === k ? "bg-pokemon-blue text-white" : "text-gray-300 hover:bg-gray-700"}`}>
            {label}{c ? ` (${k === "low" ? c.low : k === "no_reference" ? c.no_reference : c.dismissed})` : ""}
          </button>
        ))}
      </div>

      {data && data.items.length === 0 && <p className="text-gray-500 text-sm">{t.weak_empty}</p>}
      <ul className="space-y-2">
        {data?.items.map((it) => {
          const src = imageUrl(it.photo, API_BASE, it.checked_at);
          const fx = fixed[it.card_id];
          return (
            <li key={it.card_id} data-testid="weak-item"
              className={`flex gap-3 rounded p-2 ${fx ? (fx.ok ? "bg-green-900/20" : "bg-amber-900/20") : "bg-gray-800/60"}`}>
              <button type="button" onClick={() => setZoom(it)} title={t.weak_zoom} aria-label={t.weak_zoom}
                data-testid="weak-zoom" className="relative h-20 w-14 shrink-0 cursor-zoom-in">
                {/* eslint-disable-next-line @next/next/no-img-element */}
                {src ? <img src={src} alt="" loading="lazy" className="h-20 w-14 rounded object-cover" />
                  : <div className="h-20 w-14 rounded bg-gray-700" />}
                <span className="absolute bottom-0.5 right-0.5 rounded bg-black/70 px-1 text-[10px] text-white">🔍</span>
              </button>
              <div className="min-w-0 flex-1 text-xs">
                <div className="truncate text-sm text-white">{nameOf(it)}</div>
                <div className="truncate text-gray-400">{[it.set, it.number, it.tcgdex_card_id].filter(Boolean).join(" · ")}</div>
                {fx ? (
                  <div className={fx.ok ? "text-green-300" : "text-amber-300"} data-testid="weak-fixed">
                    ✓ {!fx.stats ? t.fix_crop_saved : fx.ok ? t.weak_fixed(fx.stats) : t.weak_still_weak(fx.stats)}
                  </div>
                ) : (
                  <div className="text-amber-300">{t.weak_stats(it.inliers, cornerErr(it.inliers, it.corner_err))}</div>
                )}
                {!it.own && it.username && <div className="text-gray-500">{t.weak_other_user(it.username)}</div>}
                <div className="mt-1 flex flex-wrap items-center gap-2">
                  {it.own && it.has_original && (
                    <button type="button" onClick={() => void openEditor(it)} disabled={busyId === it.card_id}
                      data-testid="weak-fix"
                      className="rounded bg-pokemon-blue px-2 py-0.5 text-white hover:opacity-90 disabled:opacity-50">
                      {busyId === it.card_id ? t.weak_rechecking : `✥ ${t.weak_fix}`}
                    </button>
                  )}
                  {it.own && (
                    <Link href={`/cards/${it.card_id}`} className="rounded bg-gray-700 px-2 py-0.5 text-gray-200 hover:text-white">
                      {it.has_original ? t.weak_open_card : t.weak_no_original} →
                    </Link>
                  )}
                  {tab !== "dismissed" && (
                    <button type="button" onClick={() => void dismiss(it.card_id)}
                      className="rounded px-2 py-0.5 text-gray-400 hover:bg-gray-700 hover:text-white">✓ {t.weak_dismiss}</button>
                  )}
                </div>
              </div>
            </li>
          );
        })}
      </ul>

      {zoom && <CompareView item={zoom} name={nameOf(zoom)} onClose={() => setZoom(null)}
        onFix={zoom.own && zoom.has_original ? () => { const it = zoom; setZoom(null); void openEditor(it); } : undefined} />}

      {editing && (
        <CornerEditor
          imageUrl={editing.url}
          initialQuad={editing.item.suggested_quad ?? editing.item.quad ?? [[0.1, 0.08], [0.9, 0.08], [0.9, 0.92], [0.1, 0.92]]}
          onCancel={closeEditor}
          onApply={(quad, transform) => void applyCrop(quad, transform)}
        />
      )}
    </div>
  );
}

// Vergrößert: dein Foto und das Katalogbild nebeneinander (zeigt, ob der
// Zuschnitt schief sitzt oder die Karte gar nicht passt).
function CompareView({ item, name, onClose, onFix }: {
  item: ScanQualityItem; name: string; onClose: () => void; onFix?: () => void;
}) {
  const { t } = useI18n();
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => { window.removeEventListener("keydown", onKey); document.body.style.overflow = prev; };
  }, [onClose]);
  const photo = imageUrl(item.photo, API_BASE, item.checked_at);
  const catalog = spiegelUrl(item.catalog_image, API_BASE);
  return (
    <div role="dialog" aria-modal="true" aria-label={name} onClick={onClose} data-testid="weak-compare"
      className="fixed inset-0 z-50 flex flex-col items-center justify-center gap-3 bg-black/90 p-4">
      <button type="button" onClick={onClose} aria-label={t.detail_close_image}
        className="absolute right-3 top-3 flex h-10 w-10 items-center justify-center rounded-full bg-black/40 text-3xl leading-none text-white/80 hover:bg-black/60 hover:text-white">
        ×
      </button>
      <div className="text-sm text-white">{name} · <span className="text-amber-300">{t.weak_stats(item.inliers, cornerErr(item.inliers, item.corner_err))}</span></div>
      <div className="flex max-h-[80vh] min-h-0 w-full max-w-4xl flex-1 gap-3" onClick={(e) => e.stopPropagation()}>
        {([[t.weak_photo, photo], [t.weak_catalog, catalog]] as const).map(([label, src]) => (
          <figure key={label} className="flex min-h-0 min-w-0 flex-1 flex-col items-center gap-1">
            {/* eslint-disable-next-line @next/next/no-img-element */}
            {src ? <img src={src} alt={label} className="min-h-0 max-w-full flex-1 rounded-lg object-contain" />
              : <div className="w-full flex-1 rounded-lg bg-gray-800" />}
            <figcaption className="text-xs text-gray-400">{label}</figcaption>
          </figure>
        ))}
      </div>
      {onFix && (
        <button type="button" onClick={(e) => { e.stopPropagation(); onFix(); }}
          className="rounded bg-pokemon-blue px-4 py-1.5 text-sm text-white hover:opacity-90">✥ {t.weak_fix}</button>
      )}
    </div>
  );
}
