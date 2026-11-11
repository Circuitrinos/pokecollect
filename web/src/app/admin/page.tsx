"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import toast from "react-hot-toast";
import { scanApi, catalogApi, dataApi, usersApi, AppSettingsUpdate, ScanDatasetSummary, ScanUsage, ScanModelSuggestion, UserRow } from "@/lib/api";
import { useMe } from "@/lib/useMe";
import { useI18n } from "@/lib/i18n";
import { apiErrorText } from "@/lib/uiLang";
import { MARK_LETTERS } from "@/lib/regulation";
import { Field, INPUT, Section, Toggle, triggerBlobDownload, useSettingsEditor } from "@/components/settings/SettingsUI";

// Secret-Eingabefelder: lokaler State, getrennt von den (maskierten) Settings.
// Leer = gespeicherten Wert behalten; nur ausgefüllte Felder gehen ins PUT.
const EMPTY_SECRETS = {
  cardmarket_app_token: "",
  cardmarket_app_secret: "",
  cardmarket_access_token: "",
  cardmarket_access_secret: "",
  gemini_api_key: "",
  openai_api_key: "",
  openrouter_api_key: "",
};
type SecretKey = keyof typeof EMPTY_SECRETS;

// Keys, die im Scan-Abschnitt gespeichert werden (Issue #57) – der
// Cardmarket-Speicherknopf lässt sie deshalb aus.
const SCAN_SECRET_KEYS: SecretKey[] = ["gemini_api_key", "openai_api_key", "openrouter_api_key"];

