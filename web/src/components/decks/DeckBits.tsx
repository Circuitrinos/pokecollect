"use client";
import { DeckLegality } from "@/lib/api";
import { useI18n } from "@/lib/i18n";

const STATUS_CLS: Record<DeckLegality["status"], string> = {
  legal: "bg-green-900/60 text-green-300",
  check: "bg-yellow-900/60 text-yellow-300",
  illegal: "bg-red-900/60 text-red-300",
};

/** Legal / Prüfen / Nicht legal — Deckliste und Deckseite. */
export function DeckStatusBadge({ status }: { status: DeckLegality["status"] }) {
  const { t } = useI18n();
  return (
    <span className={`text-[11px] rounded px-1.5 py-0.5 ${STATUS_CLS[status] ?? STATUS_CLS.check}`} data-testid="deck-status">
      {status === "legal" ? "✓ " : status === "illegal" ? "✕ " : "! "}{t.deck_status(status)}
    </span>
  );
}
