"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import toast from "react-hot-toast";
import {
  API_BASE, Card, CatalogItem, DeckDetail, DeckFormat, DeckImportResult, DeckMatch, DeckPrint, DeckSection, DeckSlot,
  catalogApi, deckApi,
} from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import { apiErrorText } from "@/lib/uiLang";
import { cardImageSrc, imgFallback } from "@/lib/utils";
import { valueLabel } from "@/lib/enumLabels";
import { DeckStatusBadge } from "@/components/decks/DeckBits";

const FORMATS: DeckFormat[] = ["standard", "expanded", "unlimited"];
const SECTIONS: DeckSection[] = ["pokemon", "trainer", "energy"];

/** Text in die Zwischenablage — auch ohne sicheren Kontext (http im LAN). */
async function copyText(text: string): Promise<boolean> {
  try {
    if (navigator.clipboard?.writeText) { await navigator.clipboard.writeText(text); return true; }
  } catch { /* Rückfall unten */ }
  const ta = document.createElement("textarea");
  ta.value = text;
  ta.style.position = "fixed";
  ta.style.opacity = "0";
  document.body.appendChild(ta);
  ta.select();
  let ok = false;
  try { ok = document.execCommand("copy"); } catch { ok = false; }
  ta.remove();
  return ok;
}

function Thumb({ card, className }: { card: Card; className?: string }) {
  const { src, fallbacks } = cardImageSrc(card, API_BASE, true, "thumb");
  if (!src) return <div className={`bg-gray-800 rounded ${className ?? ""}`} />;
  // eslint-disable-next-line @next/next/no-img-element
  return <img src={src} alt="" loading="lazy" onError={imgFallback(fallbacks)} className={`rounded object-cover bg-gray-800 ${className ?? ""}`} />;
}

const FOIL_SHORT: Record<string, string> = { "Reverse Holo": "RH", Holo: "Holo" };

/** Eine eigene Karte im Deck als Bild (statt nur „EN"): Folierung/abweichende Sprache als
 *  kleines Schild, ⚠ + gelber Rand bei anderem Druck, ✕ nimmt sie aus dem Deck. */
function CardTile({ card, match, title, mainLang, busy, onRemove, removeTitle }: {
  card: Card; match?: DeckMatch; title: string; mainLang: string | null; busy: boolean;
  onRemove: () => void; removeTitle: string;
}) {
  const tags = [
    card.sprache && card.sprache !== mainLang ? card.sprache : null,
    card.folierung && card.folierung !== "Normal" ? (FOIL_SHORT[card.folierung] ?? card.folierung) : null,
  ].filter(Boolean) as string[];
  return (
    <div data-testid="slot-card" title={title}
      className={`relative w-14 sm:w-16 shrink-0 rounded ${match === "other_print" ? "ring-2 ring-yellow-500/80" : ""}`}>
      <Link href={`/cards/${card.id}`} className="block aspect-[63/88] rounded overflow-hidden bg-gray-800 hover:ring-2 hover:ring-pokemon-yellow">
        <Thumb card={card} className="w-full h-full" />
      </Link>
      {(tags.length > 0 || match === "other_print") && (
        <div className="pointer-events-none absolute inset-x-0 bottom-0 flex flex-wrap gap-0.5 p-0.5">
          {match === "other_print" && <span className="rounded bg-yellow-600/90 px-0.5 text-[9px] leading-tight text-black">⚠</span>}
          {tags.map((tag) => (
            <span key={tag} className="rounded bg-black/75 px-1 text-[9px] leading-tight text-gray-100">{tag}</span>
          ))}
        </div>
      )}
      <button type="button" disabled={busy} title={removeTitle} aria-label={removeTitle} data-testid="slot-card-remove"
        onClick={onRemove}
        className="absolute -top-1.5 -right-1.5 h-5 w-5 rounded-full border border-gray-700 bg-gray-900/90 text-[10px] leading-none text-gray-400 hover:text-red-400">✕</button>
    </div>
  );
}

