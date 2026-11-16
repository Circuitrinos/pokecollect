// Live-Scan: Folie mit der Taschenlampe des Handys. Bevor die Prüfung aufgeht, blinkt die
// Lampe ein paar Mal, während das Handy ruhig gehalten wird. Je Umschalten zwei Bilder kurz
// hintereinander (Lampe aus / an, vor dem Nachregeln der Belichtung): Papier wird überall um
// denselben Anteil heller, Folie wirft das Licht stellenweise viel stärker zurück. Gemessen
// wird, welcher Anteil des Bildfensters deutlich stärker aufhellt als die Karte im Mittel,
// weniger derselbe Anteil im Rest der Karte: Holo > 0, Reverse Holo < 0, Normal ≈ 0.
// Weil beide Bilder mit demselben Umriss ausgeschnitten werden, heben sich Druck und kleine
// Umriss-Fehler auf (die Live-Folie ohne Lampe scheiterte genau daran).
//
// Brians Video (16.11., sechsmal Ethan's Adventure, Lampe von Hand, 20 Umschalter je Karte;
// Median je Karte, mit Hülle / ohne): Normal +0.00 / +0.03, Holo +0.15 / +0.48, Reverse Holo
// −0.28 / −0.08 — gleich bei 40×55 Bildpunkten je Karte und mit 5 % Umriss-Fehler. Dasselbe
// Video im Browser mit der Bewegungsverfolgung der App (6 Umschalter in 5 s, Kamera um ein
// Fünftel des Bilds bewegt): Normal +0.03 / +0.07, Holo +0.27 / +0.40, Reverse Holo −0.29 / +0.01.
// Holo trennt sich deutlich; Reverse Holo ohne Hülle ist zu schwach (dafür gibt es die
// umgekehrte Set-Code-Box im Foto). Vorerst nur gemessen und protokolliert („live.flash.*").
// Reine Rechnung ohne DOM, damit sie sich für sich prüfen lässt.
import { ART_BOX } from "./liveFoil";

export const FLASH_W = 40;
export const FLASH_H = 55;
export const FLASH_SWITCHES = 8;      // so oft schaltet die Lampe (an, aus, …), endet aus
export const FLASH_STEP_MS = 600;     // so lange bleibt sie je Zustand
const BORDER = 0.05;                  // Außenrand der Karte: zählt nicht
const EDGE = 0.03;                    // Abstand zur Kante des Bildfensters (Umriss-Fehler)
const STAMP = { x0: 0.74, y0: 0.4, y1: 0.56 };   // Play!-Stempel unten rechts im Bild: keine Folie
const CLIP = 250;                     // überstrahlt (Spiegelung): nicht messbar
const LIFT = 0.15;                    // so viel stärker (log) als die Karte im Mittel: „hellt deutlich auf"
const SWITCH = 0.03;                  // so viel ändert sich die mittlere Helligkeit beim Umschalten mindestens
const SETTLE_MS = 350;                // so lange nach einem Umschalten regelt die Kamera nach (kein neues)

const LIN = new Float32Array(256).map((_, i) => {
  const c = i / 255;
  return c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
});

type Masks = { art: Uint8Array; frame: Uint8Array };
const maskCache = new Map<string, Masks>();

function masks(w: number, h: number): Masks {
  const key = `${w}x${h}`;
  const hit = maskCache.get(key);
  if (hit) return hit;
  const art = new Uint8Array(w * h), frame = new Uint8Array(w * h);
  for (let y = 0; y < h; y++) {
    for (let x = 0; x < w; x++) {
      const u = (x + 0.5) / w, v = (y + 0.5) / h, i = y * w + x;
      if (u < BORDER || u > 1 - BORDER || v < BORDER || v > 1 - BORDER) continue;
      const core = u > ART_BOX.x0 + EDGE && u < ART_BOX.x1 - EDGE && v > ART_BOX.y0 + EDGE && v < ART_BOX.y1 - EDGE;
      const near = u > ART_BOX.x0 - EDGE && u < ART_BOX.x1 + EDGE && v > ART_BOX.y0 - EDGE && v < ART_BOX.y1 + EDGE;
      const stamp = u > STAMP.x0 && v > STAMP.y0 && v < STAMP.y1;
      if (core && !stamp) art[i] = 1;
      else if (!near) frame[i] = 1;
    }
  }
  const m = { art, frame };
  maskCache.set(key, m);
  return m;
}

const median = (a: number[]) => {
  if (!a.length) return NaN;
  const s = [...a].sort((x, y) => x - y);
  return s.length % 2 ? s[s.length >> 1] : (s[s.length / 2 - 1] + s[s.length / 2]) / 2;
};

/** Ein Umschalten, eine Karte (RGBA w×h, Lampe aus / an) → Anteil Bild − Anteil Rahmen, die
 *  deutlich stärker aufhellen als die Karte im Mittel; null, wenn zu viel überstrahlt ist. */
