// Kamera-Zoom für den Scan: Handy höher halten und hineinzoomen gibt dasselbe Bild, aber das Licht
// des Blitz-Tests fällt mit dem Abstand² ab (sanfter, weniger überbelichtet), und bei ~40 cm liegen
// alle sechs Karten eines 3×2-Rasters zugleich im Messbereich (3–14°, lib/flashCheck).
// Chrome auf Android meldet Zoom als Fähigkeit der Kamera, aber nur, wenn getUserMedia danach gefragt
// hat (video: { zoom: true }). Gezoomt wird ein Ausschnitt des Bildsensors — die Hauptkamera hat weit
// mehr Pixel als die 1920×1080 des Scans, 2× sollte keine Schärfe kosten. Manche Geräte zählen den
// Zoom in 100ern — darum alles relativ zum Zoom beim Start.

export const ZOOM_KEY = "scan_zoom";         // gewählte Zoom-Stufe (localStorage, je Browser)
export const ZOOM_STEPS = [1, 1.5, 2, 3];    // × gegenüber dem Start der Kamera
export type ZoomCaps = { min: number; max: number; base: number };

/** Kann die Kamera zoomen (mindestens 1.5×)? base: Zoom beim Start (meist 1; manche Geräte zählen in 100ern). */
export function readZoomCaps(track: MediaStreamTrack): ZoomCaps | null {
  const z = (track.getCapabilities?.() as { zoom?: { min?: number; max?: number } } | undefined)?.zoom;
  if (!z || z.min == null || z.max == null || !(z.max > z.min)) return null;
  const cur = (track.getSettings?.() as { zoom?: number } | undefined)?.zoom;
  const base = cur && cur > 0 ? cur : z.min <= 1 && z.max >= 1 ? 1 : z.min;
  return z.max >= base * 1.5 - 1e-6 ? { min: z.min, max: z.max, base } : null;
}

export const storedZoom = (): number => {
  try { return Number(localStorage.getItem(ZOOM_KEY)) || 1; } catch { return 1; }
};
export const fmtZoom = (z: number) => String(Math.round(z * 10) / 10);

/** Lampe schalten — mit dem gewählten Zoom im selben Satz, damit er beim Schalten sicher bleibt. */
export const lightSet = (track: MediaStreamTrack, on: boolean, zoom: number | null) =>
  track.applyConstraints({ advanced: [{ torch: on, ...(zoom != null ? { zoom } : {}) } as MediaTrackConstraintSet] });