export default function DeckPage() {
  const { t, lang } = useI18n();
  const router = useRouter();
  const params = useParams();
  const id = Number(params.id);
  const [deck, setDeck] = useState<DeckDetail | null>(null);
  const [missing, setMissing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [editName, setEditName] = useState<string | null>(null);
  const [notes, setNotes] = useState("");
  const [showImport, setShowImport] = useState(false);
  const [importText, setImportText] = useState("");
  const [importResult, setImportResult] = useState<DeckImportResult | null>(null);
  const [exportText, setExportText] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<CatalogItem[] | null>(null);
  const [picker, setPicker] = useState<{ slot: DeckSlot; items: { match: DeckMatch; card: Card }[] | null } | null>(null);
  const refreshed = useRef(false);

  const show = useCallback((d: DeckDetail) => { setDeck(d); setNotes(d.notizen ?? ""); }, []);

  useEffect(() => {
    deckApi.get(id).then((r) => show(r.data)).catch(() => setMissing(true));
  }, [id, show]);

  // Ältere Katalogzeilen ohne Stufe/Trainer-Art: einmal bei TCGdex nachholen
  useEffect(() => {
    if (!deck?.legality.need_data || refreshed.current) return;
    refreshed.current = true;
    deckApi.refresh(id).then((r) => show(r.data)).catch(() => {});
  }, [deck, id, show]);

  // Katalogsuche zum Hinzufügen
  useEffect(() => {
    const q = query.trim();
    if (q.length < 2) { setResults(null); return; }
    const timer = window.setTimeout(() => {
      catalogApi.list({ q, region: "west", limit: 8, sort: "name", lang })
        .then((r) => setResults(r.data.items)).catch(() => setResults([]));
    }, 300);
    return () => window.clearTimeout(timer);
  }, [query, lang]);

  // Druck einer Zeile von Hand wählen (z. B. nur über den Namen gefunden, falsches Bild)
  const [printFor, setPrintFor] = useState<DeckSlot | null>(null);

  const run = async (fn: () => Promise<{ data: DeckDetail }>, ok?: string) => {
    setBusy(true);
    try {
      show((await fn()).data);
      if (ok) toast.success(ok);
    } catch (e) {
      toast.error(apiErrorText(e) ?? t.decks_error);
    } finally {
      setBusy(false);
    }
  };

  if (missing) return <div className="max-w-4xl mx-auto"><Link href="/decks" className="text-pokemon-accent">{t.deck_back}</Link></div>;
  // die Sprache der meisten Karten im Deck: nur Karten in einer anderen Sprache bekommen ein Schild
  const mainLang = (() => {
    const n = new Map<string, number>();
    for (const c of [...(deck?.slots ?? []).flatMap((s) => s.cards.map((x) => x.card)), ...(deck?.extra ?? [])]) {
      if (c.sprache) n.set(c.sprache, (n.get(c.sprache) ?? 0) + 1);
    }
    return Array.from(n.entries()).sort((a, b) => b[1] - a[1])[0]?.[0] ?? null;
  })();
  if (!deck) return <div className="flex items-center justify-center h-64 text-gray-500">{t.detail_loading}</div>;

  const fmt = t.deck_format_label(deck.format);
  const flagged = new Map<number, "error" | "warning">();
  for (const i of deck.legality.issues) for (const s of i.slots) {
    if (i.level === "error" || !flagged.has(s)) flagged.set(s, i.level);
  }

  const saveName = () => {
    const n = (editName ?? "").trim();
    setEditName(null);
    if (n && n !== deck.name) void run(() => deckApi.update(id, { name: n }));
  };
  const saveNotes = () => {
    if ((deck.notizen ?? "") !== notes) void run(() => deckApi.update(id, { notizen: notes || null }));
  };
  const doImport = async (replace: boolean) => {
    if (!importText.trim()) return;
    setBusy(true);
    try {
      const r = await deckApi.importList(id, importText, replace);
      show(r.data.deck);
      setImportResult(r.data);
      setImportText("");
      toast.success(t.deck_import_result(r.data.matched, r.data.unmatched));
    } catch (e) {
      toast.error(apiErrorText(e) ?? t.decks_error);
    } finally {
      setBusy(false);
    }
  };
  const doExport = async () => {
    try {
      const r = await deckApi.exportList(id);
      setExportText(r.data);
      if (await copyText(r.data)) toast.success(t.deck_export_copied);
    } catch (e) {
      toast.error(apiErrorText(e) ?? t.decks_error);
    }
  };
  const download = () => {
    if (exportText == null) return;
    const url = URL.createObjectURL(new Blob([exportText], { type: "text/plain;charset=utf-8" }));
    const a = document.createElement("a");
    a.href = url;
    a.download = `${deck.name.replace(/[^\w.-]+/g, "_") || "deck"}.txt`;
    a.click();
    URL.revokeObjectURL(url);
  };
  const doFill = async () => {
    setBusy(true);
    try {
      const r = await deckApi.fill(id);
      show(r.data.deck);
      toast.success(t.deck_filled(r.data.added));
    } catch (e) {
      toast.error(apiErrorText(e) ?? t.decks_error);
    } finally {
      setBusy(false);
    }
  };
  const doDelete = async () => {
    if (!confirm(t.deck_delete_confirm(deck.name))) return;
    try {
      await deckApi.delete(id);
      toast.success(t.deck_deleted);
      router.push("/decks");
    } catch (e) {
      toast.error(apiErrorText(e) ?? t.decks_error);
    }
  };
  const openPicker = async (slot: DeckSlot) => {
    setPicker({ slot, items: null });
    try {
      const r = await deckApi.candidates(id, slot.id);
      setPicker({ slot, items: r.data });
    } catch {
      setPicker({ slot, items: [] });
    }
  };
  const pick = async (cardId: number) => {
    setPicker(null);
    await run(() => deckApi.addCards(id, [cardId]));
  };

  // PTCGL schreibt Energie-Typen als Symbol: „Basic {P} Energy" → „Basic Psychic Energy"
  const slotName = (s: DeckSlot) => s.name.replace(/\{([A-Z])\}/g, (_m, c: string) => ({
    G: "Grass", R: "Fire", W: "Water", L: "Lightning", P: "Psychic", F: "Fighting", D: "Darkness",
    M: "Metal", Y: "Fairy", N: "Dragon", C: "Colorless",
  } as Record<string, string>)[c] ?? c);

  return (
    <div className="max-w-4xl mx-auto space-y-4" data-testid="deck-page">
      <Link href="/decks" className="text-pokemon-accent hover:text-blue-400 text-sm">{t.deck_back}</Link>

      {/* Kopf */}
      <div className="bg-pokemon-card rounded-lg p-4 space-y-3">
        <div className="flex flex-wrap items-center gap-2">
          {editName !== null ? (
            <input autoFocus value={editName} onChange={(e) => setEditName(e.target.value)} maxLength={120}
              onBlur={saveName} onKeyDown={(e) => { if (e.key === "Enter") saveName(); if (e.key === "Escape") setEditName(null); }}
              className="flex-1 min-w-0 bg-gray-800 border border-gray-700 rounded px-2 py-1 text-white text-xl font-bold" />
          ) : (
            <h1 className="text-2xl font-bold text-white truncate cursor-text" title={t.deck_rename}
              onClick={() => setEditName(deck.name)} data-testid="deck-title">{deck.name}</h1>
          )}
          <DeckStatusBadge status={deck.legality.status} />
          <select value={deck.format} disabled={busy} data-testid="deck-format"
            onChange={(e) => void run(() => deckApi.update(id, { format: e.target.value as DeckFormat }))}
            className="ml-auto bg-gray-800 border border-gray-700 rounded px-2 py-1 text-white text-sm">
            {FORMATS.map((f) => <option key={f} value={f}>{t.deck_format_label(f)}</option>)}
          </select>
        </div>
        <p className="text-gray-400 text-sm" data-testid="deck-counts">
          {t.deck_cards_count(deck.total)} · {SECTIONS.map((s) => `${t.deck_section(s)} ${deck.legality.sections[s] ?? 0}`).join(" · ")}
          {" · "}{t.deck_owned_count(deck.in_deck, deck.total)}
        </p>
        <div className="flex flex-wrap gap-2">
          <button type="button" onClick={() => void doFill()} disabled={busy} data-testid="deck-fill"
            className="bg-pokemon-blue text-white text-sm px-3 py-1.5 rounded disabled:opacity-50">🃏 {t.deck_fill}</button>
          <Link href={`/scan?deck=${id}&from=${encodeURIComponent(`/decks/${id}`)}`}
            className="bg-gray-700 text-white text-sm px-3 py-1.5 rounded hover:bg-gray-600">📷 {t.deck_scan}</Link>
          <button type="button" onClick={() => { setShowImport((v) => !v); setImportResult(null); }} data-testid="deck-import-toggle"
            className="bg-gray-700 text-white text-sm px-3 py-1.5 rounded hover:bg-gray-600">⬇ {t.deck_import}</button>
          <button type="button" onClick={() => void doExport()} data-testid="deck-export"
            className="bg-gray-700 text-white text-sm px-3 py-1.5 rounded hover:bg-gray-600">⬆ {t.deck_export}</button>
          <button type="button" onClick={() => void doDelete()}
            className="ml-auto text-red-400 hover:text-red-300 text-sm px-2">{t.deck_delete}</button>
        </div>
        {showImport && (
          <div className="space-y-2 border-t border-gray-800 pt-3">
            <textarea value={importText} onChange={(e) => setImportText(e.target.value)} rows={7} spellCheck={false}
              placeholder={t.decks_import_placeholder} data-testid="deck-import-text"
              className="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1.5 text-white text-xs font-mono" />
            <div className="flex gap-2">
              <button type="button" disabled={busy || !importText.trim()} onClick={() => void doImport(false)}
                className="bg-green-600 text-white text-sm px-3 py-1.5 rounded disabled:opacity-50">{t.deck_import_add}</button>
              <button type="button" disabled={busy || !importText.trim()} onClick={() => void doImport(true)} data-testid="deck-import-replace"
                className="bg-gray-700 text-white text-sm px-3 py-1.5 rounded disabled:opacity-50">{t.deck_import_replace}</button>
            </div>
          </div>
        )}
        {importResult && (importResult.not_found.length > 0 || importResult.unreadable.length > 0) && (
          <div className="text-xs text-yellow-200 bg-yellow-950/40 rounded p-2 space-y-1" data-testid="deck-import-problems">
            {importResult.not_found.length > 0 && (
              <div><span className="text-yellow-400">{t.deck_not_found_title}</span> {importResult.not_found.join(" · ")}</div>
            )}
            {importResult.unreadable.length > 0 && (
              <div><span className="text-yellow-400">{t.deck_unreadable_title}</span> {importResult.unreadable.join(" · ")}</div>
            )}
          </div>
        )}
        <textarea value={notes} onChange={(e) => setNotes(e.target.value)} onBlur={saveNotes} rows={2} maxLength={4000}
          placeholder={t.deck_notes}
          className="w-full bg-gray-800/60 border border-gray-800 rounded px-2 py-1.5 text-gray-200 text-sm" />
      </div>

      {/* Legalität */}
      <div className="bg-pokemon-card rounded-lg p-4" data-testid="deck-legality">
        {deck.legality.issues.length === 0 ? (
          <p className="text-green-300 text-sm">✓ {t.deck_legal_ok(fmt)}</p>
        ) : (
          <ul className="space-y-1 text-sm">
            {deck.legality.issues.map((i, k) => (
              <li key={k} className={i.level === "error" ? "text-red-300" : "text-yellow-300"} data-testid={`deck-issue-${i.code}`}>
                {i.level === "error" ? "✕" : "!"} {t.deck_issue(i.code, i.count, i.name, fmt)}
              </li>
            ))}
          </ul>
        )}
        {deck.legality.need_data && !refreshed.current && <p className="text-gray-500 text-xs mt-1">{t.deck_need_data}</p>}
      </div>

      {/* Karte hinzufügen */}
      <div className="bg-pokemon-card rounded-lg p-4 relative">
        <input type="search" value={query} onChange={(e) => setQuery(e.target.value)} placeholder={t.deck_add_search}
          data-testid="deck-add-search"
          className="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1.5 text-white text-sm" />
        {results && (
          <div className="mt-2 space-y-1" data-testid="deck-add-results">
            {results.length === 0 && <p className="text-gray-500 text-sm">{t.deck_add_none}</p>}
            {results.map((r) => (
              <button key={r.card_id} type="button" disabled={busy}
                onClick={() => void run(() => deckApi.addSlot(id, r.card_id))}
                className="w-full flex items-center gap-2 text-left rounded px-2 py-1 hover:bg-gray-800">
                <div className="w-8 shrink-0 aspect-[63/88] bg-gray-800 rounded overflow-hidden">
                  {r.image_url && (
                    // eslint-disable-next-line @next/next/no-img-element
                    <img src={r.image_url} alt="" className="w-full h-full object-cover" loading="lazy"
                      onError={(e) => { e.currentTarget.style.display = "none"; }} />
                  )}
                </div>
                <span className="text-white text-sm truncate">{r.name_en ?? r.name}</span>
                <span className="text-gray-500 text-xs shrink-0">{r.set_code ?? r.set_id} {r.local_id}</span>
                {r.regulation_mark && <span className="text-gray-500 text-[10px] border border-gray-700 rounded px-1">{r.regulation_mark}</span>}
                <span className="ml-auto text-pokemon-yellow text-sm">＋</span>
              </button>
            ))}
          </div>
        )}
      </div>

      {/* Liste */}
      {SECTIONS.map((sec) => {
        const slots = deck.slots.filter((s) => s.section === sec);
        if (!slots.length) return null;
        return (
          <section key={sec} className="bg-pokemon-card rounded-lg p-4" data-testid={`deck-section-${sec}`}>
            <h2 className="text-gray-300 font-medium mb-2">{t.deck_section(sec)} ({deck.legality.sections[sec] ?? 0})</h2>
            <div className="space-y-2">
              {slots.map((s) => {
                const have = s.cards.length;
                const flag = flagged.get(s.id);
                const covCls = have >= s.count ? "bg-green-900/60 text-green-300"
                  : have > 0 ? "bg-yellow-900/60 text-yellow-300" : "bg-gray-800 text-gray-400";
                return (
                  <div key={s.id} data-testid="deck-slot"
                    className={`flex gap-2 rounded p-2 bg-gray-900/40 ${flag === "error" ? "ring-1 ring-red-700/70" : flag === "warning" ? "ring-1 ring-yellow-700/50" : ""}`}>
                    <div className="w-11 shrink-0 aspect-[63/88] rounded bg-gray-800 overflow-hidden">
                      {s.image_url && (
                        // eslint-disable-next-line @next/next/no-img-element
                        <img src={s.image_url} alt="" className="w-full h-full object-cover" loading="lazy"
                          onError={(e) => { e.currentTarget.style.display = "none"; }} />
                      )}
                    </div>
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                        <span className="inline-flex items-center rounded bg-gray-800">
                          <button type="button" disabled={busy} title={t.deck_count_minus} data-testid="slot-minus"
                            onClick={() => void run(() => deckApi.updateSlot(id, s.id, { count: s.count - 1 }))}
                            className="px-1.5 text-gray-400 hover:text-white">−</button>
                          <span className="text-white text-sm font-semibold w-5 text-center" data-testid="slot-count">{s.count}</span>
                          <button type="button" disabled={busy || s.count >= 60} title={t.deck_count_plus} data-testid="slot-plus"
                            onClick={() => void run(() => deckApi.updateSlot(id, s.id, { count: s.count + 1 }))}
                            className="px-1.5 text-gray-400 hover:text-white">＋</button>
                        </span>
                        {s.tcgdex_card_id ? (
                          <span className="text-white text-sm truncate" data-testid="slot-name">{slotName(s)}</span>
                        ) : (
                          <span className="text-gray-300 text-sm truncate italic" data-testid="slot-name">{slotName(s)}</span>
                        )}
                        <span className="text-gray-500 text-xs">{[s.set_code, s.number].filter(Boolean).join(" ")}</span>
                        {s.ace_spec && <span className="text-[10px] rounded px-1 bg-pink-900/60 text-pink-200">ACE SPEC</span>}
                        {s.stage && s.section === "pokemon" && s.stage.toLowerCase() === "basic" && (
                          <span className="text-[10px] rounded px-1 bg-gray-800 text-gray-400">Basic</span>
                        )}
                        {(s.status === "not_legal" || s.status === "unknown" || s.status === "unmatched") && (
                          <span className={`text-[10px] rounded px-1 ${s.status === "not_legal" ? "bg-red-900/60 text-red-300" : "bg-yellow-900/50 text-yellow-300"}`}
                            data-testid="slot-status">{t.deck_slot_status(s.status, s.legal_mark)}</span>
                        )}
                        {s.status === "reprint_text" && (
                          <span className="text-[10px] rounded px-1 border border-amber-400/60 text-amber-300" data-testid="slot-status"
                            title={t.deck_slot_reprint_text(s.legal_mark)}>{t.deck_slot_status(s.status, s.legal_mark)}</span>
                        )}
                        {s.match === "name" && (
                          <button type="button" onClick={() => setPrintFor(s)} data-testid="slot-by-name"
                            title={t.deck_print_title}
                            className="text-[10px] rounded px-1 bg-yellow-900/50 text-yellow-300 hover:bg-yellow-800/60 underline decoration-dotted">
                            {t.deck_slot_by_name}</button>
                        )}
                        {s.tcgdex_card_id && s.status !== "basic" && (
                          <button type="button" onClick={() => setPrintFor(s)} data-testid="slot-print"
                            title={t.deck_print_title} aria-label={t.deck_print_title}
                            className="text-[11px] text-gray-500 hover:text-white">⇄</button>
                        )}
                      </div>
                      <div className="flex flex-wrap items-center gap-2 mt-2">
                        <span className={`text-[11px] rounded px-1.5 py-0.5 ${covCls}`} title={t.deck_slot_have} data-testid="slot-have">
                          {have}/{s.count}
                        </span>
                        {s.cards.map(({ card, match }) => (
                          <CardTile key={card.id} card={card} match={match} mainLang={mainLang} busy={busy}
                            title={[t.deck_match(match), card.set_edition, card.karten_nr, valueLabel(card.folierung, lang), card.sprache].filter(Boolean).join(" · ")}
                            removeTitle={t.deck_remove_card} onRemove={() => void run(() => deckApi.removeCard(id, card.id))} />
                        ))}
                        {have < s.count && (s.available > 0 || s.available_other > 0) && (
                          <button type="button" onClick={() => void openPicker(s)} data-testid="slot-available"
                            title={t.deck_slot_available_title}
                            className={`text-[11px] rounded px-1.5 py-0.5 ${s.available > 0 ? "bg-blue-900/60 text-blue-200 hover:bg-blue-800" : "bg-gray-800 text-gray-400 hover:text-white"}`}>
                            {s.available > 0 ? t.deck_slot_available(s.available) : t.deck_slot_available_other(s.available_other)}
                          </button>
                        )}
                      </div>
                    </div>
                    <button type="button" disabled={busy} title={t.deck_remove_slot} data-testid="slot-remove"
                      onClick={() => void run(() => deckApi.removeSlot(id, s.id))}
                      className="self-start text-gray-600 hover:text-red-400 px-1">✕</button>
                  </div>
                );
              })}
            </div>
          </section>
        );
      })}

      {deck.extra.length > 0 && (
        <section className="bg-pokemon-card rounded-lg p-4" data-testid="deck-extra">
          <h2 className="text-gray-300 font-medium mb-2">{t.deck_extra_title}</h2>
          <div className="flex flex-wrap gap-3">
            {deck.extra.map((c) => (
              <div key={c.id} className="w-14 sm:w-16" data-testid="deck-extra-card">
                <CardTile card={c} mainLang={mainLang} busy={busy}
                  title={[c.englischer_name ?? c.kartenname, c.set_edition, c.karten_nr, valueLabel(c.folierung, lang), c.sprache].filter(Boolean).join(" · ")}
                  removeTitle={t.deck_remove_card} onRemove={() => void run(() => deckApi.removeCard(id, c.id))} />
                <div className="mt-0.5 text-[10px] leading-tight text-gray-300 line-clamp-2">{c.englischer_name ?? c.kartenname}</div>
              </div>
            ))}
          </div>
        </section>
      )}

      {/* Karte aus der Sammlung wählen */}
      {picker && (
        <div className="fixed inset-0 z-50 bg-black/70 flex items-center justify-center p-4" onClick={() => setPicker(null)}>
          <div className="bg-pokemon-card rounded-lg p-4 w-full max-w-lg max-h-[80vh] overflow-y-auto" onClick={(e) => e.stopPropagation()}
            data-testid="deck-picker">
            <div className="flex items-center gap-2 mb-3">
              <h3 className="text-white font-medium flex-1 truncate">{t.deck_pick_title}: {slotName(picker.slot)}</h3>
              <button type="button" onClick={() => setPicker(null)} className="text-gray-400 hover:text-white">✕</button>
            </div>
            {picker.items === null ? (
              <p className="text-gray-500 text-sm">{t.detail_loading}</p>
            ) : picker.items.length === 0 ? (
              <p className="text-gray-500 text-sm">{t.deck_pick_none}</p>
            ) : (
              <div className="space-y-1">
                {picker.items.map(({ card, match }) => (
                  <button key={card.id} type="button" onClick={() => void pick(card.id)} data-testid="deck-pick-item"
                    className="w-full flex items-center gap-2 text-left rounded px-2 py-1.5 hover:bg-gray-800">
                    <Thumb card={card} className="w-9 h-12 shrink-0" />
                    <span className="min-w-0 flex-1">
                      <span className="block text-white text-sm truncate">{card.englischer_name ?? card.kartenname}</span>
                      <span className="block text-gray-500 text-xs truncate">
                        {[card.set_edition, card.karten_nr, card.sprache, card.folierung ? valueLabel(card.folierung, lang) : null].filter(Boolean).join(" · ")}
                      </span>
                      {match !== "exact" && (
                        <span className={`text-[10px] ${match === "other_print" ? "text-yellow-300" : "text-gray-400"}`}>{t.deck_match(match)}</span>
                      )}
                    </span>
                  </button>
                ))}
              </div>
            )}
          </div>
        </div>
      )}

      {/* Export */}
      {exportText !== null && (
        <div className="fixed inset-0 z-50 bg-black/70 flex items-center justify-center p-4" onClick={() => setExportText(null)}>
          <div className="bg-pokemon-card rounded-lg p-4 w-full max-w-lg" onClick={(e) => e.stopPropagation()} data-testid="deck-export-dialog">
            <div className="flex items-center gap-2 mb-2">
              <h3 className="text-white font-medium flex-1">{t.deck_export_title}</h3>
              <button type="button" onClick={() => setExportText(null)} className="text-gray-400 hover:text-white">✕</button>
            </div>
            <textarea readOnly value={exportText} rows={16} spellCheck={false} data-testid="deck-export-text"
              onFocus={(e) => e.currentTarget.select()}
              className="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1.5 text-white text-xs font-mono" />
            <div className="flex gap-2 mt-2">
              <button type="button" onClick={async () => { if (await copyText(exportText)) toast.success(t.deck_export_copied); }}
                className="bg-pokemon-blue text-white text-sm px-3 py-1.5 rounded">{t.deck_copy}</button>
              <button type="button" onClick={download} className="bg-gray-700 text-white text-sm px-3 py-1.5 rounded">{t.deck_download}</button>
            </div>
          </div>
        </div>
      )}
      {printFor && (
        <PrintPicker deckId={id} slot={printFor} onClose={() => setPrintFor(null)}
          onPicked={(d) => { show(d); setPrintFor(null); toast.success(t.deck_print_done); }} />
      )}
    </div>
  );
}


/** Die Drucke derselben Karte; Antippen stellt die Zeile (und Karten ohne Druck) darauf um. */
function PrintPicker({ deckId, slot, onClose, onPicked }: {
  deckId: number; slot: DeckSlot; onClose: () => void; onPicked: (d: DeckDetail) => void;
}) {
  const { t } = useI18n();
  const [prints, setPrints] = useState<DeckPrint[] | null>(null);
  const [busy, setBusy] = useState(false);
  const unlinked = slot.cards.filter(({ card }) => !card.tcgdex_card_id).length;
  const [linkCards, setLinkCards] = useState(true);
  useEffect(() => {
    deckApi.prints(deckId, slot.id).then((r) => setPrints(r.data)).catch(() => setPrints([]));
  }, [deckId, slot.id]);
  const pick = async (p: DeckPrint) => {
    setBusy(true);
    try {
      onPicked((await deckApi.updateSlot(deckId, slot.id, { tcgdex_card_id: p.tcgdex_card_id, link_cards: linkCards })).data);
    } catch (e) {
      toast.error(apiErrorText(e) ?? t.decks_error);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="fixed inset-0 z-50 bg-black/70 flex items-center justify-center p-4" onClick={onClose}>
      <div className="bg-pokemon-card rounded-lg p-4 w-full max-w-2xl max-h-[85vh] flex flex-col" onClick={(e) => e.stopPropagation()}
        data-testid="deck-print-picker">
        <div className="flex items-center gap-2 mb-1">
          <h3 className="text-white font-medium flex-1">{t.deck_print_heading(slot.name)}</h3>
          <button type="button" onClick={onClose} className="text-gray-400 hover:text-white">✕</button>
        </div>
        <p className="text-gray-400 text-xs mb-2">{t.deck_print_hint}</p>
        {unlinked > 0 && (
          <label className="flex items-center gap-2 text-xs text-gray-300 mb-2">
            <input type="checkbox" checked={linkCards} onChange={(e) => setLinkCards(e.target.checked)} data-testid="deck-print-link" />
            {t.deck_print_link(unlinked)}
          </label>
        )}
        <div className="overflow-y-auto grid grid-cols-3 sm:grid-cols-5 gap-2">
          {prints === null && <div className="col-span-full text-gray-500 text-sm">{t.detail_loading}</div>}
          {prints?.length === 0 && <div className="col-span-full text-gray-500 text-sm">{t.deck_print_none}</div>}
          {prints?.map((p) => (
            // der aktuelle Druck ist nur antippbar, um ihn den Karten ohne Druck zu geben
            <button key={p.tcgdex_card_id} type="button" disabled={busy || (p.current && !(unlinked > 0 && linkCards))}
              onClick={() => void pick(p)}
              data-testid="deck-print" title={[p.name, p.set_name, p.local_id, p.rarity].filter(Boolean).join(" · ")}
              className={`text-left rounded border p-1 ${p.current ? "border-pokemon-yellow ring-1 ring-pokemon-yellow" : "border-gray-700 hover:border-gray-400"}`}>
              <div className="aspect-[63/88] bg-gray-800 rounded overflow-hidden">
                {p.image_url && (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img src={p.image_url} alt="" loading="lazy" className="w-full h-full object-cover"
                    onError={(e) => { e.currentTarget.style.display = "none"; }} />
                )}
              </div>
              <div className="text-[11px] text-gray-200 mt-0.5 truncate">
                {[p.set_code || p.set_name, p.local_id].filter(Boolean).join(" ")}
                {p.regulation_mark && <span className="ml-1 text-gray-500">{p.regulation_mark}</span>}
              </div>
              <div className="text-[10px] text-gray-500 truncate">
                {p.current ? (unlinked > 0 && linkCards ? t.deck_print_current_link : t.deck_print_current) : p.rarity}
              </div>
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
