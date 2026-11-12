"use client";
import { useEffect, useState } from "react";

/**
 * Modul-Cache für API-Ressourcen (Issue #14): eine Routine für
 * useEnums/useSets/useSettings (Kredo: DRY).
 *
 * - Ein Fetch pro Session (Modul-Lebensdauer), geteilt über alle Komponenten.
 * - refresh() invalidiert und lädt neu; alle eingehängten Komponenten
 *   bekommen den neuen Wert.
 * - Fehler werden nicht gecacht — der nächste Mount versucht es erneut.
 */
export function createCachedResource<T>(fetcher: () => Promise<T>) {
  let cache: T | null = null;
  let pending: Promise<T> | null = null;
  let gen = 0;                       // nur der jüngste Abruf schreibt (refresh während eines Abrufs)
  const listeners = new Set<(value: T) => void>();

  const publish = (value: T) => {
    cache = value;
    listeners.forEach((l) => l(value));
  };

  const load = (): Promise<T> => {
    if (!pending) {
      const mine = ++gen;
      pending = fetcher()
        .then((value) => {
          if (mine === gen) publish(value);
          return value;
        })
        .catch((err) => {
          if (mine === gen) pending = null;
          throw err;
        });
    }
    return pending;
  };

  const refresh = (): Promise<T> => {
    pending = null;
    return load();
  };

  /** Lokal ändern (z. B. gelöschten Eintrag sofort ausblenden); ein laufender Abruf zählt nicht mehr. */
  const update = (fn: (value: T | null) => T) => {
    gen++;
    const value = fn(cache);
    pending = Promise.resolve(value);
    publish(value);
  };

  function useResource(): { data: T | null; refresh: () => Promise<T> } {
    const [data, setData] = useState<T | null>(cache);
    useEffect(() => {
      listeners.add(setData);
      load().catch(() => {});
      return () => {
        listeners.delete(setData);
      };
    }, []);
    return { data, refresh };
  }

  return { useResource, refresh, update };
}
