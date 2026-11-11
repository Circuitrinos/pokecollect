"use client";
import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import toast from "react-hot-toast";
import { API_BASE, ScanQualityOverview, scanQualityApi } from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import { imageUrl } from "@/lib/utils";

// Admin: gespeicherte Scan-Fotos, die schlecht zu ihrer Karte passen
// (backend/app/services/scan_quality.py) — zum Korrigieren auf der Kartenseite.
type Tab = "low" | "no_reference" | "dismissed";

export default function WeakScans() {
  const { t, lang } = useI18n();
  const [tab, setTab] = useState<Tab>("low");
  const [data, setData] = useState<ScanQualityOverview | null>(null);

  const load = useCallback(() => {
    scanQualityApi.overview(tab).then((r) => setData(r.data)).catch(() => {});
  }, [tab]);
  useEffect(() => { load(); }, [load]);
  // Während der Prüfung läuft: Stand nachladen
  const running = !!data?.job.running;
  useEffect(() => {
    if (!running) return;
    const h = window.setInterval(load, 2000);
    return () => window.clearInterval(h);
  }, [running, load]);

  const run = async (full: boolean) => {
    try {
      await scanQualityApi.run(full);
      load();
    } catch {
      toast.error(t.form_save_error);
    }
  };
  const dismiss = async (id: number) => {
    try {
      await scanQualityApi.dismiss(id);
      setData((d) => d && { ...d, items: d.items.filter((i) => i.card_id !== id) });
    } catch {
      toast.error(t.form_save_error);
    }
  };

  const c = data?.counts;
  const checked = c ? c.ok + c.low + c.no_reference + c.error + c.dismissed : 0;
  return (
    <div className="space-y-3" data-testid="weak-scans">
      <p className="text-gray-400 text-xs">{t.weak_hint}</p>
      {c && <p className="text-gray-300 text-sm">{t.weak_counts(checked, c.photos, c.low, c.no_reference, c.unchecked)}</p>}
      <div className="flex flex-wrap items-center gap-2">
        <button type="button" onClick={() => void run(false)} disabled={running}
          className="bg-blue-700 text-white text-sm px-3 py-1.5 rounded hover:bg-blue-600 disabled:opacity-50">
          {t.weak_run}
        </button>
        <button type="button" onClick={() => void run(true)} disabled={running}
          className="bg-gray-700 text-white text-sm px-3 py-1.5 rounded hover:bg-gray-600 disabled:opacity-50">
          {t.weak_run_all}
        </button>
        {running && data && <span className="text-xs text-amber-300">{t.weak_running(data.job.done, data.job.total)}</span>}
      </div>
      {!running && data?.job.error && <p className="text-xs text-red-400">{t.weak_error(data.job.error)}</p>}

      <div className="flex gap-1 rounded-lg bg-gray-800/70 p-1 text-xs" role="tablist">
        {([["low", t.weak_tab_low], ["no_reference", t.weak_tab_noref], ["dismissed", t.weak_tab_dismissed]] as const).map(([k, label]) => (
          <button key={k} type="button" role="tab" aria-selected={tab === k} onClick={() => setTab(k)}
            className={`flex-1 rounded-md px-2 py-1 ${tab === k ? "bg-pokemon-blue text-white" : "text-gray-300 hover:bg-gray-700"}`}>
            {label}{c ? ` (${k === "low" ? c.low : k === "no_reference" ? c.no_reference : c.dismissed})` : ""}
          </button>
        ))}
      </div>

      {data && data.items.length === 0 && <p className="text-gray-500 text-sm">{t.weak_empty}</p>}
      <ul className="space-y-2">
        {data?.items.map((it) => {
          const src = imageUrl(it.photo, API_BASE, it.checked_at);
          const name = (lang === "EN" && it.english_name) || it.name;
          return (
            <li key={it.card_id} data-testid="weak-item" className="flex gap-3 rounded bg-gray-800/60 p-2">
              {/* eslint-disable-next-line @next/next/no-img-element */}
              {src ? <img src={src} alt="" loading="lazy" className="h-20 w-14 shrink-0 rounded object-cover" />
                : <div className="h-20 w-14 shrink-0 rounded bg-gray-700" />}
              <div className="min-w-0 flex-1 text-xs">
                <div className="truncate text-sm text-white">{name}</div>
                <div className="truncate text-gray-400">{[it.set, it.number, it.tcgdex_card_id].filter(Boolean).join(" · ")}</div>
                <div className="text-amber-300">{t.weak_stats(it.inliers, it.corner_err)}</div>
                {!it.own && it.username && <div className="text-gray-500">{t.weak_other_user(it.username)}</div>}
                <div className="mt-1 flex flex-wrap gap-2">
                  {it.own && it.has_original && (
                    <Link href={`/cards/${it.card_id}?fixcrop=1`} className="rounded bg-pokemon-blue px-2 py-0.5 text-white hover:opacity-90">
                      ✥ {t.weak_fix}
                    </Link>
                  )}
                  {it.own && !it.has_original && (
                    <Link href={`/cards/${it.card_id}`} className="rounded bg-gray-700 px-2 py-0.5 text-gray-200 hover:text-white">
                      {t.weak_no_original} →
                    </Link>
                  )}
                  {tab !== "dismissed" && (
                    <button type="button" onClick={() => void dismiss(it.card_id)}
                      className="rounded px-2 py-0.5 text-gray-400 hover:bg-gray-700 hover:text-white">✓ {t.weak_dismiss}</button>
                  )}
                </div>
              </div>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
