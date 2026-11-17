// Kamera-Zoom für den Scan: Handy höher halten und hineinzoomen gibt dasselbe Bild, aber das Licht
// des Blitz-Tests fällt mit dem Abstand² ab (sanfter, weniger überbelichtet), und bei ~40 cm liegen
// alle sechs Karten eines 3×2-Rasters zugleich im Messbereich (3–14°, lib/flashCheck).
// Chrome auf Android meldet Zoom als Fähigkeit der Kamera, aber nur, wenn getUserMedia danach gefragt
// hat (video: { zoom: true }). Gezoomt wird ein Ausschnitt des Bildsensors — die Hauptkamera hat weit
// mehr Pixel als die 1920×1080 des Scans, 2× sollte keine Schärfe kosten. Manche Geräte zählen den
// Zoom in 100ern — darum alles relativ zum Zoom beim Start.

export const ZOOM_KEY = "scan_zoom";         // gewählte Zoom-Stufe (localStorage, je Browser)
export const ZOOM_STEPS = [1, 1.5, 2, 3];    // × gegenüber dem Start der Kamera
export const ZOOM_DEAD_KEY = "scan_zoom_dead"; // Kamera (Name), bei der Zoom im Bild nichts ändert
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

// ---- Hat der Zoom gewirkt? ----
// Brians Fairphone 6 (/e/OS-Browser, 16.11.): Zoom wird gemeldet und angenommen, aber das Bild bleibt
// gleich (Bildmaßstab von Bild zu Bild höchstens 1.008 rund um jedes Tippen). Darum am Bild selbst
// nachsehen: kleines Graubild vorher und nachher; passt das Nachher besser zum vorherigen Bild in der
// Mitte um r vergrößert (r = neuer / alter Zoom) als zum unveränderten? Die Hand bewegt sich dabei:
// Verschiebungen bis ±12 % werden mitgesucht.

export const THUMB_W = 96;
export type Thumb = { px: Float32Array; w: number; h: number };

/** Graubild (Luma) des Kamerabilds, THUMB_W breit — null, solange noch kein Bild da ist. */
export function grayThumb(video: HTMLVideoElement, w = THUMB_W): Thumb | null {
  if (!video.videoWidth || !video.videoHeight) return null;
  const h = Math.max(8, Math.round((w * video.videoHeight) / video.videoWidth));
  const c = document.createElement("canvas");
  c.width = w;
  c.height = h;
  const ctx = c.getContext("2d", { willReadFrequently: true });
  if (!ctx) return null;
  ctx.drawImage(video, 0, 0, w, h);
  const d = ctx.getImageData(0, 0, w, h).data;
  const px = new Float32Array(w * h);
  for (let i = 0; i < w * h; i++) px[i] = 0.299 * d[4 * i] + 0.587 * d[4 * i + 1] + 0.114 * d[4 * i + 2];
  return { px, w, h };
}

const bilinear = (a: Float32Array, w: number, h: number, x: number, y: number): number => {
  const x0 = Math.max(0, Math.min(w - 2, Math.floor(x))), y0 = Math.max(0, Math.min(h - 2, Math.floor(y)));
  const fx = Math.max(0, Math.min(1, x - x0)), fy = Math.max(0, Math.min(1, y - y0));
  const i = y0 * w + x0;
  return (a[i] * (1 - fx) + a[i + 1] * fx) * (1 - fy) + (a[i + w] * (1 - fx) + a[i + w + 1] * fx) * fy;
};

/** Wie gut passt `after` zu `before`, in der Mitte um r vergrößert (beste Verschiebung, Korrelation −1…1). */
export function zoomScore(before: Thumb, after: Thumb, r: number): number {
  const { w, h } = after;
  const cx = (w - 1) / 2, cy = (h - 1) / 2;
  const m = Math.round(Math.min(w, h) * 0.18);          // Rand frei: dort fehlt nach dem Verschieben Bild
  const S = Math.round(w * 0.12);
  let best = -1;
  for (let dy = -S; dy <= S; dy += 2) {
    for (let dx = -S; dx <= S; dx += 2) {
      let n = 0, sa = 0, sb = 0, saa = 0, sbb = 0, sab = 0;
      for (let y = m; y < h - m; y += 2) {
        for (let x = m; x < w - m; x += 2) {
          const bx = cx + (x - cx - dx) / r, by = cy + (y - cy - dy) / r;
          if (bx < 0 || by < 0 || bx > w - 1 || by > h - 1) continue;
          const p = bilinear(before.px, w, h, bx, by), q = after.px[y * w + x];
          n++; sa += p; sb += q; saa += p * p; sbb += q * q; sab += p * q;
        }
      }
      if (n < 50) continue;
      const va = saa - (sa * sa) / n, vb = sbb - (sb * sb) / n;
      if (va <= 1e-6 || vb <= 1e-6) continue;
      const c = (sab - (sa * sb) / n) / Math.sqrt(va * vb);
      if (c > best) best = c;
    }
  }
  return best;
}

/**
 * true: das Bild ist um r gezoomt; false: es ist gleich geblieben; null: nicht zu sagen (zu viel Bewegung).
 * An Brians Video (16.11.): ohne Zoom „gleich" 0.55–0.81 gegen „gezoomt" 0.10–0.48; ein nachgestellter
 * Zoom (Mitte vergrößert, auch mit verschobener Hand) „gezoomt" 0.36–0.73 gegen „gleich" 0.07–0.19.
 */
export function zoomVerdict(before: Thumb, after: Thumb, r: number): { took: boolean | null; same: number; zoomed: number } {
  // herauszoomen: dann ist das vorherige Bild die vergrößerte Mitte des neuen
  const same = zoomScore(before, after, 1), zoomed = r >= 1 ? zoomScore(before, after, r) : zoomScore(after, before, 1 / r);
  const took = zoomed >= 0.3 && zoomed - same >= 0.1 ? true : same >= 0.4 && same - zoomed >= 0.1 ? false : null;
  return { took, same: Math.round(same * 1000) / 1000, zoomed: Math.round(zoomed * 1000) / 1000 };
}

/**
 * Nach dem Umstellen des Zooms im Kamerabild nachsehen (bis 1.5 s): „gezoomt" zählt sofort, „gleich"
 * erst ab 0.9 s (manche Kameras zoomen erst nach ein paar Bildern).
 */
export async function checkZoom(video: HTMLVideoElement, before: Thumb, r: number): Promise<{ took: boolean | null; same: number; zoomed: number }> {
  let last: { took: boolean | null; same: number; zoomed: number } = { took: null, same: 0, zoomed: 0 };
  const t0 = performance.now();
  for (const at of [300, 500, 700, 900, 1100, 1300, 1500]) {
    const wait = t0 + at - performance.now();
    if (wait > 0) await new Promise((res) => window.setTimeout(res, wait));
    const after = grayThumb(video, before.w);
    if (!after || after.h !== before.h) continue;
    const v = zoomVerdict(before, after, r);
    if (v.took === true || (v.took === false && at >= 900)) return v;
    if (v.took !== null) last = v;
  }
  return last;
}