/** Admin-Bereich: Einstellungen und Wartung für den ganzen Server (alle Konten). */
export default function AdminPage() {
  const { t } = useI18n();
  const me = useMe();
  const isAdmin = me?.is_admin ?? false;
  const { s, set, save: saveSettings, saving } = useSettingsEditor();
  const [users, setUsers] = useState<UserRow[] | null>(null);
  const [secrets, setSecrets] = useState({ ...EMPTY_SECRETS });
  const [usage, setUsage] = useState<ScanUsage | null>(null);
  const [syncingCatalog, setSyncingCatalog] = useState(false);
  const [backingUp, setBackingUp] = useState(false);
  const [exportingScans, setExportingScans] = useState(false);
  const [restoring, setRestoring] = useState(false);
  const [restoreFile, setRestoreFile] = useState<File | null>(null);
  const [restoreAck, setRestoreAck] = useState(false);
  const [scanSummary, setScanSummary] = useState<ScanDatasetSummary | null>(null);
  // Scan-Stufe B (Issue #57): bild-fähige Modell-Vorschläge für die Combobox.
  const [modelList, setModelList] = useState<ScanModelSuggestion[]>([]);
  const [modelSource, setModelSource] = useState<"live" | "curated" | null>(null);
  // Lokaler Bild-Cache (#43): Katalog-Kennzahlen für Status + Größenschätzung.
  const [catalogMeta, setCatalogMeta] = useState<{ total: number; cached_images: number } | null>(null);
  const [regStatus, setRegStatus] = useState<{ min_mark: string; pending_owned: number } | null>(null);
  const [regLoading, setRegLoading] = useState(false);
  // Nachholen läuft im Hintergrund: Stand alle 5 s neu holen, bis nichts mehr
  // offen ist (höchstens 5 Minuten).
  const loadRegulation = async () => {
    try {
      await catalogApi.loadRegulation();
      toast.success(t.settings_reg_started);
    } catch {
      toast.error(t.form_save_error);
      return;
    }
    setRegLoading(true);
    for (let i = 0; i < 60; i++) {
      await new Promise((r) => setTimeout(r, 5000));
      try {
        const r = await catalogApi.regulation();
        setRegStatus(r.data);
        if (r.data.pending_owned === 0) break;
      } catch { /* weiter versuchen */ }
    }
    setRegLoading(false);
  };

  useEffect(() => {
    if (!isAdmin) return;
    scanApi.usage().then((r) => setUsage(r.data)).catch(() => {});
    catalogApi.meta().then((r) => setCatalogMeta(r.data)).catch(() => {});
    catalogApi.regulation().then((r) => setRegStatus(r.data)).catch(() => {});
    usersApi.list().then((r) => setUsers(r.data)).catch(() => setUsers(null));
    dataApi.scanDatasetSummary().then((r) => setScanSummary(r.data)).catch(() => {});
  }, [isAdmin]);

  // Modell-Vorschläge laden, sobald ein Cloud-Provider aktiv ist / gewechselt wird.
  const provider = s?.scan_reader_provider;
  useEffect(() => {
    if (!provider || provider === "ocr" || provider === "local") { setModelList([]); setModelSource(null); return; }
    let alive = true;
    scanApi.models(provider)
      .then((r) => { if (alive) { setModelList(r.data.models); setModelSource(r.data.source); } })
      .catch(() => { if (alive) { setModelList([]); setModelSource(null); } });
    return () => { alive = false; };
  }, [provider]);

  // Nach dem Speichern Passwortfelder leeren — die Antwort ist maskiert, der
  // Klartext bleibt nicht im Formular stehen (Issue #1).
  const save = async (patch: AppSettingsUpdate, clearSecrets: SecretKey[] = []) => {
    const ok = await saveSettings(patch);
    if (ok && clearSecrets.length) {
      setSecrets((prev) => {
        const next = { ...prev };
        clearSecrets.forEach((k) => { next[k] = ""; });
        return next;
      });
    }
  };

  const setSecret = (key: SecretKey, value: string) =>
    setSecrets((prev) => ({ ...prev, [key]: value }));

  // Placeholder für Secret-Felder: zeigt den maskierten Bestand ("gesetzt: •••• XXXX")
  const secretPlaceholder = (isSet: boolean, masked: string) =>
    isSet ? t.settings_secret_set_placeholder(masked) : undefined;

  // Scan-Stufe B (Issue #57): Provider + nur die Felder des aktiven Providers
  // speichern; Key nur mitschicken, wenn eingegeben (leer = Bestand behalten).
  const saveScan = () => {
    if (!s) return;
    const patch: AppSettingsUpdate = { scan_reader_provider: s.scan_reader_provider };
    const clear: SecretKey[] = [];
    const withKey = (key: SecretKey) => {
      if (secrets[key] !== "") { patch[key] = secrets[key]; clear.push(key); }
    };
    if (s.scan_reader_provider === "gemini") {
      patch.gemini_model = s.gemini_model;
      patch.gemini_daily_limit = s.gemini_daily_limit;
      withKey("gemini_api_key");
    } else if (s.scan_reader_provider === "openai") {
      patch.openai_model = s.openai_model;
      withKey("openai_api_key");
    } else if (s.scan_reader_provider === "openrouter") {
      patch.openrouter_model = s.openrouter_model;
      withKey("openrouter_api_key");
    }
    save(patch, clear);
  };

  const handleCatalogSync = async () => {
    setSyncingCatalog(true);
    try {
      await catalogApi.sync();
      toast.success(t.settings_catalog_sync_started);
    } catch {
      toast.error(t.settings_catalog_sync_error);
    } finally {
      setSyncingCatalog(false);
    }
  };

  const handleBackup = async () => {
    setBackingUp(true);
    try {
      const r = await dataApi.backup();
      triggerBlobDownload(r.data, r.headers["content-disposition"], `pokecollect-backup-${new Date().toISOString().slice(0, 10)}.zip`);
    } catch {
      toast.error(t.settings_backup_error);
    } finally {
      setBackingUp(false);
    }
  };

  const handleScanDataset = async () => {
    setExportingScans(true);
    try {
      const r = await dataApi.scanDataset();
      triggerBlobDownload(r.data, r.headers["content-disposition"], `pokecollect-scans-${new Date().toISOString().slice(0, 10)}.zip`);
    } catch {
      toast.error(t.settings_backup_error);
    } finally {
      setExportingScans(false);
    }
  };

  const handleRestore = async () => {
    if (!restoreFile || !restoreAck) return;
    if (!confirm(t.settings_restore_confirm)) return;
    setRestoring(true);
    try {
      await dataApi.restore(restoreFile);
      toast.success(t.settings_restore_success);
      // Harte Neuladung: alle Caches/Listen spiegeln danach den Backup-Stand.
      setTimeout(() => window.location.reload(), 800);
    } catch (e: unknown) {
      const msg = apiErrorText(e);
      toast.error(msg ?? t.settings_restore_error);
      setRestoring(false);
    }
  };

  const patchUser = async (id: number, patch: { is_admin?: boolean; is_active?: boolean }) => {
    try {
      const r = await usersApi.update(id, patch);
      setUsers(r.data);
    } catch (e: unknown) {
      const msg = apiErrorText(e);
      toast.error(msg ?? t.users_update_error);
    }
  };

  if (me && !isAdmin) {
    return (
      <div className="max-w-2xl mx-auto space-y-4" data-testid="admin-denied">
        <h1 className="text-2xl font-bold text-white">{t.admin_title}</h1>
        <p className="text-gray-400 text-sm">{t.admin_only}</p>
        <Link href="/settings" className="text-blue-400 text-sm hover:underline">← {t.admin_back}</Link>
      </div>
    );
  }
  if (!s || !me) return <div className="text-gray-500 p-8">{t.detail_loading}</div>;

  return (
    <div className="max-w-2xl mx-auto space-y-6">
      <div className="space-y-1">
        <div className="flex items-center justify-between gap-3">
          <h1 className="text-2xl font-bold text-white">🛡️ {t.admin_title}</h1>
          <Link href="/settings" className="text-gray-400 text-sm hover:text-white whitespace-nowrap">← {t.admin_back}</Link>
        </div>
        <p className="text-gray-500 text-xs">{t.admin_intro}</p>
      </div>

      {/* Preise: Zeitplan und Quelle für alle Konten */}
      <Section title={`💰 ${t.settings_section_prices}`}>
        <Field label={t.settings_price_update} hint={t.settings_price_update_hint}>
          <div className="flex items-center gap-3 mt-1">
            <Toggle checked={s.price_update_enabled} onChange={(v) => { set("price_update_enabled", v); save({ price_update_enabled: v }); }} />
            <span className="text-sm text-gray-300">{s.price_update_enabled ? t.settings_enabled : t.settings_disabled}</span>
          </div>
        </Field>
        <Field label={t.settings_price_update_hour} hint={t.settings_price_update_hour_hint}>
          <input type="number" min={0} max={23} value={s.price_update_hour} onChange={(e) => set("price_update_hour", Number(e.target.value))} className={INPUT} style={{ width: "80px" }} />
        </Field>
        <Field label={t.settings_price_source}>
          {/* Alt-Wert "current" (früheres UI) zählt als "daily" */}
          <select value={s.price_source === "current" ? "daily" : s.price_source} onChange={(e) => set("price_source", e.target.value)} className={INPUT} style={{ width: "auto" }}>
            <option value="30d_avg">{t.settings_price_source_avg}</option>
            <option value="daily">{t.settings_price_source_current}</option>
          </select>
        </Field>
        <div className="pt-1">
          <button type="button" onClick={() => save({ price_update_hour: s.price_update_hour, price_source: s.price_source === "current" ? "daily" : s.price_source })} disabled={saving} className="bg-blue-700 text-white text-sm px-4 py-1.5 rounded hover:bg-blue-600 disabled:opacity-50">
            {t.form_save}
          </button>
        </div>
      </Section>

      <Section title={`🔑 ${t.settings_section_api_keys}`}>
        <p className="text-gray-500 text-xs">{t.settings_api_keys_hint}</p>
        <p className="text-gray-600 text-xs">{t.settings_secret_keep_hint}</p>
        <div className="space-y-3">
          <p className="text-gray-400 text-xs font-medium pt-1">Cardmarket (OAuth 1.0a)</p>
          <Field label="App Token">
            <input type="password" value={secrets.cardmarket_app_token} onChange={(e) => setSecret("cardmarket_app_token", e.target.value)} placeholder={secretPlaceholder(s.cardmarket_app_token_set, s.cardmarket_app_token_masked)} className={INPUT} autoComplete="off" />
          </Field>
          <Field label="App Secret">
            <input type="password" value={secrets.cardmarket_app_secret} onChange={(e) => setSecret("cardmarket_app_secret", e.target.value)} placeholder={secretPlaceholder(s.cardmarket_app_secret_set, s.cardmarket_app_secret_masked)} className={INPUT} autoComplete="off" />
          </Field>
          <Field label="Access Token">
            <input type="password" value={secrets.cardmarket_access_token} onChange={(e) => setSecret("cardmarket_access_token", e.target.value)} placeholder={secretPlaceholder(s.cardmarket_access_token_set, s.cardmarket_access_token_masked)} className={INPUT} autoComplete="off" />
          </Field>
          <Field label="Access Secret">
            <input type="password" value={secrets.cardmarket_access_secret} onChange={(e) => setSecret("cardmarket_access_secret", e.target.value)} placeholder={secretPlaceholder(s.cardmarket_access_secret_set, s.cardmarket_access_secret_masked)} className={INPUT} autoComplete="off" />
          </Field>
        </div>
        <div className="pt-1">
          <button
            type="button"
            onClick={() => {
              // Nur ausgefüllte Felder PUTen — leer heißt: Bestand behalten.
              const entered = (Object.keys(EMPTY_SECRETS) as SecretKey[])
                .filter((k) => !SCAN_SECRET_KEYS.includes(k) && secrets[k] !== "");
              if (!entered.length) return;
              const patch: AppSettingsUpdate = {};
              entered.forEach((k) => { patch[k] = secrets[k]; });
              save(patch, entered);
            }}
            disabled={saving || (Object.keys(EMPTY_SECRETS) as SecretKey[]).every((k) => SCAN_SECRET_KEYS.includes(k) || secrets[k] === "")}
            className="bg-blue-700 text-white text-sm px-4 py-1.5 rounded hover:bg-blue-600 disabled:opacity-50"
          >
            {t.settings_save_api_keys}
          </button>
        </div>
      </Section>

      <Section title={`📷 ${t.settings_section_scan}`}>
        <Field label={t.settings_scan_provider} hint={t.settings_scan_provider_hint}>
          <select value={s.scan_reader_provider} onChange={(e) => set("scan_reader_provider", e.target.value)} className={INPUT}>
            <option value="gemini">{t.settings_scan_provider_gemini}</option>
            <option value="openai">{t.settings_scan_provider_openai}</option>
            <option value="openrouter">{t.settings_scan_provider_openrouter}</option>
            <option value="local">{t.settings_scan_provider_local}</option>
            <option value="ocr">{t.settings_scan_provider_ocr}</option>
          </select>
        </Field>

        {s.scan_reader_provider === "gemini" && (
          <>
            <p className="text-gray-400 text-xs">
              {t.settings_gemini_hint}{" "}
              <a href="https://aistudio.google.com/apikey" target="_blank" rel="noreferrer" className="text-blue-400 hover:underline">aistudio.google.com/apikey</a>
            </p>
            <Field label="Gemini API Key" hint={t.settings_secret_keep_hint}>
              <input type="password" value={secrets.gemini_api_key} onChange={(e) => setSecret("gemini_api_key", e.target.value)} className={INPUT} autoComplete="off" placeholder={secretPlaceholder(s.gemini_api_key_set, s.gemini_api_key_masked) ?? "AIza…"} />
            </Field>
            <Field label={t.settings_gemini_model} hint={t.settings_gemini_model_hint}>
              <input type="text" list="scan-model-suggestions" value={s.gemini_model} onChange={(e) => set("gemini_model", e.target.value)} className={INPUT} placeholder="gemini-2.5-flash" />
            </Field>
            <Field label={t.settings_gemini_daily_limit} hint={t.settings_gemini_daily_limit_hint}>
              <input type="number" min={0} value={s.gemini_daily_limit} onChange={(e) => set("gemini_daily_limit", Number(e.target.value))} className={INPUT} style={{ width: "120px" }} />
            </Field>
          </>
        )}

        {s.scan_reader_provider === "openai" && (
          <>
            <p className="text-gray-400 text-xs">
              <a href="https://platform.openai.com/api-keys" target="_blank" rel="noreferrer" className="text-blue-400 hover:underline">platform.openai.com/api-keys</a>
            </p>
            <Field label="OpenAI API Key" hint={t.settings_secret_keep_hint}>
              <input type="password" value={secrets.openai_api_key} onChange={(e) => setSecret("openai_api_key", e.target.value)} className={INPUT} autoComplete="off" placeholder={secretPlaceholder(s.openai_api_key_set, s.openai_api_key_masked) ?? "sk-…"} />
            </Field>
            <Field label={t.settings_openai_model} hint={t.settings_openai_model_hint}>
              <input type="text" list="scan-model-suggestions" value={s.openai_model} onChange={(e) => set("openai_model", e.target.value)} className={INPUT} placeholder="gpt-4o-mini" />
            </Field>
          </>
        )}

        {s.scan_reader_provider === "openrouter" && (
          <>
            <p className="text-gray-400 text-xs">
              <a href="https://openrouter.ai/keys" target="_blank" rel="noreferrer" className="text-blue-400 hover:underline">openrouter.ai/keys</a>
            </p>
            <Field label="OpenRouter API Key" hint={t.settings_secret_keep_hint}>
              <input type="password" value={secrets.openrouter_api_key} onChange={(e) => setSecret("openrouter_api_key", e.target.value)} className={INPUT} autoComplete="off" placeholder={secretPlaceholder(s.openrouter_api_key_set, s.openrouter_api_key_masked) ?? "sk-or-…"} />
            </Field>
            <Field label={t.settings_openrouter_model} hint={t.settings_openrouter_model_hint}>
              <input type="text" list="scan-model-suggestions" value={s.openrouter_model} onChange={(e) => set("openrouter_model", e.target.value)} className={INPUT} placeholder="google/gemini-2.5-flash" />
            </Field>
          </>
        )}

        {/* Combobox-Vorschläge (bild-fähige Modelle) — Feld bleibt frei tippbar */}
        {s.scan_reader_provider !== "ocr" && s.scan_reader_provider !== "local" && (
          <>
            <datalist id="scan-model-suggestions">
              {modelList.map((m) => <option key={m.id} value={m.id}>{m.name}</option>)}
            </datalist>
            {modelSource && modelList.length > 0 && (
              <p className="text-gray-600 text-xs">{t.settings_model_hint(modelList.length, modelSource === "live")}</p>
            )}
          </>
        )}

        <div className="pt-1">
          <button
            type="button"
            onClick={saveScan}
            disabled={saving}
            className="bg-blue-700 text-white text-sm px-4 py-1.5 rounded hover:bg-blue-600 disabled:opacity-50"
          >
            {t.settings_save_scan}
          </button>
        </div>
        {s.scan_reader_provider === "gemini" && usage && (
          <div className="mt-3 border-t border-gray-700 pt-3 text-sm">
            <p className="text-gray-400 text-xs mb-2">{t.settings_gemini_usage(usage.model)}</p>
            <div className="grid grid-cols-2 gap-3">
              <div className="bg-gray-900 rounded p-2">
                <div className="text-gray-500 text-xs">{t.settings_usage_today}</div>
                <div className="text-white">{usage.today.requests} / {usage.limits.rpd}</div>
                <div className="mt-1 h-1.5 bg-gray-700 rounded-full">
                  <div
                    className={`h-full rounded-full ${usage.today.requests >= usage.limits.rpd ? "bg-pokemon-red" : "bg-green-500"}`}
                    style={{ width: `${Math.min(100, (usage.today.requests / usage.limits.rpd) * 100)}%` }}
                  />
                </div>
                <div className="text-gray-500 text-xs mt-0.5">
                  {t.settings_usage_scans_left(Math.max(0, usage.limits.rpd - usage.today.requests))}
                </div>
                <div className="text-gray-400 text-xs mt-1">{t.settings_usage_tokens_today(usage.today.tokens.toLocaleString(t.date_locale))}</div>
              </div>
              <div className="bg-gray-900 rounded p-2">
                <div className="text-gray-500 text-xs">{t.settings_usage_per_scan}</div>
                <div className="text-white">{t.settings_usage_tokens_per_scan(usage.avg_tokens_per_scan.toLocaleString(t.date_locale))}</div>
                <div className="text-gray-400 text-xs mt-1">{t.settings_usage_rpm(usage.rpm_used.toLocaleString(t.date_locale), usage.limits.rpm.toLocaleString(t.date_locale))}</div>
                <div className="text-gray-400 text-xs">{t.settings_usage_tpm(usage.tpm_used.toLocaleString(t.date_locale), usage.limits.tpm.toLocaleString(t.date_locale))}</div>
                <div className="text-gray-500 text-xs mt-1">{t.settings_usage_total(usage.total.requests)}</div>
              </div>
            </div>
            <p className="text-gray-600 text-xs mt-2">
              {t.settings_usage_footnote}
            </p>
          </div>
        )}
      </Section>

      <Section title={`📚 ${t.settings_section_catalog}`}>
        <p className="text-gray-400 text-xs">{t.settings_catalog_hint}</p>

        {/* Lokaler Bild-Cache (#43) */}
        <Field label={t.settings_catalog_cache_level} hint={t.settings_catalog_cache_level_hint}>
          <select
            value={s.catalog_image_cache_level}
            onChange={(e) => set("catalog_image_cache_level", e.target.value)}
            className={INPUT}
            style={{ width: "auto" }}
          >
            <option value="urls">{t.settings_catalog_cache_urls}</option>
            <option value="owned">{t.settings_catalog_cache_owned}</option>
            <option value="all">
              {t.settings_catalog_cache_all((((catalogMeta?.total ?? 0) * 0.1) / 1000).toFixed(1))}
            </option>
          </select>
        </Field>
        {catalogMeta && (
          <p className="text-gray-600 text-xs">
            {/* #43-Nacharbeit: bei Stufe "owned" ist total (ganzer Katalog)
                nicht die Zielmenge -- "X von Y" waere irrefuehrend, dort nur
                die reine Zahl zeigen; bei "all" bleibt "X von Y". */}
            {s.catalog_image_cache_level === "all"
              ? t.settings_catalog_cache_status(
                  catalogMeta.cached_images.toLocaleString(t.date_locale),
                  catalogMeta.total.toLocaleString(t.date_locale),
                )
              : t.settings_catalog_cache_status_owned(
                  catalogMeta.cached_images.toLocaleString(t.date_locale),
                )}
          </p>
        )}
        <div className="pt-1">
          <button
            type="button"
            onClick={() => save({ catalog_image_cache_level: s.catalog_image_cache_level })}
            disabled={saving}
            className="bg-blue-700 text-white text-sm px-4 py-1.5 rounded hover:bg-blue-600 disabled:opacity-50"
          >
            {t.form_save}
          </button>
        </div>

        <p className="text-gray-600 text-xs">{t.settings_backfill_logs_hint} <code>docker logs pokecollect-api-1 -f</code></p>
        <div className="pt-3 border-t border-gray-700">
          <button type="button" onClick={handleCatalogSync} disabled={syncingCatalog} className="bg-blue-700 text-white text-sm px-4 py-1.5 rounded hover:bg-blue-600 disabled:opacity-50">
            {t.settings_catalog_sync_now}
          </button>
        </div>
      </Section>

      {/* Turnier-Legalität: niedrigstes Standard-Zeichen + Nachladen */}
      <Section title={`⚖️ ${t.settings_reg_title}`}>
        <Field label={t.settings_reg_min} hint={t.settings_reg_hint}>
          <select
            aria-label={t.settings_reg_min}
            value={s.standard_min_mark ?? "H"}
            onChange={(e) => { set("standard_min_mark", e.target.value); save({ standard_min_mark: e.target.value }); }}
            className={INPUT}
            style={{ width: "auto" }}
          >
            {MARK_LETTERS.slice(3, 16).map((m) => <option key={m} value={m}>{m}</option>)}
          </select>
        </Field>
        {regStatus && <p className="text-gray-600 text-xs">{t.settings_reg_pending(regStatus.pending_owned)}</p>}
        <div className="pt-1">
          <button
            type="button"
            onClick={() => void loadRegulation()}
            disabled={regLoading}
            className="bg-blue-700 text-white text-sm px-4 py-1.5 rounded hover:bg-blue-600 disabled:opacity-60"
          >
            {regLoading ? t.settings_reg_loading : t.settings_reg_load}
          </button>
        </div>
      </Section>

      {/* Sicherung: komplettes Backup, Scan-Datensatz, Wiederherstellen */}
      <Section title={`💾 ${t.admin_section_backup}`}>
        <p className="text-gray-400 text-xs">{t.admin_backup_hint}</p>
        <div className="flex gap-3 pt-1 flex-wrap">
          <button
            type="button"
            onClick={handleBackup}
            disabled={backingUp}
            className="bg-blue-700 text-white text-sm px-4 py-1.5 rounded hover:bg-blue-600 disabled:opacity-50"
          >
            {backingUp ? t.settings_data_busy : t.settings_backup_download}
          </button>
          <button
            type="button"
            onClick={handleScanDataset}
            disabled={exportingScans}
            title={t.settings_scan_dataset_hint}
            className="bg-gray-700 text-white text-sm px-4 py-1.5 rounded hover:bg-gray-600 disabled:opacity-50"
          >
            {exportingScans ? t.settings_data_busy : t.settings_scan_dataset}
          </button>
        </div>
        {scanSummary && (
          <div className="space-y-2 text-xs" data-testid="scan-export-summary">
            <p className="text-gray-400">{t.admin_export_ok(scanSummary.exportable)}</p>
            {scanSummary.not_exportable > 0 && (
              <div className="rounded border border-amber-700/60 bg-amber-900/20 px-3 py-2 text-amber-200 space-y-1">
                <p className="font-medium">⚠ {t.admin_export_warn(scanSummary.not_exportable)}</p>
                <ul className="list-disc pl-4 text-amber-100/90">
                  {scanSummary.no_card > 0 && <li>{t.admin_export_no_card(scanSummary.no_card)}</li>}
                  {scanSummary.no_original > 0 && <li>{t.admin_export_no_original(scanSummary.no_original)}</li>}
                </ul>
                <p className="text-amber-100/80">
                  {scanSummary.users.filter((u) => u.not_exportable > 0)
                    .map((u) => `${u.display_name || u.username}: ${u.not_exportable}`).join(" · ")}
                </p>
                {scanSummary.users.some((u) => u.username === me.username && u.not_exportable > 0) && (
                  <Link href="/owned?bild_status=kein_export" className="inline-block text-blue-300 hover:underline">{t.admin_export_find}</Link>
                )}
              </div>
            )}
          </div>
        )}
        <div className="border-t border-gray-700 pt-3 space-y-3">
          <p className="text-white text-sm font-medium">{t.settings_restore_title}</p>
          <p className="text-gray-500 text-xs">{t.settings_restore_hint}</p>
          <input
            type="file"
            accept=".zip,application/zip"
            onChange={(e) => setRestoreFile(e.target.files?.[0] ?? null)}
            className="block w-full text-sm text-gray-300 file:mr-3 file:rounded file:border-0 file:bg-gray-700 file:px-3 file:py-1.5 file:text-sm file:text-white hover:file:bg-gray-600"
          />
          <label className="flex items-start gap-2 text-sm text-gray-300 cursor-pointer">
            <input
              type="checkbox"
              checked={restoreAck}
              onChange={(e) => setRestoreAck(e.target.checked)}
              className="mt-0.5"
            />
            <span>{t.settings_restore_ack}</span>
          </label>
          <button
            type="button"
            onClick={handleRestore}
            disabled={restoring || !restoreFile || !restoreAck}
            className="bg-pokemon-red text-white text-sm px-4 py-1.5 rounded hover:opacity-90 disabled:opacity-50"
          >
            {restoring ? t.settings_restore_running : t.settings_restore_button}
          </button>
        </div>
      </Section>

      {users && (
        <Section title={`👥 ${t.settings_section_users}`}>
          <p className="text-gray-400 text-xs">{t.settings_users_hint}</p>
          <ul className="divide-y divide-gray-700">
            {users.map((u) => {
              const self = u.id === me?.id;
              return (
                <li key={u.id} className="py-3 flex flex-wrap items-center gap-x-3 gap-y-2">
                  <div className="min-w-0 flex-1">
                    <div className="text-sm text-white truncate">
                      {u.display_name || u.username}
                      {u.display_name && <span className="text-gray-500"> · {u.username}</span>}
                      {self && <span className="text-gray-500"> ({t.users_you})</span>}
                      {u.is_admin && <span className="ml-2 text-[10px] uppercase tracking-wide bg-pokemon-yellow/20 text-pokemon-yellow rounded px-1.5 py-0.5">{t.account_admin_badge}</span>}
                      {!u.is_active && <span className="ml-2 text-[10px] uppercase tracking-wide bg-red-900/50 text-red-300 rounded px-1.5 py-0.5">{t.users_inactive}</span>}
                    </div>
                    <div className="text-xs text-gray-500">
                      {t.users_items(u.owned_cards, u.collections, u.sealed)}
                      {" · "}
                      {u.last_login_at ? t.users_last_login(new Date(u.last_login_at + "Z").toLocaleDateString(t.date_locale)) : t.users_never}
                    </div>
                  </div>
                  {!self && (
                    <div className="flex gap-2">
                      <button
                        type="button"
                        onClick={() => patchUser(u.id, { is_admin: !u.is_admin })}
                        className="bg-gray-700 text-white text-xs px-3 py-1 rounded hover:bg-gray-600"
                      >
                        {u.is_admin ? t.users_remove_admin : t.users_make_admin}
                      </button>
                      <button
                        type="button"
                        onClick={() => patchUser(u.id, { is_active: !u.is_active })}
                        className={`text-white text-xs px-3 py-1 rounded ${u.is_active ? "bg-red-900 hover:bg-red-800" : "bg-green-800 hover:bg-green-700"}`}
                      >
                        {u.is_active ? t.users_deactivate : t.users_activate}
                      </button>
                    </div>
                  )}
                </li>
              );
            })}
          </ul>
        </Section>
      )}
    </div>
  );
}
