"use client";
import { useEffect, useRef, useState } from "react";
import toast from "react-hot-toast";
import { FoilModel as FoilModelInfo, scanQualityApi } from "@/lib/api";
import { useI18n } from "@/lib/i18n";

// Admin: Reverse-Holo-Modell für den Scan hochladen (matcher/tools/foil_eval.py --save).
// Mit Modell fragt die Scan-Prüfung bei normalen Karten, die es als Reverse Holo gibt, „Reverse Holo?".
export default function FoilModel() {
  const { t } = useI18n();
  const [info, setInfo] = useState<FoilModelInfo | null>(null);
  const [busy, setBusy] = useState(false);
  const fileRef = useRef<HTMLInputElement | null>(null);
  useEffect(() => { scanQualityApi.foilModel().then((r) => setInfo(r.data)).catch(() => {}); }, []);

  const upload = async (f: File | undefined) => {
    if (!f) return;
    setBusy(true);
    try {
      const r = await scanQualityApi.uploadFoilModel(f);
      setInfo(r.data);
      toast.success(t.foil_model_saved);
    } catch (e) {
      const detail = (e as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
      toast.error(typeof detail === "string" ? detail : t.form_save_error);
    } finally {
      setBusy(false);
      if (fileRef.current) fileRef.current.value = "";
    }
  };
  const remove = async () => {
    if (!confirm(t.foil_model_remove_confirm)) return;
    const r = await scanQualityApi.deleteFoilModel().catch(() => null);
    if (r) setInfo(r.data);
  };

  return (
    <div className="flex flex-wrap items-center gap-3 rounded bg-gray-800/60 px-3 py-2" data-testid="foil-model">
      <span className="min-w-0 flex-1 text-sm text-gray-300">
        {info?.exists
          ? t.foil_model_status(Math.round((info.precision ?? 0) * 100), Math.round((info.recall ?? 0) * 100),
              info.trained_on?.["Reverse Holo"] ?? 0, info.trained_on?.["Normal"] ?? 0)
            + (info.features?.includes("code_corr") ? ` · ${t.foil_model_uses_code}` : ` · ${t.foil_model_no_code}`)
          : t.foil_model_none}
        <span className="block text-[11px] text-gray-500">{t.foil_model_hint}</span>
      </span>
      <input ref={fileRef} type="file" accept="application/json,.json" className="hidden"
        data-testid="foil-model-file" onChange={(e) => void upload(e.target.files?.[0])} />
      <button type="button" onClick={() => fileRef.current?.click()} disabled={busy}
        className="rounded bg-pokemon-blue px-3 py-1.5 text-sm text-white hover:opacity-90 disabled:opacity-50">
        {busy ? t.foil_model_uploading : t.foil_model_upload}
      </button>
      {info?.exists && (
        <button type="button" onClick={() => void remove()} className="text-xs text-gray-400 hover:text-red-300">
          {t.foil_model_remove}
        </button>
      )}
    </div>
  );
}
