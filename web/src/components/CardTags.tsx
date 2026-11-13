"use client";
import { Card } from "@/lib/api";
import { useI18n } from "@/lib/i18n";

// Kleine Schilder für das, was ein Exemplar von anderen derselben Karte unterscheidet:
// Folierung, Play!-Stempel, 1st Edition, Sprache (nur wenn sie von der üblichen abweicht).
const FOIL_SHORT: Record<string, string> = { "Reverse Holo": "RH", Holo: "Holo" };

export type CardTag = { key: string; label: string; cls: string; title?: string };

export function useCardTags() {
  const { t } = useI18n();
  return (card: Card, opts: { mainLang?: string | null; showLang?: boolean } = {}): CardTag[] => {
    const out: CardTag[] = [];
    if (card.folierung && card.folierung !== "Normal")
      out.push({ key: "foil", label: FOIL_SHORT[card.folierung] ?? card.folierung, cls: "bg-purple-900/70 text-purple-100", title: card.folierung });
    if (card.prize_pack) out.push({ key: "play", label: t.tag_play, cls: "bg-red-700/90 text-white", title: t.prize_pack_label });
    if (card.erste_edition) out.push({ key: "1st", label: t.tag_first, cls: "bg-gray-100 text-black", title: t.first_edition_label });
    if (card.sprache && (opts.showLang || (opts.mainLang !== undefined && card.sprache !== opts.mainLang)))
      out.push({ key: "lang", label: card.sprache, cls: "bg-black/75 text-gray-100" });
    return out;
  };
}

export function CardTagPills({ tags, className = "" }: { tags: CardTag[]; className?: string }) {
  if (!tags.length) return null;
  return (
    <span className={`inline-flex flex-wrap gap-0.5 ${className}`} data-testid="card-tags">
      {tags.map((tag) => (
        <span key={tag.key} title={tag.title} className={`rounded px-1 text-[9px] leading-tight font-medium ${tag.cls}`}>{tag.label}</span>
      ))}
    </span>
  );
}
