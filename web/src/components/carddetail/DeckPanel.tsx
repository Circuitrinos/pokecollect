"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import toast from "react-hot-toast";
import { Card, DeckSummary, cardApi, deckApi } from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import { apiErrorText } from "@/lib/uiLang";

// Deck der Karte (höchstens eines): anzeigen, hineinlegen, verschieben, herausnehmen.
type Props = {
  card: Card;
  onChanged: (card: Card) => void;
};

export default function DeckPanel({ card, onChanged }: Props) {
  const { t } = useI18n();
  const [decks, setDecks] = useState<DeckSummary[]>([]);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    deckApi.list().then((r) => setDecks(r.data)).catch(() => {});
  }, []);

  if (!card.besessen && !card.deck_id) return null;

  const reload = async () => onChanged((await cardApi.get(card.id)).data);

  const put = async (deckId: number) => {
    const target = decks.find((d) => d.id === deckId);
    if (!target) return;
    const move = !!card.deck_id && card.deck_id !== deckId;
    if (move && !confirm(t.detail_deck_move_confirm(card.deck_name ?? "?", target.name))) return;
    setBusy(true);
    try {
      await deckApi.addCards(deckId, [card.id], move);
      await reload();
      toast.success(t.detail_deck_added(target.name));
    } catch (e) {
      toast.error(apiErrorText(e) ?? t.decks_error);
    } finally {
      setBusy(false);
    }
  };

  const takeOut = async () => {
    if (!card.deck_id) return;
    setBusy(true);
    try {
      await deckApi.removeCard(card.deck_id, card.id);
      await reload();
      toast.success(t.detail_deck_removed);
    } catch (e) {
      toast.error(apiErrorText(e) ?? t.decks_error);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="bg-pokemon-card rounded-lg p-4" data-testid="card-deck-panel">
      <h2 className="text-gray-300 font-medium mb-3">{t.detail_deck}</h2>
      {card.deck_id ? (
        <div className="flex flex-wrap gap-2 mb-3">
          <span className="inline-flex items-center gap-1 bg-indigo-900/60 rounded-full px-3 py-1 text-sm text-indigo-100" data-testid="card-deck-chip">
            🃏 <Link href={`/decks/${card.deck_id}`} className="hover:text-pokemon-yellow">{card.deck_name ?? "…"}</Link>
            <button type="button" disabled={busy} onClick={() => void takeOut()} title={t.deck_remove_card}
              className="text-indigo-300 hover:text-red-400 ml-1">✕</button>
          </span>
        </div>
      ) : (
        <p className="text-gray-500 text-sm mb-3">{t.detail_deck_none}</p>
      )}
      {card.besessen && decks.some((d) => d.id !== card.deck_id) && (
        <select value="" disabled={busy} data-testid="card-deck-select"
          onChange={(e) => { if (e.target.value) void put(Number(e.target.value)); }}
          className="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1.5 text-white text-sm">
          <option value="">{t.detail_deck_choose}</option>
          {decks.filter((d) => d.id !== card.deck_id).map((d) => (
            <option key={d.id} value={d.id}>{d.name}</option>
          ))}
        </select>
      )}
    </div>
  );
}
