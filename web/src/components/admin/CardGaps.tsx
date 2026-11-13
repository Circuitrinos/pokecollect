"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import toast from "react-hot-toast";
import { API_BASE, CardGap, CardGapItem, CardGapOverview, cardApi, cardGapsApi } from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import { imageUrl, spiegelUrl } from "@/lib/utils";
import RematchDialog from "@/components/carddetail/RematchDialog";

// Admin: Karten (aller Konten), denen Daten fehlen — kein Katalog-Druck, Nummer ohne
// Nenner, Illustrator, Seltenheit, Folierung … (backend/app/services/rematch.py).
// „Neu abgleichen" öffnet je Karte den Abgleich der Kartenseite; „Alle sicheren
// abgleichen" geht die Liste durch und übernimmt nur, was ohne Rückfrage sicher ist:
// sicherer Druck mit gleichem Namen, bei gleichem Druck nur Fehlendes, Folierung nur,
// wenn es die Karte nur so gibt.
type Owner = "all" | "mine" | "others";
type Result = "done" | "nothing" | "unsure" | "none" | "error";

export default function CardGaps() {
  const { t } = useI18n();
  const [gap, setGap] = useState<CardGap | null>(null);
  const [owner, setOwner] = useState<Owner>("all");
  const [data, setData] = useState<CardGapOverview | null>(null);
  const [loading, setLoading] = useState(false);
  const [open, setOpen] = useState<CardGapItem | null>(null);
  const [results, setResults] = useState<Record<number, Result>>({});
  const [run, setRun] = useState<{ done: number; total: number } | null>(null);
  const stopRef = useRef(false);

  const load = useCallback(() => {
    setLoading(true);
    cardGapsApi.overview(gap, owner).then((r) => setData(r.data)).catch(() => {}).finally(() => setLoading(false));
  }, [gap, owner]);
  useEffect(() => { load(); }, [load]);

  const one = async (item: CardGapItem): Promise<Result> => {
    try {
      const p = (await cardApi.rematch(item.card_id)).data;
      if (!p.found) return "none";
      if (!p.safe) return "unsure";
      const relink = !p.found.current;
      const fields = p.changes.filter((c) => relink || c.kind === "fill").map((c) => c.field);
      const folierung = !p.foil.current && p.foil.why === "only" ? p.foil.suggested : null;
      if (!relink && fields.length === 0 && !folierung) return "nothing";
      await cardApi.applyRematch(item.card_id, { tcgdex_card_id: p.found.tcgdex_card_id, fields, folierung });
      return "done";
    } catch {
      return "error";
    }
  };

  const runAll = async () => {
    const items = data?.items ?? [];
    stopRef.current = false;
    setRun({ done: 0, total: items.length });
    for (let i = 0; i < items.length && !stopRef.current; i++) {
      const r = await one(items[i]);
      setResults((all) => ({ ...all, [items[i].card_id]: r }));
      setRun({ done: i + 1, total: items.length });
    }
    setRun(null);
    load();
  };

  const [copying, setCopying] = useState(false);
  const copyPhotos = async () => {
    setCopying(true);
    try {
      const r = await cardGapsApi.copyPhotos();
      toast.success(t.gaps_photos_done(r.data.shared));
      load();
    } catch {
      toast.error(t.form_save_error);
    } finally {
      setCopying(false);
    }
  };

  const counts = data?.counts;
  const chips = (Object.keys(counts ?? {}) as CardGap[]).filter((g) => (counts?.[g] ?? 0) > 0);

  return (
    <div className="space-y-3" data-testid="card-gaps">
      <p className="text-gray-400 text-xs">{t.gaps_intro}</p>
      <div className="flex flex-wrap items-center gap-1.5">
        <select value={owner} onChange={(e) => setOwner(e.target.value as Owner)} data-testid="gaps-owner"
          className="bg-gray-800 border border-gray-700 rounded px-1.5 py-0.5 text-white text-xs">
          {(["all", "mine", "others"] as Owner[]).map((o) => <option key={o} value={o}>{t.gaps_owner(o)}</option>)}
        </select>
        <button type="button" onClick={() => setGap(null)} data-testid="gaps-chip"
          className={`rounded-full px-2 py-0.5 text-xs ${gap === null ? "bg-pokemon-blue text-white" : "bg-gray-800 text-gray-300 hover:text-white"}`}>
          {t.gaps_all} {data ? `(${data.total})` : ""}
        </button>
        {chips.map((g) => (
          <button key={g} type="button" onClick={() => setGap(g)} data-testid="gaps-chip"
            className={`rounded-full px-2 py-0.5 text-xs ${gap === g ? "bg-pokemon-blue text-white" : "bg-gray-800 text-gray-300 hover:text-white"}`}>
            {t.gaps_label(g)} ({counts?.[g]})
          </button>
        ))}
        <button type="button" onClick={load} disabled={loading} className="ml-auto text-xs text-gray-400 hover:text-white disabled:opacity-40">
          ↻ {t.gaps_reload}
        </button>
      </div>

      {data && data.items.length > 0 && (
        <div className="flex flex-wrap items-center gap-2 text-xs">
          <span className="text-gray-500">{t.gaps_shown(data.shown, data.matching)}</span>
          {run ? (
            <>
              <span className="text-gray-300" data-testid="gaps-progress">{t.gaps_progress(run.done, run.total)}</span>
              <button type="button" onClick={() => { stopRef.current = true; }} className="rounded bg-gray-800 px-2 py-1 text-gray-200 hover:text-white">
                {t.gaps_stop}
              </button>
            </>
          ) : (
            <button type="button" onClick={() => void runAll()} data-testid="gaps-run-all"
              className="rounded bg-pokemon-blue px-2 py-1 text-white hover:opacity-90">↻ {t.gaps_run_all}</button>
          )}
          {(counts?.photo ?? 0) > 0 && !run && (
            <button type="button" onClick={() => void copyPhotos()} disabled={copying} data-testid="gaps-copy-photos"
              title={t.gaps_photos_hint}
              className="rounded bg-gray-800 px-2 py-1 text-gray-200 hover:text-white disabled:opacity-40">
              📷 {t.gaps_photos(counts?.photo ?? 0)}
            </button>
          )}
        </div>
      )}

      {data && data.items.length === 0 && <p className="text-gray-500 text-sm">{t.gaps_none}</p>}
      <div className="space-y-1">
        {data?.items.map((item) => {
          const pic = imageUrl(item.photo, API_BASE) ?? spiegelUrl(item.image, API_BASE);
          const res = results[item.card_id];
          return (
            <div key={item.card_id} className="flex items-center gap-2 rounded bg-gray-900/40 p-1.5" data-testid="gaps-item">
              <Link href={`/cards/${item.card_id}`} className="w-9 shrink-0 self-start aspect-[63/88] rounded overflow-hidden bg-gray-800">
                {/* eslint-disable-next-line @next/next/no-img-element */}
                {pic && <img src={pic} alt="" loading="lazy" className="w-full h-full object-cover" />}
              </Link>
              <div className="min-w-0 flex-1">
                <div className="text-sm text-white truncate">
                  {item.english_name || item.name}
                  {!item.own && item.username && <span className="ml-1 text-[10px] text-gray-500">{t.gaps_by(item.username)}</span>}
                </div>
                <div className="text-[11px] text-gray-500 truncate">{[item.set, item.number, item.language].filter(Boolean).join(" · ")}</div>
                <div className="mt-0.5 flex flex-wrap gap-1">
                  {item.gaps.map((g) => (
                    <span key={g} className={`rounded px-1 text-[10px] ${g === "print" ? "bg-red-900/60 text-red-200" : g === "differs" ? "bg-amber-900/50 text-amber-200" : "bg-gray-800 text-gray-300"}`}>
                      {t.gaps_label(g)}
                    </span>
                  ))}
                  {res && <span className={`text-[10px] ${res === "done" ? "text-green-300" : res === "unsure" ? "text-yellow-300" : "text-gray-400"}`}
                    data-testid="gaps-result">{t.gaps_result(res)}</span>}
                </div>
              </div>
              <button type="button" onClick={() => setOpen(item)} disabled={!!run} data-testid="gaps-rematch"
                className="shrink-0 rounded bg-gray-800 px-2 py-1 text-xs text-gray-200 hover:text-white disabled:opacity-40">
                ↻ {t.gaps_rematch}
              </button>
            </div>
          );
        })}
      </div>

      {open && (
        <RematchDialog cardId={open.card_id} title={open.english_name || open.name}
          photoUrl={imageUrl(open.photo, API_BASE)}
          onClose={() => setOpen(null)}
          onApplied={() => { setResults((all) => ({ ...all, [open.card_id]: "done" })); load(); }} />
      )}
    </div>
  );
}
