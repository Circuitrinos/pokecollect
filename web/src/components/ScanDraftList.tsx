"use client";
// Offene Scan-Entwürfe als Liste: kompakt oben auf der Scan-Seite, ausführlich
// auf /scan/drafts. Fortsetzen übernimmt der Aufrufer, Verwerfen die Liste.
import Link from "next/link";
import { useEffect, useState } from "react";
import { ScanDraftSummary, scanDraftApi } from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import { removeScanDraft } from "@/lib/useScanDrafts";

/** Vorschaubild — braucht die Anmeldung, daher als Blob geladen. */
export function DraftThumb({ id, className }: { id: number; className: string }) {
  const [url, setUrl] = useState<string | null>(null);
  useEffect(() => {
    let alive = true;
    let made: string | null = null;
    scanDraftApi.thumb(id).then((r) => {
      if (!alive) return;
      made = URL.createObjectURL(r.data);
      setUrl(made);
    }).catch(() => {});
    return () => {
      alive = false;
      if (made) URL.revokeObjectURL(made);
    };
  }, [id]);
  return url
    // eslint-disable-next-line @next/next/no-img-element
    ? <img src={url} alt="" className={`${className} object-cover`} />
    : <div className={`${className} bg-gray-800`} />;
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
  const discard = (id: number) => {
    if (!confirm(t.scan_draft_discard_confirm)) return;
    void removeScanDraft(id);
  };
  const modeLabel = (m?: string | null) =>
    m === "single" ? t.scan_mode_single : m === "multi" ? t.scan_mode_multi : m === "binder" ? t.scan_mode_binder : null;
  const targetLabel = (x?: string | null) =>
    x === "pokedex" ? t.scan_target_pokedex : x === "collection" ? t.scan_target_collection
      : x === "deck" ? t.scan_target_deck : x === "wishlist" ? t.scan_target_wishlist : null;

  return (
    <ul className={detailed ? "divide-y divide-gray-800" : "space-y-2"}>
      {drafts.map((d) => {
        const names = d.summary.names ?? [];
        const more = (d.summary.open ?? 0) - names.length;
        const when = d.aktualisiert_am
          ? new Date(d.aktualisiert_am).toLocaleString(lang === "EN" ? "en-US" : "de-DE", { dateStyle: "short", timeStyle: "short" })
          : "";
        const place = detailed
          ? [targetLabel(d.summary.target), placeName?.(d), modeLabel(d.summary.mode)].filter(Boolean).join(" · ")
          : "";
        return (
          <li key={d.id} className={`flex items-center gap-3 ${detailed ? "py-3" : ""}`} data-testid="scan-draft">
            <button type="button" onClick={() => onResume(d.id)} disabled={busy} className="shrink-0" tabIndex={-1} aria-hidden>
              <DraftThumb id={d.id} className={detailed ? "h-20 w-20 rounded" : "h-12 w-12 rounded"} />
            </button>
            <div className={`min-w-0 flex-1 ${detailed ? "text-sm" : "text-xs"}`}>
              <div className="truncate text-gray-200">
                {names.join(", ") || "—"}{more > 0 ? ` ${t.scan_draft_more_cards(more)}` : ""}
              </div>
              {place && <div className="truncate text-xs text-gray-400">{place}</div>}
              <div className="text-xs text-gray-500">{t.scan_draft_meta(d.summary.open ?? 0, d.summary.saved ?? 0, when)}</div>
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
