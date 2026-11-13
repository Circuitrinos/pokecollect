import { useI18n } from "@/lib/i18n";

// Wo eine Karte im Binder steckt (Seite/Fach, wie die Binder-Ansicht zählt) — zum Heraussuchen.
export type BinderPlace = { page: number; slot: number; row: number; col: number };
export type CollectionPlace = { id: number; name: string; place?: BinderPlace | null };

export function useBinderPlace() {
  const { t } = useI18n();
  return {
    /** „Binder A · S.3 #5" */
    short: (c: CollectionPlace) => (c.place ? `${c.name} · ${t.place_short(c.place.page, c.place.slot)}` : c.name),
    /** „Binder A: Seite 3, Fach 5 (Reihe 2, Spalte 2)" */
    long: (c: CollectionPlace) => (c.place ? `${c.name}: ${t.place_long(c.place.page, c.place.slot, c.place.row, c.place.col)}` : c.name),
    /** nur Seite/Fach, ohne Namen */
    bare: (p?: BinderPlace | null) => (p ? t.place_short(p.page, p.slot) : ""),
    longBare: (p?: BinderPlace | null) => (p ? t.place_long(p.page, p.slot, p.row, p.col) : ""),
  };
}
