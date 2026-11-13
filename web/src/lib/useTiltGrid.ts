"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { deviceToEarth, earthToCamera, IDENTITY, relativeTurn, type Mat3 } from "./tiltGrid";

export type TiltState = "off" | "starting" | "on" | "unavailable" | "denied";

type OrientationEventCtor = typeof DeviceOrientationEvent & {
  requestPermission?: () => Promise<"granted" | "denied" | "default">;
};

function screenAngle(): number {
  const a = window.screen?.orientation?.angle ?? (window as unknown as { orientation?: number }).orientation ?? 0;
  return ((Number(a) % 360) + 360) % 360;
}

let granted = false;          // motion access given in this page session (iOS asks only on a tap)

const SMOOTH = 0.35;          // share of a new sensor reading (the rest: the previous one) — calms jitter
const NO_SENSOR_MS = 1500;    // no reading by then: no orientation sensor (desktop, blocked)

/**
 * The phone's turn since "straight", for the tilted binder grid (lib/tiltGrid).
 * start() from a tap may ask for motion access (iOS, newer Chrome);
 * start(true) — on its own, e.g. remembered from last time — stays quiet and
 * just stays off if that isn't possible without a tap. The moment
 * it starts (and every level()) counts as straight; turning the screen
 * between portrait and landscape starts over from straight.
 */
export function useTiltGrid() {
  const [state, setState] = useState<TiltState>("off");
  const [turn, setTurn] = useState<Mat3>(IDENTITY);
  const ref = useRef<Mat3 | null>(null);
  const now = useRef<Mat3 | null>(null);
  const angle = useRef<number | null>(null);
  const shown = useRef<Mat3>(IDENTITY);
  const handler = useRef<((e: DeviceOrientationEvent) => void) | null>(null);
  const frame = useRef(0);
  const timer = useRef<number | null>(null);

  const detach = useCallback(() => {
    if (handler.current) window.removeEventListener("deviceorientation", handler.current);
    handler.current = null;
    if (frame.current) cancelAnimationFrame(frame.current);
    frame.current = 0;
    if (timer.current !== null) window.clearTimeout(timer.current);
    timer.current = null;
  }, []);

  const show = useCallback(() => {
    frame.current = 0;
    if (!ref.current || !now.current) return;
    const m = relativeTurn(now.current, ref.current);
    if (m.some((v, i) => Math.abs(v - shown.current[i]) > 0.0015)) {
      shown.current = m;
      setTurn(m);
    }
  }, []);

  const start = useCallback(async (quiet = false) => {
    if (typeof window === "undefined" || !("DeviceOrientationEvent" in window)) {
      setState(quiet ? "off" : "unavailable");
      return;
    }
    const Ctor = window.DeviceOrientationEvent as OrientationEventCtor;
    if (typeof Ctor.requestPermission === "function" && !granted) {
      setState("starting");
      try {
        if ((await Ctor.requestPermission()) !== "granted") { setState(quiet ? "off" : "denied"); return; }
        granted = true;
      } catch {
        setState(quiet ? "off" : "denied");   // iOS without a tap: just stay off
        return;
      }
    }
    detach();
    ref.current = null;
    now.current = null;
    angle.current = null;
    shown.current = IDENTITY;
    setTurn(IDENTITY);
    setState("starting");
    const onReading = (e: DeviceOrientationEvent) => {
      if (e.alpha == null || e.beta == null || e.gamma == null) return;
      const a = screenAngle();
      const m = earthToCamera(deviceToEarth(e.alpha, e.beta, e.gamma), a);
      if (a !== angle.current || !ref.current || !now.current) {   // first reading, or the screen turned
        angle.current = a;
        ref.current = m;
        now.current = m;
        shown.current = IDENTITY;
        setTurn(IDENTITY);
        setState("on");
        return;
      }
      const prev = now.current;
      now.current = prev.map((v, i) => v + SMOOTH * (m[i] - v));
      if (!frame.current) frame.current = requestAnimationFrame(show);
    };
    handler.current = onReading;
    window.addEventListener("deviceorientation", onReading);
    timer.current = window.setTimeout(() => {
      timer.current = null;
      if (!ref.current) { detach(); setState(quiet ? "off" : "unavailable"); }
    }, NO_SENSOR_MS);
  }, [detach, show]);

  const stop = useCallback(() => {
    detach();
    ref.current = null;
    now.current = null;
    shown.current = IDENTITY;
    setTurn(IDENTITY);
    setState("off");
  }, [detach]);

  /** The phone's current position counts as straight from now on. */
  const level = useCallback(() => {
    if (!now.current) return;
    ref.current = now.current;
    shown.current = IDENTITY;
    setTurn(IDENTITY);
  }, []);

  useEffect(() => detach, [detach]);

  return { state, turn, start, stop, level };
}
