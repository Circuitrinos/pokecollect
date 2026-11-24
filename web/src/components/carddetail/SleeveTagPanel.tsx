"use client";
import { useEffect, useState } from "react";
import toast from "react-hot-toast";
import { QrCode } from "lucide-react";
import { Card, SleeveTag, tagError, tagsApi } from "@/lib/api";
import { useI18n } from "@/lib/i18n";

// Hüllen-Aufkleber der Karte (backend/app/services/sleeve_tags.py): Code zeigen,
// lösen — oder, falls die Kamera den QR-Code nicht liest, per 4-Zeichen-Code zuordnen.
export default function SleeveTagPanel({ card }: { card: Card }) {
  const { t } = useI18n();
  const [tag, setTag] = useState<SleeveTag | null | undefined>(undefined);
  const [number, setNumber] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!card.besessen) return;
    tagsApi.byCard(card.id).then((r) => setTag(r.data)).catch(() => setTag(null));
  }, [card.id, card.besessen]);

  if (!card.besessen || tag === undefined) return null;

  const link = async (replace = false) => {
    const code = number.trim();
    if (!code) return;
    setBusy(true);
    try {
      const r = await tagsApi.link(code, card.id, replace);
      setTag({ ...r.data, card: null });
      setNumber("");
    } catch (e) {
      const { reason, other } = tagError(e);
      if (reason === "card_tagged") {
        if (confirm(t.tag_card_tagged(String(other)))) await link(true);
        return;
      }
      toast.error(reason === "tag_linked" ? t.tag_already_used
        : reason === "format" ? t.tag_err_format
        : reason === "unknown" ? t.tag_err_unknown : t.tags_error);
    } finally {
      setBusy(false);
    }
  };

  const unlink = async () => {
    if (!tag || !confirm(t.tag_unlink_confirm)) return;
    setBusy(true);
    try {
      await tagsApi.unlink(tag.code);
      setTag(null);
    } catch {
      toast.error(t.tags_error);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="bg-pokemon-card rounded-lg p-4" data-testid="card-tag-panel">
      <h2 className="text-gray-300 font-medium mb-3">{t.tag_panel_title}</h2>
      {tag ? (
        <span className="inline-flex items-center gap-1.5 bg-gray-800 rounded-full px-3 py-1 text-sm text-white">
          <QrCode className="w-4 h-4 text-pokemon-yellow" aria-hidden />
          <span className="font-mono tracking-wider">{tag.code}</span>
          <button type="button" disabled={busy} onClick={() => void unlink()} title={t.tag_unlink}
            className="text-gray-400 hover:text-red-400 ml-1">✕</button>
        </span>
      ) : (
        <>
          <p className="text-gray-500 text-sm mb-2">{t.tag_panel_none}</p>
          <form className="flex gap-2" onSubmit={(e) => { e.preventDefault(); void link(); }}>
            <input value={number} onChange={(e) => setNumber(e.target.value)} placeholder={t.tag_panel_number}
              maxLength={12} autoCapitalize="characters" spellCheck={false} className="w-32 font-mono uppercase bg-gray-800 border border-gray-700 rounded px-2 py-1.5 text-white text-sm" />
            <button type="submit" disabled={busy || !number.trim()}
              className="bg-gray-700 hover:bg-gray-600 text-white text-sm px-3 py-1.5 rounded disabled:opacity-50">
              {t.tag_panel_link}
            </button>
          </form>
        </>
      )}
    </div>
  );
}
