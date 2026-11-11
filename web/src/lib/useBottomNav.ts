"use client";
// BottomNav-Konfiguration: die Leiste zeigt alle Ziele; gespeichert wird nur,
// was der Nutzer ausgeblendet hat (localStorage, per Gerät). So erscheinen auch
// später neu hinzugekommene Seiten von selbst.
// Live-Sync zwischen BottomNav und der Konfig-UI in den Einstellungen über ein
// eigenes Window-Event (das native "storage"-Event feuert nur tab-übergreifend).
import { useCallback, useEffect, useState } from "react";
import { NAV_KEYS_IN_ORDER, NavKey, isNavKey } from "./navRegistry";

// Neuer Schlüssel: die frühere Auswahl (höchstens 5 Ziele) gilt bewusst nicht
// weiter — alle starten wieder mit dem vollen Menü.
const STORAGE_KEY = "pokecollect.bottomNavHidden";
const CHANGE_EVENT = "pokecollect:bottomnav-change";

function readHidden(): NavKey[] {
  if (typeof window === "undefined") return [];
  try {
    const parsed: unknown = JSON.parse(window.localStorage.getItem(STORAGE_KEY) ?? "[]");
    return Array.isArray(parsed) ? parsed.filter(isNavKey) : [];
  } catch {
    return [];
  }
}

export function useBottomNavConfig() {
  // Start mit "nichts ausgeblendet" → server- und erste Client-Render sind
  // identisch (keine Hydration-Diskrepanz); der echte Wert kommt im Effect nach.
  const [hidden, setHiddenState] = useState<NavKey[]>([]);
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    setHiddenState(readHidden());
    setMounted(true);
    const onChange = () => setHiddenState(readHidden());
    window.addEventListener(CHANGE_EVENT, onChange);
    window.addEventListener("storage", onChange);
    return () => {
      window.removeEventListener(CHANGE_EVENT, onChange);
      window.removeEventListener("storage", onChange);
    };
  }, []);

  const toggle = useCallback(
    (key: NavKey) => {
      const next = hidden.includes(key) ? hidden.filter((k) => k !== key) : [...hidden, key];
      setHiddenState(next);
      if (typeof window !== "undefined") {
        window.localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
        // Andere Hook-Instanzen (BottomNav) im selben Tab benachrichtigen.
        window.dispatchEvent(new Event(CHANGE_EVENT));
      }
    },
    [hidden],
  );

  // Gezeigte Ziele in kanonischer Reihenfolge
  const keys = NAV_KEYS_IN_ORDER.filter((k) => !hidden.includes(k));
  return { keys, toggle, mounted };
}
