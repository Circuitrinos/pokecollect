"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import toast from "react-hot-toast";
import { API_BASE, StampItem, StampOverview, StampTemplate, cardApi, scanQualityApi } from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import { imageUrl } from "@/lib/utils";
import StampEditor from "@/components/StampEditor";

// Admin: Prize-Pack-Fotos, deren Play!-Stempel noch nicht markiert ist — der
// schnelle Weg, Trainingsdaten für einen Stempel-Detektor zu sammeln.
// „Bei allen markieren": die gelernte Vorlage sucht auf jedem Foto der Liste;
// gespeichert wird erst nach Bestätigen (sichere Funde gesammelt, unsichere einzeln).
type Found =
  | { state: "sure" | "maybe"; quad: number[][]; pct: number }
  | { state: "none" | "error" | "skipped" };
const BULK_LANES = 3;   // gleichzeitige Suchen im Bild-Abgleich

export default function StampMarks() {
  const { t, lang } = useI18n();
  const [marked, setMarked] = useState(false);
  const [data, setData] = useState<StampOverview | null>(null);
  const [editing, setEditing] = useState<StampItem | null>(null);
  const [template, setTemplate] = useState<StampTemplate | null>(null);
  const [learning, setLearning] = useState(false);
  const [found, setFound] = useState<Record<number, Found>>({});
  const [bulk, setBulk] = useState<{ done: number; total: number } | null>(null);
  const [saving, setSaving] = useState(false);
  const stopRef = useRef(false);
  const setOne = (id: number, f: Found | null) => setFound((all) => {
    const next = { ...all };
    if (f) next[id] = f; else delete next[id];
    return next;
  });

  const load = useCallback(() => {
    scanQualityApi.stamps(marked).then((r) => setData(r.data)).catch(() => {});
  }, [marked]);
  useEffect(() => { load(); }, [load]);
  useEffect(() => { scanQualityApi.stampTemplate().then((r) => setTemplate(r.data)).catch(() => {}); }, []);

  const learn = async () => {
    setLearning(true);
    try {
      const r = await scanQualityApi.learnStampTemplate();
      toast.success(t.stamps_learned(r.data.samples, r.data.marked, r.data.guessed));
      setTemplate({ exists: true, samples: r.data.samples, marked: r.data.marked, built_at: r.data.built_at });
    } catch (e) {
      const detail = (e as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
      toast.error(typeof detail === "string" ? detail : t.form_save_error);
    } finally {
      setLearning(false);
    }
  };

  const runBulk = async () => {
    const todo = (data?.items ?? []).filter((i) => !i.stamp_quad && !found[i.card_id]);
    if (!todo.length) return;
    stopRef.current = false;
    let next = 0, done = 0, noTemplate = false;
    setBulk({ done: 0, total: todo.length });
    const lane = async () => {
      while (!stopRef.current && next < todo.length) {
        const it = todo[next++];
        let f: Found = { state: "error" };
        try {
          const r = (await cardApi.stampSuggest(it.card_id, null)).data;
          if (!r.template) { noTemplate = true; stopRef.current = true; break; }
          const pct = Math.round((r.score ?? 0) * 100);
          f = !r.quad ? { state: "none" } : { state: r.ok && r.sure ? "sure" : "maybe", quad: r.quad, pct };
        } catch { /* f bleibt "error" */ }
        setOne(it.card_id, f);
        setBulk({ done: ++done, total: todo.length });
      }
    };
    await Promise.all(Array.from({ length: Math.min(BULK_LANES, todo.length) }, lane));
    setBulk(null);
    if (noTemplate) toast.error(t.stamp_suggest_no_template);
  };

  const keep = async (it: StampItem) => {
    const f = found[it.card_id];
    if (!f || !("quad" in f)) return false;
    try {
      await cardApi.setStamp(it.card_id, f.quad);
      setOne(it.card_id, null);
      return true;
    } catch {
      return false;
    }
  };
  const keepOne = async (it: StampItem) => {
    setSaving(true);
    const ok = await keep(it);
    setSaving(false);
    if (ok) toast.success(t.stamp_saved); else toast.error(t.form_save_error);
    load();
  };
  const saveSure = async () => {
    const items = (data?.items ?? []).filter((i) => found[i.card_id]?.state === "sure");
    setSaving(true);
    let n = 0;
    for (const it of items) if (await keep(it)) n++;
    setSaving(false);
    if (n) toast.success(t.stamps_bulk_saved(n));
    if (n < items.length) toast.error(t.form_save_error);
    load();
  };

  const nameOf = (it: StampItem) => (lang === "EN" && it.english_name) || it.name;
  const c = data?.counts;
  const unmarked = (data?.items ?? []).filter((i) => !i.stamp_quad);
  const tally = { sure: 0, maybe: 0, none: 0 };
  for (const i of unmarked) {
    const st = found[i.card_id]?.state;
    if (st === "sure" || st === "maybe") tally[st]++;
    else if (st === "none" || st === "error") tally.none++;
  }
  const canBulk = unmarked.some((i) => !found[i.card_id]);
  const hasTemplate = !!template?.exists;
  return (
    <div className="space-y-3" data-testid="stamp-marks">
      <p className="text-gray-400 text-xs">{t.stamps_hint}</p>
      <div className="flex flex-wrap items-center gap-3 rounded bg-gray-800/60 px-3 py-2" data-testid="stamp-template">
        <span className="text-sm text-gray-300 flex-1 min-w-0">
          {template?.exists ? t.stamps_template(template.samples ?? 0, template.marked ?? 0) : t.stamps_template_none}
          <span className="block text-[11px] text-gray-500">{t.stamps_learn_hint}</span>
        </span>
        <button type="button" onClick={() => void learn()} disabled={learning} data-testid="stamp-template-learn"
          className="rounded bg-pokemon-blue px-3 py-1.5 text-sm text-white hover:opacity-90 disabled:opacity-50">
          {learning ? t.stamps_learning : t.stamps_learn}
        </button>
      </div>
      <div className="flex flex-wrap items-center gap-3">
        {c && <span className="text-gray-300 text-sm">{t.stamps_counts(c.marked, c.photos)}</span>}
        <label className="ml-auto flex items-center gap-2 text-xs text-gray-300 cursor-pointer">
          <input type="checkbox" checked={marked} onChange={(e) => setMarked(e.target.checked)} className="accent-pokemon-yellow" />
          {t.stamps_show_marked}
        </label>
      </div>
      {!marked && unmarked.length > 0 && (
        <div className="flex flex-wrap items-center gap-2 rounded border border-gray-800 p-2" data-testid="stamp-bulk">
          {bulk ? (
            <>
              <span className="text-xs text-amber-300" data-testid="stamp-bulk-progress">{t.stamps_bulk_running(bulk.done, bulk.total)}</span>
              <button type="button" onClick={() => { stopRef.current = true; }}
                className="rounded bg-gray-700 px-2 py-0.5 text-xs text-white hover:bg-gray-600">{t.stamps_bulk_stop}</button>
            </>
          ) : canBulk && (
            <button type="button" onClick={() => void runBulk()} disabled={!hasTemplate || saving} data-testid="stamp-bulk-run"
              className="rounded bg-pokemon-blue px-3 py-1.5 text-sm text-white hover:opacity-90 disabled:opacity-50">
              🎯 {t.stamps_bulk}
            </button>
          )}
          {tally.sure > 0 && !bulk && (
            <button type="button" onClick={() => void saveSure()} disabled={saving} data-testid="stamp-bulk-save"
              className="rounded bg-green-700 px-3 py-1.5 text-sm text-white hover:bg-green-600 disabled:opacity-50">
              ✓ {t.stamps_bulk_save(tally.sure)}
            </button>
          )}
          {tally.sure + tally.maybe + tally.none > 0 && (
            <span className="text-xs text-gray-300" data-testid="stamp-bulk-summary">{t.stamps_bulk_summary(tally.sure, tally.maybe, tally.none)}</span>
          )}
          <span className="basis-full text-[11px] text-gray-500">{hasTemplate ? t.stamps_bulk_hint : t.stamps_bulk_need_template}</span>
        </div>
      )}
      {data && data.items.length === 0 && <p className="text-gray-500 text-sm">{t.stamps_empty}</p>}
      <ul className="grid grid-cols-2 sm:grid-cols-4 gap-2">
        {data?.items.map((it) => {
          const src = imageUrl(it.photo, API_BASE, it.updated_at);
          const f = it.stamp_quad ? undefined : found[it.card_id];
          const prop = f && "quad" in f ? f : null;
          return (
            <li key={it.card_id} className="rounded bg-gray-800/60 p-2 text-xs" data-testid="stamp-item">
              <div className="relative aspect-[63/88] overflow-hidden rounded bg-gray-700">
                {/* eslint-disable-next-line @next/next/no-img-element */}
                {src && <img src={src} alt="" loading="lazy" className="h-full w-full object-cover" />}
                {it.stamp_quad && (
                  <svg className="absolute inset-0 h-full w-full" viewBox="0 0 1 1" preserveAspectRatio="none">
                    <polygon points={it.stamp_quad.map(([x, y]) => `${x},${y}`).join(" ")}
                      fill="rgba(239,68,68,0.15)" stroke="#ef4444" strokeWidth={2} vectorEffect="non-scaling-stroke" />
                  </svg>
                )}
                {prop && (
                  <svg className="absolute inset-0 h-full w-full" viewBox="0 0 1 1" preserveAspectRatio="none" data-testid="stamp-item-found">
                    <polygon points={prop.quad.map(([x, y]) => `${x},${y}`).join(" ")}
                      fill={prop.state === "sure" ? "rgba(34,197,94,0.18)" : "rgba(245,158,11,0.15)"}
                      stroke={prop.state === "sure" ? "#22c55e" : "#f59e0b"} strokeWidth={2}
                      strokeDasharray={prop.state === "sure" ? undefined : "4 3"} vectorEffect="non-scaling-stroke" />
                  </svg>
                )}
              </div>
              <div className="mt-1 truncate text-white">{nameOf(it)}</div>
              <div className="truncate text-gray-500">{[it.set, it.number].filter(Boolean).join(" · ")}</div>
              {!it.own && it.username && <div className="mt-1 truncate text-gray-500">{t.weak_other_user(it.username)}</div>}
              {f && f.state !== "skipped" && (
                <div data-testid="stamp-item-status" className={`mt-1 truncate ${f.state === "sure" ? "text-green-400"
                  : f.state === "maybe" ? "text-amber-300" : f.state === "error" ? "text-red-400" : "text-gray-500"}`}>
                  {f.state === "sure" ? t.stamps_found_sure(f.pct) : f.state === "maybe" ? t.stamps_found_maybe(f.pct)
                    : f.state === "error" ? t.stamps_found_error : t.stamps_found_none}
                </div>
              )}
              {prop && (
                <div className="mt-1 flex gap-1">
                  <button type="button" onClick={() => void keepOne(it)} disabled={saving || !!bulk} data-testid="stamp-item-keep"
                    className="flex-1 rounded bg-green-700 px-2 py-1 text-white hover:bg-green-600 disabled:opacity-50">✓ {t.stamps_keep}</button>
                  <button type="button" onClick={() => setOne(it.card_id, { state: "skipped" })} disabled={saving} data-testid="stamp-item-discard"
                    className="flex-1 rounded bg-gray-700 px-2 py-1 text-white hover:bg-gray-600 disabled:opacity-50">✕ {t.stamps_discard}</button>
                </div>
              )}
              {/* Admin-Werkzeug: auch Karten anderer Konten markieren (Trainingsdaten) */}
              <button type="button" onClick={() => setEditing(it)} data-testid="stamp-item-mark"
                className="mt-1 w-full rounded bg-pokemon-blue px-2 py-1 text-white hover:opacity-90">
                🎯 {it.stamp_quad ? t.stamp_marked : t.stamp_mark}
              </button>
              {it.own && (
                <Link href={`/cards/${it.card_id}`} className="mt-1 block text-center text-gray-400 hover:text-white">{t.weak_open_card} →</Link>
              )}
            </li>
          );
        })}
      </ul>
      {editing && (
        <StampEditor cardId={editing.card_id} photo={editing.photo} version={editing.updated_at}
          initialQuad={editing.stamp_quad} proposal={(() => { const e = found[editing.card_id]; return e && "quad" in e ? e.quad : null; })()}
          onClose={() => setEditing(null)}
          onSaved={() => { setOne(editing.card_id, null); load(); }} />
      )}
    </div>
  );
}
