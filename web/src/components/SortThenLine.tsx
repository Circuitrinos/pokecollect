"use client";
import { X } from "lucide-react";
import { useI18n } from "@/lib/i18n";
import { SortStep } from "@/lib/sortChain";
import { sortDir } from "@/lib/sortDirection";

/** „danach nach Name (A → Z)" + ✕ zum Entfernen der Untersortierung. */
export default function SortThenLine({ steps, label, onClear, className = "" }: {
  steps: SortStep[];
  label: (key: string) => string;
  onClear: () => void;
  className?: string;
}) {
  const { t } = useI18n();
  if (!steps.length) return null;
  return (
    <span className={`inline-flex items-center gap-1 text-xs text-gray-400 ${className}`} data-testid="sort-then">
      {t.sort_then(sortThenText(steps, label, t.sort_dir))}
      <button type="button" onClick={onClear} title={t.sort_then_clear} aria-label={t.sort_then_clear}
        className="rounded p-0.5 text-gray-400 hover:bg-gray-700 hover:text-white">
        <X className="h-3.5 w-3.5" aria-hidden />
      </button>
    </span>
  );
}

export function sortThenText(steps: SortStep[], label: (key: string) => string, dirText: (d: string) => string) {
  return steps.map((s) => `${label(s.key)} (${dirText(sortDir(s.key, s.reverse))})`).join(", ");
}
