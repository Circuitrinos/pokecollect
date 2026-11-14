"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import toast from "react-hot-toast";
import { settingsApi, cardApi, pricesApi, dataApi, logout } from "@/lib/api";
import { useMe } from "@/lib/useMe";
import { APP_VERSION } from "@/lib/version";
import { debugEnabled, setDebug } from "@/lib/debugLog";
import { openWhatsNew } from "@/lib/whatsnew";
import { useI18n } from "@/lib/i18n";
import { apiErrorText } from "@/lib/uiLang";
import BottomNavSettings from "@/components/BottomNavSettings";
import SortDirectionButton from "@/components/SortDirectionButton";
import { Field, INPUT, Section, Toggle, triggerBlobDownload, useSettingsEditor } from "@/components/settings/SettingsUI";

/** Ein Werkzeug im Abschnitt „Sammlungs-Werkzeuge": Titel, Erklärung, Knöpfe. */
function Tool({ title, hint, children }: { title: string; hint: string; children: React.ReactNode }) {
  return (
    <div className="space-y-2 border-t border-gray-700 pt-3 first:border-t-0 first:pt-0">
      <p className="text-white text-sm font-medium">{title}</p>
      <p className="text-gray-500 text-xs">{hint}</p>
      <div className="flex gap-3 flex-wrap">{children}</div>
    </div>
  );
}

const BTN = "bg-blue-700 text-white text-sm px-4 py-1.5 rounded hover:bg-blue-600 disabled:opacity-50";
const BTN_GRAY = "bg-gray-700 text-white text-sm px-4 py-1.5 rounded hover:bg-gray-600 disabled:opacity-50";

