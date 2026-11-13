"use client";
import { useEffect, useState } from "react";
import Image from "@/components/AppImage";
import { API_BASE, Card, SearchWarning, cardApi } from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import { cardImageSrc, extractSetCode } from "@/lib/utils";
import RarityBadge from "@/components/RarityBadge";
import { SearchSyntaxHelp, SearchWarnings, appendTerm } from "@/components/SearchSyntax";
import { CardTagPills, useCardTags } from "@/components/CardTags";
import { useBinderPlace } from "@/lib/binderPlace";

// „Karte zuweisen" (Sammlung, Deck): eigene Karten mit der Suchsyntax finden (wie
// bei Limitless: set:, n:, is:holo, is:stamp, in:none …), Schnellfilter, und je
// Treffer, was ihn ausmacht (Folierung, Play!-Stempel, 1st, Sprache) und wo er
// schon steckt (Sammlungen, Deck). Antippen fügt hinzu.
export default function AssignCards({
  excludeIds, onAdd, planningToggle = false, freeFilter = false, deckFilter = true, defaultNoDeck = false,
  dragType, initialQuery = "", currentDeckId, testId = "assign-panel",
}: {
  excludeIds: number[];                      // stecken schon im Ziel
  onAdd: (card: Card) => void | Promise<void>;
  planningToggle?: boolean;                  // „Geplante (Wunschliste) einbeziehen" (Sammlungen)
  freeFilter?: boolean;                      // „Noch in keiner Sammlung"
  deckFilter?: boolean;                      // „In keinem Deck"
  defaultNoDeck?: boolean;
  dragType?: string;                         // Treffer auf Binder-Fächer ziehbar
  initialQuery?: string;
  currentDeckId?: number;                    // Karten in anderen Decks kennzeichnen
  testId?: string;
}) {
  const { t, lang } = useI18n();
  const tagsOf = useCardTags();
  const place = useBinderPlace();
  const [query, setQuery] = useState(initialQuery);
  const [planning, setPlanning] = useState(false);
  const [free, setFree] = useState(false);
  const [noDeck, setNoDeck] = useState(defaultNoDeck);
  const [items, setItems] = useState<Card[]>([]);
  const [info, setInfo] = useState<{ total: number; warnings: SearchWarning[] }>({ total: 0, warnings: [] });
  const [searching, setSearching] = useState(false);
  useEffect(() => { setQuery(initialQuery); }, [initialQuery]);

  useEffect(() => {
    const handle = setTimeout(async () => {
      setSearching(true);
      try {
        // Planungs-Modus → Wunschliste (nicht-besessen) statt besessener Karten
        const p: Record<string, unknown> = planning ? { limit: 80, wunschliste: true } : { limit: 80, besessen: true };
        const q = query.trim();
        if (q) p.search = q;
        if (free) p.collection = "none";
        if (noDeck && !planning) p.deck = "none";
        if (!q) { p.sort = "hinzugefuegt_am"; p.reverse = true; }   // ohne Suche: die zuletzt hinzugefügten zuerst
        const r = await cardApi.list(p);
        setItems(r.data.items);
        setInfo({ total: r.data.total, warnings: r.data.search_warnings ?? [] });
      } catch {
        setItems([]);
      } finally {
        setSearching(false);
      }
    }, 350);
    return () => clearTimeout(handle);
  }, [query, planning, free, noDeck]);

  const shown = items.filter((c) => !excludeIds.includes(c.id));

  return (
    <div className="bg-pokemon-card rounded-lg p-4" data-testid={testId}>
      <div className="flex items-center gap-2 mb-1">
        <input
          type="text"
          autoFocus
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder={t.collection_search_placeholder}
          data-testid="assign-search"
          className="min-w-0 flex-1 bg-gray-800 border border-gray-700 rounded px-3 py-2 text-white text-sm"
        />
        <SearchSyntaxHelp align="right" onPick={(ex) => setQuery((q) => appendTerm(q, ex))} />
      </div>
      <SearchWarnings warnings={info.warnings} />
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-gray-400 text-xs mt-2 mb-3">
        {planningToggle && (
          <label className="flex items-center gap-2 cursor-pointer">
            <input type="checkbox" checked={planning} onChange={(e) => setPlanning(e.target.checked)} />
            {t.collection_add_planning}
          </label>
        )}
        {freeFilter && (
          <label className="flex items-center gap-2 cursor-pointer">
            <input type="checkbox" checked={free} onChange={(e) => setFree(e.target.checked)} data-testid="assign-free" />
            {t.collection_assign_free}
          </label>
        )}
        {deckFilter && !planning && (
          <label className="flex items-center gap-2 cursor-pointer">
            <input type="checkbox" checked={noDeck} onChange={(e) => setNoDeck(e.target.checked)} data-testid="assign-no-deck" />
            {t.collection_assign_no_deck}
          </label>
        )}
        {!searching && <span className="ml-auto text-gray-500" data-testid="assign-count">{t.collection_assign_count(items.length, info.total)}</span>}
      </div>
      {searching ? (
        <div className="text-gray-500 text-sm py-4 text-center">{t.detail_loading}</div>
      ) : shown.length === 0 ? (
        <div className="text-gray-500 text-sm py-4 text-center">{t.collection_no_results}</div>
      ) : (
        <div className="grid grid-cols-3 sm:grid-cols-4 md:grid-cols-6 lg:grid-cols-8 gap-2 max-h-[28rem] overflow-y-auto pr-1">
          {shown.map((c) => {
            const { src, isPlaceholder, fallbacks } = cardImageSrc(c, API_BASE);
            const name = lang === "EN" && c.englischer_name ? c.englischer_name : c.kartenname;
            const otherDeck = currentDeckId !== undefined && c.deck_id != null && c.deck_id !== currentDeckId;
            const tags = tagsOf(c, { showLang: true });
            return (
              <button type="button"
                key={c.id}
                onClick={() => void onAdd(c)}
                draggable={!!dragType}
                onDragStart={dragType ? (e) => e.dataTransfer.setData(dragType, String(c.id)) : undefined}
                title={[name, c.set_edition, c.karten_nr, ...tags.map((x) => x.title ?? x.label)].filter(Boolean).join(" · ")}
                data-testid="assign-item"
                className={`group text-left bg-gray-800/40 rounded-lg overflow-hidden border border-transparent hover:border-green-500 ${dragType ? "cursor-grab" : ""}`}
              >
                <div className="aspect-[63/88] relative bg-gray-800">
                  {src && (
                    <Image src={src} fallbacks={fallbacks} alt={name} fill className={isPlaceholder ? "object-contain p-1 opacity-70" : "object-cover"} sizes="120px" />
                  )}
                  <CardTagPills tags={tags} className="absolute bottom-0.5 left-0.5 right-0.5 pointer-events-none" />
                  <div className="absolute inset-0 bg-green-600/0 group-hover:bg-green-600/20 flex items-center justify-center opacity-0 group-hover:opacity-100 transition">
                    <span className="bg-green-700 text-white text-xs px-2 py-1 rounded">+ {t.collection_add}</span>
                  </div>
                </div>
                <div className="px-1 py-1">
                  <div className="text-white text-[11px] truncate">{name}</div>
                  <div className="flex items-center gap-1 text-gray-500 text-[10px]">
                    <span className="truncate">{extractSetCode(c.set_edition)} {c.karten_nr ?? ""}</span>
                    {c.seltenheit && <RarityBadge rarity={c.seltenheit} language={c.sprache} size="sm" />}
                  </div>
                  {(c.sammlungen?.length || c.deck_name) ? (
                    <div className="truncate text-[10px]" data-testid="assign-where"
                      title={[...(c.sammlungen ?? []).map((x) => `📁 ${place.long(x)}`), c.deck_name ? `🃏 ${c.deck_name}` : ""].filter(Boolean).join("\n")}>
                      {(c.sammlungen ?? []).length > 0 && <span className="text-sky-300/90">📁 {(c.sammlungen ?? []).map((x) => place.short(x)).join(", ")} </span>}
                      {c.deck_name && <span className={otherDeck ? "text-amber-300" : "text-pink-300/90"}>🃏 {c.deck_name}</span>}
                    </div>
                  ) : null}
                </div>
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
