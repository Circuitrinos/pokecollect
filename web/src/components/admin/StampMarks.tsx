"use client";
import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import toast from "react-hot-toast";
import { API_BASE, StampItem, StampOverview, StampTemplate, scanQualityApi } from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import { imageUrl } from "@/lib/utils";
import StampEditor from "@/components/StampEditor";

// Admin: Prize-Pack-Fotos, deren Play!-Stempel noch nicht markiert ist — der
// schnelle Weg, Trainingsdaten für einen Stempel-Detektor zu sammeln.
export default function StampMarks() {
  const { t, lang } = useI18n();
  const [marked, setMarked] = useState(false);
  const [data, setData] = useState<StampOverview | null>(null);
  const [editing, setEditing] = useState<StampItem | null>(null);
  const [template, setTemplate] = useState<StampTemplate | null>(null);
  const [learning, setLearning] = useState(false);

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

  const nameOf = (it: StampItem) => (lang === "EN" && it.english_name) || it.name;
  const c = data?.counts;
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
      {data && data.items.length === 0 && <p className="text-gray-500 text-sm">{t.stamps_empty}</p>}
      <ul className="grid grid-cols-2 sm:grid-cols-4 gap-2">
        {data?.items.map((it) => {
          const src = imageUrl(it.photo, API_BASE, it.updated_at);
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
              </div>
              <div className="mt-1 truncate text-white">{nameOf(it)}</div>
              <div className="truncate text-gray-500">{[it.set, it.number].filter(Boolean).join(" · ")}</div>
              {it.own ? (
                <button type="button" onClick={() => setEditing(it)} data-testid="stamp-item-mark"
                  className="mt-1 w-full rounded bg-pokemon-blue px-2 py-1 text-white hover:opacity-90">
                  🎯 {it.stamp_quad ? t.stamp_marked : t.stamp_mark}
                </button>
              ) : (
                <div className="mt-1 text-gray-500">{it.username ? t.weak_other_user(it.username) : ""}</div>
              )}
              {it.own && (
                <Link href={`/cards/${it.card_id}`} className="mt-1 block text-center text-gray-400 hover:text-white">{t.weak_open_card} →</Link>
              )}
            </li>
          );
        })}
      </ul>
      {editing && (
        <StampEditor cardId={editing.card_id} photo={editing.photo} version={editing.updated_at}
          initialQuad={editing.stamp_quad} onClose={() => setEditing(null)}
          onSaved={() => load()} />
      )}
    </div>
  );
}
