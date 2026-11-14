"use client";

import { useCallback, useEffect, useRef } from "react";

// Bewegungssensoren fürs Live-Scannen: Kreisel (Drehung) und Beschleunigung.
// Die Lage im Raum lässt sich daraus nicht verlässlich ablesen (die Beschleunigung
// zweimal aufaddiert driftet in Sekunden um Zentimeter) — wohl aber, ob das Handy
// gerade still gehalten wird: dann ist das nächste Bild sehr wahrscheinlich scharf.

type MotionCtor = typeof DeviceMotionEvent & {
  requestPermission?: () => Promise<"granted" | "denied" | "default">;
};

const TURN_DEG_S = 8;       // darüber dreht sich das Handy merklich
const SHAKE_M_S2 = 0.35;    // darüber wird es bewegt (ohne Schwerkraft)
const CALM_MS = 250;        // so lange ruhig = still

/**
 * still(): true/false, solange Sensorwerte kommen; null ohne Sensor (Rechner, gesperrt) —
 * dann entscheidet das Kamerabild allein. ask() aus einem Tipp heraus fragt (iOS) nach dem Zugriff.
 */
export function useMotion(active: boolean) {
  const lastMove = useRef(0);
  const lastReading = useRef(0);

  useEffect(() => {
    if (!active || typeof window === "undefined" || !("DeviceMotionEvent" in window)) return;
    const onMotion = (e: DeviceMotionEvent) => {
      const r = e.rotationRate, a = e.acceleration;
      if (!r && !a) return;
      const now = performance.now();
      lastReading.current = now;
      const turn = r ? Math.hypot(r.alpha ?? 0, r.beta ?? 0, r.gamma ?? 0) : 0;
      const shake = a ? Math.hypot(a.x ?? 0, a.y ?? 0, a.z ?? 0) : 0;
      if (turn > TURN_DEG_S || shake > SHAKE_M_S2) lastMove.current = now;
    };
    window.addEventListener("devicemotion", onMotion);
    return () => window.removeEventListener("devicemotion", onMotion);
  }, [active]);

  const still = useCallback((): boolean | null => {
    const now = performance.now();
    if (!lastReading.current || now - lastReading.current > 1000) return null;
    return now - lastMove.current > CALM_MS;
  }, []);

  const ask = useCallback(async () => {
    const Ctor = (typeof window !== "undefined" && "DeviceMotionEvent" in window
      ? window.DeviceMotionEvent : null) as MotionCtor | null;
    if (typeof Ctor?.requestPermission !== "function") return;
    try { await Ctor.requestPermission(); } catch { /* ohne Tipp oder abgelehnt: das Kamerabild reicht */ }
  }, []);

  return { still, ask };
}
