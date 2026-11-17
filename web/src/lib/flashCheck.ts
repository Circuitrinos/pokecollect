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
import { FLASH_GRID, FLASH_ROOM_SPLIT, FLASH_TABLE } from "./flashTable";

export const FLASH_W = 40;
export const FLASH_H = 55;
export const FLASH_STEP_MS = 600;     // so lange bleibt die Lampe je Zustand — Platz für Nachregeln (~0.25 s) und ruhige Bilder davor
// Wo die Kamera über der Karte steht, entscheidet, was gemessen wird — nicht nur der Winkel. Die Lampe
// sitzt an der Kamera; ihr Glanzpunkt (Lack, Hülle) und das Licht der Folie hängen davon ab, ob die
// Kamera über dem Bildfenster (oberer Teil der Karte) oder über dem unteren Rahmen steht. In Brians
// Video (v66) und in der daran angepassten Nachbildung (tools/flashsim): Holo mit Hülle misst +0.7,
// wenn die Kamera über der Kartenmitte steht, aber ±0.0, wenn sie 15° unterhalb steht; Reverse Holo
// misst −0.5 gerade darüber und ±0.0, wenn die Kamera oberhalb steht; eine normale Karte +0.6, wenn der
// Glanzpunkt auf dem Bildfenster liegt. Ein festes Winkelband mit festen Grenzen trennt das nicht.
// Darum: je Messung die Lage der Kamera zur Karte (tx, ty — Versatz / Höhe, aus dem Umriss) und
// für jede Folierung der dort erwartete Messwert (lib/flashTable, aus der Nachbildung); das Urteil
// ist die Folierung, deren Erwartung alle Messungen einer Karte am besten erklärt (flashCosts).
export const FLASH_MIN_DEG = 3;       // (nur noch fürs Protokoll / alte Auswertungen)
export const FLASH_MAX_DEG = 14;
/** So weit (Versatz / Höhe, je Richtung) darf die Kamera neben der Karte stehen — weiter weg sagt die Messung nichts. */
export const FLASH_REACH = 0.32;
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

/** Wie hell der Raum gegen die Lampe ist: Karte mit / ohne Lampe bei gleicher Belichtung (die Bilder
 *  direkt vor und nach einem Umschalten — die Kamera hat noch nicht nachgeregelt), lineare Helligkeit. */
