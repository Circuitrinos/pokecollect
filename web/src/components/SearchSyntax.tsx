"use client";
import { useState } from "react";
import { SearchWarning } from "@/lib/api";
import { useI18n } from "@/lib/i18n";

// Suchsyntax wie bei Limitless TCG (backend/app/services/card_search.py): „?" klappt
// die Übersicht auf, ein Beispiel antippen hängt es an die Suche an.
export function SearchSyntaxHelp({ onPick, className = "", align = "left" }: {
  onPick?: (example: string) => void; className?: string; align?: "left" | "right";
}) {
  const { t } = useI18n();
  const [open, setOpen] = useState(false);
  return (
    <span className={`relative ${className}`}>
      <button type="button" onClick={() => setOpen((o) => !o)} title={t.search_help_title} aria-label={t.search_help_title}
        data-testid="search-help" aria-expanded={open}
        className="rounded-full w-5 h-5 text-[11px] bg-gray-800 text-gray-400 hover:text-white">?</button>
      {open && (
        <div className={`absolute z-40 ${align === "right" ? "right-0" : "left-0"} top-6 w-[min(26rem,85vw)] max-h-[70vh] overflow-y-auto rounded-lg border border-gray-700 bg-pokemon-dark p-3 text-xs shadow-xl`}
          data-testid="search-help-panel">
          <div className="flex items-center gap-2 mb-1.5">
            <span className="text-white font-medium flex-1">{t.search_help_title}</span>
            <button type="button" onClick={() => setOpen(false)} className="text-gray-400 hover:text-white" aria-label={t.form_cancel}>✕</button>
          </div>
          <p className="text-gray-400 mb-2">{t.search_help_intro}</p>
          <table className="w-full">
            <tbody>
              {t.search_help_rows.map(([examples, what]) => (
                <tr key={examples.join(" ")} className="align-top">
                  <td className="py-0.5 pr-2 whitespace-nowrap">
                    {examples.map((e, i) => (
                      <button key={i} type="button" onClick={() => onPick?.(e)} disabled={!onPick} data-testid="search-help-example"
                        className="mr-1 font-mono text-blue-300 hover:underline disabled:no-underline disabled:text-gray-300">{e}</button>
                    ))}
                  </td>
                  <td className="py-0.5 text-gray-400">{what}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </span>
  );
}

/** Suchbegriffe, die nicht angewandt wurden (unbekannt, keine Daten, Wert unklar). */
export function SearchWarnings({ warnings }: { warnings?: SearchWarning[] | null }) {
  const { t } = useI18n();
  if (!warnings?.length) return null;
  return (
    <div className="mt-1 space-y-0.5 text-[11px] text-amber-300" data-testid="search-warnings">
      {warnings.map((w) => (
        <div key={w.term}>⚠ <span className="font-mono">{w.term}</span> — {t.search_warning(w.reason)}</div>
      ))}
    </div>
  );
}

/** Beispiel an die Suche anhängen (ein Leerzeichen dazwischen). */
export function appendTerm(search: string | undefined, term: string): string {
  const s = (search ?? "").trim();
  return s ? `${s} ${term}` : term;
}
