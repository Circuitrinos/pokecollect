"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import toast from "react-hot-toast";
import { DeckFormat, DeckSummary, deckApi } from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import { apiErrorText } from "@/lib/uiLang";
import { DeckStatusBadge } from "@/components/decks/DeckBits";

const FORMATS: DeckFormat[] = ["standard", "expanded", "unlimited"];

export default function DecksPage() {
  const { t } = useI18n();
  const router = useRouter();
  const [decks, setDecks] = useState<DeckSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [showNew, setShowNew] = useState(false);
  const [name, setName] = useState("");
  const [format, setFormat] = useState<DeckFormat>("standard");
  const [text, setText] = useState("");
  const [fill, setFill] = useState(true);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    deckApi.list().then((r) => setDecks(r.data)).catch(() => {}).finally(() => setLoading(false));
  }, []);

  // Name aus der eingefügten Liste vorschlagen: das erste Pokémon
  const suggestName = (txt: string) => {
    if (name.trim()) return;
    const m = txt.match(/^\s*\d+\s+(.+?)\s+[A-Za-z0-9-]{2,9}\s+[A-Za-z]{0,6}\d+\s*$/m);
    if (m) setName(m[1]);
  };

  const create = async () => {
    if (!name.trim()) { toast.error(t.decks_name_required); return; }
    setBusy(true);
    try {
      const r = await deckApi.create({ name: name.trim(), format, text: text.trim() || null, fill: !!text.trim() && fill });
      if (r.data.lines) toast.success(t.deck_import_result(r.data.matched, r.data.unmatched));
      else toast.success(t.decks_created);
      router.push(`/decks/${r.data.deck.id}`);
    } catch (e) {
      toast.error(apiErrorText(e) ?? t.decks_error);
      setBusy(false);
    }
  };

  return (
    <div className="max-w-4xl mx-auto">
      <div className="flex items-start justify-between gap-3 mb-6">
        <div>
          <h1 className="text-2xl font-bold text-white">{t.decks_title}</h1>
          <p className="text-gray-400 text-sm">{t.decks_subtitle}</p>
        </div>
        <button type="button" onClick={() => setShowNew((v) => !v)} data-testid="deck-new"
          className="shrink-0 bg-pokemon-red text-white text-sm px-3 py-1.5 rounded hover:bg-red-600">
          {t.decks_new}
        </button>
      </div>

      {showNew && (
        <div className="bg-pokemon-card rounded-lg p-4 mb-6 space-y-3" data-testid="deck-new-form">
          <div className="grid grid-cols-1 sm:grid-cols-[1fr_auto] gap-3">
            <div>
              <label className="text-gray-400 text-xs block mb-1">{t.decks_name}</label>
              <input type="text" value={name} onChange={(e) => setName(e.target.value)} maxLength={120}
                placeholder={t.decks_name_placeholder} data-testid="deck-name"
                className="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1.5 text-white text-sm" />
            </div>
            <div>
              <label className="text-gray-400 text-xs block mb-1">{t.deck_format}</label>
              <select value={format} onChange={(e) => setFormat(e.target.value as DeckFormat)}
                className="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1.5 text-white text-sm">
                {FORMATS.map((f) => <option key={f} value={f}>{t.deck_format_label(f)}</option>)}
              </select>
            </div>
          </div>
          <div>
            <label className="text-gray-400 text-xs block mb-1">{t.decks_import_label}</label>
            <textarea value={text} onChange={(e) => setText(e.target.value)} onBlur={() => suggestName(text)}
              rows={8} placeholder={t.decks_import_placeholder} data-testid="deck-text" spellCheck={false}
              className="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1.5 text-white text-xs font-mono" />
          </div>
          {!!text.trim() && (
            <label className="flex items-center gap-2 text-sm text-gray-300 cursor-pointer">
              <input type="checkbox" checked={fill} onChange={(e) => setFill(e.target.checked)} className="accent-pokemon-yellow" />
              {t.decks_fill_now}
            </label>
          )}
          <div className="flex gap-2">
            <button type="button" onClick={() => void create()} disabled={busy} data-testid="deck-create"
              className="bg-green-600 text-white text-sm px-3 py-1.5 rounded hover:bg-green-700 disabled:opacity-50">
              {t.decks_create}
            </button>
            <button type="button" onClick={() => setShowNew(false)} className="bg-gray-700 text-white text-sm px-3 py-1.5 rounded">
              {t.form_cancel}
            </button>
          </div>
        </div>
      )}

      {loading ? (
        <div className="flex items-center justify-center h-64 text-gray-500">{t.detail_loading}</div>
      ) : decks.length === 0 ? (
        <p className="text-gray-500 text-sm">{t.decks_empty}</p>
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4" data-testid="deck-list">
          {decks.map((d) => (
            <Link key={d.id} href={`/decks/${d.id}`}
              className="flex gap-3 bg-pokemon-card rounded-lg p-3 hover:ring-1 hover:ring-pokemon-yellow/60 transition">
              <div className="w-16 shrink-0 aspect-[63/88] rounded bg-gray-800 overflow-hidden">
                {d.cover_url && (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img src={d.cover_url} alt="" className="w-full h-full object-cover" loading="lazy"
                    onError={(e) => { e.currentTarget.style.display = "none"; }} />
                )}
              </div>
              <div className="min-w-0 flex-1">
                <h2 className="text-lg font-semibold text-white truncate">{d.name}</h2>
                <div className="flex flex-wrap items-center gap-2 mt-1">
                  <DeckStatusBadge status={d.status} />
                  <span className="text-xs text-gray-400">{t.deck_format_label(d.format)}</span>
                </div>
                <p className="text-gray-500 text-xs mt-1">
                  {t.deck_cards_count(d.total)} · {t.deck_owned_count(d.in_deck, d.total)}
                </p>
              </div>
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}
