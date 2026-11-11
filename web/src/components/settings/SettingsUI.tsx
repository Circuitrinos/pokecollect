"use client";
// Gemeinsame Bausteine der Einstellungs-Seiten (persönlich: /settings,
// Server: /admin) — Formularfelder, Abschnitte und das Laden/Speichern der
// Einstellungen.
import { useEffect, useState } from "react";
import toast from "react-hot-toast";
import { settingsApi, AppSettings, AppSettingsUpdate } from "@/lib/api";
import { refreshSettings } from "@/lib/useSettings";
import { useI18n } from "@/lib/i18n";

export const SECTION = "bg-pokemon-card rounded-lg p-5 space-y-4";
export const LABEL = "block text-gray-400 text-xs mb-1";
export const INPUT = "w-full bg-gray-900 border border-gray-700 rounded px-3 py-2 text-white text-sm focus:outline-none focus:border-blue-500";
export const TOGGLE_BASE = "relative inline-flex h-6 w-11 items-center rounded-full transition-colors cursor-pointer";

export function Toggle({ checked, onChange }: { checked: boolean; onChange: (v: boolean) => void }) {
  return (
    <button
      type="button"
      onClick={() => onChange(!checked)}
      className={`${TOGGLE_BASE} ${checked ? "bg-blue-600" : "bg-gray-600"}`}
    >
      <span className={`inline-block h-4 w-4 rounded-full bg-white shadow transition-transform ${checked ? "translate-x-6" : "translate-x-1"}`} />
    </button>
  );
}

export function Field({ label, children, hint }: { label: string; children: React.ReactNode; hint?: string }) {
  // Label umschließt das Feld → implizite Label-Input-Verknüpfung (A11y, #15)
  return (
    <div>
      <label className="block">
        <span className={LABEL}>{label}</span>
        {children}
      </label>
      {hint && <p className="text-gray-600 text-xs mt-1">{hint}</p>}
    </div>
  );
}

export function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className={SECTION}>
      <h2 className="text-white font-semibold text-sm border-b border-gray-700 pb-2">{title}</h2>
      {children}
    </div>
  );
}

/** Einstellungen laden, lokal ändern (set) und speichern (save → true bei Erfolg). */
export function useSettingsEditor() {
  const { t } = useI18n();
  const [s, setS] = useState<AppSettings | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    settingsApi.get().then((r) => setS(r.data)).catch(() => toast.error(t.settings_load_error));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const save = async (patch: AppSettingsUpdate): Promise<boolean> => {
    if (!s) return false;
    setSaving(true);
    try {
      const r = await settingsApi.update(patch);
      setS(r.data);
      // Geteilten Einstellungs-Cache invalidieren (useSettings, Issue #14)
      refreshSettings().catch(() => {});
      toast.success(t.settings_saved);
      return true;
    } catch {
      toast.error(t.form_save_error);
      return false;
    } finally {
      setSaving(false);
    }
  };

  const set = <K extends keyof AppSettings>(key: K, value: AppSettings[K]) => {
    setS((prev) => (prev ? { ...prev, [key]: value } : prev));
  };

  return { s, set, save, saving };
}

  // Downloads: authentifizierter axios-GET (Blob) → createObjectURL →
  // programmatischer a[download]-Klick. Ein nackter <a href> hätte keinen
  // Authorization-Header und liefe in 401 (Issue #17).
export function triggerBlobDownload(data: Blob, contentDisposition: unknown, fallbackName: string) {
    const m = String(contentDisposition ?? "").match(/filename="?([^";]+)"?/);
    const url = URL.createObjectURL(data);
    const a = document.createElement("a");
    a.href = url;
    a.download = m?.[1] ?? fallbackName;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
}
