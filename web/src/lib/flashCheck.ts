// Live-Scan: Folie mit der Taschenlampe des Handys. Sind alle Karten erkannt, blinkt die Lampe,
// während das Handy über die Karten geführt wird (lib/flashCapture). Je Umschalten zwei Bilder kurz
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
// umgekehrte Set-Code-Box im Foto). Protokolliert wird je Karte „live.flash.final".
// Reine Rechnung ohne DOM, damit sie sich für sich prüfen lässt.
import { ART_BOX } from "./liveFoil";

export const FLASH_W = 40;
export const FLASH_H = 55;
export const FLASH_STEP_MS = 400;     // so lange bleibt die Lampe je Zustand (an, aus, …)
// Die Folie wirft das Licht nur zurück, wenn die Karte fast gerade unter dem Handy liegt (Brian:
// „nicht aus jedem Winkel"). In seinem Video, je Umschalten und Karte nach dem Blickwinkel: bis 14°
// Holo +0.45, Reverse Holo −0.22; darüber Holo +0.00…+0.07, Reverse Holo −0.02…−0.05 — die Folie
// ist dann von Papier nicht zu unterscheiden. Unter 3° liegt der Glanzpunkt von Hülle und Lack auf
// der Karte (eine normale Karte maß dort einmal +0.60). Gezählt wird darum nur 3–14°: Normal
// −0.03…+0.06, Holo +0.10…+0.76, Reverse Holo −0.34…+0.01 (47 von 54 einzelnen Umschaltern
// richtig bei ±0.10). Beim Blinken das Handy langsam über die Karten führen.
export const FLASH_MIN_DEG = 3;
export const FLASH_MAX_DEG = 14;
const FOCAL = 0.73;                   // Brennweite ≈ 0.73 × lange Bildseite (Hauptkamera ~26 mm KB)
const CARD_MM = [63, 88];
const BORDER = 0.05;                  // Außenrand der Karte: zählt nicht
const EDGE = 0.03;                    // Abstand zur Kante des Bildfensters (Umriss-Fehler)
const STAMP = { x0: 0.74, y0: 0.4, y1: 0.56 };   // Play!-Stempel unten rechts im Bild: keine Folie
const CLIP = 250;                     // überstrahlt (Spiegelung): nicht messbar
const LIFT = 0.15;                    // so viel stärker (log) als die Karte im Mittel: „hellt deutlich auf"
const SWITCH = 0.03;                  // so viel ändert sich die mittlere Helligkeit beim Umschalten mindestens
const SETTLE_MS = 250;                // so lange nach einem Umschalten regelt die Kamera nach (kein neues)

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

/** Helligkeit der Karte (Median der Luma über Bild und Rahmen, sRGB 0..255; fürs Protokoll) — gleich,
 *  ob ein Browser die Farbebenen vertauscht (lib/cameraColors). */
export function litLevel(px: Uint8ClampedArray, w = FLASH_W, h = FLASH_H): number {
  const { art, frame } = masks(w, h);
  const v: number[] = [];
  for (let i = 0; i < w * h; i++) {
    if (!art[i] && !frame[i]) continue;
    const o = 4 * i;
    v.push(0.299 * px[o] + 0.587 * px[o + 1] + 0.114 * px[o + 2]);
  }
  return median(v);
}

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

/**
 * Umschalten der Lampe im laufenden Kamerabild finden: Bild für Bild die mittlere Helligkeit.
 * Ein Sprung zwischen zwei Bildern, der im nächsten Bild bleibt, ist ein Umschalten → Paar aus
 * dem Bild vor dem Sprung und dem nach dem Sprungbild (das kann halb belichtet sein).
 * Danach regelt die Kamera die Belichtung nach (in Brians Video ab dem 3.–4. Bild, ~0.2 s) —
 * das ist kein Umschalten: bis SETTLE_MS nach einem Sprung zählt keiner.
 */
export class SwitchFinder {
  private means: number[] = [];
  private times: number[] = [];
  private quietUntil = -Infinity;
  private count = 0;

