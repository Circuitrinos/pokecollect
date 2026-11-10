"use client";
import { useCallback, useState } from "react";

/** Auswahl-Modus für Kartenraster: an/aus + ausgewählte Karten-IDs. */
export function useSelection() {
  const [active, setActiveState] = useState(false);
  const [ids, setIds] = useState<Set<number>>(new Set());

  const setActive = useCallback((on: boolean) => {
    setActiveState(on);
    if (!on) setIds(new Set());
  }, []);

  const toggle = useCallback((id: number) => {
    setIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  }, []);

  /** Alle übergebenen an- bzw. abwählen (z. B. „alle auf der Seite"). */
  const setMany = useCallback((list: number[], on: boolean) => {
    setIds((prev) => {
      const next = new Set(prev);
      list.forEach((id) => (on ? next.add(id) : next.delete(id)));
      return next;
    });
  }, []);

  const clear = useCallback(() => setIds(new Set()), []);

  return { active, setActive, ids, toggle, setMany, clear };
}

export type Selection = ReturnType<typeof useSelection>;
