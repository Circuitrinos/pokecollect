"use client";
import { useMemo } from "react";
import { PokemonSet } from "@/lib/api";
import { SelectOption } from "@/components/SearchableSelect";
import { seriesLabel, SERIES_LABEL } from "@/lib/utils";
import { useI18n } from "@/lib/i18n";

/**
 * Sets → gruppierte SearchableSelect-Optionen, sortiert nach Serien-Reihenfolge
 * (SERIES_LABEL) und Name. Eine Routine für FilterSidebar + Katalog (Issue #8).
 */
export function useSetOptions(sets: PokemonSet[]): SelectOption[] {
  const { lang } = useI18n();
  return useMemo(() => {
    const nameOf = (s: PokemonSet) => (lang === "EN" && s.name_en ? s.name_en : s.name) ?? "";
    const order = Object.keys(SERIES_LABEL);
    const sorted = [...sets].sort((a, b) => {
      const ai = order.indexOf(a.series_id ?? ""); const bi = order.indexOf(b.series_id ?? "");
      const av = ai === -1 ? 999 : ai; const bv = bi === -1 ? 999 : bi;
      if (av !== bv) return av - bv;
      return nameOf(a).localeCompare(nameOf(b));
    });
    return sorted.map((s) => ({
      value: s.code,
      label: `${nameOf(s)}${s.code ? ` (${s.code})` : ""}`,
      group: seriesLabel(s.series_id, lang),
      image: s.logo_url ?? null,
    }));
  }, [sets, lang]);
}
