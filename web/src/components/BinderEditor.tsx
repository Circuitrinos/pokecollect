"use client";
import { useEffect, useMemo, useState } from "react";
import { useI18n } from "@/lib/i18n";
import { useCoarsePointer, useLongPress } from "@/lib/useLongPress";
import { cardImageSrc } from "@/lib/utils";
import type { BinderItem } from "@/components/BinderView";

type PagesChange = {
  removeCardIds?: number[];
  positions?: { card_id: number; position: number }[];
  binderSlots?: number;
};

type Props = {
  items: BinderItem[];
  apiBase: string;
  layout: string;
  binderSlots?: number | null;
  placeholderEnabled?: boolean;
  onPagesChange: (payload: PagesChange) => void;
  /** Karten verschieben (Modus „Karten verschieben"): Slot setzen, belegt = tauschen. */
  onMoveToSlot?: (cardId: number, slot: number) => Promise<void> | void;
};

function parseLayout(layout: string): { cols: number; rows: number } {
  const m = layout.match(/^(\d+)x(\d+)$/);
  if (!m) return { cols: 3, rows: 3 };
  return { cols: Number(m[1]), rows: Number(m[2]) };
}

export default function BinderEditor({
  items: itemsProp, apiBase, layout, binderSlots, placeholderEnabled = true, onPagesChange, onMoveToSlot,
}: Props) {
  const { t, lang } = useI18n();
  const { cols, rows } = parseLayout(layout);
  const perPage = cols * rows;
  const [dragPage, setDragPage] = useState<number | null>(null);
  // Handy: Seite halten → aufgenommen, dann die Seite antippen, an deren Stelle sie soll
  const [pickedPage, setPickedPage] = useState<number | null>(null);
  // Modus „Karten verschieben": alle Seiten auf einen Blick, Karten per Tippen
  // (aufnehmen → Ziel antippen) oder Ziehen über den ganzen Binder verschieben.
  const [cardMode, setCardMode] = useState(false);
  const [pickedCard, setPickedCard] = useState<number | null>(null);
  const [dragCard, setDragCard] = useState<number | null>(null);
  // Sofort sichtbare Verschiebungen, bis die neuen Daten vom Server da sind
  const [moved, setMoved] = useState<Map<number, number>>(new Map());
  const serverKey = itemsProp.map((it) => `${it.card.id}:${it.position}`).join(",");
  useEffect(() => { setMoved(new Map()); }, [serverKey]);   // neue Serverdaten ersetzen die Vorschau
  const items = useMemo(
    () => (moved.size ? itemsProp.map((it) => (moved.has(it.card.id) ? { ...it, position: moved.get(it.card.id)! } : it)) : itemsProp),
    [itemsProp, moved],
  );
  const pageHold = useLongPress((p) => setPickedPage(p), !cardMode);
  const coarse = useCoarsePointer();     // Touch: halten/antippen statt Ziehen

  const slotMap = new Map<number, BinderItem["card"]>();
  for (const it of items) slotMap.set(it.position, it.card);
  const nameOf = (c: BinderItem["card"]) => (lang === "EN" && c.englischer_name ? c.englischer_name : c.kartenname);

  const moveCard = (cardId: number, slot: number) => {
    const from = items.find((it) => it.card.id === cardId)?.position;
    if (from == null || from === slot || !onMoveToSlot) return;
    const next = new Map(moved);
    next.set(cardId, slot);
    const occupant = slotMap.get(slot);
    if (occupant) next.set(occupant.id, from);              // belegt → tauschen (wie der Server)
    setMoved(next);
    void onMoveToSlot(cardId, slot);
  };
  const tapPocket = (slot: number) => {
    const card = slotMap.get(slot);
    if (pickedCard == null) { if (card) setPickedCard(card.id); return; }
    if (card?.id === pickedCard) { setPickedCard(null); return; }
    moveCard(pickedCard, slot);
    setPickedCard(null);
  };

  const maxSlot = items.length ? Math.max(...items.map((i) => i.position)) : -1;
  const contentPages = maxSlot >= 0 ? Math.floor(maxSlot / perPage) + 1 : 1;
  const slotPages = binderSlots != null ? Math.ceil(binderSlots / perPage) : 0;
  const totalPages = Math.max(contentPages, slotPages, 1);

  const cardsOnPage = (p: number) =>
    items.filter((it) => Math.floor(it.position / perPage) === p);

  const handleAddPage = () => {
    onPagesChange({ binderSlots: (totalPages + 1) * perPage });
  };

  const handleDeletePage = (p: number) => {
    const onPage = cardsOnPage(p);
    if (onPage.length && !confirm(t.binder_delete_page_warn(onPage.length))) return;
    const removeCardIds = onPage.map((it) => it.card.id);
    // Nachfolgende Seiten rücken eine Seite nach vorne
    const positions: { card_id: number; position: number }[] = [];
    for (const it of items) {
      const op = Math.floor(it.position / perPage);
      if (op <= p) continue;
      const slotInPage = it.position % perPage;
      positions.push({ card_id: it.card.id, position: (op - 1) * perPage + slotInPage });
    }
    onPagesChange({
      removeCardIds,
      positions,
      binderSlots: Math.max(0, (totalPages - 1) * perPage),
    });
  };

  const handleDropPage = (target: number, source: number | null = dragPage) => {
    if (source == null || source === target) { setDragPage(null); return; }
    const order = Array.from({ length: totalPages }, (_, i) => i);
    const [movedPage] = order.splice(source, 1);
    order.splice(target, 0, movedPage);
    // order[k] = alte Seitennummer, die jetzt an Stelle k steht
    const positions: { card_id: number; position: number }[] = [];
    for (const it of items) {
      const op = Math.floor(it.position / perPage);
      const slotInPage = it.position % perPage;
      const newPage = order.indexOf(op);
      positions.push({ card_id: it.card.id, position: newPage * perPage + slotInPage });
    }
    setDragPage(null);
    onPagesChange({ positions });
  };

  return (
    <div>
      <div className="flex items-center justify-between mb-3 flex-wrap gap-2">
        <p className="text-gray-400 text-sm">{cardMode ? t.binder_editor_cards_hint : t.binder_editor_hint}</p>
        <div className="flex items-center gap-2">
          {onMoveToSlot && (
            <label className={`flex items-center gap-2 text-sm rounded px-3 py-1.5 border cursor-pointer select-none ${
              cardMode ? "bg-pokemon-accent border-pokemon-accent text-white" : "bg-pokemon-card border-gray-700 text-gray-200"}`}>
              <input type="checkbox" checked={cardMode}
                onChange={(e) => { setCardMode(e.target.checked); setPickedCard(null); setPickedPage(null); }} />
              {t.binder_move_cards}
            </label>
          )}
          {!cardMode && (
            <button type="button"
              onClick={handleAddPage}
              className="text-sm bg-pokemon-accent text-white rounded px-3 py-1.5 hover:bg-blue-700"
            >
              {t.binder_add_page}
            </button>
          )}
        </div>
      </div>

      {(pickedCard != null || pickedPage != null) && (
        <div className="fixed inset-x-3 z-40 bottom-[calc(4rem_+_env(safe-area-inset-bottom))] md:bottom-4 mx-auto max-w-xl flex items-center justify-center gap-2 rounded bg-pokemon-dark/95 border border-pokemon-yellow/60 px-3 py-2 text-xs text-pokemon-yellow shadow-lg">
          <span>{pickedCard != null
            ? t.binder_moving_all((() => { const c = items.find((it) => it.card.id === pickedCard)?.card; return c ? nameOf(c) : ""; })())
            : t.binder_moving_page(pickedPage! + 1)}</span>
          <button type="button" onClick={() => { setPickedCard(null); setPickedPage(null); }} className="underline shrink-0">{t.binder_cancel}</button>
        </div>
      )}

      <div className={`grid gap-4 ${cardMode ? "grid-cols-2 sm:grid-cols-3 lg:grid-cols-4" : "grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5"}`}>
        {Array.from({ length: totalPages }).map((_, p) => {
          const count = cardsOnPage(p).length;
          return (
            <div
              key={p}
              draggable={!coarse && !cardMode && pickedPage == null}
              onDragStart={() => setDragPage(p)}
              onDragOver={(e) => e.preventDefault()}
              onDrop={() => (cardMode ? undefined : handleDropPage(p))}
              {...(!cardMode ? pageHold.bind(p) : {})}
              onClick={!cardMode && pickedPage != null ? () => {
                if (pageHold.suppressed(p)) return;
                handleDropPage(p, pickedPage);
                setPickedPage(null);
              } : undefined}
              style={{ WebkitTouchCallout: "none" }}
              className={`group relative bg-pokemon-card rounded-lg p-2 border select-none ${cardMode ? "" : "cursor-move"} ${
                dragPage === p || pickedPage === p ? "border-pokemon-yellow" : pickedPage != null ? "border-pokemon-yellow/40" : "border-gray-700"
              }`}
            >
              {!cardMode && (
              <button type="button"
                onClick={(e) => { e.stopPropagation(); handleDeletePage(p); }}
                title={t.collection_remove}
                className="absolute -top-2 -right-2 z-10 bg-red-700 text-white rounded-full w-6 h-6 flex items-center justify-center text-sm opacity-100 md:opacity-0 md:group-hover:opacity-100 transition hover:bg-red-600"
              >
                ✕
              </button>
              )}
              <div
                className="grid gap-0.5 mb-1"
                style={{ gridTemplateColumns: `repeat(${cols}, minmax(0, 1fr))` }}
              >
                {Array.from({ length: perPage }).map((_, i) => {
                  const slot = p * perPage + i;
                  const card = slotMap.get(slot) ?? null;
                  const target = cardMode ? {
                    onClick: () => tapPocket(slot),
                    onDragOver: (e: React.DragEvent) => e.preventDefault(),
                    onDrop: (e: React.DragEvent) => { e.stopPropagation(); if (dragCard != null) moveCard(dragCard, slot); setDragCard(null); },
                  } : {};
                  if (!card) {
                    return <div key={slot} {...target} className={`aspect-[63/88] rounded-sm border border-dashed ${
                      cardMode && pickedCard != null ? "border-pokemon-yellow/70 bg-pokemon-yellow/10 cursor-pointer" : "border-gray-700/50 bg-gray-800/30"}`} />;
                  }
                  const { src } = cardImageSrc(card, apiBase, placeholderEnabled);
                  return (
                    <div
                      key={slot}
                      {...target}
                      draggable={cardMode && !coarse}
                      onDragStart={cardMode ? (e) => { e.stopPropagation(); setDragCard(card.id); } : undefined}
                      onDragEnd={cardMode ? () => setDragCard(null) : undefined}
                      title={cardMode ? nameOf(card) : undefined}
                      className={`aspect-[63/88] rounded-sm bg-gray-800 bg-center bg-cover ${cardMode ? "cursor-pointer" : ""} ${
                        pickedCard === card.id || dragCard === card.id ? "ring-2 ring-pokemon-yellow opacity-70" : ""}`}
                      style={src ? { backgroundImage: `url("${src}")` } : undefined}
                    />
                  );
                })}
              </div>
              <div className="flex items-center justify-between text-[11px] text-gray-400">
                <span>{t.binder_page_n(p + 1)}</span>
                <span>{count > 0 ? t.collections_card_count(count) : ""}</span>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
