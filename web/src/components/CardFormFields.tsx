"use client";
import { useId } from "react";
import { PokemonSet } from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import { valueLabel } from "@/lib/enumLabels";
import { isPromoSet, joinCardNr, setTotal, splitCardNr } from "@/lib/cardNumber";
import RaritySelect from "@/components/RaritySelect";

// Gemeinsame Feld-Bausteine für die Karten-Formulare (Neuanlage + Detail-Edit,
// Issue #5). `dense` ist die kompaktere Variante der Detailseite (py-1,
// Label ohne Abstand) — die Neuanlage nutzt die Standard-Variante.

const labelCls = (dense?: boolean) =>
  dense ? "text-gray-500 text-xs block" : "text-gray-400 text-xs block mb-1";

const inputCls = (dense?: boolean, error?: boolean) =>
  `w-full bg-gray-800 border rounded px-2 ${dense ? "py-1" : "py-1.5"} text-white text-sm ${
    error ? "border-red-500" : "border-gray-700"
  }`;

export function TextField({ label, value, onChange, type = "text", dense }: {
  label: string;
  value: unknown;
  onChange: (v: string | number | null) => void;
  type?: "text" | "number";
  dense?: boolean;
}) {
  const id = useId();
  return (
    <div>
      <label htmlFor={id} className={labelCls(dense)}>{label}</label>
      <input
        id={id}
        type={type}
        value={String(value ?? "")}
        onChange={(e) => onChange(type === "number" ? Number(e.target.value) || null : e.target.value)}
        className={inputCls(dense)}
      />
    </div>
  );
}

/** Betrags-Feld (€) — hält den Rohstring, damit Cent-Nachkommastellen (12,50)
 * beim Tippen nicht durch Number()-Rundung verlorengehen (#26). */
export function CurrencyField({ label, value, onChange, dense }: {
  label: string;
  value: unknown;
  onChange: (v: string | null) => void;
  dense?: boolean;
}) {
  const id = useId();
  return (
    <div>
      <label htmlFor={id} className={labelCls(dense)}>{label}</label>
      <input
        id={id}
        type="number"
        step="0.01"
        min="0"
        inputMode="decimal"
        value={value == null ? "" : String(value)}
        onChange={(e) => onChange(e.target.value === "" ? null : e.target.value)}
        className={inputCls(dense)}
      />
    </div>
  );
}

/** Datums-Feld (native Date-Picker) — speichert ISO „YYYY-MM-DD" oder null (#26). */
export function DateField({ label, value, onChange, dense }: {
  label: string;
  value: unknown;
  onChange: (v: string | null) => void;
  dense?: boolean;
}) {
  const id = useId();
  return (
    <div>
      <label htmlFor={id} className={labelCls(dense)}>{label}</label>
      <input
        id={id}
        type="date"
        value={value ? String(value).slice(0, 10) : ""}
        onChange={(e) => onChange(e.target.value || null)}
        className={inputCls(dense)}
      />
    </div>
  );
}

export function SelectField({ fieldKey, label, value, onChange, options, language, dense }: {
  fieldKey: string;
  label: string;
  value: unknown;
  onChange: (v: string | null) => void;
  options: string[];
  language?: string | null;
  dense?: boolean;
}) {
  const id = useId();
  const { lang } = useI18n();
  // Seltenheit bekommt den Symbol-Picker statt eines nackten Selects
  if (fieldKey === "seltenheit") {
    return (
      <RaritySelect
        label={label}
        value={String(value ?? "")}
        onChange={onChange}
        options={options}
        language={language ?? null}
      />
    );
  }
  return (
    <div>
      <label htmlFor={id} className={labelCls(dense)}>{label}</label>
      <select
        id={id}
        value={String(value ?? "")}
        onChange={(e) => onChange(e.target.value || null)}
        className={inputCls(dense)}
      >
        <option value="">–</option>
        {options.map((o) => <option key={o} value={o}>{valueLabel(o, lang)}</option>)}
      </select>
    </div>
  );
}