  /** Neues Bild (Nr. = Anzahl der Bilder davor) → [aus, an] als Bild-Nummern, sobald ein Umschalten fertig ist. */
  push(mean: number, t: number): { off: number; on: number; lightOn: boolean } | null {
    this.means.push(mean);
    this.times.push(t);
    if (this.means.length > 3) { this.means.shift(); this.times.shift(); }
    const k = this.count++;
    if (this.means.length < 3) return null;
    const [before, mid, after] = this.means;
    if (this.times[1] < this.quietUntil) return null;
    const step = mid - before, total = after - before;
    if (Math.abs(step) < SWITCH * Math.max(before, 1)) return null;
    if (Math.sign(total) !== Math.sign(step) || Math.abs(total) < Math.abs(step) * 0.8) return null;
    this.quietUntil = this.times[1] + SETTLE_MS;
    return step > 0 ? { off: k - 2, on: k, lightOn: true } : { off: k, on: k - 2, lightOn: false };
  }

  reset(): void {
    this.means = [];
    this.times = [];
    this.quietUntil = -Infinity;
  }
}

/** Alle Umschalter einer aufgenommenen Folge (Helligkeit je Bild, Zeit je Bild in ms) → [aus, an]. */
export function flashPairs(means: number[], times?: number[]): [number, number][] {
  const f = new SwitchFinder();
  const out: [number, number][] = [];
  means.forEach((m, i) => {
    const p = f.push(m, times ? times[i] : i * 33);
    if (p) out.push([p.off, p.on]);
  });
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

export type FlashResult = { share: number; n: number; all: number[]; deg?: number[] };

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

/** 4 Punkte → Homographie (8 Unbekannte, Gauß mit Pivot). */
function homography(src: number[][], dst: number[][]): number[][] | null {
  const A: number[][] = [];
  for (let i = 0; i < 4; i++) {
    const [x, y] = src[i], [u, v] = dst[i];
    A.push([x, y, 1, 0, 0, 0, -u * x, -u * y, u]);
    A.push([0, 0, 0, x, y, 1, -v * x, -v * y, v]);
  }
  for (let c = 0; c < 8; c++) {
    let p = c;
    for (let r = c + 1; r < 8; r++) if (Math.abs(A[r][c]) > Math.abs(A[p][c])) p = r;
    if (Math.abs(A[p][c]) < 1e-12) return null;
    [A[c], A[p]] = [A[p], A[c]];
    for (let r = 0; r < 8; r++) {
      if (r === c) continue;
      const k = A[r][c] / A[c][c];
      for (let j = c; j < 9; j++) A[r][j] -= k * A[c][j];
    }
  }
  const h = A.map((row, i) => row[8] / row[i]);
  return [[h[0], h[1], h[2]], [h[3], h[4], h[5]], [h[6], h[7], 1]];
}

/** Blickwinkel auf eine Karte (Grad): zwischen ihrer Senkrechten und der Richtung zur Kamera, aus
 *  ihrem Umriss im Bild (TL, TR, BR, BL; Pixel eines Bilds w×h) und der Kartengröße. */
export function viewAngle(quad: number[][], w: number, h: number, focal = FOCAL * Math.max(w, h)): number | null {
  const [cw, ch] = CARD_MM;
  const H = homography([[0, 0], [cw, 0], [cw, ch], [0, ch]], quad.map(([x, y]) => [(x - w / 2) / focal, (y - h / 2) / focal]));
  if (!H) return null;
  const c1 = [H[0][0], H[1][0], H[2][0]], c2 = [H[0][1], H[1][1], H[2][1]], c3 = [H[0][2], H[1][2], H[2][2]];
  const lam = 1 / Math.hypot(...c1);
  const r1 = c1.map((v) => v * lam), r2 = c2.map((v) => v * lam), t = c3.map((v) => v * lam);
  const r3 = [r1[1] * r2[2] - r1[2] * r2[1], r1[2] * r2[0] - r1[0] * r2[2], r1[0] * r2[1] - r1[1] * r2[0]];
  // Kamera in Kartenkoordinaten: −Rᵀ·t (Spalten von R: r1, r2, r3)
  const cam = [r1, r2, r3].map((r) => -(r[0] * t[0] + r[1] * t[1] + r[2] * t[2]));
  const d = [cam[0] - cw / 2, cam[1] - ch / 2, cam[2]];
  const len = Math.hypot(...d);
  return len ? (Math.acos(Math.min(1, Math.abs(d[2]) / len)) * 180) / Math.PI : null;
}

// ── Urteil je Karte ─────────────────────────────────────────────────────────

export type FlashKind = "Holo" | "Reverse Holo" | "Normal";
// Vorläufig, aus Brians einem Versuch (16.11., 3–14°, je Umschalten): Normal −0.03…+0.06,
// Holo +0.10…+0.76, Reverse Holo −0.34…+0.01. Die Protokolle („live.flash.final") eichen es nach.
export const FLASH_MIN_N = 3;         // so viele gezählte Umschalter mindestens
const FLASH_CUT = 0.12;               // Median darüber: Holo, darunter (negativ): Reverse Holo
const FLASH_FLAT = 0.05;              // Median so nah an 0 (ab 4 Umschaltern): Normal
const FLASH_LATE_N = 8;               // danach entscheidet die Seite (±0.06), sonst Normal
const FLASH_LATE_CUT = 0.06;

/** Normal / Holo / Reverse Holo aus den gezählten Umschaltern einer Karte — null: noch offen. */
export function flashKind(shares: number[]): FlashKind | null {
  const n = shares.length;
  if (n < FLASH_MIN_N) return null;
  const m = median(shares);
  if (m >= FLASH_CUT) return "Holo";
  if (m <= -FLASH_CUT) return "Reverse Holo";
  if (n >= 4 && Math.abs(m) <= FLASH_FLAT) return "Normal";
  if (n >= FLASH_LATE_N) return m >= FLASH_LATE_CUT ? "Holo" : m <= -FLASH_LATE_CUT ? "Reverse Holo" : "Normal";
  return null;
}

/**
 * Holo nur, wenn es auch beim Ausschalten so aussieht. Beim Einschalten ist die Belichtung noch auf
 * das dunkle Bild gestellt: das erste helle Bild ist überbelichtet, die Kamera staucht die hellen
 * Stellen (Schulter der Tonkurve) — der helle Rahmen legt weniger zu als das dunklere Bild, und auch
 * eine normale Karte sieht nach Holo aus. Beim Ausschalten ist das helle Bild das nachgeregelte.
 * Brians Brave-Test (16.11.): alle sechs „Holo"; im Kamerabild springt die Karte beim Einschalten
 * von Median 157 auf 204 (90 %: 175 → 228) und regelt in ~0.3 s auf 163 zurück. Nachgestellt an den
 * echten Paaren (Lampe ×2.2, Schulter ab 40 %): Einschalten normal +0.29/+0.35, Ausschalten
 * +0.01/+0.05; Holo beim Ausschalten so gefilmt +0.12/+0.50.
 */
const FLASH_OFF_HOLO = 0.06;
const FLASH_OFF_MIN_N = 2;

/** Wie flashKind, mit Richtung je Umschalten (true: Lampe ging an). */
export function flashKindDir(shares: number[], lightOn: boolean[]): FlashKind | null {
  const k = flashKind(shares);
  if (k !== "Holo") return k;
  const offs = shares.filter((_, i) => !lightOn[i]);
  if (offs.length < FLASH_OFF_MIN_N) return null;
  return median(offs) >= FLASH_OFF_HOLO ? "Holo" : flashKind(offs);
}

/** Gezählte Umschalter je Karte (Schlüssel der Live-Karte). */
export class FlashTally {
  private m = new Map<string, { s: number[]; deg: number[]; on: boolean[] }>();

  add(key: string, share: number, deg: number, lightOn = false): void {
    const e = this.m.get(key) ?? { s: [], deg: [], on: [] };
    e.s.push(share);
    e.deg.push(Math.round(deg * 10) / 10);
    e.on.push(lightOn);
    this.m.set(key, e);
  }

  n(key: string): number {
    return this.m.get(key)?.s.length ?? 0;
  }

  kind(key: string): FlashKind | null {
    const e = this.m.get(key);
    return e ? flashKindDir(e.s, e.on) : null;
  }

  result(key: string): (FlashResult & { kind: FlashKind | null; offShare: number | null }) | null {
    const e = this.m.get(key);
    const r = e ? summarize(e.s) : null;
    if (!r || !e) return null;
    const offs = e.s.filter((_, i) => !e.on[i]);
    const offShare = offs.length ? Math.round(median(offs) * 1000) / 1000 : null;
    return { ...r, deg: e.deg, kind: flashKindDir(e.s, e.on), offShare };
  }

  forget(keep: Set<string>): void {
    for (const k of Array.from(this.m.keys())) if (!keep.has(k)) this.m.delete(k);
  }

  clear(): void {
    this.m.clear();
  }
}
