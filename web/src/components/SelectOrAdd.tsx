"use client";
import { useMemo, useState } from "react";

// Auswahlliste mit „＋ Neu …" am Ende (Brian 18.11.: Felder mit festen Werten wie
// die Seltenheit als Dropdown, mit + für neue Werte). „＋" macht ein Textfeld mit
// ✓ / ✕ daraus (Enter / Esc); der neue Wert bleibt danach in der Liste wählbar.
const ADD = "__select_or_add__";

export default function SelectOrAdd({
  value, options, onChange, addLabel, className, emptyLabel = "–", maxLength, upper, testId,
}: {
  value: string;
  options: string[];
  onChange: (value: string) => void;
  addLabel: string;
  className?: string;
  emptyLabel?: string;
  maxLength?: number;
  upper?: boolean;
  testId?: string;
}) {
  const [adding, setAdding] = useState(false);
  const [draft, setDraft] = useState("");
  const all = useMemo(() => (value && !options.includes(value) ? [...options, value] : options), [options, value]);

  const close = () => { setAdding(false); setDraft(""); };
  const confirm = () => {
    const v = draft.trim();
    if (v) onChange(v);
    close();
  };

  if (adding) {
    return (
      <div className="flex gap-1">
        <input autoFocus value={draft} maxLength={maxLength} data-testid={testId ? `${testId}-new` : undefined}
          onChange={(e) => setDraft(upper ? e.target.value.toUpperCase() : e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") { e.preventDefault(); confirm(); }
            if (e.key === "Escape") { e.preventDefault(); close(); }
          }}
          className={`${className ?? ""} min-w-0 flex-1`} placeholder={addLabel} />
        <button type="button" onClick={confirm} disabled={!draft.trim()} aria-label="✓"
          className="rounded bg-green-700 px-2.5 text-white hover:bg-green-600 disabled:opacity-40">✓</button>
        <button type="button" onClick={close} aria-label="✕"
          className="rounded bg-gray-700 px-2.5 text-white hover:bg-gray-600">✕</button>
      </div>
    );
  }
  return (
    <select value={value} className={className} data-testid={testId}
      onChange={(e) => (e.target.value === ADD ? setAdding(true) : onChange(e.target.value))}>
      <option value="">{emptyLabel}</option>
      {all.map((o) => <option key={o} value={o}>{o}</option>)}
      <option value={ADD}>＋ {addLabel}</option>
    </select>
  );
}
