"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import Link from "next/link";
import Image from "@/components/AppImage";
import toast from "react-hot-toast";
import { API_BASE, Card, CollectionCard, Collection, SearchWarning, cardApi, collectionApi } from "@/lib/api";
import SortableCardGrid from "@/components/SortableCardGrid";
import BinderView, { BinderItem, BinderGhost, ASSIGN_DRAG_TYPE } from "@/components/BinderView";
import BinderEditor from "@/components/BinderEditor";
import GoalProgress from "@/components/GoalProgress";
import SollView from "@/components/SollView";
import ViewToggle, { ViewMode } from "@/components/ViewToggle";
import { useSelection } from "@/lib/useSelection";
import { GRID_SORTS, GridSort, sortCards } from "@/lib/cardSort";
import { sortDir } from "@/lib/sortDirection";
import SortDirectionButton from "@/components/SortDirectionButton";
import ShareDialog from "@/components/ShareDialog";
import { Share2 } from "lucide-react";
import { sortThenText } from "@/components/SortThenLine";
import { SortStep, decodeThen, encodeThen, nextThen } from "@/lib/sortChain";
import BulkBar, { SelectToggle } from "@/components/BulkBar";
import { cardImageSrc, extractSetCode } from "@/lib/utils";
import RarityBadge from "@/components/RarityBadge";
import { useI18n } from "@/lib/i18n";
import { SearchSyntaxHelp, SearchWarnings, appendTerm } from "@/components/SearchSyntax";
import { scanHref } from "@/lib/navRegistry";
import { useSets } from "@/lib/useSets";

