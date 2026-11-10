"use client";
import { PointerEvent as ReactPointerEvent, useEffect, useRef, useState } from "react";

/**
 * Touch-Bildschirm (grober Zeiger)? Dort kein HTML5-Drag&Drop anbieten: der
 * Browser startet sonst beim Halten einen eigenen Zieh-Vorgang, der mit
 * „halten → antippen" kollidiert.
 */
export function useCoarsePointer(): boolean {
  const [coarse, setCoarse] = useState(false);
  useEffect(() => {
    const mq = window.matchMedia?.("(pointer: coarse)");
    if (!mq) return;
    const update = () => setCoarse(mq.matches);
    update();
    mq.addEventListener?.("change", update);
    return () => mq.removeEventListener?.("change", update);
  }, []);
  return coarse;
}

/**
 * Halten (Finger/Stift, ~0,45 s) → onLong(id). Für Handys, auf denen Ziehen
 * die Seite scrollt statt die Karte zu bewegen. Bewegt sich der Finger vorher
 * (Scrollen), passiert nichts. Der Klick, den manche Browser nach dem Halten
 * noch auslösen, lässt sich über suppressed() verwerfen.
 */
export function useLongPress(onLong: (id: number) => void, enabled: boolean, ms = 450) {
  const st = useRef<{ id: number; x: number; y: number; t: number; fired: boolean } | null>(null);
  // Nur den Klick auf DIESELBE Karte direkt nach dem Halten verwerfen – ein
  // schnelles Antippen des Ziels gleich danach muss durchgehen.
  const suppress = useRef<{ id: number; until: number; any: number }>({ id: -1, until: 0, any: 0 });

  const end = (fired: boolean) => {
    const s = st.current;
    if (!s) return;
    window.clearTimeout(s.t);
    // Direkt nach dem Loslassen jeden Klick verwerfen (verschiebt sich das Layout,
    // landet er sonst auf einem Nachbarn), danach nur noch auf der gehaltenen Karte.
    if (s.fired || fired) suppress.current = { id: s.id, until: Date.now() + 700, any: Date.now() + 200 };
    st.current = null;
  };

  const bind = (id: number) => (!enabled ? {} : {
    onPointerDown: (e: ReactPointerEvent) => {
      if (e.pointerType === "mouse") return;          // Maus: Drag & Drop wie bisher
      const t = window.setTimeout(() => {
        if (!st.current) return;
        st.current.fired = true;
        try { navigator.vibrate?.(15); } catch { /* nicht überall erlaubt */ }
        onLong(id);
      }, ms);
      st.current = { id, x: e.clientX, y: e.clientY, t, fired: false };
    },
    onPointerMove: (e: ReactPointerEvent) => {
      const s = st.current;
      if (s && !s.fired && Math.hypot(e.clientX - s.x, e.clientY - s.y) > 10) end(false);
    },
    onPointerUp: () => end(false),
    onPointerCancel: () => end(false),
    onContextMenu: (e: { preventDefault: () => void }) => e.preventDefault(),   // Android-Menü beim Halten
  });

  const suppressed = (id: number) => {
    const now = Date.now();
    return now < suppress.current.any || (suppress.current.id === id && now < suppress.current.until);
  };
  return { bind, suppressed };
}
