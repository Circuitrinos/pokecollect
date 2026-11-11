"use client";
import { useEffect, useMemo, useState } from "react";
import { Enums, PokemonSet, catalogApi } from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import { rarityOptionLabel } from "@/components/RarityBadge";
import SearchableSelect, { SelectOption } from "@/components/SearchableSelect";
import SortDirectionButton from "@/components/SortDirectionButton";
import SortThenLine from "@/components/SortThenLine";
import { useMe } from "@/lib/useMe";
import { decodeThen, encodeThen, nextThen } from "@/lib/sortChain";
import { useIsDesktop } from "@/lib/useIsDesktop";
import { useSetOptions } from "@/lib/useSetOptions";

export type Filters = {
  besessen?: boolean;
  erste_edition?: boolean;
  prize_pack?: boolean;
  generation?: number;
  set?: string;
  seltenheit?: string;
  sprache?: string;
  illustrator?: string;
  search?: string;
  sort?: string;
  reverse?: boolean;   // Sortierung umkehren
  then?: string;       // Untersortierungen, z.B. "set,-wert" (zuvor gewählte Sortierungen)
  bild_status?: string;
  standard?: "legal" | "not_legal" | "unknown";
  regulation_mark?: string;
};

type Props = {
  filters: Filters;
  onChange: (f: Filters) => void;
  enums: Enums | null;
  sets: PokemonSet[];
  // Optional gesteuert (z.B. über eine Lupe in der Statistik-Zeile); ohne diese
  // Props verwaltet die Komponente das Auf-/Zuklappen selbst und zeigt einen Toggle.
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
};

const GENERATIONS = [1, 2, 3, 4, 5, 6, 7, 8, 9];
const CARD_SORTS = ["pokedex_nr", "set", "wert", "regulation", "hinzugefuegt_am"] as const;
// Regulation Marks gibt es seit Schwert & Schild (D); neueste zuerst
const REG_FILTER_MARKS = ["L", "K", "J", "I", "H", "G", "F", "E", "D"];

