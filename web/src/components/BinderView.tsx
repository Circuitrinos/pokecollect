"use client";
import { DragEvent as ReactDragEvent, TouchEvent as ReactTouchEvent, useEffect, useMemo, useRef, useState } from "react";
import Image from "@/components/AppImage";
import Link from "next/link";
import { Card, BINDER_LAYOUTS } from "@/lib/api";
import { cardImageSrc, setCodeFromEdition } from "@/lib/utils";
import { rememberCardOrder } from "@/lib/cardNav";
import RarityBadge from "@/components/RarityBadge";
import RegulationBadge from "@/components/RegulationBadge";
import { useI18n } from "@/lib/i18n";
import { useCoarsePointer, useLongPress } from "@/lib/useLongPress";
import { Camera } from "lucide-react";

export type BinderItem = { card: Card; position: number };

// Geister-Slot (Issue #16): fehlende Soll-Karte einer Set-Sammlung —
// gedimmter Katalog-Platzhalter statt leerer Tasche, optional mit
// Wunschlisten-Aktion. Externe Bild-URLs kommen als <img> (kein next/image).
export type BinderGhost = {
  position: number;
  imageUrl: string | null;
  label: string;
  sub?: string | null;
  onWishlist?: () => void;
  wishlistTitle?: string;
  /** Kleine Ecken-Markierung, z.B. „geplant" für Wunschlisten-Karten (Issue #29). */
  badge?: string | null;
  /** Optionaler Link zur Karte — der Geister-Slot wird anklickbar (Issue #29). */
  href?: string | null;
};

type Props = {
  items: BinderItem[];
  apiBase: string;
  placeholderEnabled?: boolean;
  layout: string;
  onLayoutChange?: (l: string) => void;
  editable?: boolean;
  onMoveToSlot?: (cardId: number, slot: number) => void;
  onAddAtSlot?: (cardId: number, slot: number) => void;
  /** Leeres Fach antippen → in genau dieses Fach scannen (Sammlungen). */
  onScanAtSlot?: (slot: number) => void;
  binderSlots?: number | null;
  onAddPage?: (newSlots: number) => void;
  onDeleteLastPage?: (cardIdsOnLastPage: number[], newSlots: number) => void;
  /** sessionStorage-Key, um die aktuelle Binder-Seite über Navigation hinweg zu merken */
  storageKey?: string;
  /** Wenn gesetzt: Karten, deren id NICHT enthalten ist, werden ausgegraut (Filter-Hervorhebung). */
  highlightIds?: Set<number> | null;
  /** Fehlende Soll-Slots einer Set-Sammlung (gedimmte Katalog-Platzhalter). */
  ghosts?: BinderGhost[];
  /** Auswahl-Modus (Sammel-Aktionen): Tippen wählt statt zu öffnen. */
  selection?: { active: boolean; ids: Set<number>; toggle: (id: number) => void } | null;
  /** Welche Karten auswählbar sind (Standard: alle). */
  isSelectable?: (card: Card) => boolean;
  /** Meldet die Karten der gerade sichtbaren Seite(n) – für „alle auf der Seite". */
  onVisibleIdsChange?: (ids: number[]) => void;
  /** false: Karten öffnen keine Detailseite (z.B. Sammlung eines Freundes). */
  linkCards?: boolean;
};

export const ASSIGN_DRAG_TYPE = "application/x-pokecollect-add";

function parseLayout(layout: string): { cols: number; rows: number } {
  const m = layout.match(/^(\d+)x(\d+)$/);
  if (!m) return { cols: 3, rows: 3 };
  return { cols: Number(m[1]), rows: Number(m[2]) };
}

const GAP = 8;        // gap zwischen Pockets
const PAGE_PAD = 26;  // p-3 links+rechts + 1 px Rahmen je Seite
const PAGE_CHROME_Y = 26 + 16;  // p-3 oben+unten + Rahmen + Seitenzahl darunter
const MIN_POCKET = 56;
const SPREAD_GAP = 16;
const SIZE_KEY = "binder_card_size";
// Bereich des Größenreglers (Taschenbreite in px)
const SIZE_MIN = 70;
const SIZE_MAX = 260;

