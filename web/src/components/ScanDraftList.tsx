"use client";
// Offene Scan-Entwürfe als Liste: kompakt oben auf der Scan-Seite, ausführlich
// auf /scan/drafts. Fortsetzen übernimmt der Aufrufer, Verwerfen die Liste.
import Link from "next/link";
import { useEffect, useState } from "react";
import { X } from "lucide-react";
import { ScanDraftSummary, scanDraftApi } from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import { removeScanDraft } from "@/lib/useScanDrafts";
import { ringOrder } from "@/lib/cropOrient";

/** Foto des Entwurfs als Blob-URL (braucht die Anmeldung): Vorschau oder das ganze Foto. */
function useDraftImage(id: number, full = false): string | null {
  const [url, setUrl] = useState<string | null>(null);
  useEffect(() => {
    let alive = true;
    let made: string | null = null;
    (full ? scanDraftApi.photo(id) : scanDraftApi.thumb(id)).then((r) => {
      if (!alive) return;
      made = URL.createObjectURL(r.data);
      setUrl(made);
    }).catch(() => {});
    return () => {
      alive = false;
      if (made) URL.revokeObjectURL(made);
    };
  }, [id, full]);
  return url;
}

/** Kleines Vorschaubild (das ganze Foto, eingepasst). */
export function DraftThumb({ id, className }: { id: number; className: string }) {
  const url = useDraftImage(id);
  return url
    // eslint-disable-next-line @next/next/no-img-element
    ? <img src={url} alt="" className={`${className} object-contain bg-black`} />
    : <div className={`${className} bg-gray-800`} />;
}

/**
 * Das ganze Scan-Foto mit den Umrissen der Karten darin: gelb = noch zu prüfen,
 * grün = schon gespeichert. full = Originalauflösung (Großansicht).
 */
export function DraftPhoto({ draft, full = false, imgClassName = "block w-full h-auto" }: {
  draft: ScanDraftSummary; full?: boolean; imgClassName?: string;
}) {
  const url = useDraftImage(draft.id, full);
  const outlines = draft.summary.outlines ?? [];
  if (!url) return <div className="aspect-[4/3] w-full rounded bg-gray-800" />;
  return (
    <div className="relative inline-block max-w-full align-top" data-testid="scan-draft-photo">
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img src={url} alt="" className={`${imgClassName} rounded`} />
      {outlines.length > 0 && (
        <svg className="pointer-events-none absolute inset-0 h-full w-full" viewBox="0 0 1 1" preserveAspectRatio="none">
          {outlines.map((o, i) => (
            <polygon key={i} points={ringOrder(o.quad).map(([x, y]) => `${x},${y}`).join(" ")}
              fill={o.saved ? "rgba(34,197,94,0.18)" : "rgba(250,204,21,0.10)"}
              stroke={o.saved ? "#22c55e" : "#facc15"} strokeWidth={full ? 2.5 : 2} vectorEffect="non-scaling-stroke"
              data-testid={o.saved ? "draft-outline-saved" : "draft-outline-open"} />
          ))}
        </svg>
      )}
    </div>
  );
}

