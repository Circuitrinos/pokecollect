"use client";
// BottomNav-Konfiguration: die Leiste zeigt alle Ziele; gespeichert wird nur,
// was der Nutzer ausgeblendet hat — im Konto auf dem Server (übersteht neues
// Anmelden, anderes Gerät, anderen Browser). localStorage hält eine Kopie für
// die erste Anzeige, bevor die Einstellungen geladen sind.
// Live-Sync zwischen BottomNav und der Konfig-UI in den Einstellungen über ein
// eigenes Window-Event (das native "storage"-Event feuert nur tab-übergreifend).
import { useCallback, useEffect, useState } from "react";
import { settingsApi } from "./api";
import { NAV_KEYS_IN_ORDER, NavKey, isNavKey } from "./navRegistry";
import { refreshSettings, useSettings } from "./useSettings";

const STORAGE_KEY = "pokecollect.bottomNavHidden";
// Einmalig: eine nur im Browser gespeicherte Auswahl (Version davor) ins Konto übernehmen
const MIGRATED_KEY = "pokecollect.bottomNavHidden.migrated";
const CHANGE_EVENT = "pokecollect:bottomnav-change";

function readLocal(): NavKey[] {
  if (typeof window === "undefined") return [];
  try {
    const parsed: unknown = JSON.parse(window.localStorage.getItem(STORAGE_KEY) ?? "[]");
    return Array.isArray(parsed) ? parsed.filter(isNavKey) : [];
  } catch {
    return [];
  }
}

function writeLocal(keys: NavKey[]) {
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(keys));
    window.dispatchEvent(new Event(CHANGE_EVENT));
  } catch { /* egal */ }
}

export function useBottomNavConfig() {
  const { settings } = useSettings();
  const server = settings?.bottom_nav_hidden;
  // Start mit "nichts ausgeblendet" → server- und erste Client-Render sind
  // identisch (keine Hydration-Diskrepanz); der echte Wert kommt im Effect nach.
  const [hidden, setHiddenState] = useState<NavKey[]>([]);
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    setHiddenState(readLocal());
    setMounted(true);
    const onChange = () => setHiddenState(readLocal());
    window.addEventListener(CHANGE_EVENT, onChange);
    window.addEventListener("storage", onChange);
    return () => {
      window.removeEventListener(CHANGE_EVENT, onChange);
      window.removeEventListener("storage", onChange);
    };
  }, []);

  // Stand aus dem Konto übernehmen, sobald die Einstellungen geladen sind
  useEffect(() => {
    if (!server) return;
    const local = readLocal();
    let migrated = true;
    try { migrated = window.localStorage.getItem(MIGRATED_KEY) === "1"; window.localStorage.setItem(MIGRATED_KEY, "1"); } catch { /* egal */ }
    if (!migrated && server.length === 0 && local.length > 0) {
      settingsApi.update({ bottom_nav_hidden: local }).then(() => refreshSettings()).catch(() => {});
      return;
    }
    const fromServer = server.filter(isNavKey);
    if (JSON.stringify(fromServer) !== JSON.stringify(local)) writeLocal(fromServer);
    setHiddenState(fromServer);
  }, [server]);

  const toggle = useCallback(
    (key: NavKey) => {
      const next = hidden.includes(key) ? hidden.filter((k) => k !== key) : [...hidden, key];
      setHiddenState(next);
      if (typeof window !== "undefined") writeLocal(next);
      settingsApi.update({ bottom_nav_hidden: next }).then(() => refreshSettings()).catch(() => {});
    },
    [hidden],
  );

  // Gezeigte Ziele in kanonischer Reihenfolge
  const keys = NAV_KEYS_IN_ORDER.filter((k) => !hidden.includes(k));
  return { keys, toggle, mounted };
}