export function TextareaField({ label, value, onChange, dense }: {
  label: string;
  value: unknown;
  onChange: (v: string) => void;
  dense?: boolean;
}) {
  const id = useId();
  return (
    <div className="col-span-full">
      <label htmlFor={id} className={labelCls(dense)}>{label}</label>
      <textarea
        id={id}
        value={String(value ?? "")}
        onChange={(e) => onChange(e.target.value)}
        rows={3}
        className={inputCls(dense)}
      />
    </div>
  );
}

/** Pokédex-Nr. mit Lade-Indikator für den Namens-Lookup. */
export function PokedexNrField({ value, onChange, nameLoading, dense }: {
  value: unknown;
  onChange: (nr: number | null) => void;
  nameLoading: boolean;
  dense?: boolean;
}) {
  const { t } = useI18n();
  const id = useId();
  return (
    <div>
      <label htmlFor={id} className={labelCls(dense)}>
        {t.form_pokedex_nr}
        {nameLoading && <span className="ml-2 text-gray-500 animate-pulse">…</span>}
      </label>
      <input
        id={id}
        type="number"
        value={String(value ?? "")}
        onChange={(e) => onChange(Number(e.target.value) || null)}
        className={inputCls(dense)}
      />
    </div>
  );
}

/** Karten-Nr. als zwei Felder — Nummer und Gesamtzahl des Sets („117" / „132") —, gespeichert
 *  zusammen („117/132"). Ist das Set bekannt, kommt die Gesamtzahl von dort, sobald die
 *  Nummer eingetragen ist. Validierungsfehler aus useCardForm. */
export function CardNrField({ value, onChange, validate, selectedSet, error, dense, loading }: {
  value: unknown;
  onChange: (v: string) => void;
  validate: (v: string) => boolean;
  selectedSet: PokemonSet | null;
  error: string | null;
  dense?: boolean;
  loading?: boolean;
}) {
  const { t } = useI18n();
  const id = useId();
  const [number, total] = splitCardNr(value);
  const fromSet = setTotal(selectedSet);
  const setNumber = (v: string) => {
    if (v.includes("/")) { onChange(joinCardNr(...splitCardNr(v))); return; }   // „117/132" eingefügt
    onChange(joinCardNr(v, total));
  };
  const fillTotal = () => {
    const combined = !total && fromSet && /^\d+$/.test(number) ? joinCardNr(number, fromSet) : joinCardNr(number, total);
    if (combined !== String(value ?? "")) onChange(combined);
    validate(combined);
  };
  return (
    <div>
      <label htmlFor={id} className={labelCls(dense)}>
        {t.form_card_nr}
        {loading && <span className="ml-2 text-gray-500 animate-pulse">…</span>}
      </label>
      <div className="flex items-center gap-1" data-testid="card-nr-field">
        <input
          id={id}
          type="text"
          value={number}
          onChange={(e) => setNumber(e.target.value)}
          onBlur={fillTotal}
          placeholder={fromSet ? "001" : selectedSet && isPromoSet(selectedSet) ? "SWSH050" : t.form_card_nr_placeholder_number}
          aria-label={t.form_card_nr}
          data-testid="card-nr-number"
          className={`${inputCls(dense, !!error)} min-w-0 flex-1`}
        />
        <span className="text-gray-500 text-sm">/</span>
        <input
          type="text"
          value={total}
          onChange={(e) => onChange(joinCardNr(number, e.target.value.replace("/", "")))}
          onBlur={(e) => validate(joinCardNr(number, e.target.value))}
          placeholder={fromSet || t.form_card_total_placeholder}
          aria-label={t.form_card_total}
          title={t.form_card_total}
          data-testid="card-nr-total"
          className={`${inputCls(dense, !!error)} !w-20 shrink-0`}
        />
      </div>
      {selectedSet && !error && (isPromoSet(selectedSet) ? (
        <p className={`text-gray-500 text-xs ${dense ? "mt-0.5" : "mt-1"}`} data-testid="card-nr-promo-hint">
          {t.form_card_nr_hint_promo}
        </p>
      ) : selectedSet.max_card_nr ? (
        <p className={`text-gray-500 text-xs ${dense ? "mt-0.5" : "mt-1"}`}>
          {t.form_card_nr_hint(selectedSet.max_card_nr)}
        </p>
      ) : null)}
      {error && <p className={`text-red-400 text-xs ${dense ? "mt-0.5" : "mt-1"}`}>{error}</p>}
    </div>
  );
}
