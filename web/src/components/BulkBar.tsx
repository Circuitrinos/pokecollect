"use client";
import { useEffect, useState } from "react";
import toast from "react-hot-toast";
import { BulkAction, cardApi, Collection, collectionApi } from "@/lib/api";
import { useEnums } from "@/lib/useEnums";
import { useI18n } from "@/lib/i18n";
import type { Selection } from "@/lib/useSelection";

/** Knopf „Auswählen" / „Fertig" für die Kopfzeile eines Kartenrasters. */
export function SelectToggle({ selection, onStart }: { selection: Selection; onStart?: () => void }) {
  const { t } = useI18n();
  return (
    <button type="button"
      onClick={() => { if (!selection.active) onStart?.(); selection.setActive(!selection.active); }}
      aria-pressed={selection.active}
      className={`text-sm rounded px-3 py-1.5 border whitespace-nowrap ${
        selection.active ? "bg-pokemon-accent border-pokemon-accent text-white"
          : "bg-pokemon-card border-gray-700 text-gray-200 hover:text-white"}`}
    >
      {selection.active ? t.sel_done : t.sel_start}
    </button>
  );
}

type Option = { key: string; label: string; action: BulkAction; value?: unknown; needs?: "zustand" | "collection" };

/**
 * Aktionsleiste im Auswahl-Modus (unten fixiert, auf dem Handy über der
 * Bottom-Nav). pageIds = auswählbare Karten der aktuellen Ansicht.
 */
export default function BulkBar({ selection, pageIds, collectionId, onDone }: {
  selection: Selection;
  pageIds: number[];
  collectionId?: number;          // auf einer Sammlungsseite: „aus dieser Sammlung entfernen"
  onDone: () => void;             // nach der Aktion neu laden
}) {
  const { t } = useI18n();
  const { enums } = useEnums();
  const [collections, setCollections] = useState<Collection[]>([]);
  const [choice, setChoice] = useState("");
  const [arg, setArg] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (selection.active) collectionApi.list().then((r) => setCollections(r.data)).catch(() => {});
  }, [selection.active]);

  if (!selection.active) return null;

  const options: Option[] = [
    { key: "catalog", label: t.bulk_catalog_image, action: "catalog_image" },
    { key: "photo", label: t.bulk_own_photo, action: "own_photo" },
    { key: "pp_on", label: t.bulk_pp_on, action: "prize_pack", value: true },
    { key: "pp_off", label: t.bulk_pp_off, action: "prize_pack", value: false },
    { key: "zustand", label: t.bulk_condition, action: "zustand", needs: "zustand" },
    { key: "add", label: t.bulk_add_collection, action: "add_to_collection", needs: "collection" },
    ...(collectionId ? [{ key: "remove", label: t.bulk_remove_here, action: "remove_from_collection" as BulkAction, value: collectionId }] : []),
    { key: "delete", label: t.bulk_delete, action: "delete" },
  ];
  const opt = options.find((o) => o.key === choice);
  const n = selection.ids.size;
  const allOnPage = pageIds.length > 0 && pageIds.every((id) => selection.ids.has(id));
  const ready = !!opt && n > 0 && (!opt.needs || !!arg);

  const apply = async () => {
    if (!opt || !ready || busy) return;
    if (opt.action === "delete" && !confirm(t.bulk_delete_confirm(n))) return;
    const value = opt.needs === "zustand" ? arg : opt.needs === "collection" ? Number(arg) : opt.value;
    setBusy(true);
    try {
      const r = await cardApi.bulk(opt.action, Array.from(selection.ids), value);
      toast.success(t.bulk_result(r.data.done, r.data.unchanged));
      selection.clear();
      setChoice("");
      setArg("");
      onDone();
    } catch {
      toast.error(t.bulk_error);
    } finally {
      setBusy(false);
    }
  };

  const ctl = "bg-gray-800 border border-gray-700 rounded px-2 py-1.5 text-white text-sm disabled:opacity-50";
  return (
    <div className="fixed inset-x-0 z-40 bottom-[calc(3.5rem_+_env(safe-area-inset-bottom))] md:bottom-0 bg-pokemon-dark/95 backdrop-blur border-t border-gray-800 p-3">
      <div className="max-w-screen-2xl mx-auto flex items-center gap-2 flex-wrap">
        <span className="text-sm text-gray-200 font-medium shrink-0">{t.sel_count(n)}</span>
        <button type="button" onClick={() => selection.setMany(pageIds, !allOnPage)} disabled={!pageIds.length}
          className="text-xs text-pokemon-blue hover:underline disabled:opacity-40 whitespace-nowrap">
          {allOnPage ? t.sel_none : t.sel_all_page}
        </button>
        <div className="flex items-center gap-2 flex-wrap justify-end flex-1 min-w-0">
          <select value={choice} onChange={(e) => { setChoice(e.target.value); setArg(""); }} disabled={busy}
            aria-label={t.bulk_action} className={`${ctl} max-w-[55vw] md:max-w-none`}>
            <option value="">{t.bulk_action}</option>
            {options.map((o) => <option key={o.key} value={o.key}>{o.label}</option>)}
          </select>
          {opt?.needs === "zustand" && (
            <select value={arg} onChange={(e) => setArg(e.target.value)} disabled={busy} aria-label={t.bulk_condition} className={ctl}>
              <option value="">–</option>
              {(enums?.zustand ?? []).map((z) => <option key={z} value={z}>{z}</option>)}
            </select>
          )}
          {opt?.needs === "collection" && (
            <select value={arg} onChange={(e) => setArg(e.target.value)} disabled={busy} aria-label={t.bulk_add_collection}
              className={`${ctl} max-w-[45vw] md:max-w-none`}>
              <option value="">–</option>
              {collections.filter((c) => c.id !== collectionId).map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
            </select>
          )}
          <button type="button" onClick={() => void apply()} disabled={!ready || busy}
            className={`text-white text-sm rounded px-3 py-1.5 disabled:opacity-50 whitespace-nowrap ${
              opt?.action === "delete" ? "bg-red-700 hover:bg-red-600" : "bg-pokemon-accent hover:bg-blue-700"}`}>
            {busy ? "…" : t.bulk_apply}
          </button>
        </div>
      </div>
    </div>
  );
}
