"use client";
import { ArrowDownUp } from "lucide-react";
import { useI18n } from "@/lib/i18n";
import { sortDir } from "@/lib/sortDirection";

/** Kehrt eine Sortierung um; zeigt, wie gerade sortiert ist (z.B. „A → Z", „Höchster zuerst"). */
export default function SortDirectionButton({ sort, reverse, onChange, className = "" }: {
  sort: string | undefined;
  reverse: boolean;
  onChange: (reverse: boolean) => void;
  className?: string;
}) {
  const { t } = useI18n();
  return (
    <button type="button" onClick={() => onChange(!reverse)} aria-pressed={reverse} title={t.sort_reverse}
      data-testid="sort-reverse"
      className={`inline-flex items-center gap-1 whitespace-nowrap rounded border px-2 py-1.5 text-sm ${reverse
        ? "border-blue-500 bg-blue-900/40 text-white" : "border-gray-700 bg-pokemon-card text-gray-200 hover:text-white"} ${className}`}>
      <ArrowDownUp className="h-4 w-4 shrink-0" aria-hidden />
      {t.sort_dir(sortDir(sort, reverse))}
    </button>
  );
}
