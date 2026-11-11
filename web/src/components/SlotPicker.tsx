"use client";
import { useEffect, useMemo, useState } from "react";
import { API_BASE, Collection, CollectionCard, collectionApi } from "@/lib/api";
import { cardImageSrc, imgFallback } from "@/lib/utils";
import { useI18n } from "@/lib/i18n";
import { MapPin, X } from "lucide-react";

// Wohin ein Scan in der Sammlung kommt: ein bestimmtes leeres Fach (bzw. beim
// Mappenseiten-Scan eine Seite) statt des ersten freien. Zeigt die Seiten der
// Sammlung klein; belegte Fächer mit Bild, leere antippbar.

export function pageSizeOf(layout: string | null | undefined): number {
  const m = (layout ?? "3x3").match(/^(\d+)x(\d+)$/);
  return m ? Math.max(1, Number(m[1]) * Number(m[2])) : 9;
}

function colsOf(layout: string | null | undefined): number {
  const m = (layout ?? "3x3").match(/^(\d+)x(\d+)$/);
  return m ? Number(m[1]) : 3;
}

type Props = {
  collection: Collection;
  value: number | null;
  onChange: (slot: number | null) => void;
  /** Mappenseiten-Scan: eine Seite wählen statt eines Fachs. */
  pageMode: boolean;
  /** Ändert sich nach dem Speichern → Belegung neu laden. */
  refreshKey?: number;
};

export default function SlotPicker({ collection, value, onChange, pageMode, refreshKey = 0 }: Props) {
  const { t } = useI18n();
  const [open, setOpen] = useState(false);
  const [cards, setCards] = useState<CollectionCard[] | null>(null);
  const ps = pageSizeOf(collection.binder_layout);
  const cols = colsOf(collection.binder_layout);

  useEffect(() => {
    if (!open && cards === null) return;
    let alive = true;
    collectionApi.cards(collection.id).then((r) => { if (alive) setCards(r.data); }).catch(() => {});
    return () => { alive = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, collection.id, refreshKey]);

  const bySlot = useMemo(() => {
    const m = new Map<number, CollectionCard>();
    for (const c of cards ?? []) if (c.position != null) m.set(c.position, c);
    return m;
  }, [cards]);
  const maxSlot = Math.max(-1, ...Array.from(bySlot.keys()));
  const contentPages = maxSlot >= 0 ? Math.floor(maxSlot / ps) + 1 : 1;
  const slotPages = collection.binder_slots ? Math.ceil(collection.binder_slots / ps) : 0;
  const totalPages = Math.max(contentPages, slotPages) + 1;     // + eine leere Seite zum Weitermachen

  const firstFree = () => { let s = 0; while (bySlot.has(s)) s++; return s; };
  const [page, setPage] = useState(0);
  useEffect(() => {
    if (!open || cards === null) return;
    setPage(Math.floor((value ?? (pageMode ? (maxSlot >= 0 ? contentPages * ps : 0) : firstFree())) / ps));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, cards === null]);
  const safePage = Math.min(page, totalPages - 1);

  const label = value == null
    ? (pageMode ? t.scan_slot_new_page : t.scan_slot_next_free)
    : pageMode
      ? t.scan_slot_page(Math.floor(value / ps) + 1)
      : t.scan_slot_pocket(Math.floor(value / ps) + 1, (value % ps) + 1);

  const choose = (slot: number) => {
    onChange(pageMode ? Math.floor(slot / ps) * ps : slot);
    setOpen(false);
  };

  return (
    <div className="rounded-lg border border-gray-800 bg-gray-900/40 px-2 py-1.5 text-xs" data-testid="slot-picker">
      <div className="flex items-center gap-2">
        <MapPin size={14} className="shrink-0 text-pokemon-blue" />
        <span className="min-w-0 truncate">
          <span className="text-white font-medium" data-testid="slot-label">{label}</span>
          <span className="hidden sm:inline text-gray-500"> · {collection.name}</span>
        </span>
        <div className="ml-auto flex shrink-0 items-center gap-1">
          <button type="button" onClick={() => setOpen((o) => !o)}
            className="rounded border border-gray-700 px-2 py-0.5 text-gray-200 hover:text-white">
            {open ? t.form_cancel : pageMode ? t.scan_slot_pick_page : t.scan_slot_pick}
          </button>
          {value != null && (
            <button type="button" onClick={() => onChange(null)} title={pageMode ? t.scan_slot_new_page : t.scan_slot_next_free}
              aria-label={pageMode ? t.scan_slot_new_page : t.scan_slot_next_free}
              className="rounded p-0.5 text-gray-400 hover:text-white">
              <X size={14} />
            </button>
          )}
        </div>
      </div>
      {open && (
        <div className="mt-2">
          {cards === null ? (
            <p className="py-6 text-center text-gray-500">{t.detail_loading}</p>
          ) : (
            <>
              <div className="mb-1.5 flex items-center justify-center gap-3 text-gray-300">
                <button type="button" aria-label="‹" disabled={safePage <= 0} onClick={() => setPage(safePage - 1)}
                  className="rounded px-2 py-0.5 hover:bg-gray-800 disabled:opacity-30">‹</button>
                <span>{t.binder_page(safePage + 1, totalPages)}</span>
                <button type="button" aria-label="›" disabled={safePage >= totalPages - 1} onClick={() => setPage(safePage + 1)}
                  className="rounded px-2 py-0.5 hover:bg-gray-800 disabled:opacity-30">›</button>
              </div>
              <p className="mb-1.5 text-center text-gray-500">{pageMode ? t.scan_slot_hint_page : t.scan_slot_hint}</p>
              <div className={`mx-auto grid max-w-[18rem] gap-1.5 rounded-lg p-1.5 ${
                pageMode && value != null && Math.floor(value / ps) === safePage ? "ring-2 ring-pokemon-yellow" : ""}`}
                style={{ gridTemplateColumns: `repeat(${cols}, minmax(0, 1fr))` }}>
                {Array.from({ length: ps }).map((_, i) => {
                  const slot = safePage * ps + i;
                  const card = bySlot.get(slot);
                  const selected = !pageMode && value === slot;
                  if (card) {
                    const { src, fallbacks } = cardImageSrc(card, API_BASE, true);
                    return (
                      <button type="button" key={slot} disabled={!pageMode} onClick={() => choose(slot)}
                        title={card.kartenname} aria-label={`${t.scan_slot_taken}: ${card.kartenname}`}
                        className="aspect-[63/88] overflow-hidden rounded bg-gray-800 opacity-60 disabled:cursor-not-allowed">
                        {/* eslint-disable-next-line @next/next/no-img-element */}
                        {src && <img src={src} alt="" loading="lazy" onError={imgFallback(fallbacks)} className="h-full w-full object-cover" />}
                      </button>
                    );
                  }
                  return (
                    <button type="button" key={slot} onClick={() => choose(slot)} data-pick-slot={slot}
                      aria-label={pageMode ? t.scan_slot_page(safePage + 1) : t.scan_slot_pocket(safePage + 1, i + 1)}
                      className={`aspect-[63/88] rounded border-2 border-dashed text-[10px] ${
                        selected ? "border-pokemon-yellow bg-pokemon-yellow/10 text-pokemon-yellow"
                          : "border-gray-700 text-gray-600 hover:border-pokemon-blue hover:text-pokemon-blue"}`}>
                      {i + 1}
                    </button>
                  );
                })}
              </div>
            </>
          )}
        </div>
      )}
    </div>
  );
}
