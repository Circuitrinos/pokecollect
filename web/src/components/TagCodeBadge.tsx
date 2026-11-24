"use client";
import { QrCode } from "lucide-react";
import { useI18n } from "@/lib/i18n";

// Code des Hüllen-Aufklebers auf der Kartenkachel (Raster, Binder) — weiß wie der
// Aufkleber selbst, damit man die Karte in der Hülle daran wiederfindet.
export default function TagCodeBadge({ code, className = "" }: { code?: string | null; className?: string }) {
  const { t } = useI18n();
  if (!code) return null;
  return (
    <span title={t.tag_badge_title(code)} data-testid="card-tag-badge"
      className={`inline-flex items-center gap-0.5 rounded bg-white/95 px-1 font-mono text-[10px] font-bold leading-4 tracking-wide text-gray-900 shadow pointer-events-none ${className}`}>
      <QrCode className="h-2.5 w-2.5" aria-hidden />
      {code}
    </span>
  );
}