/** Großansicht: das Originalfoto mit den Umrissen; Fortsetzen von hier aus. */
export function DraftPhotoViewer({ draft, onClose, onResume }: {
  draft: ScanDraftSummary | null; onClose: () => void; onResume: (id: number) => void;
}) {
  const { t } = useI18n();
  useEffect(() => {
    if (!draft) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      window.removeEventListener("keydown", onKey);
      document.body.style.overflow = prev;
    };
  }, [draft, onClose]);
  if (!draft) return null;
  const outlines = draft.summary.outlines ?? [];
  return (
    <div role="dialog" aria-modal="true" aria-label={t.scan_draft_photo} onClick={onClose} data-testid="scan-draft-viewer"
      className="fixed inset-0 z-50 flex flex-col items-center justify-center gap-3 bg-black/90 p-3">
      <button type="button" onClick={onClose} aria-label={t.detail_close_image}
        className="absolute right-3 top-3 flex h-10 w-10 items-center justify-center rounded-full bg-black/40 text-white/80 hover:bg-black/60 hover:text-white">
        <X size={24} />
      </button>
      <div onClick={(e) => e.stopPropagation()} className="max-w-full">
        <DraftPhoto draft={draft} full imgClassName="block h-auto w-auto max-h-[78vh] max-w-full" />
      </div>
      <div onClick={(e) => e.stopPropagation()} className="flex flex-wrap items-center justify-center gap-3 text-xs text-gray-300">
        {outlines.length > 0 && (
          <span>
            <span className="mr-1 inline-block h-2.5 w-2.5 rounded-sm border-2 border-yellow-400 align-middle" />{t.scan_draft_legend_open}
            <span className="ml-3 mr-1 inline-block h-2.5 w-2.5 rounded-sm border-2 border-green-500 bg-green-500/30 align-middle" />{t.scan_draft_legend_saved}
          </span>
        )}
        <button type="button" onClick={() => onResume(draft.id)} data-testid="scan-draft-viewer-resume"
          className="rounded bg-pokemon-blue px-3 py-1.5 text-white hover:opacity-90">
          {t.scan_draft_resume}
        </button>
      </div>
    </div>
  );
}

type Props = {
  drafts: ScanDraftSummary[];
  onResume: (id: number) => void;
  busy?: boolean;
  /** Ausführlich: größeres Bild, Ziel und Modus */
  detailed?: boolean;
  /** Ziel-Namen (Sammlung/Deck) für die ausführliche Ansicht */
  placeName?: (d: ScanDraftSummary) => string | null;
};

export default function ScanDraftList({ drafts, onResume, busy, detailed, placeName }: Props) {
  const { t, lang } = useI18n();
  const [viewing, setViewing] = useState<ScanDraftSummary | null>(null);
  const discard = (id: number) => {
    if (!confirm(t.scan_draft_discard_confirm)) return;
    void removeScanDraft(id);
  };
  const modeLabel = (m?: string | null) =>
    m === "single" ? t.scan_mode_single : m === "multi" ? t.scan_mode_multi : m === "binder" ? t.scan_mode_binder : null;
  const targetLabel = (x?: string | null) =>
    x === "pokedex" ? t.scan_target_pokedex : x === "collection" ? t.scan_target_collection
      : x === "deck" ? t.scan_target_deck : x === "wishlist" ? t.scan_target_wishlist : null;

  const info = (d: ScanDraftSummary) => {
    const names = d.summary.names ?? [];
    const more = (d.summary.open ?? 0) - names.length;
    const when = d.aktualisiert_am
      ? new Date(d.aktualisiert_am).toLocaleString(lang === "EN" ? "en-US" : "de-DE", { dateStyle: "short", timeStyle: "short" })
      : "";
    const empty = !(d.summary.open ?? 0) && !(d.summary.saved ?? 0);
    return {
      // nichts erkannt: Foto zum Markieren von Hand
      names: empty ? t.scan_draft_none_found
        : (names.join(", ") || "—") + (more > 0 ? ` ${t.scan_draft_more_cards(more)}` : ""),
      place: [targetLabel(d.summary.target), placeName?.(d), modeLabel(d.summary.mode)].filter(Boolean).join(" · "),
      meta: t.scan_draft_meta(d.summary.open ?? 0, d.summary.saved ?? 0, when),
    };
  };

  if (!detailed) {
    // kompakt (Scan-Seite): kleines Vorschaubild, eine Zeile je Entwurf
    return (
      <ul className="space-y-2">
        {drafts.map((d) => {
          const x = info(d);
          return (
            <li key={d.id} className="flex items-center gap-3" data-testid="scan-draft">
              <button type="button" onClick={() => onResume(d.id)} disabled={busy} className="shrink-0" tabIndex={-1} aria-hidden>
                <DraftThumb id={d.id} className="h-12 w-12 rounded" />
              </button>
              <div className="min-w-0 flex-1 text-xs">
                <div className="truncate text-gray-200">{x.names}</div>
                <div className="text-gray-500">{x.meta}</div>
              </div>
              <button type="button" onClick={() => onResume(d.id)} disabled={busy} data-testid="scan-draft-resume"
                className="shrink-0 rounded bg-pokemon-blue px-3 py-1.5 text-xs text-white hover:opacity-90 disabled:opacity-50">
                {t.scan_draft_resume}
              </button>
              <button type="button" onClick={() => discard(d.id)} aria-label={t.scan_draft_discard}
                title={t.scan_draft_discard} data-testid="scan-draft-discard"
                className="shrink-0 px-1 text-gray-500 hover:text-red-400">✕</button>
            </li>
          );
        })}
      </ul>
    );
  }

  // ausführlich (Offene Scans): das ganze Foto mit den Karten darin, antippen = groß
  return (
    <>
      <ul className="divide-y divide-gray-800">
        {drafts.map((d) => {
          const x = info(d);
          return (
            <li key={d.id} className="flex flex-col gap-3 py-4 sm:flex-row" data-testid="scan-draft">
              <button type="button" onClick={() => setViewing(d)} title={t.scan_draft_photo} aria-label={t.scan_draft_photo}
                data-testid="scan-draft-photo-open" className="w-full shrink-0 cursor-zoom-in self-start text-left sm:w-64">
                <DraftPhoto draft={d} imgClassName="block h-auto w-auto max-w-full max-h-[45vh] sm:max-h-56" />
              </button>
              <div className="flex min-w-0 flex-1 flex-col gap-1 text-sm">
                <div className="text-gray-200">{x.names}</div>
                {x.place && <div className="text-xs text-gray-400">{x.place}</div>}
                <div className="text-xs text-gray-500">{x.meta}</div>
                <div className="mt-2 flex items-center gap-2 sm:mt-auto">
                  <button type="button" onClick={() => onResume(d.id)} disabled={busy} data-testid="scan-draft-resume"
                    className="rounded bg-pokemon-blue px-3 py-1.5 text-xs text-white hover:opacity-90 disabled:opacity-50">
                    {t.scan_draft_resume}
                  </button>
                  <button type="button" onClick={() => discard(d.id)} data-testid="scan-draft-discard"
                    className="rounded px-2 py-1.5 text-xs text-gray-400 hover:bg-gray-800 hover:text-red-300">
                    ✕ {t.scan_draft_discard}
                  </button>
                </div>
              </div>
            </li>
          );
        })}
      </ul>
      <DraftPhotoViewer draft={viewing} onClose={() => setViewing(null)}
        onResume={(id) => { setViewing(null); onResume(id); }} />
    </>
  );
}