export default function BinderView({
  items, apiBase, placeholderEnabled = true, layout,
  onLayoutChange, editable = false, onMoveToSlot, onAddAtSlot, onScanAtSlot,
  binderSlots, onAddPage, onDeleteLastPage, storageKey, highlightIds, ghosts,
  selection, isSelectable, onVisibleIdsChange, linkCards = true,
}: Props) {
  const { t, lang } = useI18n();
  const { cols, rows } = parseLayout(layout);
  const perPage = cols * rows;

  const [showOpts, setShowOpts] = useState(false);
  const [page, setPage] = useState<number>(() => {
    if (storageKey && typeof window !== "undefined") {
      const v = Number(sessionStorage.getItem(storageKey));
      if (Number.isFinite(v) && v >= 0) return v;
    }
    return 0;
  });

  // Aktuelle Seite merken (z.B. um nach Öffnen einer Detailansicht zurückzukehren)
  useEffect(() => {
    if (storageKey && typeof window !== "undefined") {
      try { sessionStorage.setItem(storageKey, String(page)); } catch {}
    }
  }, [page, storageKey]);
  const [dragId, setDragId] = useState<number | null>(null);
  // Verschieben per Tippen (Handy: Ziehen scrollt): Karte halten oder im
  // Verschiebe-Modus antippen → „aufgenommen", dann die Ziel-Tasche antippen.
  const [moveMode, setMoveMode] = useState(false);
  const [picked, setPicked] = useState<number | null>(null);
  const selecting = !!selection?.active;
  const moving = editable && !selecting && (moveMode || picked != null);
  const longPress = useLongPress((id) => setPicked(id), editable && !selecting && !!onMoveToSlot);
  const coarse = useCoarsePointer();     // Touch: halten/antippen statt Ziehen
  useEffect(() => { if (selecting) { setPicked(null); setMoveMode(false); } }, [selecting]);
  const [containerWidth, setContainerWidth] = useState(0);
  const [viewport, setViewport] = useState<{ w: number; h: number }>({ w: 0, h: 0 });
  const [cardSize, setCardSize] = useState<number>(() => {
    if (typeof window !== "undefined") {
      const s = Number(localStorage.getItem(SIZE_KEY));
      if (s >= SIZE_MIN && s <= SIZE_MAX) return s;
    }
    return 140;
  });
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const ro = new ResizeObserver((entries) => {
      for (const e of entries) setContainerWidth(e.contentRect.width);
    });
    ro.observe(el);
    const onResize = () => setViewport({ w: window.innerWidth, h: window.innerHeight });
    onResize();
    window.addEventListener("resize", onResize);
    return () => { ro.disconnect(); window.removeEventListener("resize", onResize); };
  }, []);

  const setSize = (v: number) => {
    setCardSize(v);
    if (typeof window !== "undefined") localStorage.setItem(SIZE_KEY, String(v));
  };

  const slotMap = useMemo(() => {
    const m = new Map<number, Card>();
    for (const it of items) m.set(it.position, it.card);
    return m;
  }, [items]);

  const ghostMap = useMemo(() => {
    const m = new Map<number, BinderGhost>();
    for (const g of ghosts ?? []) m.set(g.position, g);
    return m;
  }, [ghosts]);

  const maxItemSlot = items.length ? Math.max(...items.map((i) => i.position)) : -1;
  const maxGhostSlot = ghosts?.length ? Math.max(...ghosts.map((g) => g.position)) : -1;
  const maxSlot = Math.max(maxItemSlot, maxGhostSlot);
  const contentPages = maxSlot >= 0 ? Math.floor(maxSlot / perPage) + 1 : 1;
  const slotPages = binderSlots != null ? Math.ceil(binderSlots / perPage) : 0;
  const totalPages = Math.max(contentPages, slotPages, 1);

  const pageWidth = cols * cardSize + (cols - 1) * GAP + PAGE_PAD;
  const twoPage = containerWidth >= 2 * pageWidth + SPREAD_GAP;

  // Auf kleinen Bildschirmen passt die Seite ganz hinein: so breit wie der
  // Platz, und so hoch, dass sie (hochgescrollt) samt Blätter-Leiste über der
  // Bottom-Nav sichtbar ist. Die Kartengröße aus ⚙ gilt als Obergrenze.
  const small = viewport.w > 0 && (viewport.w < 768 || viewport.h < 600);
  // Ohne Grenze SIZE_MAX, NICHT die aktuelle Größe: sonst lag das Regler-Maximum
  // immer bei der eingestellten Größe — kleiner ging, größer nie wieder.
  const fitW = containerWidth > 0
    ? Math.floor((containerWidth - PAGE_PAD - (cols - 1) * GAP) / cols) : SIZE_MAX;
  const reserveY = (viewport.w < 768 ? 56 : 0) + 64;   // Bottom-Nav + Blätter-Leiste
  const fitH = small
    ? Math.floor(((viewport.h - reserveY - PAGE_CHROME_Y - (rows - 1) * GAP) / rows) * (63 / 88)) : SIZE_MAX;
  const maxFit = Math.max(MIN_POCKET, Math.min(fitW, fitH));        // größte Tasche, die passt
  const pocket = twoPage ? cardSize : Math.max(MIN_POCKET, Math.min(cardSize, maxFit));
  const compact = pocket < 120;    // schmale Taschen: nur Name + Seltenheit
  const sliderMax = Math.max(SIZE_MIN, Math.min(SIZE_MAX, maxFit));

  const safePage = Math.min(Math.max(0, page), totalPages - 1);

  // Sichtbare Seiten: Einzelseite, oder Buch-Doppelseite (Seite 0 allein, dann Paare 1|2, 3|4 …)
  const visiblePages: number[] = !twoPage
    ? [safePage]
    : safePage === 0
    ? [0]
    : (() => {
        const left = safePage % 2 === 1 ? safePage : safePage - 1;
        return left + 1 < totalPages ? [left, left + 1] : [left];
      })();

  const leftVisible = visiblePages[0];
  const rightVisible = visiblePages[visiblePages.length - 1];

  // Sichtbare Karten nach außen melden (nur bei Änderung – sonst Render-Schleife)
  const lastVisible = useRef("");
  useEffect(() => {
    if (!onVisibleIdsChange) return;
    const ids: number[] = [];
    for (const p of visiblePages) {
      for (let i = 0; i < perPage; i++) {
        const c = slotMap.get(p * perPage + i);
        if (c && (!isSelectable || isSelectable(c))) ids.push(c.id);
      }
    }
    const key = ids.join(",");
    if (key !== lastVisible.current) { lastVisible.current = key; onVisibleIdsChange(ids); }
  });

  const moveTo = (slot: number) => {
    if (picked == null || !onMoveToSlot) return;
    onMoveToSlot(picked, slot);
    setPicked(null);
  };
  const pickedName = (() => {
    if (picked == null) return "";
    const c = items.find((it) => it.card.id === picked)?.card;
    return c ? (lang === "EN" && c.englischer_name ? c.englischer_name : c.kartenname) : "";
  })();

  const goPrev = () => {
    if (!twoPage) setPage(safePage - 1);
    else setPage(leftVisible <= 1 ? 0 : leftVisible - 2);
  };
  const goNext = () => {
    if (!twoPage) setPage(safePage + 1);
    else setPage(leftVisible === 0 ? 1 : leftVisible + 2);
  };

  // Wischen links/rechts zum Blättern (Mobile)
  const touchX = useRef<number | null>(null);
  const touchY = useRef<number | null>(null);
  const onTouchStart = (e: ReactTouchEvent) => {
    touchX.current = e.touches[0].clientX;
    touchY.current = e.touches[0].clientY;
  };
  const onTouchEnd = (e: ReactTouchEvent) => {
    if (touchX.current == null || touchY.current == null) return;
    const dx = e.changedTouches[0].clientX - touchX.current;
    const dy = e.changedTouches[0].clientY - touchY.current;
    touchX.current = null; touchY.current = null;
    if (Math.abs(dx) > 60 && Math.abs(dx) > Math.abs(dy) * 1.5) {
      if (dx < 0) { if (rightVisible < totalPages - 1) goNext(); }
      else { if (leftVisible > 0) goPrev(); }
    }
  };

  const flipThrottle = useRef(0);
  const handleEdgeOver = (dir: "prev" | "next") => (e: ReactDragEvent) => {
    e.preventDefault();
    const now = Date.now();
    if (now - flipThrottle.current < 700) return;
    flipThrottle.current = now;
    if (dir === "prev") goPrev();
    else goNext();
  };

  const handleDrop = (slot: number, e: ReactDragEvent) => {
    const addId = e.dataTransfer.getData(ASSIGN_DRAG_TYPE);
    if (addId && onAddAtSlot) {
      onAddAtSlot(Number(addId), slot);
      setDragId(null);
      return;
    }
    if (dragId != null && onMoveToSlot) onMoveToSlot(dragId, slot);
    setDragId(null);
  };

  const handleAddPage = () => {
    if (onAddPage) onAddPage((totalPages + 1) * perPage);
  };

  const handleDeleteLastPage = () => {
    if (!onDeleteLastPage) return;
    const last = totalPages - 1;
    const ids = items
      .filter((it) => Math.floor(it.position / perPage) === last)
      .map((it) => it.card.id);
    onDeleteLastPage(ids, last * perPage);
  };

  const renderPage = (pageNum: number) => {
    const startSlot = pageNum * perPage;
    return (
      <div
        key={pageNum}
        className="bg-gradient-to-br from-gray-900 to-gray-950 border border-gray-700 rounded-xl p-3 shadow-xl shrink-0"
      >
        <div className="grid" style={{ gridTemplateColumns: `repeat(${cols}, ${pocket}px)`, gap: `${GAP}px` }}>
          {Array.from({ length: perPage }).map((_, i) => {
            const slot = startSlot + i;
            const card = slotMap.get(slot) ?? null;
            const dropProps = editable
              ? { onDragOver: (e: ReactDragEvent) => e.preventDefault(), onDrop: (e: ReactDragEvent) => handleDrop(slot, e) }
              : {};
            const canScanHere = !!onScanAtSlot && !selecting && !moving && dragId == null;

            if (!card) {
              const ghost = ghostMap.get(slot);
              if (ghost) {
                const media = ghost.imageUrl ? (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img
                    src={ghost.imageUrl}
                    alt={ghost.label}
                    loading="lazy"
                    className="w-full h-full object-cover opacity-30 grayscale"
                  />
                ) : (
                  <div className="flex items-center justify-center h-full text-gray-600 text-[10px] text-center p-1">
                    {ghost.label}
                  </div>
                );
                return (
                  <div
                    key={slot}
                    {...dropProps}
                    className="aspect-[63/88] rounded-lg overflow-hidden relative bg-gray-800/60 ring-1 ring-black/40"
                  >
                    {ghost.href ? (
                      <Link href={ghost.href} className="block w-full h-full">{media}</Link>
                    ) : (
                      media
                    )}
                    {ghost.badge && (
                      <span className="absolute top-1 left-1 bg-black/70 text-gray-300 text-[10px] px-1.5 py-0.5 rounded pointer-events-none">
                        {ghost.badge}
                      </span>
                    )}
                    <div className="absolute bottom-0 inset-x-0 bg-black/60 px-1 py-0.5 flex items-center justify-between gap-1 pointer-events-none">
                      <span className="text-[10px] text-gray-500 truncate">{ghost.label}</span>
                      {ghost.sub && (
                        <span className="text-[10px] text-gray-600 font-mono shrink-0">{ghost.sub}</span>
                      )}
                    </div>
                    {ghost.onWishlist && (
                      <button
                        type="button"
                        onClick={ghost.onWishlist}
                        title={ghost.wishlistTitle}
                        className="absolute top-1 right-1 rounded-full px-1.5 py-1 bg-black/60 text-white hover:bg-black/80 text-[11px] leading-none"
                      >
                        ★
                      </button>
                    )}
                    {canScanHere && (
                      <button
                        type="button"
                        onClick={() => onScanAtSlot!(slot)}
                        title={t.binder_scan_here}
                        aria-label={t.binder_scan_here}
                        className={`absolute ${ghost.onWishlist ? "top-8" : "top-1"} right-1 rounded-full p-1 bg-black/60 text-white hover:bg-black/80`}
                      >
                        <Camera size={12} />
                      </button>
                    )}
                  </div>
                );
              }
              return (
                <div
                  key={slot}
                  {...dropProps}
                  onClick={picked != null ? () => moveTo(slot) : canScanHere ? () => onScanAtSlot!(slot) : undefined}
                  role={canScanHere ? "button" : undefined}
                  title={canScanHere ? t.binder_scan_here : undefined}
                  aria-label={canScanHere ? t.binder_scan_here : undefined}
                  data-slot={slot}
                  className={`group aspect-[63/88] rounded-lg border-2 border-dashed flex items-center justify-center text-[10px] ${
                    picked != null ? "border-pokemon-yellow/70 bg-pokemon-yellow/5 text-pokemon-yellow cursor-pointer"
                      : canScanHere ? "border-gray-700/60 bg-gray-800/30 text-gray-600 cursor-pointer hover:border-pokemon-blue/70 hover:text-pokemon-blue"
                      : "border-gray-700/60 bg-gray-800/30 text-gray-700"}`}
                >
                  {picked != null ? "↓" : canScanHere ? (
                    <span className="flex flex-col items-center gap-1 pointer-events-none">
                      <Camera size={pocket < 80 ? 12 : 16} className="opacity-60 group-hover:opacity-100" />
                      {pocket >= 70 && <span>{t.binder_scan_here_short}</span>}
                    </span>
                  ) : t.binder_empty_pocket}
                </div>
              );
            }

            const { src, isPlaceholder, fallbacks } = cardImageSrc(card, apiBase, placeholderEnabled);
            const name = lang === "EN" && card.englischer_name ? card.englischer_name : card.kartenname;
            const code = setCodeFromEdition(card.set_edition) ?? "";
            const dimmed = highlightIds ? !highlightIds.has(card.id) : false;
            const canSelect = selecting && (!isSelectable || isSelectable(card));
            const isSel = selecting && selection!.ids.has(card.id);
            const isPicked = picked === card.id;
            return (
              <div
                key={slot}
                {...dropProps}
                {...longPress.bind(card.id)}
                draggable={editable && !selecting && !moving && !coarse}
                onDragStart={editable ? (e) => { e.dataTransfer.setData("text/plain", String(card.id)); setDragId(card.id); } : undefined}
                onDragEnd={editable ? () => setDragId(null) : undefined}
                style={{ WebkitTouchCallout: "none" }}
                className={`aspect-[63/88] rounded-lg overflow-hidden relative bg-gray-800 ring-1 ring-black/40 transition-transform select-none ${
                  selecting || moving ? "" : "hover:scale-[1.03] hover:ring-pokemon-yellow"} ${
                  editable && !selecting ? "cursor-move" : ""
                } ${dimmed ? "opacity-20 grayscale" : card.besessen ? "" : "opacity-50"} ${
                  isSel ? "ring-4 ring-pokemon-blue" : ""} ${isPicked ? "ring-4 ring-pokemon-yellow scale-95 opacity-80" : ""} ${
                  selecting && !canSelect ? "cursor-not-allowed" : ""}`}
              >
                {canSelect && (
                  <span aria-hidden className={`absolute top-1 right-1 z-20 flex h-6 w-6 items-center justify-center rounded-full border-2 text-sm font-bold ${
                    isSel ? "border-pokemon-blue bg-pokemon-blue text-white" : "border-white/80 bg-black/40 text-transparent"}`}>✓</span>
                )}
                <Link
                  href={linkCards ? `/cards/${card.id}` : "#"}
                  title={linkCards ? undefined : name}
                  draggable={false}
                  className="block w-full h-full"
                  role={selecting ? "checkbox" : undefined}
                  aria-checked={selecting ? isSel : undefined}
                  onClick={(e) => {
                    if (!linkCards) { e.preventDefault(); return; }
                    if (longPress.suppressed(card.id)) { e.preventDefault(); return; }
                    if (selecting) { e.preventDefault(); if (canSelect) selection!.toggle(card.id); return; }
                    if (moving) {
                      e.preventDefault();
                      if (picked == null) setPicked(card.id);
                      else if (picked === card.id) setPicked(null);
                      else moveTo(slot);                     // belegte Tasche: Karten tauschen
                      return;
                    }
                    rememberCardOrder(items.map((it) => it.card.id));
                  }}
                >
                  {src ? (
                    <Image
                      src={src}
                      fallbacks={fallbacks}
                      alt={name}
                      fill
                      draggable={false}
                      className={isPlaceholder ? "object-contain p-2 opacity-70" : "object-cover"}
                      sizes={`${pocket}px`}
                    />
                  ) : (
                    <div className="flex items-center justify-center h-full text-gray-600 text-xs">
                      {card.pokedex_nr ? `#${String(card.pokedex_nr).padStart(4, "0")}` : "?"}
                    </div>
                  )}
                  <div className={`absolute bottom-0 inset-x-0 bg-black/60 px-1 py-0.5 grid items-center gap-1 ${compact ? "grid-cols-[1fr_auto]" : "grid-cols-3"}`}>
                    <span className="text-[10px] text-white truncate">{name}</span>
                    {!compact && (
                      <span className="flex items-center justify-center gap-1 text-[10px] text-gray-400 font-mono truncate">
                        {code}<RegulationBadge card={card} />
                      </span>
                    )}
                    <span className="flex justify-end"><RarityBadge rarity={card.seltenheit} language={card.sprache} size="sm" /></span>
                  </div>
                </Link>
              </div>
            );
          })}
        </div>
        <div className="text-center text-gray-600 text-[10px] mt-1">{pageNum + 1}</div>
      </div>
    );
  };

  const pageLabel = twoPage && visiblePages.length === 2
    ? t.binder_pages(leftVisible + 1, rightVisible + 1, totalPages)
    : t.binder_page((leftVisible ?? 0) + 1, totalPages);

  return (
    <div className="flex flex-col items-center w-full" onTouchStart={onTouchStart} onTouchEnd={onTouchEnd}>
      {editable && !selecting && (
        picked != null ? (
          <div className="mb-2 flex items-center justify-center gap-2 rounded bg-pokemon-yellow/10 border border-pokemon-yellow/40 px-3 py-1.5 text-xs text-pokemon-yellow">
            <span>{t.binder_moving(pickedName)}</span>
            <button type="button" onClick={() => setPicked(null)} className="underline shrink-0">{t.binder_cancel}</button>
          </div>
        ) : (
          <p className="text-gray-500 text-xs mb-2 text-center">{moveMode ? t.binder_move_hint : t.binder_dnd_hint}</p>
        )
      )}

      {/* Seiten */}
      <div ref={containerRef} className="w-full overflow-x-auto relative">
        <div className="flex gap-4 justify-center items-start min-w-min mx-auto">
          {visiblePages.map((p) => renderPage(p))}
        </div>
        {editable && dragId != null && leftVisible > 0 && (
          <div
            onDragOver={handleEdgeOver("prev")}
            onDrop={(e) => { e.preventDefault(); setDragId(null); }}
            className="absolute left-0 top-0 bottom-0 w-12 bg-gradient-to-r from-pokemon-yellow/40 to-transparent flex items-center pl-1 text-pokemon-yellow text-3xl"
          >
            ‹
          </div>
        )}
        {editable && dragId != null && rightVisible < totalPages - 1 && (
          <div
            onDragOver={handleEdgeOver("next")}
            onDrop={(e) => { e.preventDefault(); setDragId(null); }}
            className="absolute right-0 top-0 bottom-0 w-12 bg-gradient-to-l from-pokemon-yellow/40 to-transparent flex items-center justify-end pr-1 text-pokemon-yellow text-3xl"
          >
            ›
          </div>
        )}
      </div>

      {/* Darstellungsoptionen (über ⚙ in der Navigationszeile, keine extra Zeile) */}
      {showOpts && (
        <div className="flex items-center gap-3 mt-4 flex-wrap justify-center bg-pokemon-card/60 rounded px-3 py-2">
          {onLayoutChange && (
            <label className="flex items-center gap-2 text-sm text-gray-400">
              {t.binder_layout_label}
              <select
                value={layout}
                onChange={(e) => onLayoutChange(e.target.value)}
                className="bg-pokemon-card border border-gray-700 rounded px-2 py-1 text-white text-sm"
              >
                {BINDER_LAYOUTS.map((l) => (
                  <option key={l} value={l}>{l}</option>
                ))}
              </select>
            </label>
          )}
          <label className="flex items-center gap-2 text-sm text-gray-400">
            {t.binder_card_size}
            <input
              type="range" min={SIZE_MIN} max={sliderMax} step={10}
              value={Math.min(cardSize, Math.max(SIZE_MIN, maxFit))}
              // Ganz rechts = so groß, wie es passt (der 10er-Schritt trifft das Maximum sonst nicht)
              onChange={(e) => { const v = Number(e.target.value); setSize(v > sliderMax - 10 ? SIZE_MAX : v); }}
              className="accent-pokemon-yellow"
            />
          </label>
          {editable && onAddPage && (
            <button type="button" onClick={handleAddPage} className="text-xs bg-pokemon-card text-gray-300 hover:text-white rounded px-2 py-1">
              {t.binder_add_page}
            </button>
          )}
          {editable && onDeleteLastPage && totalPages > 1 && (
            <button type="button" onClick={handleDeleteLastPage} className="text-xs bg-red-950/60 text-red-300 hover:text-red-100 rounded px-2 py-1">
              {t.binder_delete_last_page}
            </button>
          )}
        </div>
      )}

      {/* Navigation (mit ⚙ direkt daneben) */}
      <div className="flex items-center gap-3 mt-4 text-sm">
        <button type="button"
          disabled={leftVisible <= 0}
          onClick={goPrev}
          className="px-3 py-1.5 bg-pokemon-card rounded disabled:opacity-40 hover:bg-gray-700"
        >
          ‹
        </button>
        <span className="text-gray-400">{pageLabel}</span>
        <button type="button"
          disabled={rightVisible >= totalPages - 1}
          onClick={goNext}
          className="px-3 py-1.5 bg-pokemon-card rounded disabled:opacity-40 hover:bg-gray-700"
        >
          ›
        </button>
        {editable && onMoveToSlot && !selecting && (
          <button type="button"
            onClick={() => { setMoveMode((m) => !m); setPicked(null); }}
            aria-pressed={moveMode}
            title={t.binder_move_hint}
            className={`px-3 py-1.5 rounded ${moveMode ? "bg-pokemon-accent text-white" : "bg-pokemon-card text-gray-300 hover:text-white"}`}
          >
            ✥ {t.binder_move}
          </button>
        )}
        <button type="button"
          onClick={() => setShowOpts((o) => !o)}
          title={t.binder_display_options}
          className={`px-3 py-1.5 rounded ${showOpts ? "bg-pokemon-accent text-white" : "bg-pokemon-card text-gray-300 hover:text-white"}`}
        >
          ⚙
        </button>
      </div>

      {/* Seiten-Schieber: schnell zu einer weit entfernten Seite (zusätzlich zu ‹ ›) */}
      {totalPages > 1 && (
        <label className="mt-2 flex w-full max-w-md items-center gap-2 px-2 text-xs text-gray-500">
          <span className="tabular-nums">1</span>
          <input
            type="range" min={1} max={totalPages} step={1}
            value={safePage + 1}
            onChange={(e) => setPage(Number(e.target.value) - 1)}
            aria-label={t.binder_page_slider}
            data-testid="binder-page-slider"
            className="flex-1 accent-pokemon-yellow"
          />
          <span className="tabular-nums">{totalPages}</span>
        </label>
      )}
    </div>
  );
}
