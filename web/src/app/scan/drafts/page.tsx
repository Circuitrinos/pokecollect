"use client";
// Offene Scans: Prüfseiten, die als Entwurf gespeichert und noch nicht fertig
// bestätigt sind — von diesem oder einem anderen Gerät. Fortsetzen öffnet die
// Scan-Seite mit dem Entwurf; danach geht es hierher zurück.
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { Collection, DeckSummary, ScanDraftSummary, collectionApi, deckApi } from "@/lib/api";
import ScanDraftList from "@/components/ScanDraftList";
import { useI18n } from "@/lib/i18n";
import { resumeDraftHref } from "@/lib/navRegistry";
import { refreshScanDrafts, useScanDrafts } from "@/lib/useScanDrafts";

export default function ScanDraftsPage() {
  const { t } = useI18n();
  const router = useRouter();
  const drafts = useScanDrafts();
  const [collections, setCollections] = useState<Collection[]>([]);
  const [decks, setDecks] = useState<DeckSummary[]>([]);

  useEffect(() => {
    refreshScanDrafts();
    collectionApi.list().then((r) => setCollections(r.data)).catch(() => {});
    deckApi.list().then((r) => setDecks(r.data)).catch(() => {});
  }, []);

  const placeName = (d: ScanDraftSummary) =>
    d.summary.collection_id != null ? collections.find((c) => c.id === d.summary.collection_id)?.name ?? null
      : d.summary.deck_id != null ? decks.find((x) => x.id === d.summary.deck_id)?.name ?? null
      : null;

  return (
    <div className="max-w-2xl mx-auto space-y-4">
      <div className="flex items-center justify-between gap-3">
        <h1 className="text-2xl font-bold text-white">{t.scan_drafts_page_title}</h1>
        <Link href="/scan" className="shrink-0 rounded bg-pokemon-blue px-3 py-1.5 text-sm text-white hover:bg-blue-500">
          {t.nav_scan}
        </Link>
      </div>
      <p className="text-sm text-gray-400">{t.scan_drafts_page_hint}</p>

      <div className="rounded-lg bg-pokemon-card px-4" data-testid="scan-drafts-page">
        {drafts == null ? (
          <p className="py-6 text-center text-sm text-gray-500">{t.scan_drafts_loading}</p>
        ) : drafts.length === 0 ? (
          <p className="py-6 text-center text-sm text-gray-500" data-testid="scan-drafts-empty">{t.scan_drafts_empty}</p>
        ) : (
          <ScanDraftList drafts={drafts} detailed placeName={placeName} onResume={(id) => router.push(resumeDraftHref(id))} />
        )}
      </div>
    </div>
  );
}
