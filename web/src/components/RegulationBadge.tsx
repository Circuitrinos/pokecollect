"use client";
import { useI18n } from "@/lib/i18n";
import { useSettings } from "@/lib/useSettings";
import { standardStatus, isStandardLegal } from "@/lib/regulation";

type Regulated = { regulation_mark?: string | null; legal_mark?: string | null; basic_energy?: boolean };

// Regulation Mark wie auf der Karte (kleiner Buchstabe im Kästchen), grün wenn
// Standard-legal (auch über Reprint/Basis-Energie), sonst grau. Ohne bekanntes
// Zeichen wird auf Kacheln nichts gezeigt (withText: Status als Text dazu).
export default function RegulationBadge({ card, className = "", withText = false }: {
  card: Regulated; className?: string; withText?: boolean;
}) {
  const { t } = useI18n();
  const { settings } = useSettings();
  const status = standardStatus(card, settings?.standard_min_mark);
  const mark = card.regulation_mark;
  if (!mark && !withText) return null;
  const legal = isStandardLegal(status);
  const title = t.reg_status(status);
  return (
    <span className={`inline-flex items-center gap-1 ${className}`} title={title} data-testid="regulation-badge"
          data-status={status}>
      {mark && (
        <span className={`inline-flex h-3.5 min-w-[0.875rem] items-center justify-center rounded-sm border px-0.5 text-[9px] font-bold leading-none ${
          legal ? "border-green-400 text-green-300" : "border-gray-500 text-gray-400"}`}>
          {mark}
        </span>
      )}
      {status === "reprint" && <span className="text-[9px] leading-none text-green-300">↻</span>}
      {withText && <span className={legal ? "text-green-300" : status === "unknown" ? "text-gray-400" : "text-gray-300"}>{title}</span>}
    </span>
  );
}