export default function CollectionDetailPage() {
  const params = useParams();
  const router = useRouter();
  const id = Number(params?.id);
  const { t, lang } = useI18n();
  const { sets } = useSets();
  const [collection, setCollection] = useState<Collection | null>(null);
  const [cards, setCards] = useState<CollectionCard[]>([]);
  const [loading, setLoading] = useState(true);
  const [view, setView] = useState<ViewMode>("grid");
  const selection = useSelection();
  const [layout, setLayout] = useState("3x3");
  const [editingPages, setEditingPages] = useState(false);

  // Ansicht (Raster/Binder) merkt sich die Sammlung auf dem Server – gilt auf
  // allen Geräten. Gelesen beim ersten Laden der Sammlung (loadMeta), gespeichert
  // bei jedem Umschalten. Der Auswahl-Modus schaltet nur vorübergehend aufs Raster.
  const viewFromServer = useRef(false);
  const [binderVisible, setBinderVisible] = useState<number[]>([]);   // Karten der sichtbaren Binder-Seite(n)
  // Sortierung im Raster (serverseitig je Sammlung); „manual" = Binder-Reihenfolge, umsortierbar
  const [gridSort, setGridSort] = useState<GridSort>("manual");
  const [showShare, setShowShare] = useState(false);
  const [gridReverse, setGridReverse] = useState(false);
  // Untersortierungen: zuvor gewählte Sortierungen (Binder-Reihenfolge beginnt neu)
  const [gridThen, setGridThen] = useState<SortStep[]>([]);
  const changeGridSort = (s: GridSort) => {
    if (s === gridSort) return;
    const then = nextThen({ key: gridSort, reverse: gridReverse }, gridThen, s, "manual");
    setGridSort(s);
    setGridReverse(false);
    setGridThen(then);
    collectionApi.update(id, { grid_sort: s, grid_sort_reverse: false, grid_sort_then: encodeThen(then) }).catch(() => {});
  };
  const clearGridThen = () => {
    setGridThen([]);
    collectionApi.update(id, { grid_sort_then: "" }).catch(() => {});
  };
  const changeGridReverse = (r: boolean) => {
    setGridReverse(r);
    collectionApi.update(id, { grid_sort_reverse: r }).catch(() => {});
  };
  const changeView = (v: ViewMode) => {
    setView(v);
    collectionApi.update(id, { view_mode: v }).catch(() => {});
  };

  // Karten-Zuweisung (Suche)
  const [showAssign, setShowAssign] = useState(false);
  // Planungs-Modus: statt besessener Karten die Wunschliste durchsuchen (#29)
  const [assignPlanning, setAssignPlanning] = useState(false);
  const [query, setQuery] = useState("");
  // Schnellfilter: nur Karten, die noch in keiner Sammlung / in keinem Deck stecken
  const [assignFree, setAssignFree] = useState(false);
  const [assignNoDeck, setAssignNoDeck] = useState(false);
  const [assignInfo, setAssignInfo] = useState<{ total: number; warnings: SearchWarning[] }>({ total: 0, warnings: [] });
  // allSearchResults: aus API (nur bei Query-Änderung aktualisiert, kein Flicker bei cards-Änderung)
  const [allSearchResults, setAllSearchResults] = useState<Card[]>([]);
  const [searching, setSearching] = useState(false);
  // results: lokal gefiltert (Karten die schon in der Sammlung sind, werden ausgeblendet)
  const results = allSearchResults.filter((c) => !cards.some((cc) => cc.id === c.id));

  const loadCards = useCallback((silent = false) => {
    if (!silent) setLoading(true);
    collectionApi.cards(id).then((r) => {
      const data = r.data;
      // Alt-Daten ohne Position normalisieren: NUR die positionslosen Karten
      // an freie Slots hinten anhängen — bestehende Positionen bleiben, damit
      // der Binder nicht kompaktiert und Karten verrutschen (Issue #30).
      const missing = data.filter((c) => c.position == null);
      if (missing.length) {
        const used = data.map((c) => c.position).filter((p): p is number => p != null);
        let next = used.length ? Math.max(...used) + 1 : 0;
        const positions = missing.map((c) => ({ card_id: c.id, position: next++ }));
        collectionApi
          .setPositions(id, positions)
          .then(() => collectionApi.cards(id).then((r2) => setCards(r2.data)))
          .catch(() => setCards(data))
          .finally(() => { if (!silent) setLoading(false); });
      } else {
        setCards(data);
        if (!silent) setLoading(false);
      }
    }).catch(() => { if (!silent) setLoading(false); });
  }, [id]);

  const loadMeta = useCallback(() => {
    collectionApi.get(id).then((r) => {
      setCollection(r.data);
      setLayout(r.data.binder_layout || "3x3");
      if (!viewFromServer.current) {
        viewFromServer.current = true;
        setView(r.data.view_mode === "binder" ? "binder" : "grid");
        setGridSort(GRID_SORTS.includes(r.data.grid_sort as GridSort) ? (r.data.grid_sort as GridSort) : "manual");
        setGridReverse(Boolean(r.data.grid_sort_reverse));
        setGridThen(decodeThen(r.data.grid_sort_then, GRID_SORTS));
      }
    }).catch(() => setCollection(null));
  }, [id]);

  useEffect(() => {
    if (!Number.isNaN(id)) { loadMeta(); loadCards(); }
  }, [id, loadMeta, loadCards]);

  // Suche: nur besessene Karten (keine Pokédex-Platzhalter)
  // Kein 'cards' in deps → kein Flicker wenn Karte hinzugefügt wird (lokales Filter reicht)
  useEffect(() => {
    if (!showAssign) { setAllSearchResults([]); return; }
    const handle = setTimeout(async () => {
      setSearching(true);
      try {
        // Planungs-Modus → Wunschliste (nicht-besessen) statt besessener Karten
        const p: Record<string, unknown> = assignPlanning
          ? { limit: 80, wunschliste: true }
          : { limit: 80, besessen: true };
        const q = query.trim();
        if (q) p.search = q; // Suchsyntax wie bei Limitless (set:, n:, is:holo, in:none …)
        if (assignFree) p.collection = "none";
        if (assignNoDeck) p.deck = "none";
        if (!q) p.sort = "hinzugefuegt_am";   // ohne Suche: die zuletzt hinzugefügten zuerst
        if (!q) p.reverse = true;
        const r = await cardApi.list(p);
        setAllSearchResults(r.data.items);
        setAssignInfo({ total: r.data.total, warnings: r.data.search_warnings ?? [] });
      } finally {
        setSearching(false);
      }
    }, 350);
    return () => clearTimeout(handle);
  }, [query, showAssign, assignPlanning, assignFree, assignNoDeck]);

  const handleAdd = async (cardId: number) => {
    try {
      await collectionApi.addCard(id, cardId);
      toast.success(t.collection_added);
      loadCards(true);
      loadMeta();
    } catch {
      toast.error(t.collections_error);
    }
  };

  const handleRemove = async (cardId: number) => {
    try {
      await collectionApi.removeCard(id, cardId);
      loadCards(true);
      loadMeta();
    } catch {
      toast.error(t.collections_error);
    }
  };

  const handleRemoveConfirm = (cardId: number) => {
    if (!confirm(t.collection_remove_confirm)) return;
    handleRemove(cardId);
  };

  const sortedCards = sortCards(cards, gridSort, lang, gridReverse, gridThen);
  // Umordnen von Hand nur in der Binder-Reihenfolge (nicht umgekehrt)
  const isSorted = gridSort !== "manual" || gridReverse;
  // Sortierte Reihenfolge als Binder-Reihenfolge übernehmen (lückenlos ab Tasche 1)
  const applySortToBinder = async () => {
    if (!confirm(t.sort_apply_confirm)) return;
    try {
      await collectionApi.reorder(id, sortedCards.map((c) => c.id));
      changeGridSort("manual");
      toast.success(t.sort_applied);
      loadCards(true);
    } catch {
      toast.error(t.collections_error);
    }
  };

  const handleReorder = async (orderedIds: number[]) => {
    try {
      await collectionApi.reorder(id, orderedIds);
      loadCards(true);
    } catch {
      toast.error(t.collections_error);
    }
  };

  const handleMoveToSlot = async (cardId: number, slot: number) => {
    try {
      await collectionApi.moveToSlot(id, cardId, slot);
      loadCards(true);
    } catch {
      toast.error(t.collections_error);
    }
  };

  const handleAddAtSlot = async (cardId: number, slot: number) => {
    try {
      await collectionApi.addCard(id, cardId);
      await collectionApi.moveToSlot(id, cardId, slot);
      toast.success(t.collection_added);
      loadCards(true);
      loadMeta();
    } catch {
      toast.error(t.collections_error);
    }
  };

  const handleLayoutChange = async (l: string) => {
    setLayout(l);
    try {
      await collectionApi.update(id, { binder_layout: l });
    } catch {/* nicht kritisch */}
  };

  const handleAddPage = async (newSlots: number) => {
    try {
      await collectionApi.update(id, { binder_slots: newSlots });
      setCollection((c) => (c ? { ...c, binder_slots: newSlots } : c));
    } catch {
      toast.error(t.collections_error);
    }
  };

  const handlePagesChange = async (payload: {
    removeCardIds?: number[];
    positions?: { card_id: number; position: number }[];
    binderSlots?: number;
  }) => {
    try {
      if (payload.removeCardIds?.length) {
        for (const cid of payload.removeCardIds) await collectionApi.removeCard(id, cid);
      }
      if (payload.positions?.length) {
        await collectionApi.setPositions(id, payload.positions);
      }
      if (payload.binderSlots != null) {
        await collectionApi.update(id, { binder_slots: payload.binderSlots });
        setCollection((c) => (c ? { ...c, binder_slots: payload.binderSlots ?? null } : c));
      }
      loadCards(true);
      loadMeta();
    } catch {
      toast.error(t.collections_error);
    }
  };

  // Binder: besessene Karten sind echte Slots; nicht-besessene (Wunschliste/
  // geplant) erscheinen als gedimmte Geister-Slots — dieselbe Optik wie die
  // fehlenden Soll-Slots der Set-Sammlungen (#16, BinderGhost). Wird die Karte
  // besessen, flippt derselbe Slot automatisch auf „echt" (keine Doppelanlage,
  // #29). Die Seiten-Verwaltung (BinderEditor) sieht weiterhin ALLE Slots.
  const allItems: BinderItem[] = cards.map((c, idx) => ({ card: c, position: c.position ?? idx }));
  const binderItems: BinderItem[] = [];
  const planningGhosts: BinderGhost[] = [];
  cards.forEach((c, idx) => {
    const position = c.position ?? idx;
    if (c.besessen) {
      binderItems.push({ card: c, position });
    } else {
      planningGhosts.push({
        position,
        imageUrl: cardImageSrc(c, API_BASE).src,
        label: lang === "EN" && c.englischer_name ? c.englischer_name : c.kartenname,
        sub: c.karten_nr,
        badge: t.binder_planned,
        href: `/cards/${c.id}`,
      });
    }
  });

  // Set-Sammlung (Issue #16): Soll-Ansicht statt freier Karten-Zuweisung
  const isGoal = collection?.typ === "set_ziel";
  const zielSet = isGoal
    ? sets.find((s) => s.set_id === collection?.ziel_set_id) ?? null
    : null;

  return (
    <div className="max-w-6xl mx-auto">
      <div className="mb-4">
        <Link href="/collections" className="text-gray-500 hover:text-white text-sm">{t.collection_back}</Link>
      </div>

      <div className="flex items-start justify-between mb-6 flex-wrap gap-3">
        <div className="min-w-0">
          <h1 className="text-2xl font-bold text-white">{collection?.name ?? "…"}</h1>
          {collection?.beschreibung && <p className="text-gray-400 text-sm">{collection.beschreibung}</p>}
          {isGoal && collection ? (
            <>
              <p className="text-gray-500 text-xs mt-1">
                {zielSet ? `${lang === "EN" && zielSet.name_en ? zielSet.name_en : zielSet.name} (${zielSet.code})` : collection.ziel_set_id}
                {collection.ziel_folierung ? ` · ${collection.ziel_folierung}` : ""}
                {collection.ziel_sprache ? ` · ${collection.ziel_sprache}` : ""}
                {` · ${collection.ziel_master_set ? t.soll_master_badge : t.soll_official_badge}`}
              </p>
              {collection.fortschritt && (
                <GoalProgress progress={collection.fortschritt} className="mt-2 max-w-xs" />
              )}
            </>
          ) : (
            <p className="text-gray-500 text-xs mt-1">{t.collections_card_count(collection?.karten_anzahl ?? cards.length)}</p>
          )}
        </div>
        <div className="flex items-center gap-2 flex-wrap">
        {!isGoal && (<>
            <ViewToggle value={view} onChange={changeView} />
            {view === "grid" && !isGoal && cards.length > 1 && (
              <span className="flex items-center gap-1.5">
              <label className="flex items-center gap-1.5 text-sm text-gray-300">
                <span className="hidden sm:inline">{t.sort_label}</span>
                <select value={gridSort} onChange={(e) => changeGridSort(e.target.value as GridSort)} aria-label={t.sort_label}
                  className="bg-pokemon-card border border-gray-700 rounded px-2 py-1.5 text-sm text-white">
                  {GRID_SORTS.map((s) => <option key={s} value={s}>{t.sort_option(s)}</option>)}
                </select>
              </label>
              <SortDirectionButton sort={gridSort} reverse={gridReverse} onChange={changeGridReverse} />
              </span>
            )}
            {cards.length > 0 && <SelectToggle selection={selection} />}
            <button type="button"
              onClick={() => setShowAssign((v) => !v)} data-testid="assign-open"
              className="bg-pokemon-accent text-white text-sm px-3 py-1.5 rounded hover:bg-blue-700"
            >
              {t.collection_add_existing}
            </button>
            <Link
              href={`/cards/new?collection=${id}`}
              className="bg-pokemon-red text-white text-sm px-3 py-1.5 rounded hover:bg-red-600"
            >
              {t.collection_create_new}
            </Link>
        </>)}
          {collection && (
            <button type="button" onClick={() => setShowShare(true)} data-testid="share-button"
              className="inline-flex items-center gap-1.5 bg-gray-700 text-white text-sm px-3 py-1.5 rounded hover:bg-gray-600">
              <Share2 className="h-4 w-4" aria-hidden />
              {collection.geteilt_mit ? t.share_count(collection.geteilt_mit) : t.share_button}
            </button>
          )}
        </div>
      </div>
      {showShare && collection && (
        <ShareDialog collectionId={collection.id} name={collection.name} onClose={() => setShowShare(false)}
          onSaved={(n) => setCollection((c) => (c ? { ...c, geteilt_mit: n } : c))} />
      )}

      {/* Zuweisungs-Panel */}
      {!isGoal && showAssign && (
        <div className="bg-pokemon-card rounded-lg p-4 mb-6">
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
          <SearchWarnings warnings={assignInfo.warnings} />
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-gray-400 text-xs mt-2 mb-3">
            <label className="flex items-center gap-2 cursor-pointer">
              <input
                type="checkbox"
                checked={assignPlanning}
                onChange={(e) => setAssignPlanning(e.target.checked)}
              />
              {t.collection_add_planning}
            </label>
            <label className="flex items-center gap-2 cursor-pointer">
              <input type="checkbox" checked={assignFree} onChange={(e) => setAssignFree(e.target.checked)} data-testid="assign-free" />
              {t.collection_assign_free}
            </label>
            <label className="flex items-center gap-2 cursor-pointer">
              <input type="checkbox" checked={assignNoDeck} onChange={(e) => setAssignNoDeck(e.target.checked)} data-testid="assign-no-deck" />
              {t.collection_assign_no_deck}
            </label>
            {!searching && <span className="ml-auto text-gray-500" data-testid="assign-count">{t.collection_assign_count(allSearchResults.length, assignInfo.total)}</span>}
          </div>
          {searching ? (
            <div className="text-gray-500 text-sm py-4 text-center">{t.detail_loading}</div>
          ) : results.length === 0 ? (
            <div className="text-gray-500 text-sm py-4 text-center">{t.collection_no_results}</div>
          ) : (
            <div className="grid grid-cols-3 sm:grid-cols-4 md:grid-cols-6 lg:grid-cols-8 gap-2 max-h-[28rem] overflow-y-auto pr-1">
              {results.map((c) => {
                const { src, isPlaceholder, fallbacks } = cardImageSrc(c, API_BASE);
                const name = lang === "EN" && c.englischer_name ? c.englischer_name : c.kartenname;
                return (
                  <button type="button"
                    key={c.id}
                    onClick={() => handleAdd(c.id)}
                    draggable
                    onDragStart={(e) => e.dataTransfer.setData(ASSIGN_DRAG_TYPE, String(c.id))}
                    title={t.collection_add}
                    className="group text-left bg-gray-800/40 rounded-lg overflow-hidden border border-transparent hover:border-green-500 cursor-grab"
                  >
                    <div className="aspect-[63/88] relative bg-gray-800">
                      {src && (
                        <Image src={src} fallbacks={fallbacks} alt={name} fill className={isPlaceholder ? "object-contain p-1 opacity-70" : "object-cover"} sizes="120px" />
                      )}
                      <div className="absolute inset-0 bg-green-600/0 group-hover:bg-green-600/20 flex items-center justify-center opacity-0 group-hover:opacity-100 transition">
                        <span className="bg-green-700 text-white text-xs px-2 py-1 rounded">+ {t.collection_add}</span>
                      </div>
                    </div>
                    <div className="px-1 py-1">
                      <div className="text-white text-[11px] truncate">{name}</div>
                      <div className="flex items-center gap-1 text-gray-500 text-[10px]">
                        <span className="truncate">{extractSetCode(c.set_edition)} {c.karten_nr ?? ""}</span>
                        {c.seltenheit && <RarityBadge rarity={c.seltenheit} language={c.sprache} size="sm" />}
                        {c.folierung && c.folierung !== "Normal" && <span className="shrink-0 text-gray-400">{c.folierung === "Reverse Holo" ? "RH" : c.folierung}</span>}
                      </div>
                      {(c.sammlungen?.length || c.deck_name) ? (
                        <div className="truncate text-[10px]" data-testid="assign-where"
                          title={[...(c.sammlungen ?? []).map((x) => `📁 ${x.name}`), c.deck_name ? `🃏 ${c.deck_name}` : ""].filter(Boolean).join("\n")}>
                          {(c.sammlungen ?? []).length > 0 && <span className="text-sky-300/90">📁 {(c.sammlungen ?? []).map((x) => x.name).join(", ")} </span>}
                          {c.deck_name && <span className="text-pink-300/90">🃏 {c.deck_name}</span>}
                        </div>
                      ) : null}
                    </div>
                  </button>
                );
              })}
            </div>
          )}
        </div>
      )}

      {isGoal && collection ? (
        <SollView collection={collection} onMetaChange={loadMeta} />
      ) : loading ? (
        <div className="flex items-center justify-center h-64 text-gray-500">{t.detail_loading}</div>
      ) : view === "binder" ? (
        <div>
          <div className="flex justify-end mb-2">
            <button type="button"
              onClick={() => setEditingPages((v) => !v)}
              className={`text-sm rounded px-3 py-1.5 ${
                editingPages ? "bg-green-600 text-white hover:bg-green-700" : "bg-pokemon-card text-gray-300 hover:text-white"
              }`}
            >
              {editingPages ? t.binder_done : t.binder_manage_pages}
            </button>
          </div>
          {editingPages ? (
            <BinderEditor
              items={allItems}
              apiBase={API_BASE}
              layout={layout}
              binderSlots={collection?.binder_slots ?? null}
              onPagesChange={handlePagesChange}
              onMoveToSlot={handleMoveToSlot}
            />
          ) : (
            <BinderView
              items={binderItems}
              ghosts={planningGhosts}
              apiBase={API_BASE}
              layout={layout}
              onLayoutChange={handleLayoutChange}
              editable
              onMoveToSlot={handleMoveToSlot}
              onAddAtSlot={handleAddAtSlot}
              onScanAtSlot={(slot) => router.push(`${scanHref(`/collections/${id}`)}&slot=${slot}`)}
              binderSlots={collection?.binder_slots ?? null}
              onAddPage={handleAddPage}
              storageKey={`binder_page_coll_${id}`}
              selection={selection}
              onVisibleIdsChange={setBinderVisible}
            />
          )}
        </div>
      ) : cards.length === 0 ? (
        <div className="flex items-center justify-center h-48 text-gray-500 text-center px-4">{t.collection_empty}</div>
      ) : (
        <>
        {isSorted && (
          <div className="mb-2 flex flex-wrap items-center gap-2 text-xs text-gray-400">
            <span>{t.sort_hint(`${t.sort_option(gridSort)} (${t.sort_dir(sortDir(gridSort, gridReverse))})`
              + (gridThen.length ? `, ${t.sort_then(sortThenText(gridThen, t.sort_option, t.sort_dir))}` : ""))}</span>
            {gridThen.length > 0 && (
              <button type="button" onClick={clearGridThen} data-testid="sort-then-clear"
                className="rounded border border-gray-700 bg-pokemon-card px-2 py-1 text-gray-200 hover:text-white">
                {t.sort_then_clear}
              </button>
            )}
            <button type="button" onClick={() => void applySortToBinder()}
              className="rounded border border-gray-700 bg-pokemon-card px-2 py-1 text-gray-200 hover:text-white">
              {t.sort_apply}
            </button>
          </div>
        )}
        <SortableCardGrid
          cards={sortedCards}
          apiBase={API_BASE}
          onReorder={handleReorder}
          onRemove={handleRemoveConfirm}
          selection={selection}
          reorderable={!isSorted}
        />
        </>
      )}
      {!isGoal && (
        <BulkBar selection={selection} pageIds={view === "binder" ? binderVisible : sortedCards.map((c) => c.id)} collectionId={id}
          onDone={() => { loadCards(true); loadMeta(); }} />
      )}
      {selection.active && <div className="h-28" />}
    </div>
  );
}
