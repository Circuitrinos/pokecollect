"use client";
// Sammlung eines Freundes ansehen (nur ansehen): Raster oder Binder, in der
// Sortierung des Besitzers. Kaufpreise und Notizen liefert der Server nicht mit.
import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import { API_BASE, sharedApi, SharedCollectionDetail } from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import { useSettings } from "@/lib/useSettings";
import { formatEur } from "@/lib/utils";
import { GRID_SORTS, GridSort, sortCards } from "@/lib/cardSort";
import { decodeThen } from "@/lib/sortChain";
import CardGrid from "@/components/CardGrid";
import BinderView, { BinderItem } from "@/components/BinderView";
import ViewToggle, { ViewMode } from "@/components/ViewToggle";

export default function SharedCollectionPage() {
  const { t, lang } = useI18n();
  const params = useParams<{ id: string }>();
  const id = Number(params.id);
  const { settings } = useSettings();
  const [data, setData] = useState<SharedCollectionDetail | null>(null);
  const [missing, setMissing] = useState(false);
  const [view, setView] = useState<ViewMode>("grid");

  useEffect(() => {
    sharedApi.get(id)
      .then((r) => { setData(r.data); setView(r.data.view_mode === "binder" ? "binder" : "grid"); })
      .catch(() => setMissing(true));
  }, [id]);

  const sorted = useMemo(() => {
    if (!data) return [];
    const sort = GRID_SORTS.includes(data.grid_sort as GridSort) ? (data.grid_sort as GridSort) : "manual";
    return sortCards(data.cards, sort, lang, data.grid_sort_reverse, decodeThen(data.grid_sort_then, GRID_SORTS));
  }, [data, lang]);

  if (missing) {
    return (
      <div className="max-w-2xl mx-auto space-y-3" data-testid="shared-missing">
        <p className="text-gray-400">{t.shared_not_found}</p>
        <Link href="/collections" className="text-blue-400 text-sm hover:underline">← {t.nav_collections}</Link>
      </div>
    );
  }
  if (!data) return <div className="text-gray-500 p-8">{t.detail_loading}</div>;

  const owner = data.owner.display_name || data.owner.username;
  const total = data.cards.reduce((sum, c) => sum + (c.wert_eur ? parseFloat(c.wert_eur) : 0), 0);
  const placeholders = settings?.placeholder_images_enabled ?? true;
  const binderItems: BinderItem[] = data.cards
    .map((c, idx) => ({ card: c, position: c.position ?? idx }))
    .filter((it) => it.card.besessen);

  return (
    <div className="max-w-6xl mx-auto">
      <div className="mb-4">
        <Link href="/collections" className="text-gray-500 hover:text-white text-sm">← {t.nav_collections}</Link>
      </div>
      <div className="flex items-start justify-between mb-6 flex-wrap gap-3">
        <div className="min-w-0">
          <h1 className="text-2xl font-bold text-white">{data.name}</h1>
          {data.beschreibung && <p className="text-gray-400 text-sm">{data.beschreibung}</p>}
          <p className="text-gray-500 text-xs mt-1" data-testid="shared-meta">
            {t.shared_by(owner)} · {t.shared_view_only} · {t.collections_card_count(data.cards.length)}
            {total > 0 && <> · <span className="text-yellow-400">{formatEur(total.toFixed(2))}</span></>}
          </p>
        </div>
        <ViewToggle value={view} onChange={setView} />
      </div>
      {data.cards.length === 0 ? (
        <p className="text-gray-500 text-sm">{t.shared_empty}</p>
      ) : view === "binder" ? (
        <BinderView items={binderItems} apiBase={API_BASE} placeholderEnabled={placeholders}
          layout={data.binder_layout || "3x3"} binderSlots={data.binder_slots} linkCards={false}
          storageKey={`binder_page_shared_${id}`} />
      ) : (
        <CardGrid cards={sorted} apiBase={API_BASE} placeholderEnabled={placeholders} linkCards={false} />
      )}
    </div>
  );
}