export function flashRoom(dark: Uint8ClampedArray, lit: Uint8ClampedArray, w = FLASH_W, h = FLASH_H): number | null {
  const { art, frame } = masks(w, h);
  const a: number[] = [], b: number[] = [];
  for (let i = 0; i < w * h; i++) {
    if (!art[i] && !frame[i]) continue;
    const o = 4 * i;
    a.push(0.2126 * LIN[dark[o]] + 0.7152 * LIN[dark[o + 1]] + 0.0722 * LIN[dark[o + 2]]);
    b.push(0.2126 * LIN[lit[o]] + 0.7152 * LIN[lit[o + 1]] + 0.0722 * LIN[lit[o + 2]]);
  }
  const ma = median(a), mb = median(b);
  return ma > 0.002 ? mb / ma : null;
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

  /**
   * Neues Bild (Nr. = Anzahl der Bilder davor) → [aus, an] als Bild-Nummern, sobald ein Umschalten fertig ist.
   * expect: in welche Richtung gerade umgeschaltet wird (true: an) — ein Sprung in die andere Richtung
   * zählt dann nicht und sperrt auch nichts: die Kamera stellt die Belichtung 1–2 Bilder vor dem
   * Umschalten gegenläufig um (Fairphone: vor dem Einschalten dunkler, vor dem Ausschalten heller).
   */
  push(mean: number, t: number, expect?: boolean): { off: number; on: number; lightOn: boolean } | null {
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
    if (expect !== undefined && (step > 0) !== expect) return null;
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
/** Brennweite in Pixeln bei dieser Bildgröße — mit Zoom entsprechend länger (2× Zoom: doppelt). */
export function flashFocal(w: number, h: number, zoom = 1): number {
  return FOCAL * Math.max(w, h) * zoom;
}

/** Wo die Kamera über der Karte steht: Versatz / Höhe in Kartenkoordinaten (tx nach rechts, ty nach
 *  unten zur Kartenunterkante) und der Blickwinkel (Grad) — aus dem Umriss (TL, TR, BR, BL; Pixel eines Bilds w×h). */
export type CardCamera = { tx: number; ty: number; deg: number };

export function cardCamera(quad: number[][], w: number, h: number, focal = flashFocal(w, h)): CardCamera | null {
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
  const z = Math.abs(d[2]), len = Math.hypot(...d);
  if (!len || z < 1e-6) return null;
  return { tx: d[0] / z, ty: d[1] / z, deg: (Math.acos(Math.min(1, z / len)) * 180) / Math.PI };
}

export function viewAngle(quad: number[][], w: number, h: number, focal = flashFocal(w, h)): number | null {
  return cardCamera(quad, w, h, focal)?.deg ?? null;
}

/** Steht die Kamera nah genug über der Karte, dass eine Messung etwas sagt? */
export function flashReachable(p: { tx: number; ty: number } | null | undefined): boolean {
  return !!p && Math.abs(p.tx) <= FLASH_REACH && Math.abs(p.ty) <= FLASH_REACH;
}

// ── Urteil je Karte ─────────────────────────────────────────────────────────

export type FlashKind = "Holo" | "Reverse Holo" | "Normal";
const KINDS: FlashKind[] = ["Holo", "Normal", "Reverse Holo"];
/** Eine Messung: Messwert (flashShare), wo die Kamera stand (Mittel beider Phasen), wie hell der Raum
 *  gegen die Lampe ist (Karte mit / ohne Lampe bei gleicher Belichtung; null: unbekannt). */
export type FlashReading = { share: number; tx: number; ty: number; room: number | null };
// Urteil: je Folierung die Kosten aller Messungen (robust: log(1 + z²) + log sd, z = Abstand zur
// Erwartung in sd). Gilt, wenn die beste um FLASH_MARGIN vor der zweitbesten liegt, ab FLASH_MIN_N
// Messungen, und FLASH_HOLD Messungen lang so blieb. Eingestellt an der Nachbildung (6 Karten im Raster,
// Handy darüber, hell bis dunkel; je Folge in zufälliger Reihenfolge): 2.7 % falsch (vorher 16 %,
// dazu 58 % nach 12 Messungen noch offen); an Brians Video v66 von jedem Startpunkt: 62 richtig,
// 1 falsch, 18 offen nach 12 Messungen (vorher 28 / 0 / 53), im Mittel nach 7.4 Messungen.
export const FLASH_MIN_N = 3;         // so viele Messungen mindestens
export const FLASH_HOLD = 2;          // ein Urteil gilt, wenn es noch so viele Messungen später gleich ist
export const FLASH_TYPICAL_N = 7;     // so viele braucht es meist (Anzeige)
const FLASH_MARGIN = 1.5;             // Vorsprung der besten Folierung (Kosten)
const FLASH_TENTATIVE = 0.5;          // ab hier „vorläufig" (Anzeige)

/** Erwartung (mu, sd) je Folierung dort, wo die Kamera stand (bilinear in der Tabelle, außen: Rand). */
export function flashExpect(tx: number, ty: number, room: number | null): Record<FlashKind, { mu: number; sd: number }> {
  const tab = room != null && room >= FLASH_ROOM_SPLIT ? FLASH_TABLE.dark : FLASH_TABLE.bright;
  const { min, step, n } = FLASH_GRID;
  const fx = Math.min(n - 1.001, Math.max(0, (tx - min) / step)), fy = Math.min(n - 1.001, Math.max(0, (ty - min) / step));
  const i = Math.floor(fx), j = Math.floor(fy), ax = fx - i, ay = fy - j;
  const at = (a: number[]) => a[j * n + i] * (1 - ax) * (1 - ay) + a[j * n + i + 1] * ax * (1 - ay)
    + a[(j + 1) * n + i] * (1 - ax) * ay + a[(j + 1) * n + i + 1] * ax * ay;
  const out = {} as Record<FlashKind, { mu: number; sd: number }>;
  for (const k of KINDS) out[k] = { mu: at(tab[k].mu), sd: Math.max(0.06, at(tab[k].sd)) };
  return out;
}

/** Kosten je Folierung über alle Messungen (kleiner: passt besser). */
export function flashCosts(rs: FlashReading[]): Record<FlashKind, number> {
  const c = { Holo: 0, Normal: 0, "Reverse Holo": 0 } as Record<FlashKind, number>;
  for (const r of rs) {
    const e = flashExpect(r.tx, r.ty, r.room);
    for (const k of KINDS) {
      const z = (r.share - e[k].mu) / e[k].sd;
      c[k] += Math.log1p(z * z) + Math.log(e[k].sd);
    }
  }
  return c;
}

function best(c: Record<FlashKind, number>, margin: number): FlashKind | null {
  const o = [...KINDS].sort((a, b) => c[a] - c[b]);
  return c[o[1]] - c[o[0]] >= margin ? o[0] : null;
}

/** Vorläufig: die Folierung, die gerade am besten passt (mit kleinem Vorsprung) — null: offen. */
export function flashKind(rs: FlashReading[]): FlashKind | null {
  return rs.length >= 2 ? best(flashCosts(rs), FLASH_TENTATIVE) : null;
}

/** Das Urteil gilt, wenn es mit FLASH_MARGIN Vorsprung FLASH_HOLD Messungen lang dasselbe ist — neue Messungen können es kippen. */
export function flashSettled(rs: FlashReading[]): FlashKind | null {
  if (rs.length < FLASH_MIN_N || rs.length < FLASH_HOLD + 1) return null;
  let k: FlashKind | null = null;
  for (let m = rs.length - FLASH_HOLD; m <= rs.length; m++) {
    const b = best(flashCosts(rs.slice(0, m)), FLASH_MARGIN);
    if (!b || (k && b !== k)) return null;
    k = b;
  }
  return k;
}

/** Zwei Ausschnitte (RGBA) mitteln — die beiden letzten Bilder einer Phase. */
export function averagePatch(a: Uint8ClampedArray, b: Uint8ClampedArray): Uint8ClampedArray {
  const out = new Uint8ClampedArray(a.length);
  for (let i = 0; i < a.length; i++) out[i] = (a[i] + b[i] + 1) >> 1;
  return out;
}

/**
 * `mov` auf `ref` ausrichten (ganze Pixel, ±r): die Phasen liegen ~0.5 s auseinander, die Umrisse
 * folgen der Hand nicht ganz genau. Vergleich der Helligkeit (Korrelation, Lampe an/aus egal).
 */
export function alignPatch(ref: Uint8ClampedArray, mov: Uint8ClampedArray, w = FLASH_W, h = FLASH_H, r = 2): Uint8ClampedArray {
  const lum = (p: Uint8ClampedArray) => {
    const o = new Float32Array(w * h);
    for (let i = 0; i < w * h; i++) o[i] = 0.299 * p[4 * i] + 0.587 * p[4 * i + 1] + 0.114 * p[4 * i + 2];
    return o;
  };
  const A = lum(ref), B = lum(mov);
  let best = -2, bx = 0, by = 0;
  for (let dy = -r; dy <= r; dy++) {
    for (let dx = -r; dx <= r; dx++) {
      let n = 0, sa = 0, sb = 0, saa = 0, sbb = 0, sab = 0;
      for (let y = r; y < h - r; y++) {
        for (let x = r; x < w - r; x++) {
          const a = A[y * w + x], b = B[(y + dy) * w + x + dx];
          n++; sa += a; sb += b; saa += a * a; sbb += b * b; sab += a * b;
        }
      }
      const va = saa - (sa * sa) / n, vb = sbb - (sb * sb) / n;
      const c = va > 0 && vb > 0 ? (sab - (sa * sb) / n) / Math.sqrt(va * vb) : -1;
      if (c > best + 1e-6) { best = c; bx = dx; by = dy; }
    }
  }
  if (!bx && !by) return mov;
  const out = new Uint8ClampedArray(mov.length);
  for (let y = 0; y < h; y++) {
    for (let x = 0; x < w; x++) {
      const sx = Math.min(w - 1, Math.max(0, x + bx)), sy = Math.min(h - 1, Math.max(0, y + by));
      const o = 4 * (y * w + x), q = 4 * (sy * w + sx);
      out[o] = mov[q]; out[o + 1] = mov[q + 1]; out[o + 2] = mov[q + 2]; out[o + 3] = 255;
    }
  }
  return out;
}

/** Gezählte Umschalter je Karte (Schlüssel der Live-Karte). */
export class FlashTally {
  private m = new Map<string, { r: FlashReading[]; deg: number[]; on: boolean[] }>();

  add(key: string, r: FlashReading, deg: number, lightOn = false): void {
    const e = this.m.get(key) ?? { r: [], deg: [], on: [] };
    e.r.push(r);
    e.deg.push(Math.round(deg * 10) / 10);
    e.on.push(lightOn);
    this.m.set(key, e);
  }

  n(key: string): number {
    return this.m.get(key)?.r.length ?? 0;
  }

  /** vorläufig (kann noch kippen) */
  kind(key: string): FlashKind | null {
    const e = this.m.get(key);
    return e ? flashKind(e.r) : null;
  }

  /** gilt (Vorsprung, hat FLASH_HOLD weitere Messungen gehalten) */
  settled(key: string): FlashKind | null {
    const e = this.m.get(key);
    return e ? flashSettled(e.r) : null;
  }

  result(key: string): (FlashResult & { kind: FlashKind | null; tentative: FlashKind | null; offShare: number | null;
    costs: Record<FlashKind, number>; pos: number[][]; room: number | null }) | null {
    const e = this.m.get(key);
    const r = e ? summarize(e.r.map((x) => x.share)) : null;
    if (!r || !e) return null;
    const offs = e.r.filter((_, i) => !e.on[i]).map((x) => x.share);
    const offShare = offs.length ? Math.round(median(offs) * 1000) / 1000 : null;
    const c = flashCosts(e.r);
    const costs = Object.fromEntries(KINDS.map((k) => [k, Math.round(c[k] * 100) / 100])) as Record<FlashKind, number>;
    const rooms = e.r.map((x) => x.room).filter((x): x is number => x != null);
    return { ...r, deg: e.deg, kind: flashSettled(e.r), tentative: flashKind(e.r), offShare, costs,
      pos: e.r.map((x) => [Math.round(x.tx * 100) / 100, Math.round(x.ty * 100) / 100]),
      room: rooms.length ? Math.round(median(rooms) * 100) / 100 : null };
  }

  forget(keep: Set<string>): void {
    for (const k of Array.from(this.m.keys())) if (!keep.has(k)) this.m.delete(k);
  }

  clear(): void {
    this.m.clear();
  }
}