export function flashShare(off: Uint8ClampedArray, on: Uint8ClampedArray, w = FLASH_W, h = FLASH_H): number | null {
  const { art, frame } = masks(w, h);
  const n = w * h;
  const gain = new Float32Array(n).fill(NaN);
  const all: number[] = [];
  let nArt = 0, nFrame = 0;
  for (let i = 0; i < n; i++) {
    if (!art[i] && !frame[i]) continue;
    const o = 4 * i;
    if (Math.max(off[o], off[o + 1], off[o + 2]) >= CLIP || Math.max(on[o], on[o + 1], on[o + 2]) >= CLIP) continue;
    const a = 0.2126 * LIN[off[o]] + 0.7152 * LIN[off[o + 1]] + 0.0722 * LIN[off[o + 2]];
    const b = 0.2126 * LIN[on[o]] + 0.7152 * LIN[on[o + 1]] + 0.0722 * LIN[on[o + 2]];
    if (a <= 0.01) continue;
    gain[i] = Math.log(Math.max(b, 1e-3) / a);
    all.push(gain[i]);
    if (art[i]) nArt++; else nFrame++;
  }
  let artTotal = 0, frameTotal = 0;
  for (let i = 0; i < n; i++) { artTotal += art[i]; frameTotal += frame[i]; }
  if (nArt < 0.3 * artTotal || nFrame < 0.3 * frameTotal) return null;
  const base = median(all);
  let upArt = 0, upFrame = 0;
  for (let i = 0; i < n; i++) {
    if (!Number.isFinite(gain[i]) || gain[i] - base <= LIFT) continue;
    if (art[i]) upArt++; else upFrame++;
  }
  return upArt / nArt - upFrame / nFrame;
}

/** Wo die Lampe umgeschaltet hat (mittlere Helligkeit je Bild, Zeit je Bild in ms) → Bildpaare
 *  [aus, an]: das Bild vor dem Sprung und das nach dem Sprungbild (das kann halb belichtet sein).
 *  Danach regelt die Kamera die Belichtung nach (in Brians Video ab dem 3.–4. Bild, ~0.2 s) —
 *  das ist kein Umschalten: bis SETTLE_MS nach einem Sprung zählt keiner. */
export function flashPairs(means: number[], times?: number[]): [number, number][] {
  const at = (i: number) => (times ? times[i] : i * 33);
  const out: [number, number][] = [];
  let quietUntil = -Infinity;
  for (let i = 1; i < means.length - 1; i++) {
    if (at(i) < quietUntil) continue;
    const before = means[i - 1], step = means[i] - before;
    if (Math.abs(step) < SWITCH * Math.max(before, 1)) continue;
    const after = means[i + 1] - before;
    if (Math.sign(after) !== Math.sign(step) || Math.abs(after) < Math.abs(step) * 0.8) continue;
    out.push(step > 0 ? [i - 1, i + 1] : [i + 1, i - 1]);
    quietUntil = at(i) + SETTLE_MS;
  }
  return out;
}

/** Mittlere Helligkeit der Kartenausschnitte eines Bilds (zum Finden der Umschalter). */
export function patchMean(rgba: Uint8ClampedArray): number {
  let s = 0;
  for (let i = 0; i < rgba.length; i += 4) s += rgba[i] + rgba[i + 1] + rgba[i + 2];
  return s / (rgba.length / 4) / 3;
}

/** Karte i aus einem Atlas (Karten nebeneinander, je FLASH_W×FLASH_H). */
export function atlasPatch(atlas: Uint8ClampedArray, i: number, cards: number): Uint8ClampedArray {
  const out = new Uint8ClampedArray(FLASH_W * FLASH_H * 4);
  const aw = cards * FLASH_W;
  for (let y = 0; y < FLASH_H; y++) out.set(atlas.subarray((y * aw + i * FLASH_W) * 4, (y * aw + (i + 1) * FLASH_W) * 4), y * FLASH_W * 4);
  return out;
}

export type FlashResult = { share: number; n: number; all: number[] };

export function summarize(shares: number[]): FlashResult | null {
  if (!shares.length) return null;
  return { share: median(shares), n: shares.length, all: shares.map((s) => Math.round(s * 1000) / 1000) };
}

/** Ausschnitt FLASH_W×FLASH_H aus einem Bild: affine Abbildung aus drei Ecken (TL, TR, BL; Pixel),
 *  als Canvas-Transform Bild → Ausschnitt. */
export function flashTransform(quad: number[][]): [number, number, number, number, number, number] | null {
  const [tl, tr, , bl] = quad;
  const a = (tr[0] - tl[0]) / FLASH_W, c = (bl[0] - tl[0]) / FLASH_H, e = tl[0];
  const b = (tr[1] - tl[1]) / FLASH_W, d = (bl[1] - tl[1]) / FLASH_H, f = tl[1];
  const det = a * d - b * c;
  if (!det || !Number.isFinite(det)) return null;
  const ia = d / det, ib = -b / det, ic = -c / det, id = a / det;
  return [ia, ib, ic, id, -(ia * e + ic * f), -(ib * e + id * f)];
}