export default function FilterSidebar({ filters, onChange, enums, sets, open, onOpenChange }: Props) {
  const { t } = useI18n();
  const controlled = open !== undefined;
  const [internalOpen, setInternalOpen] = useState(false);
  const actualOpen = controlled ? !!open : internalOpen;
  const setOpen = (v: boolean) => { if (controlled) onOpenChange?.(v); else setInternalOpen(v); };
  const [illustrators, setIllustrators] = useState<string[]>([]);
  // Auf Desktop standardmäßig geöffnet, auf Mobile eingeklappt (nur ungesteuert).
  const isDesktop = useIsDesktop();
  useEffect(() => {
    if (!controlled && isDesktop) setInternalOpen(true);
  }, [controlled, isDesktop]);
  useEffect(() => {
    catalogApi.illustrators().then((r) => setIllustrators(r.data)).catch(() => {});
  }, []);
  const isAdmin = useMe()?.is_admin ?? false;
  const update = (key: keyof Filters, value: unknown) =>
    onChange({ ...filters, [key]: value === "" ? undefined : value });
  // Neue Sortierung: die bisherige wird Untersortierung (in ihrer Richtung)
  const currentSort = filters.sort ?? "pokedex_nr";
  const thenSteps = decodeThen(filters.then, CARD_SORTS);
  const changeSort = (next: string) => {
    if (next === currentSort) return;
    const then = nextThen({ key: currentSort, reverse: Boolean(filters.reverse) }, thenSteps, next);
    onChange({ ...filters, sort: next, reverse: false, then: encodeThen(then) || undefined });
  };
  const sortLabel = (k: string) => ({ pokedex_nr: t.filter_sort_pokedex, set: t.filter_sort_set, wert: t.filter_sort_value,
    regulation: t.filter_sort_regulation, hinzugefuegt_am: t.filter_sort_added } as Record<string, string>)[k] ?? k;

  const activeCount = (["besessen", "erste_edition", "prize_pack", "generation", "set", "seltenheit", "sprache", "illustrator", "search", "bild_status", "standard", "regulation_mark"] as const)
    .filter((k) => filters[k] !== undefined && filters[k] !== "").length;

  const illuOptions: SelectOption[] = useMemo(
    () => illustrators.map((i) => ({ value: i, label: i })), [illustrators]);

  const setOptions = useSetOptions(sets);

  return (
    <aside className="w-full text-sm">
      {/* Toggle nur im ungesteuerten Modus (sonst übernimmt die Seite die Lupe) */}
      {!controlled && (
        <button type="button"
          onClick={() => setOpen(!actualOpen)}
          className="w-full flex items-center justify-between bg-pokemon-card border border-gray-700 rounded px-3 py-2 text-gray-200"
        >
          <span>🔍 {t.filter_title}{activeCount > 0 ? ` (${activeCount})` : ""}</span>
          <span className="text-gray-500">{actualOpen ? "▲" : "▼"}</span>
        </button>
      )}

      <div className={`${actualOpen ? "grid" : "hidden"} grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-3 ${controlled ? "" : "mt-3"} items-end`}>
      <div>
        <label className="block text-gray-400 mb-1">{t.filter_search}</label>
        <input
          type="text"
          value={filters.search ?? ""}
          onChange={(e) => update("search", e.target.value)}
          placeholder={t.filter_search_placeholder}
          className="w-full bg-pokemon-card border border-gray-700 rounded px-2 py-1.5 text-white placeholder-gray-500"
        />
      </div>

      <div>
        <label className="block text-gray-400 mb-1">{t.filter_status}</label>
        <select
          value={filters.besessen === undefined ? "" : String(filters.besessen)}
          onChange={(e) => update("besessen", e.target.value === "" ? undefined : e.target.value === "true")}
          className="w-full bg-pokemon-card border border-gray-700 rounded px-2 py-1.5 text-white"
        >
          <option value="">{t.filter_all_cards}</option>
          <option value="true">{t.filter_owned}</option>
          <option value="false">{t.filter_not_owned}</option>
        </select>
      </div>

      <div>
        <label className="block text-gray-400 mb-1">{t.first_edition_label}</label>
        <select
          value={filters.erste_edition === undefined ? "" : "true"}
          onChange={(e) => update("erste_edition", e.target.value === "" ? undefined : true)}
          className="w-full bg-pokemon-card border border-gray-700 rounded px-2 py-1.5 text-white"
        >
          <option value="">{t.filter_all}</option>
          <option value="true">{t.filter_first_edition_only}</option>
        </select>
      </div>

      <div>
        <label className="block text-gray-400 mb-1">{t.prize_pack_label}</label>
        <select
          value={filters.prize_pack === undefined ? "" : "true"}
          onChange={(e) => update("prize_pack", e.target.value === "" ? undefined : true)}
          className="w-full bg-pokemon-card border border-gray-700 rounded px-2 py-1.5 text-white"
        >
          <option value="">{t.filter_all}</option>
          <option value="true">{t.filter_prize_pack_only}</option>
        </select>
      </div>

      <div>
        <label htmlFor="filter_standard" className="block text-gray-400 mb-1">{t.filter_standard}</label>
        <select
          id="filter_standard"
          value={filters.standard ?? ""}
          onChange={(e) => update("standard", e.target.value)}
          className="w-full bg-pokemon-card border border-gray-700 rounded px-2 py-1.5 text-white"
        >
          <option value="">{t.filter_all}</option>
          <option value="legal">{t.filter_standard_legal}</option>
          <option value="not_legal">{t.filter_standard_not}</option>
          <option value="unknown">{t.filter_standard_unknown}</option>
        </select>
      </div>

      <div>
        <label htmlFor="filter_regulation_mark" className="block text-gray-400 mb-1">{t.filter_reg_mark}</label>
        <select
          id="filter_regulation_mark"
          value={filters.regulation_mark ?? ""}
          onChange={(e) => update("regulation_mark", e.target.value)}
          className="w-full bg-pokemon-card border border-gray-700 rounded px-2 py-1.5 text-white"
        >
          <option value="">{t.filter_all}</option>
          {REG_FILTER_MARKS.map((m) => <option key={m} value={m}>{m}</option>)}
          <option value="-">{t.filter_reg_none}</option>
        </select>
      </div>

      <div>
        <label className="block text-gray-400 mb-1">{t.filter_generation}</label>
        <select
          value={filters.generation ?? ""}
          onChange={(e) => update("generation", e.target.value ? Number(e.target.value) : "")}
          className="w-full bg-pokemon-card border border-gray-700 rounded px-2 py-1.5 text-white"
        >
          <option value="">{t.filter_all}</option>
          {GENERATIONS.map((g) => (
            <option key={g} value={g}>Gen {g}</option>
          ))}
        </select>
      </div>

      <div>
        <label className="block text-gray-400 mb-1">{t.filter_set}</label>
        <SearchableSelect
          value={filters.set ?? ""}
          onChange={(v) => update("set", v)}
          options={setOptions}
          allLabel={t.filter_all_sets}
          placeholder={t.filter_set}
        />
      </div>

      <div>
        <label className="block text-gray-400 mb-1">{t.filter_rarity}</label>
        <select
          value={filters.seltenheit ?? ""}
          onChange={(e) => update("seltenheit", e.target.value)}
          className="w-full bg-pokemon-card border border-gray-700 rounded px-2 py-1.5 text-white"
        >
          <option value="">{t.filter_all}</option>
          {(enums?.seltenheit ?? []).map((s) => (
            <option key={s} value={s}>{rarityOptionLabel(s)}</option>
          ))}
        </select>
      </div>

      <div>
        <label className="block text-gray-400 mb-1">{t.filter_language}</label>
        <select
          value={filters.sprache ?? ""}
          onChange={(e) => update("sprache", e.target.value)}
          className="w-full bg-pokemon-card border border-gray-700 rounded px-2 py-1.5 text-white"
        >
          <option value="">{t.filter_all}</option>
          {(enums?.sprache ?? []).map((s) => (
            <option key={s} value={s}>{s}</option>
          ))}
        </select>
      </div>

      <div>
        <label className="block text-gray-400 mb-1">{t.catalog_illustrator}</label>
        <SearchableSelect
          value={filters.illustrator ?? ""}
          onChange={(v) => update("illustrator", v)}
          options={illuOptions}
          allLabel={t.catalog_all_illustrators}
          placeholder={t.catalog_illustrator}
        />
      </div>

      <div>
        <label className="block text-gray-400 mb-1">{t.filter_image}</label>
        <select
          value={filters.bild_status ?? ""}
          onChange={(e) => update("bild_status", e.target.value)}
          className="w-full bg-pokemon-card border border-gray-700 rounded px-2 py-1.5 text-white"
        >
          <option value="">{t.filter_all}</option>
          <option value="eigenes_foto">{t.filter_own_photo}</option>
          <option value="externe_url">{t.filter_external_url}</option>
          <option value="platzhalter">{t.filter_placeholder_only}</option>
          {isAdmin && <option value="kein_export">{t.filter_not_exported}</option>}
        </select>
      </div>

      <div className="col-span-2">
        <label className="block text-gray-400 mb-1">{t.filter_sort}</label>
        <div className="flex gap-2">
        <select
          value={currentSort}
          onChange={(e) => changeSort(e.target.value)}
          aria-label={t.filter_sort}
          className="min-w-0 flex-1 bg-pokemon-card border border-gray-700 rounded px-2 py-1.5 text-white"
        >
          <option value="pokedex_nr">{t.filter_sort_pokedex}</option>
          <option value="set">{t.filter_sort_set}</option>
          <option value="wert">{t.filter_sort_value}</option>
          <option value="regulation">{t.filter_sort_regulation}</option>
          <option value="hinzugefuegt_am">{t.filter_sort_added}</option>
        </select>
        <SortDirectionButton sort={currentSort} reverse={Boolean(filters.reverse)}
          onChange={(r) => onChange({ ...filters, reverse: r })} />
        </div>
        <SortThenLine steps={thenSteps} label={sortLabel} onClear={() => onChange({ ...filters, then: undefined })} className="mt-1" />
      </div>

      <button type="button"
        onClick={() => onChange({})}
        className="w-full text-gray-400 hover:text-white text-xs underline"
      >
        {t.filter_reset}
      </button>
      </div>
    </aside>
  );
}
