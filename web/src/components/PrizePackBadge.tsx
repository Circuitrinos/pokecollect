"use client";
import { useI18n } from "@/lib/i18n";

// „PLAY!"-Badge für Karten mit Play!-Pokémon-Stempel (Prize Pack Series) —
// wie FirstEditionBadge auf Kachel und Detailseite; Position per className.
export default function PrizePackBadge({ className = "" }: { className?: string }) {
  const { t } = useI18n();
  return (
    <span
      className={`inline-block bg-red-600/95 text-white text-[9px] font-bold leading-none px-1 py-0.5 rounded ${className}`}
      title={t.prize_pack_label}
    >
      {t.prize_pack_badge}
    </span>
  );
}
