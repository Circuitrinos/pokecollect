"use client";
import { useEffect, useState } from "react";
import Image from "@/components/AppImage";
import Link from "next/link";
import { CollectionCard } from "@/lib/api";
import { cardImageSrc } from "@/lib/utils";
import RarityBadge from "@/components/RarityBadge";
import { useI18n } from "@/lib/i18n";
import { useLongPress } from "@/lib/useLongPress";

type Props = {
  cards: CollectionCard[];
  apiBase: string;
  onReorder: (orderedIds: number[]) => void;
  onRemove: (cardId: number) => void;
  /** Auswahl-Modus: Tippen wählt, kein Ziehen/Entfernen. */
  selection?: { active: boolean; ids: Set<number>; toggle: (id: number) => void } | null;
};

export default function SortableCardGrid({ cards, apiBase, onReorder, onRemove, selection }: Props) {
  const selecting = !!selection?.active;
  const { t, lang } = useI18n();
  const [order, setOrder] = useState<CollectionCard[]>(cards);
  const [dragId, setDragId] = useState<number | null>(null);
  // Handy: Karte halten → aufgenommen, dann die Karte antippen, an deren Platz sie soll
  const [picked, setPicked] = useState<number | null>(null);
  const longPress = useLongPress((id) => setPicked(id), !selecting);

  useEffect(() => { setOrder(cards); }, [cards]);
  useEffect(() => { if (selecting) setPicked(null); }, [selecting]);

  const handleDropOn = (targetId: number, movingId: number | null = dragId) => {
    if (movingId == null || movingId === targetId) { setDragId(null); return; }
    const next = [...order];
    const from = next.findIndex((c) => c.id === movingId);
    const to = next.findIndex((c) => c.id === targetId);
    if (from < 0 || to < 0) { setDragId(null); return; }
    const [moved] = next.splice(from, 1);
    next.splice(to, 0, moved);
    setOrder(next);
    setDragId(null);
    onReorder(next.map((c) => c.id));
  };

  return (
    <div>
      {!selecting && (picked != null ? (
        <div className="mb-2 flex items-center gap-2 rounded bg-pokemon-yellow/10 border border-pokemon-yellow/40 px-3 py-1.5 text-xs text-pokemon-yellow">
          <span>{t.grid_moving((() => { const c = order.find((x) => x.id === picked); return c ? (lang === "EN" && c.englischer_name ? c.englischer_name : c.kartenname) : ""; })())}</span>
          <button type="button" onClick={() => setPicked(null)} className="underline shrink-0">{t.binder_cancel}</button>
        </div>
      ) : <p className="text-gray-500 text-xs mb-2">{t.collection_reorder_hint}</p>)}
      <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 xl:grid-cols-6 gap-3">
        {order.map((card) => {
          const { src, isPlaceholder } = cardImageSrc(card, apiBase);
          const name = lang === "EN" && card.englischer_name ? card.englischer_name : card.kartenname;
          const isSel = selecting && selection!.ids.has(card.id);
          return (
            <div
              key={card.id}
              draggable={!selecting && picked == null}
              {...longPress.bind(card.id)}
              style={{ WebkitTouchCallout: "none" }}
              onDragStart={() => setDragId(card.id)}
              onDragOver={(e) => e.preventDefault()}
              onDrop={() => handleDropOn(card.id)}
              role={selecting ? "checkbox" : undefined}
              aria-checked={selecting ? isSel : undefined}
              tabIndex={selecting ? 0 : undefined}
              onClick={selecting ? () => selection!.toggle(card.id) : undefined}
              onKeyDown={selecting ? (e) => { if (e.key === " " || e.key === "Enter") { e.preventDefault(); selection!.toggle(card.id); } } : undefined}
              className={`relative rounded-lg border-2 overflow-hidden ${selecting ? "cursor-pointer" : "cursor-move"} bg-pokemon-card ${
                card.besessen ? "border-gray-600" : "border-gray-700 opacity-60"
              } ${dragId === card.id ? "ring-2 ring-pokemon-yellow" : ""} ${isSel ? "ring-4 ring-pokemon-blue ring-offset-1 ring-offset-pokemon-dark" : ""} ${
                picked === card.id ? "ring-4 ring-pokemon-yellow scale-95 opacity-80" : picked != null ? "ring-1 ring-pokemon-yellow/40" : ""} select-none`}
            >
              {selecting && (
                <span aria-hidden className={`absolute top-1 right-1 z-20 flex h-6 w-6 items-center justify-center rounded-full border-2 text-sm font-bold ${
                  isSel ? "border-pokemon-blue bg-pokemon-blue text-white" : "border-white/80 bg-black/40 text-transparent"}`}>✓</span>
              )}
              <Link href={`/cards/${card.id}`} draggable={false} onClick={(e) => {
                if (selecting || longPress.suppressed(card.id)) { e.preventDefault(); return; }
                if (picked != null) {
                  e.preventDefault();
                  if (picked !== card.id) handleDropOn(card.id, picked);
                  setPicked(null);
                }
              }}>
                <div className="aspect-[63/88] relative bg-gray-800">
                  {src ? (
                    <Image
                      src={src}
                      alt={name}
                      fill
                      draggable={false}
                      className={isPlaceholder ? "object-contain p-2 opacity-70" : "object-cover"}
                      sizes="(max-width: 640px) 45vw, 15vw"
                    />
                  ) : (
                    <div className="flex items-center justify-center h-full text-gray-600 text-xs">
                      {card.pokedex_nr ? `#${String(card.pokedex_nr).padStart(4, "0")}` : "?"}
                    </div>
                  )}
                </div>
              </Link>
              <div className="px-1.5 py-1 flex items-center justify-between gap-1">
                <span className="text-xs text-white truncate">{name}</span>
                <RarityBadge rarity={card.seltenheit} language={card.sprache} size="sm" />
              </div>
              {!selecting && <button type="button"
                onClick={() => onRemove(card.id)}
                title={t.collection_remove}
                className="absolute top-1 right-1 bg-black/60 text-red-300 hover:text-red-100 rounded-full w-5 h-5 flex items-center justify-center text-xs"
              >
                ✕
              </button>}
            </div>
          );
        })}
      </div>
    </div>
  );
}