/** Persönliche Einstellungen. Alles, was den ganzen Server betrifft, steht im Admin-Bereich (/admin). */
export default function SettingsPage() {
  const { t } = useI18n();
  const me = useMe();
  const isAdmin = me?.is_admin ?? false;
  const { s, set, save, saving } = useSettingsEditor();
  const [pw, setPw] = useState({ current: "", next: "", confirm: "" });
  const [pwSaving, setPwSaving] = useState(false);
  const [refreshingPrices, setRefreshingPrices] = useState(false);
  const [fillingPokedex, setFillingPokedex] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [switchingImages, setSwitchingImages] = useState(false);
  const [debugOn, setDebugOn] = useState(false);
  useEffect(() => { setDebugOn(debugEnabled()); }, []);

  const handlePriceRefresh = async () => {
    setRefreshingPrices(true);
    try {
      await pricesApi.refresh();
      toast.success(t.settings_price_refresh_started);
    } catch {
      toast.error(t.settings_price_refresh_error);
    } finally {
      setRefreshingPrices(false);
    }
  };

  const handleExportCsv = async () => {
    setExporting(true);
    try {
      const r = await dataApi.exportCsv();
      triggerBlobDownload(r.data, r.headers["content-disposition"], `pokecollect-export-${new Date().toISOString().slice(0, 10)}.csv`);
    } catch {
      toast.error(t.settings_export_error);
    } finally {
      setExporting(false);
    }
  };

  const handleCatalogImages = async () => {
    setSwitchingImages(true);
    try {
      const r = await cardApi.bulk("catalog_image", [], undefined, true);
      toast.success(t.settings_catalog_images_done(r.data.done));
    } catch {
      toast.error(t.bulk_error);
    } finally {
      setSwitchingImages(false);
    }
  };

  const handlePasswordChange = async () => {
    if (pw.next !== pw.confirm) { toast.error(t.settings_pw_mismatch); return; }
    if (pw.next.length < 8) { toast.error(t.settings_pw_too_short); return; }
    setPwSaving(true);
    try {
      await settingsApi.changePassword(pw.current, pw.next);
      setPw({ current: "", next: "", confirm: "" });
      toast.success(t.settings_pw_changed);
    } catch (e: unknown) {
      const msg = apiErrorText(e);
      toast.error(msg ?? t.settings_pw_change_error);
    } finally {
      setPwSaving(false);
    }
  };

  if (!s) return <div className="text-gray-500 p-8">{t.detail_loading}</div>;

  return (
    <div className="max-w-2xl mx-auto space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold text-white">{t.nav_settings}</h1>
        {/* Wiederöffnen-Weg für die "Was ist neu"-Box (Issue #99): einmal
            gesehen, danach nur noch über diese Versionsnummer erreichbar. */}
        {/* Panel-Nacharbeit (#99, blinde Stimme): Vorher ERSETZTE ein aria-label
            den sichtbaren Text als Accessible Name ("Show what's new in version
            1.9.1" gegen sichtbar "PokéCollect v1.9.1") — das verletzt WCAG 2.5.3
            "Label in Name" (Level A): Sprachsteuerung ("klick PokéCollect v1.9.1")
            greift dann ins Leere. Jetzt bleibt der sichtbare Text Teil des Namens,
            der Zweck steht als title-Tooltip daneben. */}
        <button
          type="button"
          onClick={openWhatsNew}
          title={t.whatsnew_reopen_label(APP_VERSION)}
          className="text-gray-500 text-xs hover:text-gray-300 underline decoration-dotted underline-offset-2"
        >
          PokéCollect v{APP_VERSION}
        </button>
      </div>

      {isAdmin && (
        <Link href="/admin" data-testid="admin-link"
          className="flex items-center justify-between rounded-lg border border-pokemon-yellow/40 bg-pokemon-yellow/10 px-4 py-3 text-sm text-pokemon-yellow hover:bg-pokemon-yellow/20">
          <span>🛡️ {t.admin_link_hint}</span>
          <span className="font-medium whitespace-nowrap">{t.admin_open} →</span>
        </Link>
      )}

      {/* Anzeige */}
      <Section title={`🖼️ ${t.settings_section_display}`}>
        <Field label={t.settings_placeholder_images} hint={t.settings_placeholder_images_hint}>
          <div className="flex items-center gap-3 mt-1">
            <Toggle checked={s.placeholder_images_enabled} onChange={(v) => { set("placeholder_images_enabled", v); save({ placeholder_images_enabled: v }); }} />
            <span className="text-sm text-gray-300">{s.placeholder_images_enabled ? t.settings_enabled : t.settings_disabled}</span>
          </div>
        </Field>
        <Field label={t.settings_cards_per_page}>
          <select value={s.cards_per_page} onChange={(e) => set("cards_per_page", Number(e.target.value))} className={INPUT} style={{ width: "auto" }}>
            {[24, 48, 96, 200].map((n) => <option key={n} value={n}>{n}</option>)}
          </select>
        </Field>
        <Field label={t.settings_default_sort}>
          <select value={s.default_sort} onChange={(e) => set("default_sort", e.target.value)} className={INPUT} style={{ width: "auto" }}>
            <option value="pokedex_nr">{t.filter_sort_pokedex}</option>
            <option value="wert">{t.filter_sort_value}</option>
            <option value="regulation">{t.filter_sort_regulation}</option>
            <option value="hinzugefuegt_am">{t.filter_sort_added}</option>
          </select>
          <SortDirectionButton sort={s.default_sort} reverse={s.default_sort_reverse} onChange={(r) => set("default_sort_reverse", r)} className="ml-2" />
        </Field>
        <div className="pt-1">
          <button type="button" onClick={() => save({ cards_per_page: s.cards_per_page, default_sort: s.default_sort, default_sort_reverse: s.default_sort_reverse })} disabled={saving} className="bg-blue-700 text-white text-sm px-4 py-1.5 rounded hover:bg-blue-600 disabled:opacity-50">
            {t.form_save}
          </button>
        </div>
      </Section>

      {/* Navigation: konfigurierbare BottomNav (Issue #34) */}
      <Section title={`🧭 ${t.settings_section_nav}`}>
        <BottomNavSettings />
      </Section>

      {/* Sammlung */}
      <Section title={`📦 ${t.settings_section_collection}`}>
        {/* Listen entsprechen den Backend-Enums (schemas/card.py) */}
        <Field label={t.settings_default_language}>
          <select value={s.default_language} onChange={(e) => set("default_language", e.target.value)} className={INPUT} style={{ width: "auto" }}>
            <option value="">{t.settings_default_language_auto}</option>
            {["DE", "EN", "CN", "JP", "FR", "ES", "IT"].map((l) => (
              <option key={l} value={l}>{l}</option>
            ))}
          </select>
        </Field>
        <Field label={t.settings_default_condition}>
          <select value={s.default_condition} onChange={(e) => set("default_condition", e.target.value)} className={INPUT} style={{ width: "auto" }}>
            <option value="">{t.settings_none_option}</option>
            {["Mint", "Near Mint", "Excellent", "Good", "Played"].map((c) => (
              <option key={c} value={c}>{c}</option>
            ))}
          </select>
        </Field>
        <div className="pt-1">
          <button type="button" onClick={() => save({ default_language: s.default_language, default_condition: s.default_condition })} disabled={saving} className="bg-blue-700 text-white text-sm px-4 py-1.5 rounded hover:bg-blue-600 disabled:opacity-50">
            {t.form_save}
          </button>
        </div>
      </Section>

      {/* Werkzeuge für die eigene Sammlung */}
      <Section title={`🧰 ${t.settings_section_tools}`}>
        <Tool title={t.settings_price_refresh_now} hint={t.settings_prices_personal_hint}>
          <button type="button" onClick={handlePriceRefresh} disabled={refreshingPrices} className={BTN_GRAY}>
            {t.settings_price_refresh_now}
          </button>
        </Tool>
        <Tool title={t.settings_section_images} hint={t.settings_images_hint}>
          <button
            type="button"
            onClick={async () => {
              try { await cardApi.backfillImages(false); toast.success(t.settings_backfill_started); }
              catch { toast.error(t.settings_backfill_error); }
            }}
            className={BTN}
          >
            {t.settings_backfill_missing}
          </button>
          <button
            type="button"
            onClick={async () => {
              if (!confirm(t.settings_backfill_confirm)) return;
              try { await cardApi.backfillImages(true); toast.success(t.settings_backfill_full_started); }
              catch { toast.error(t.settings_backfill_error); }
            }}
            className={BTN_GRAY}
          >
            {t.settings_backfill_all}
          </button>
        </Tool>
        <Tool title={t.settings_catalog_images} hint={t.settings_catalog_images_hint}>
          <button type="button" onClick={() => void handleCatalogImages()} disabled={switchingImages} className={BTN_GRAY}>
            {switchingImages ? t.settings_data_busy : t.settings_catalog_images}
          </button>
        </Tool>
        <Tool title={t.settings_section_pokedex} hint={t.settings_fill_pokedex_hint}>
          <button
            type="button"
            disabled={fillingPokedex}
            onClick={async () => {
              setFillingPokedex(true);
              try {
                const r = await cardApi.fillPokedex();
                toast.success(t.settings_fill_pokedex_done(r.data.created, r.data.existing));
              } catch {
                toast.error(t.settings_fill_pokedex_error);
              } finally {
                setFillingPokedex(false);
              }
            }}
            className={BTN}
          >
            {fillingPokedex ? "…" : t.settings_fill_pokedex}
          </button>
        </Tool>
        <Tool title={t.settings_export_csv} hint={t.settings_data_hint_user}>
          <button type="button" onClick={handleExportCsv} disabled={exporting} className={BTN}>
            {exporting ? t.settings_data_busy : t.settings_export_csv}
          </button>
        </Tool>
        <Tool title={t.settings_debug_title} hint={t.settings_debug_hint}>
          <label className="flex items-center gap-2 text-sm text-gray-300" data-testid="settings-debug">
            <Toggle checked={debugOn} onChange={(v) => { setDebug(v); setDebugOn(v); }} />
            {debugOn ? t.settings_debug_on : t.settings_debug_off}
          </label>
        </Tool>
      </Section>

      {/* Konto */}
      <Section title={`👤 ${t.settings_section_account}`}>
        {me && (
          <div className="text-sm">
            <span className="text-gray-400">{t.account_signed_in_as} </span>
            <span className="text-white">{me.display_name || me.username}</span>
            {me.display_name && <span className="text-gray-500"> · {me.username}</span>}
            {me.is_admin && <span className="ml-2 text-[10px] uppercase tracking-wide bg-pokemon-yellow/20 text-pokemon-yellow rounded px-1.5 py-0.5">{t.account_admin_badge}</span>}
            {me.email && <div className="text-gray-500 text-xs mt-0.5">{me.email}</div>}
            {me.sso_linked && <div className="text-gray-500 text-xs mt-0.5">{t.account_sso_linked(me.oidc_provider_name)}</div>}
          </div>
        )}
        {isAdmin && me?.password_login && (<>
        <p className="text-gray-400 text-xs font-medium pt-1">{t.account_local_password}</p>
        <Field label={t.settings_pw_current}>
          <input type="password" value={pw.current} onChange={(e) => setPw((p) => ({ ...p, current: e.target.value }))} className={INPUT} autoComplete="current-password" />
        </Field>
        <Field label={t.settings_pw_new} hint={t.settings_pw_new_hint}>
          <input type="password" value={pw.next} onChange={(e) => setPw((p) => ({ ...p, next: e.target.value }))} className={INPUT} autoComplete="new-password" />
        </Field>
        <Field label={t.settings_pw_confirm}>
          <input type="password" value={pw.confirm} onChange={(e) => setPw((p) => ({ ...p, confirm: e.target.value }))} className={INPUT} autoComplete="new-password" />
        </Field>
        <div className="pt-1">
          <button type="button" onClick={handlePasswordChange} disabled={pwSaving || !pw.current || !pw.next} className="bg-blue-700 text-white text-sm px-4 py-1.5 rounded hover:bg-blue-600 disabled:opacity-50">
            {t.settings_pw_change}
          </button>
        </div>
        </>)}
        <div className="border-t border-gray-700 pt-3">
          <button
            type="button"
            onClick={() => {
              // Token + Offline-Cache verwerfen → Login-Seite (harte Navigation)
              logout();
            }}
            className="bg-gray-700 text-white text-sm px-4 py-1.5 rounded hover:bg-gray-600"
          >
            {t.settings_logout}
          </button>
        </div>
      </Section>
    </div>
  );
}
