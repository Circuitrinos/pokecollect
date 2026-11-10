"use client";
import { useEffect, useState } from "react";
import toast from "react-hot-toast";
import { Card, CatalogItem, cardApi, catalogApi } from "@/lib/api";
import CatalogCardImage from "@/components/CatalogCardImage";
import { looksLikePokemonComLink, pokemonComSearchUrl, rememberLinkTarget } from "@/lib/pokemonComLink";
import { useI18n } from "@/lib/i18n";
import { X } from "lucide-react";

// „Richtige Karte finden": Katalog nach Namen durchsuchen (Bilder von TCGdex,
// sonst aus der offiziellen Datenbank), die passende Karte antippen und
// übernehmen — Set, Nummer, Seltenheit und Bild kommen dann aus dem Katalog.
export default function CardFinder({ card, onClose, onLinked }: {
  card: Card;
  onClose: () => void;
  onLinked: (card: Card) => void;
}) {
  const { t, lang } = useI18n();
  const [q, setQ] = useState(card.englischer_name || card.kartenname || "");
  const [items, setItems] = useState<CatalogItem[] | null>(null);
  const [total, setTotal] = useState(0);
  const [picked, setPicked] = useState<CatalogItem | null>(null);
  const [saving, setSaving] = useState(false);
  const [link, setLink] = useState("");

  const applyLink = async (text: string) => {
    if (!text.trim()) return;
    setSaving(true);
    try {
      const r = await cardApi.linkPokemonCom(card.id, text.trim());
      onLinked(r.data);
      toast.success(t.finder_linked);
      onClose();
    } catch (e) {
      const status = (e as { response?: { status?: number } })?.response?.status;
      toast.error(status === 422 ? t.finder_link_invalid : status === 404 ? t.finder_link_no_image : t.form_save_error);
    } finally {
      setSaving(false);
    }
  };
  const pasteLink = async () => {
    try {
      const text = await navigator.clipboard.readText();
      setLink(text);
      if (looksLikePokemonComLink(text)) await applyLink(text);
      else toast.error(t.finder_link_invalid);
    } catch {
      toast.error(t.finder_paste_blocked);
    }
  };

  useEffect(() => {
    const term = q.trim();
    if (term.length < 2) { setItems([]); setTotal(0); return; }
    let alive = true;
    const id = window.setTimeout(() => {
      catalogApi.list({ q: term, limit: 60, sort: "set" })
        .then((r) => { if (alive) { setItems(r.data.items); setTotal(r.data.total); } })
        .catch(() => { if (alive) setItems([]); });
    }, 300);
    return () => { alive = false; window.clearTimeout(id); };
  }, [q]);

  const setLabel = (c: CatalogItem) => (lang === "EN" && c.set_name_en ? c.set_name_en : c.set_name) ?? c.set_id ?? "";
  const nameOf = (c: CatalogItem) => (lang === "EN" ? c.name_en || c.name : c.name || c.name_en) ?? c.card_id;

  const use = async () => {
    if (!picked) return;
    setSaving(true);
    try {
      const img = picked.image_url ?? picked.image_fallback ?? null;
      const r = await cardApi.linkCatalog(card.id, picked.card_id, img);
      onLinked(r.data);
      toast.success(t.finder_linked);
      onClose();
    } catch {
      toast.error(t.form_save_error);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-end sm:items-center justify-center bg-black/70" role="dialog"
      aria-label={t.finder_title} onClick={onClose}>
      <div className="flex max-h-[92vh] w-full max-w-3xl flex-col rounded-t-xl sm:rounded-xl bg-pokemon-dark border border-gray-700"
        onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center gap-2 border-b border-gray-800 p-3">
          <h2 className="text-white font-semibold text-sm flex-1">{t.finder_title}</h2>
          <button type="button" onClick={onClose} aria-label={t.form_cancel} className="text-gray-400 hover:text-white"><X size={18} /></button>
        </div>
        <div className="p-3 space-y-1">
          <input autoFocus value={q} onChange={(e) => { setQ(e.target.value); setPicked(null); }}
            placeholder={t.finder_placeholder} aria-label={t.finder_placeholder}
            className="w-full bg-gray-800 border border-gray-700 rounded px-3 py-2 text-white text-sm" />
          <p className="text-gray-500 text-xs">
            {items === null ? "" : total > items.length ? t.finder_more(items.length, total) : t.finder_count(total)}
          </p>
        </div>
        <div className="flex-1 overflow-y-auto px-3 pb-3">
          <div className="grid grid-cols-3 sm:grid-cols-5 gap-2">
            {(items ?? []).map((c) => {
              const sel = picked?.card_id === c.card_id;
              return (
                <button type="button" key={c.card_id} onClick={() => setPicked(c)} data-card={c.card_id}
                  className={`text-left rounded-lg overflow-hidden border-2 ${sel ? "border-pokemon-yellow" : "border-transparent hover:border-gray-600"}`}>
                  <div className="aspect-[63/88] bg-gray-800">
                    <CatalogCardImage url={c.image_url} fallback={c.image_fallback} alt={nameOf(c)}
                      className="w-full h-full object-cover" />
                  </div>
                  <div className="px-1 py-0.5">
                    <div className="text-[11px] text-white truncate">{nameOf(c)}</div>
                    <div className="text-[10px] text-gray-500 truncate">{setLabel(c)} · {c.local_id}</div>
                  </div>
                </button>
              );
            })}
          </div>
          {items !== null && items.length === 0 && q.trim().length >= 2 && (
            <p className="py-8 text-center text-gray-500 text-sm">{t.finder_none}</p>
          )}
        </div>
        <div className="border-t border-gray-800 p-3 space-y-2 text-xs" data-testid="pokemon-com">
          <div className="flex flex-wrap items-center gap-2 text-gray-400">
            <span>{t.finder_pokemon_com_hint}</span>
            <a href={pokemonComSearchUrl(q || card.englischer_name || card.kartenname)} target="_blank" rel="noopener noreferrer"
              onClick={() => rememberLinkTarget(card.id)}
              className="rounded border border-gray-700 px-2 py-1 text-gray-200 hover:text-white">
              {t.finder_pokemon_com_open} ↗
            </a>
          </div>
          <div className="flex gap-1">
            <input value={link} onChange={(e) => setLink(e.target.value)} placeholder={t.finder_link_placeholder}
              aria-label={t.finder_link_placeholder}
              onKeyDown={(e) => { if (e.key === "Enter") void applyLink(link); }}
              className="min-w-0 flex-1 bg-gray-800 border border-gray-700 rounded px-2 py-1.5 text-white" />
            {typeof navigator !== "undefined" && !!navigator.clipboard?.readText && (
              <button type="button" onClick={() => void pasteLink()} disabled={saving}
                className="rounded border border-gray-700 px-2 py-1 text-gray-200 hover:text-white disabled:opacity-40">
                {t.finder_paste}
              </button>
            )}
            <button type="button" onClick={() => void applyLink(link)} disabled={saving || !link.trim()}
              className="rounded bg-pokemon-blue px-2 py-1 text-white disabled:opacity-40">
              {t.finder_link_use}
            </button>
          </div>
        </div>
        <div className="flex items-center gap-2 border-t border-gray-800 p-3">
          <span className="flex-1 truncate text-xs text-gray-400">
            {picked ? `${nameOf(picked)} · ${setLabel(picked)} ${picked.local_id ?? ""}` : t.finder_hint}
          </span>
          <button type="button" onClick={() => void use()} disabled={!picked || saving}
            className="rounded bg-green-600 px-3 py-1.5 text-sm text-white hover:bg-green-700 disabled:opacity-40">
            {t.finder_use}
          </button>
        </div>
      </div>
    </div>
  );
}
