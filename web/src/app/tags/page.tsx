"use client";
import { useCallback, useEffect, useState } from "react";
import toast from "react-hot-toast";
import { QrCode } from "lucide-react";
import { TagSettings, TagSheet, tagError, tagsApi } from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import { Field, INPUT, Section, triggerBlobDownload } from "@/components/settings/SettingsUI";

// Hüllen-Aufkleber (backend/app/services/sleeve_tags.py): blanko Bögen drucken,
// Drucker kalibrieren, gedruckte Bögen nachdrucken. Zugeordnet wird ein Aufkleber
// erst, wenn er klebt — Handykamera → /T/<Code> (app/T/[code]/page.tsx).
const BTN = "bg-blue-700 hover:bg-blue-600 text-white text-sm px-4 py-2 rounded disabled:opacity-50";
const BTN_2 = "bg-gray-700 hover:bg-gray-600 text-white text-sm px-3 py-1.5 rounded disabled:opacity-50";

export default function TagsPage() {
  const { t } = useI18n();
  const [saved, setSaved] = useState<TagSettings | null>(null);
  const [base, setBase] = useState("");
  const [dx, setDx] = useState("0");
  const [dy, setDy] = useState("0");
  const [count, setCount] = useState("1");
  const [start, setStart] = useState("1");
  const [sheets, setSheets] = useState<TagSheet[] | null>(null);
  const [busy, setBusy] = useState(false);

  const loadSheets = useCallback(() => {
    tagsApi.sheets().then((r) => setSheets(r.data)).catch(() => setSheets([]));
  }, []);
  useEffect(() => {
    tagsApi.settings().then((r) => {
      setSaved(r.data);
      // Noch keine Adresse: die, unter der PokéCollect gerade läuft, vorschlagen
      setBase(r.data.base_url || window.location.origin);
      setDx(String(r.data.offset_x_mm));
      setDy(String(r.data.offset_y_mm));
    }).catch(() => {});
    loadSheets();
  }, [loadSheets]);

  const perSheet = saved?.per_sheet ?? 144;
  const num = (v: string) => Number(v.replace(",", "."));
  const dirty = saved !== null && (base.trim().replace(/\/+$/, "") !== saved.base_url
    || num(dx) !== saved.offset_x_mm || num(dy) !== saved.offset_y_mm);

  const save = async (quiet = false): Promise<boolean> => {
    if (!Number.isFinite(num(dx)) || !Number.isFinite(num(dy))) return false;
    try {
      const r = await tagsApi.saveSettings({ base_url: base.trim(), offset_x_mm: num(dx), offset_y_mm: num(dy) });
      setSaved(r.data);
      setBase(r.data.base_url);
      setDx(String(r.data.offset_x_mm));
      setDy(String(r.data.offset_y_mm));
      if (!quiet) toast.success(t.tags_saved);
      return true;
    } catch (e) {
      toast.error(tagError(e).reason === "base_url" ? t.tags_bad_address : t.tags_error);
      return false;
    }
  };

  const download = async (ids: number[]) => {
    const r = await tagsApi.sheetsPdf(ids);
    triggerBlobDownload(r.data, r.headers["content-disposition"], "sleeve-tags.pdf");
  };

  const print = async () => {
    if (!base.trim()) { toast.error(t.tags_need_address); return; }
    setBusy(true);
    try {
      if (dirty && !(await save(true))) return;
      const n = Math.min(10, Math.max(1, Math.round(num(count)) || 1));
      const first = Math.min(perSheet, Math.max(1, Math.round(num(start)) || 1));
      const r = await tagsApi.newSheets(n, first - 1);
      await download(r.data.map((s) => s.id));
      setStart("1");
      loadSheets();
    } catch {
      toast.error(t.tags_error);
    } finally {
      setBusy(false);
    }
  };

  const testPage = async () => {
    setBusy(true);
    try {
      if (dirty && !(await save(true))) return;
      const r = await tagsApi.calibrationPdf();
      triggerBlobDownload(r.data, r.headers["content-disposition"], "sleeve-tags-calibration.pdf");
    } catch {
      toast.error(t.tags_error);
    } finally {
      setBusy(false);
    }
  };

  const discard = async (s: TagSheet) => {
    if (!window.confirm(t.tags_delete_confirm(s.nr))) return;
    try {
      await tagsApi.deleteSheet(s.id);
      loadSheets();
    } catch (e) {
      toast.error(tagError(e).reason === "linked" ? t.tags_delete_linked : t.tags_error);
    }
  };

  return (
    <div className="max-w-2xl mx-auto space-y-5">
      <div className="flex items-start gap-3">
        <QrCode className="w-7 h-7 text-pokemon-yellow shrink-0 mt-0.5" aria-hidden />
        <div>
          <h1 className="text-xl font-bold text-white">{t.tags_title}</h1>
          <p className="text-gray-400 text-sm mt-1">{t.tags_intro}</p>
        </div>
      </div>

      <Section title={t.tags_print}>
        <p className="text-gray-500 text-xs">{saved?.sheet_name}</p>
        <div className="grid grid-cols-2 gap-3">
          <Field label={t.tags_sheet_count}>
            <input type="number" min={1} max={10} value={count} onChange={(e) => setCount(e.target.value)} className={INPUT} />
          </Field>
          <Field label={t.tags_start_at}>
            <input type="number" min={1} max={perSheet} value={start} onChange={(e) => setStart(e.target.value)} className={INPUT} />
          </Field>
        </div>
        <p className="text-gray-600 text-xs -mt-2">{t.tags_start_hint(perSheet)}</p>
        <button type="button" onClick={print} disabled={busy || !saved} className={BTN}>
          {t.tags_print_button(Math.min(10, Math.max(1, Math.round(num(count)) || 1)))}
        </button>
        <p className="text-amber-300/90 text-xs">{t.tags_print_hint}</p>
      </Section>

      <Section title={t.tags_link_address}>
        <Field label="URL" hint={t.tags_link_hint}>
          <input value={base} onChange={(e) => setBase(e.target.value)} className={INPUT}
            placeholder="https://tcg.example.com" inputMode="url" spellCheck={false} />
        </Field>
        <button type="button" onClick={() => save()} disabled={busy || !dirty} className={BTN_2}>{t.tags_save}</button>
      </Section>

      <Section title={t.tags_calibrate}>
        <p className="text-gray-400 text-xs leading-relaxed">{t.tags_calibrate_hint}</p>
        <div className="grid grid-cols-2 gap-3">
          <Field label={t.tags_offset_x}>
            <input type="number" step={0.1} min={-10} max={10} value={dx} onChange={(e) => setDx(e.target.value)} className={INPUT} />
          </Field>
          <Field label={t.tags_offset_y}>
            <input type="number" step={0.1} min={-10} max={10} value={dy} onChange={(e) => setDy(e.target.value)} className={INPUT} />
          </Field>
        </div>
        <div className="flex flex-wrap gap-2">
          <button type="button" onClick={testPage} disabled={busy || !saved} className={BTN_2}>{t.tags_test_page}</button>
          <button type="button" onClick={() => save()} disabled={busy || !dirty} className={BTN_2}>{t.tags_save}</button>
        </div>
      </Section>

      <Section title={t.tags_sheets}>
        {sheets === null ? null : sheets.length === 0 ? (
          <p className="text-gray-500 text-sm">{t.tags_none}</p>
        ) : (
          <ul className="divide-y divide-gray-800">
            {sheets.map((s) => (
              <li key={s.id} className="py-2.5 flex items-center gap-3">
                <div className="flex-1 min-w-0">
                  <div className="text-white text-sm">{t.tags_sheet_line(s.nr, s.count)}</div>
                  <div className="flex items-center gap-2 mt-1">
                    <div className="h-1.5 w-28 bg-gray-800 rounded-full overflow-hidden">
                      <div className="h-full bg-green-500" style={{ width: `${(100 * s.linked) / Math.max(1, s.count)}%` }} />
                    </div>
                    <span className="text-gray-500 text-xs">{t.tags_sheet_use(s.linked, s.count)}</span>
                  </div>
                </div>
                <button type="button" onClick={() => download([s.id]).catch(() => toast.error(t.tags_error))}
                  disabled={busy} className={BTN_2}>{t.tags_reprint}</button>
                {s.linked === 0 && (
                  <button type="button" onClick={() => discard(s)} className="text-gray-500 hover:text-red-400 text-xs px-1">
                    {t.tags_delete_sheet}
                  </button>
                )}
              </li>
            ))}
          </ul>
        )}
      </Section>
    </div>
  );
}