/**
 * Kasten oben auf der Scan-Seite: die neuesten drei, Rest auf /scan/drafts.
 * compact (Serienscan, Kamera an): nur eine Zeile, die Aufnahme bleibt oben.
 */
export function ScanDraftPanel({ drafts, onResume, busy, compact }: {
  drafts: ScanDraftSummary[]; onResume: (id: number) => void; busy?: boolean; compact?: boolean;
}) {
  const { t } = useI18n();
  if (!drafts.length) return null;
  if (compact) {
    return (
      <Link href="/scan/drafts" data-testid="scan-drafts-compact"
        className="flex items-center justify-between rounded-lg border border-pokemon-blue/40 bg-pokemon-card px-3 py-2 text-sm text-gray-200 hover:border-pokemon-blue">
        <span>{t.scan_drafts_title(drafts.length)}</span>
        <span className="text-xs text-pokemon-blue">{t.scan_drafts_review} →</span>
      </Link>
    );
  }
  return (
    <div className="rounded-lg border border-pokemon-blue/40 bg-pokemon-card p-3" data-testid="scan-drafts">
      <div className="mb-2 flex flex-wrap items-baseline justify-between gap-x-2">
        <h2 className="text-sm font-medium text-white">{t.scan_drafts_title(drafts.length)}</h2>
        <span className="text-[11px] text-gray-500">{t.scan_drafts_hint}</span>
      </div>
      <ScanDraftList drafts={drafts.slice(0, 3)} onResume={onResume} busy={busy} />
      {drafts.length > 3 && (
        <Link href="/scan/drafts" className="mt-2 inline-block text-xs text-pokemon-blue hover:underline" data-testid="scan-drafts-all">
          {t.scan_drafts_all(drafts.length)} →
        </Link>
      )}
    </div>
  );
}
