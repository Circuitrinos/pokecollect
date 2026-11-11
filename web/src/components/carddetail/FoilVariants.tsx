"use client";
import { useState } from "react";
import { Card } from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import { valueLabel } from "@/lib/enumLabels";

// Welche Drucke es von der Karte laut Katalog gibt (TCGdex-Varianten +
// TCGplayer-Muster-Produkte) — im Bearbeiten-Modus setzt ein Tipp Folierung
// und Muster. Dazu eine kurze Anleitung, woran man die Muster erkennt.
type Choice = { folierung: string; muster: string | null };

export default function FoilVariants({ card, folierung, muster, editing, onPick }: {
  card: Card;
  folierung: string | null | undefined;
  muster: string | null | undefined;
  editing: boolean;
  onPick: (c: Choice) => void;
}) {
  const { t, lang } = useI18n();
  const [help, setHelp] = useState(false);
  const v = card.katalog_varianten;
  const choices: Choice[] = [];
  if (v?.normal) choices.push({ folierung: "Normal", muster: null });
  if (v?.holo) choices.push({ folierung: "Holo", muster: null });
  if (v?.reverse) choices.push({ folierung: "Reverse Holo", muster: null });
  for (const p of v?.patterns ?? []) choices.push({ folierung: "Reverse Holo", muster: p });
  const label = (c: Choice) => [valueLabel(c.folierung, lang), c.muster ? valueLabel(c.muster, lang) : null].filter(Boolean).join(" · ");
  const current = (c: Choice) => (folierung ?? "") === c.folierung && (muster || null) === c.muster;
  return (
    <div className="sm:col-span-2" data-testid="foil-variants">
      <div className="flex flex-wrap items-center gap-1.5">
        {choices.length > 0 && <span className="text-gray-500 text-xs">{t.foil_exists_as}</span>}
        {choices.map((c) => (
          editing ? (
            <button key={label(c)} type="button" onClick={() => onPick(c)} data-testid="foil-variant"
              className={`rounded-full px-2 py-0.5 text-xs ${current(c) ? "bg-pokemon-blue text-white" : "bg-gray-800 text-gray-300 hover:text-white"}`}>
              {label(c)}
            </button>
          ) : (
            <span key={label(c)} data-testid="foil-variant"
              className={`rounded-full px-2 py-0.5 text-xs ${current(c) ? "bg-pokemon-blue/70 text-white" : "bg-gray-800 text-gray-400"}`}>
              {label(c)}
            </span>
          )
        ))}
        <button type="button" onClick={() => setHelp((h) => !h)} title={t.foil_guide_title} data-testid="foil-help"
          className="ml-auto rounded-full w-5 h-5 text-[11px] bg-gray-800 text-gray-400 hover:text-white">?</button>
      </div>
      {help && (
        <div className="mt-2 rounded bg-gray-900/70 p-2 text-xs text-gray-300 whitespace-pre-line" data-testid="foil-guide">
          <div className="text-white font-medium mb-1">{t.foil_guide_title}</div>
          {t.foil_guide}
        </div>
      )}
    </div>
  );
}
