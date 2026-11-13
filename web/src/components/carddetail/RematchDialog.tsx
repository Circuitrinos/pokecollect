"use client";
import { useCallback, useEffect, useState } from "react";
import toast from "react-hot-toast";
import { X } from "lucide-react";
import { Card, RematchPrint, RematchProposal, cardApi } from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import { valueLabel } from "@/lib/enumLabels";

// „Neu abgleichen": den Druck der Karte noch einmal bestimmen (eigenes Foto durch den
// Bild-Abgleich, sonst die Verknüpfung, sonst Set + Nummer) und zeigen, was der Katalog
// ergänzt oder richtigstellt — jede Änderung abwählbar, fehlende Foliierung wählbar,
// ein anderer Druck antippbar. Kartenseite und Admin-Werkzeug „Karten mit Lücken".
export default function RematchDialog({ cardId, title, photoUrl, onClose, onApplied }: {
  cardId: number;
  title: string;
  photoUrl?: string | null;
  onClose: () => void;
  onApplied: (card: Card) => void;
}) {
  const { t, lang } = useI18n();
  const [print, setPrint] = useState<string | undefined>(undefined);
  const [p, setP] = useState<RematchProposal | null>(null);
  const [failed, setFailed] = useState(false);
  const [off, setOff] = useState<Set<string>>(new Set());
  const [foil, setFoil] = useState<string | null>(null);
  const [others, setOthers] = useState(false);
  const [saving, setSaving] = useState(false);

  const load = useCallback((which?: string) => {
    setP(null);
    setFailed(false);
    cardApi.rematch(cardId, which)
      .then((r) => {
        setP(r.data);
        setOff(new Set());
        setFoil(r.data.foil.current ?? r.data.foil.suggested ?? null);
      })
      .catch(() => setFailed(true));
  }, [cardId]);
  useEffect(() => { load(print); }, [load, print]);

  const found = p?.found ?? null;
  const relink = !!found && !found.current;
  const chosen = (p?.changes ?? []).filter((c) => !off.has(c.field));
  const foilChange = !!p && foil !== null && foil !== (p.foil.current ?? null);
  const anything = relink || chosen.length > 0 || foilChange;

  const apply = async () => {
    if (!found) return;
    setSaving(true);
    try {
      const r = await cardApi.applyRematch(cardId, {
        tcgdex_card_id: found.tcgdex_card_id,
        fields: chosen.map((c) => c.field),
        folierung: foilChange ? foil : null,
      });
      toast.success(t.rematch_done);
      onApplied(r.data);
      onClose();
    } catch {
      toast.error(t.form_save_error);
    } finally {
      setSaving(false);
    }
  };

  const label = (field: string) => t.rematch_field(field);
  const show = (field: string, v: string | null) =>
    v === null || v === "" ? "–" : field === "seltenheit" ? valueLabel(v, lang) : field === "bild" ? t.rematch_image_yes : v;
  const printLine = (r: RematchPrint) => [r.set_code || r.set_name, r.local_id].filter(Boolean).join(" ");

  return (
    <div className="fixed inset-0 z-50 flex items-end sm:items-center justify-center bg-black/70" role="dialog"
      aria-label={t.rematch_title} onClick={onClose}>
      <div className="flex max-h-[92vh] w-full max-w-2xl flex-col rounded-t-xl sm:rounded-xl bg-pokemon-dark border border-gray-700"
        onClick={(e) => e.stopPropagation()} data-testid="rematch-dialog">
        <div className="flex items-center gap-2 border-b border-gray-800 p-3">
          <h2 className="text-white font-semibold text-sm flex-1 truncate">{t.rematch_title}: {title}</h2>
          <button type="button" onClick={onClose} aria-label={t.form_cancel} className="text-gray-400 hover:text-white"><X size={18} /></button>
        </div>

        <div className="flex-1 overflow-y-auto p-3 space-y-3 text-sm">
          {!p && !failed && <p className="text-gray-400 py-6 text-center" data-testid="rematch-loading">{t.rematch_loading}</p>}
          {failed && <p className="text-red-300 py-6 text-center">{t.form_save_error}</p>}
          {p && !found && <p className="text-yellow-300" data-testid="rematch-none">{t.rematch_none}</p>}
          {p && found && (
            <>
              <div className="flex gap-3 items-start">
                {photoUrl && (
                  <figure className="w-24 shrink-0">
                    {/* eslint-disable-next-line @next/next/no-img-element */}
                    <img src={photoUrl} alt="" className="w-full aspect-[63/88] object-cover rounded bg-gray-800" />
                    <figcaption className="text-[10px] text-gray-500 mt-0.5 text-center">{t.rematch_yours}</figcaption>
                  </figure>
                )}
                <figure className="w-24 shrink-0">
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img src={found.image_url ?? ""} alt="" className="w-full aspect-[63/88] object-cover rounded bg-gray-800" />
                  <figcaption className="text-[10px] text-gray-500 mt-0.5 text-center">{t.rematch_catalog}</figcaption>
                </figure>
                <div className="min-w-0 flex-1 space-y-1">
                  <div className="text-white font-medium" data-testid="rematch-found">{found.name}</div>
                  <div className="text-gray-400 text-xs">{[found.set_name, printLine(found), found.rarity].filter(Boolean).join(" · ")}</div>
                  <div className={`text-xs ${p.sure ? "text-green-300" : "text-yellow-300"}`} data-testid="rematch-how">
                    {p.sure ? "✓" : "⚠"} {t.rematch_how(p.how, p.sure, p.photo === "used")}
                  </div>
                  {relink && <div className="text-xs text-amber-300" data-testid="rematch-relink">⇄ {t.rematch_relink}</div>}
                  {(p.photo === "down" || p.photo === "off") && <div className="text-xs text-gray-500">{t.rematch_photo_down}</div>}
                </div>
              </div>

              {p.changes.length > 0 ? (
                <div className="rounded border border-gray-800 divide-y divide-gray-800" data-testid="rematch-changes">
                  {p.changes.map((c) => (
                    <label key={c.field} className="flex items-start gap-2 px-2 py-1.5 cursor-pointer" data-testid="rematch-change">
                      <input type="checkbox" className="mt-0.5" checked={!off.has(c.field)}
                        onChange={(e) => setOff((s) => { const n = new Set(s); if (e.target.checked) n.delete(c.field); else n.add(c.field); return n; })} />
                      <span className="w-28 shrink-0 text-gray-400 text-xs">{label(c.field)}</span>
                      <span className="min-w-0 flex-1 text-xs">
                        {c.kind === "change" && <span className="text-gray-500 line-through mr-1 break-all">{show(c.field, c.old)}</span>}
                        <span className="text-white break-all">{show(c.field, c.new)}</span>
                        {c.kind === "fill" && <span className="ml-1 text-[10px] text-green-400/80">{t.rematch_fill}</span>}
                      </span>
                    </label>
                  ))}
                </div>
              ) : (
                <p className="text-green-300 text-xs" data-testid="rematch-nothing">✓ {t.rematch_nothing}</p>
              )}

              <div className="space-y-1" data-testid="rematch-foil">
                <div className="flex flex-wrap items-center gap-1.5">
                  <span className="w-28 text-gray-400 text-xs">{t.field_foiling}</span>
                  {p.foil.options.map((o) => (
                    <button key={o} type="button" onClick={() => setFoil(o)} data-testid="rematch-foil-option"
                      className={`rounded-full px-2 py-0.5 text-xs ${foil === o ? "bg-pokemon-blue text-white" : "bg-gray-800 text-gray-300 hover:text-white"}`}>
                      {valueLabel(o, lang)}
                    </button>
                  ))}
                </div>
                {!p.foil.current && (
                  <p className="text-[11px] text-gray-500">
                    {p.foil.suggested ? t.rematch_foil_why(p.foil.why) : t.rematch_foil_pick}
                  </p>
                )}
                {p.foil.current && p.foil.photo_hint && p.foil.current !== "Reverse Holo" && p.foil.options.includes("Reverse Holo") && (
                  <p className="text-[11px] text-purple-300">✨ {t.rematch_foil_hint}</p>
                )}
              </div>
            </>
          )}

          {p && p.alternatives.length > 0 && (
            <div>
              <button type="button" onClick={() => setOthers((o) => !o)} data-testid="rematch-others"
                className="text-xs text-blue-300 hover:underline">
                {others ? "▾" : "▸"} {t.rematch_others(p.alternatives.length)}
              </button>
              {others && (
                <div className="mt-2 grid grid-cols-4 sm:grid-cols-6 gap-2">
                  {p.alternatives.map((a) => (
                    <button key={a.tcgdex_card_id} type="button" onClick={() => setPrint(a.tcgdex_card_id)}
                      data-testid="rematch-alt" title={[a.name, a.set_name, a.local_id, a.rarity].filter(Boolean).join(" · ")}
                      className={`text-left rounded border p-0.5 ${a.current ? "border-pokemon-yellow" : "border-gray-700 hover:border-gray-400"}`}>
                      {/* eslint-disable-next-line @next/next/no-img-element */}
                      <img src={a.image_url ?? ""} alt="" loading="lazy" className="w-full aspect-[63/88] object-cover rounded bg-gray-800" />
                      <div className="text-[10px] text-gray-300 truncate">{a.photo ? "📷 " : ""}{printLine(a)}</div>
                      {a.current && <div className="text-[9px] text-pokemon-yellow">{t.rematch_current}</div>}
                    </button>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>

        <div className="flex items-center gap-2 border-t border-gray-800 p-3">
          <span className="flex-1 text-xs text-gray-400">{p && found ? t.rematch_summary(chosen.length + (foilChange ? 1 : 0), relink) : ""}</span>
          <button type="button" onClick={onClose} className="rounded px-3 py-1.5 text-sm text-gray-300 hover:text-white">{t.form_cancel}</button>
          <button type="button" onClick={() => void apply()} disabled={!found || !anything || saving} data-testid="rematch-apply"
            className="rounded bg-green-600 px-3 py-1.5 text-sm text-white hover:bg-green-700 disabled:opacity-40">
            {t.rematch_apply}
          </button>
        </div>
      </div>
    </div>
  );
}
