// Einzelkarte: Folie beim Neigen erkennen. Die Karte wird mehrmals pro Sekunde aus dem
// Kamerabild entzerrt (126×176, perspektivisch nach dem verfolgten Umriss), an einem festen
// Bezugsbild ausgerichtet und über die Zeit verglichen. Zwei Prüfungen, an Brians Videos
// vom 16.11. geeicht (Mega Signal Holo, Team Rocket's Proton normal, Hop's Choice Band
// Reverse Holo; einzeln unter der Lampe und zu dritt im Zimmerlicht):
//  - Holo: schwankt das Bildfenster stärker als der Rahmen? Verglichen werden nur gleich
//    helle Stellen (≥ 60 % des Rahmens) — dunkle Druckfarbe schwankt immer stärker (wenig
//    Licht, viel Rauschen), ein dunkles Bild sähe sonst nach Holo aus. Bilder mit Blendung
//    zählen hier nicht. Mega Signal 2.7× (allein) / 2.1× (zu dritt), Proton 1.4×, Hop's 1.1×.
//  - Reverse Holo: schwankt der Rahmen selbst (Rahmen-Schwankung 0.35–0.47 gegen 0.08–0.21),
//    oder bekommt er in den hellsten Momenten (zur Lampe gedreht) feines Muster dazu, statt wie
//    glänzendes Papier auszubleichen? Feines Muster im Rahmen dort: Hop's 1.14×, Proton 0.66×,
//    Mega Signal 0.92× des Üblichen.
// Geurteilt wird erst, wenn die Karte genug geneigt wurde; nur unter den Folierungen, die es
// von der Karte gibt. Die Schwellen sind vorläufig (drei Karten) — „live.tiltfoil" protokolliert
// die Werte, um sie an mehr Karten nachzustellen. Reine Rechnung ohne DOM (prüfbar für sich).

import type { FoilKind } from "./liveFoil";

export const TILT_W = 126;
export const TILT_H = 176;
const N = TILT_W * TILT_H;

const KEEP = 60;               // je Karte die letzten Bilder (~8 s)
const REF_PICK = 12;           // Bezugsbild: das schärfste der ersten so vielen
const SHIFT = 6;               // Ausrichtung: so viele Punkte Versatz höchstens (Umriss liegt etwas daneben)
const CLIP = 245;              // so hell: überstrahlt (nach dem Verkleinern)
const GLARE = 0.02;            // Anteil überstrahlter Punkte: ab hier Blendung (Holo-Prüfung ohne dieses Bild)
const BRIGHT = Math.log(0.6);  // gleich hell: mindestens 60 % des Rahmens
const SIGMA = 2;               // feines Muster: Abweichung vom Mittel der Umgebung (~2 mm)
export const TILT_MIN_USED = 20;    // so viele scharfe Bilder mindestens
export const TILT_SPREAD = 0.10;    // Neigung: Spanne der Verzerrung (p10–p90) in einer Richtung
export const LIT_RANGE = 1.3;       // hellste Momente mindestens so viel heller als üblich (zum Licht gedreht)
export const HOLO_RATIO = 1.9, HOLO_RATIO_NO = 1.6, HOLO_ART = 0.15;
export const RH_SWING = 0.30, RH_SWING_NO = 0.22;
export const RH_TEX = 1.05, RH_TEX_NO = 0.95;

const LIN = new Float32Array(256);
for (let i = 0; i < 256; i++) { const c = i / 255; LIN[i] = c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4; }

// Bereiche (Anteile der Karte): innen ohne Außenrand; Bildfenster eng (passt bei Pokémon und Trainern);
// Rahmen = innen ohne ein großzügiges Bildfenster
const INNER = new Uint8Array(N), ART = new Uint8Array(N), FRAME = new Uint8Array(N);
for (let y = 0; y < TILT_H; y++) for (let x = 0; x < TILT_W; x++) {
  const fx = x / TILT_W, fy = y / TILT_H, i = y * TILT_W + x;
  INNER[i] = fx > 0.04 && fx < 0.96 && fy > 0.03 && fy < 0.97 ? 1 : 0;
  ART[i] = fx > 0.10 && fx < 0.90 && fy > 0.16 && fy < 0.48 ? 1 : 0;
  FRAME[i] = INNER[i] && !(fx > 0.07 && fx < 0.93 && fy > 0.12 && fy < 0.53) ? 1 : 0;
}

/** Einheitsquadrat → Umriss (TL, TR, BR, BL): x = (a u + b v + c) / (g u + h v + 1), y entsprechend. */
export function quadHomography(q: number[][]): number[] | null {
  const [[x0, y0], [x1, y1], [x2, y2], [x3, y3]] = q;
  const sx = x0 - x1 + x2 - x3, sy = y0 - y1 + y2 - y3;
  let g = 0, h = 0;
  if (Math.abs(sx) > 1e-9 || Math.abs(sy) > 1e-9) {
    const dx1 = x1 - x2, dx2 = x3 - x2, dy1 = y1 - y2, dy2 = y3 - y2;
    const den = dx1 * dy2 - dx2 * dy1;
    if (!den) return null;
    g = (sx * dy2 - dx2 * sy) / den;
    h = (dx1 * sy - sx * dy1) / den;
  }
  return [x1 - x0 + g * x1, x3 - x0 + h * x3, x0, y1 - y0 + g * y1, y3 - y0 + h * y3, y0, g, h];
}

export type Pixels = { data: Uint8ClampedArray; width: number; height: number; ox: number; oy: number };

/** Die Karte perspektivisch entzerren: TILT_W×TILT_H RGBA, je Punkt 2×2 Proben (bilinear) aus `src`
 *  (ein Ausschnitt des Kamerabilds ab ox/oy, in denselben Koordinaten wie `quad`). Außerhalb: Alpha 0. */
export function warpCard(src: Pixels, quad: number[][]): Uint8ClampedArray | null {
  const H = quadHomography(quad);
  if (!H) return null;
  const [a, b, c, d, e, f, g, h] = H;
  const out = new Uint8ClampedArray(N * 4);
  const { data, width, height, ox, oy } = src;
  const acc = [0, 0, 0];
  for (let y = 0; y < TILT_H; y++) for (let x = 0; x < TILT_W; x++) {
    acc[0] = acc[1] = acc[2] = 0;
    let inside = 4;
    for (let k = 0; k < 4; k++) {
      const u = (x + 0.25 + 0.5 * (k & 1)) / TILT_W, v = (y + 0.25 + 0.5 * (k >> 1)) / TILT_H;
      const w = g * u + h * v + 1;
      const px = (a * u + b * v + c) / w - ox - 0.5, py = (d * u + e * v + f) / w - oy - 0.5;
      if (!(px >= 0 && py >= 0 && px < width - 1 && py < height - 1)) { inside -= 1; continue; }
      const ix = px | 0, iy = py | 0, fx = px - ix, fy = py - iy;
      const p00 = (iy * width + ix) * 4, p10 = p00 + 4, p01 = p00 + width * 4, p11 = p01 + 4;
      for (let ch = 0; ch < 3; ch++) {
        acc[ch] += (data[p00 + ch] * (1 - fx) + data[p10 + ch] * fx) * (1 - fy) + (data[p01 + ch] * (1 - fx) + data[p11 + ch] * fx) * fy;
      }
    }
    const o = (y * TILT_W + x) * 4;
    if (inside < 4) continue;   // Alpha 0: nicht im Bild
    out[o] = acc[0] / 4; out[o + 1] = acc[1] / 4; out[o + 2] = acc[2] / 4; out[o + 3] = 255;
  }
  return out;
}

/** Verzerrung des Umrisses (oben gegen unten, links gegen rechts): ändert sich beim Neigen. */
export function keystone(q: number[][]): [number, number] {
  const d = (i: number, j: number) => Math.hypot(q[i][0] - q[j][0], q[i][1] - q[j][1]);
  const top = d(0, 1), bot = d(3, 2), lef = d(0, 3), rig = d(1, 2);
  return [(2 * (top - bot)) / (top + bot || 1), (2 * (lef - rig)) / (lef + rig || 1)];
}

type Sample = {
  lum: Float32Array; bad: Uint8Array; grad: Float32Array; sharp: number; clip: number; ks: [number, number]; t: number;
  // ausgerichtet am Bezugsbild (je Bezugsbild einmal gerechnet)
  ref?: Sample; L?: Float32Array; B?: Uint8Array; LG?: Float32Array; nm?: number; tex?: number;
};
type Card = { list: Sample[]; ref: Sample | null; frozen: boolean };

function sobel(v: Float32Array, out: Float32Array) {
  out.fill(0);
  for (let y = 1; y < TILT_H - 1; y++) for (let x = 1; x < TILT_W - 1; x++) {
    const i = y * TILT_W + x, W = TILT_W;
    const gx = v[i - W + 1] + 2 * v[i + 1] + v[i + W + 1] - v[i - W - 1] - 2 * v[i - 1] - v[i + W - 1];
    const gy = v[i + W - 1] + 2 * v[i + W] + v[i + W + 1] - v[i - W - 1] - 2 * v[i - W] - v[i - W + 1];
    out[i] = Math.hypot(gx, gy);
  }
}

function quantile(sorted: ArrayLike<number>, n: number, q: number): number {
  if (!n) return NaN;
  const p = (n - 1) * q, i = Math.floor(p), f = p - i;
  return i + 1 < n ? sorted[i] * (1 - f) + sorted[i + 1] * f : sorted[i];
}
function median(xs: number[]): number {
  const s = xs.filter((x) => Number.isFinite(x)).sort((a, b) => a - b);
  return quantile(s, s.length, 0.5);
}

/** Ein Bild der Karte (RGBA TILT_W×TILT_H aus warpCard) → Helligkeit (linear), unbrauchbare Punkte, Kanten, Schärfe. */
function toSample(rgba: Uint8ClampedArray, quad: number[][], t: number): Sample {
  const lum = new Float32Array(N), bad = new Uint8Array(N), lg = new Float32Array(N), gray = new Float32Array(N);
  let clip = 0, inner = 0;
  for (let i = 0; i < N; i++) {
    const r = rgba[4 * i], g = rgba[4 * i + 1], b = rgba[4 * i + 2], mx = Math.max(r, g, b);
    lum[i] = 0.2126 * LIN[r] + 0.7152 * LIN[g] + 0.0722 * LIN[b];
    bad[i] = rgba[4 * i + 3] < 250 || mx >= CLIP || mx <= 3 ? 1 : 0;
    lg[i] = Math.log(lum[i] + 1e-3);
    gray[i] = 0.299 * r + 0.587 * g + 0.114 * b;
    if (INNER[i]) { inner += 1; if (mx >= CLIP) clip += 1; }
  }
  let s = 0, s2 = 0, m = 0;
  for (let y = 1; y < TILT_H - 1; y++) for (let x = 1; x < TILT_W - 1; x++) {
    const i = y * TILT_W + x;
    const l = gray[i - 1] + gray[i + 1] + gray[i - TILT_W] + gray[i + TILT_W] - 4 * gray[i];
    s += l; s2 += l * l; m += 1;
  }
  const grad = new Float32Array(N);
  sobel(lg, grad);
  return { lum, bad, grad, sharp: s2 / m - (s / m) ** 2, clip: clip / Math.max(1, inner), ks: keystone(quad), t };
}

/** Versatz (ganze Punkte), um den die Kanten von `s` auf denen des Bezugsbilds liegen: grob in 2er-Schritten, dann fein. */
function findShift(s: Float32Array, r: Float32Array): [number, number] {
  const M = SHIFT + 2;
  const score = (dx: number, dy: number, step: number) => {
    let sab = 0, saa = 0, sbb = 0, sa = 0, sb = 0, n = 0;
    for (let y = M; y < TILT_H - M; y += step) for (let x = M; x < TILT_W - M; x += step) {
      const a = s[(y + dy) * TILT_W + x + dx], b = r[y * TILT_W + x];
      sab += a * b; saa += a * a; sbb += b * b; sa += a; sb += b; n += 1;
    }
    const cov = sab - (sa * sb) / n, va = saa - (sa * sa) / n, vb = sbb - (sb * sb) / n;
    return cov / Math.sqrt(va * vb + 1e-12);
  };
  let best = [-2, 0, 0];
  for (let dy = -SHIFT; dy <= SHIFT; dy += 2) for (let dx = -SHIFT; dx <= SHIFT; dx += 2) {
    const c = score(dx, dy, 2);
    if (c > best[0]) best = [c, dx, dy];
  }
  const [, cx, cy] = best;
  best = [-2, cx, cy];
  for (let dy = cy - 1; dy <= cy + 1; dy++) for (let dx = cx - 1; dx <= cx + 1; dx++) {
    if (Math.abs(dx) > SHIFT || Math.abs(dy) > SHIFT) continue;
    const c = score(dx, dy, 1);
    if (c > best[0]) best = [c, dx, dy];
  }
  return [best[1], best[2]];
}

// Glätten mit Maske (normiert), getrennt in x und y
const KERNEL = (() => { const r = Math.ceil(3 * SIGMA), k = new Float32Array(2 * r + 1); for (let i = -r; i <= r; i++) k[i + r] = Math.exp(-(i * i) / (2 * SIGMA * SIGMA)); return k; })();
function blurMasked(v: Float32Array, ok: Uint8Array, out: Float32Array) {
  const r = (KERNEL.length - 1) / 2, tv = new Float32Array(N), tw = new Float32Array(N);
  for (let y = 0; y < TILT_H; y++) for (let x = 0; x < TILT_W; x++) {
    let sv = 0, sw = 0;
    for (let k = -r; k <= r; k++) {
      const xx = x + k;
      if (xx < 0 || xx >= TILT_W) continue;
      const j = y * TILT_W + xx;
      if (!ok[j]) continue;
      sv += KERNEL[k + r] * v[j]; sw += KERNEL[k + r];
    }
    tv[y * TILT_W + x] = sv; tw[y * TILT_W + x] = sw;
  }
  for (let y = 0; y < TILT_H; y++) for (let x = 0; x < TILT_W; x++) {
    let sv = 0, sw = 0;
    for (let k = -r; k <= r; k++) {
      const yy = y + k;
      if (yy < 0 || yy >= TILT_H) continue;
      const j = yy * TILT_W + x;
      sv += KERNEL[k + r] * tv[j]; sw += KERNEL[k + r] * tw[j];
    }
    out[y * TILT_W + x] = sw > 1e-3 ? sv / sw : NaN;
  }
}

export type TiltResult = {
  n: number; used: number;          // Bilder gesamt / scharf genug
  tilt: number;                     // Spanne der Verzerrung (Neigung)
  lit: number;                      // hellste Momente gegen übliche (zum Licht gedreht?)
  art: number | null; frame: number | null; ratio: number | null;   // Holo-Prüfung (ohne Blendung, gleich hell)
  swing: number | null;             // Schwankung des Rahmens (alle scharfen Bilder)
  tex: number | null;               // feines Muster im Rahmen: hellste Momente gegen übliche
  holo: "yes" | "no" | null; rh: "yes" | "no" | null;
  ready: boolean; progress: number; // genug Bilder und genug geneigt
  kind: FoilKind | null;            // nur unter den Folierungen der Karte
};

/** Folierung aus den beiden Prüfungen, nur unter `options` (leer: alle). */
export function tiltKind(r: Pick<TiltResult, "holo" | "rh" | "ready">, options: string[]): FoilKind | null {
  if (!r.ready) return null;
  const has = (k: FoilKind) => !options.length || options.includes(k);
  if (r.rh === "yes" && has("Reverse Holo")) return "Reverse Holo";
  if (r.holo === "yes" && r.rh !== "yes" && has("Holo")) return "Holo";
  const noRh = r.rh === "no" || !has("Reverse Holo");
  const noHolo = r.holo === "no" || !has("Holo");
  if (noRh && noHolo && has("Normal")) return "Normal";
  if (noRh && has("Holo") && !has("Normal") && r.holo !== "no") return "Holo";   // Holo oder Reverse Holo: nicht Reverse Holo
  return null;
}

export class FoilTilt {
  private cards = new Map<string, Card>();

  add(key: string, rgba: Uint8ClampedArray, quad: number[][], t: number) {
    const c = this.cards.get(key) ?? { list: [], ref: null, frozen: false };
    const s = toSample(rgba, quad, t);
    c.list.push(s);
    if (c.list.length > KEEP) c.list.shift();
    // Bezugsbild: das schärfste der ersten REF_PICK — danach fest (auch wenn es aus der Liste fällt),
    // sonst müsste alles neu ausgerichtet werden
    if (!c.frozen && (!c.ref || s.sharp > c.ref.sharp)) c.ref = s;
    if (c.list.length >= REF_PICK) c.frozen = true;
    this.cards.set(key, c);
  }

  count(key: string): number { return this.cards.get(key)?.list.length ?? 0; }

  forget(keep: Set<string>) {
    for (const k of Array.from(this.cards.keys())) if (!keep.has(k)) this.cards.delete(k);
  }

  clear() { this.cards.clear(); }

  private align(s: Sample, ref: Sample, calm: Uint8Array) {
    if (s.ref === ref && s.L) return;
    const [dx, dy] = s === ref ? [0, 0] : findShift(s.grad, ref.grad);
    const L = new Float32Array(N), B = new Uint8Array(N);
    for (let y = 0; y < TILT_H; y++) for (let x = 0; x < TILT_W; x++) {
      const i = y * TILT_W + x, xx = x + dx, yy = y + dy;
      if (xx < 0 || yy < 0 || xx >= TILT_W || yy >= TILT_H) { B[i] = 1; continue; }
      const j = yy * TILT_W + xx;
      L[i] = s.lum[j]; B[i] = s.bad[j];
    }
    const fr: number[] = [];
    for (let i = 0; i < N; i++) if (FRAME[i] && !B[i]) fr.push(L[i]);
    s.nm = median(fr);
    // feines Muster im Rahmen: Abweichung vom geglätteten Bild, ohne Ränder und Punkte neben Überstrahltem
    const lg = new Float32Array(N), ok = new Uint8Array(N), sm = new Float32Array(N);
    for (let i = 0; i < N; i++) { lg[i] = Math.log(Math.max(L[i], 1e-4)); ok[i] = B[i] ? 0 : 1; }
    blurMasked(lg, ok, sm);
    const res: number[] = [];
    for (let y = 2; y < TILT_H - 2; y++) for (let x = 2; x < TILT_W - 2; x++) {
      const i = y * TILT_W + x;
      if (!FRAME[i] || !calm[i]) continue;
      let near = false;
      for (let k = -2; k <= 2 && !near; k++) for (let m = -2; m <= 2; m++) if (B[i + k * TILT_W + m]) { near = true; break; }
      if (!near && Number.isFinite(sm[i])) res.push(Math.abs(lg[i] - sm[i]));
    }
    s.tex = res.length >= 50 ? median(res) : NaN;
    // Helligkeit relativ zum Rahmen (log), unbrauchbar: NaN
    const LG = new Float32Array(N);
    for (let i = 0; i < N; i++) LG[i] = B[i] || !(s.nm > 0) ? NaN : Math.log(Math.max(L[i] / s.nm, 1e-4));
    s.ref = ref; s.L = L; s.B = B; s.LG = LG;
  }

  result(key: string, options: string[]): TiltResult | null {
    const card = this.cards.get(key);
    if (!card || !card.list.length || !card.ref) return null;
    const { list, ref } = card;
    // ruhige Stellen (keine Druckkanten): Kanten des Bezugsbilds unter dem 70. Perzentil
    const gIn: number[] = [];
    for (let i = 0; i < N; i++) if (INNER[i]) gIn.push(ref.grad[i]);
    gIn.sort((a, b) => a - b);
    const gCut = quantile(gIn, gIn.length, 0.7);
    const calm = new Uint8Array(N);
    for (let i = 0; i < N; i++) calm[i] = INNER[i] && ref.grad[i] < gCut ? 1 : 0;
    for (const s of list) this.align(s, ref, calm);

    const sharpCut = quantile(list.map((s) => s.sharp).sort((a, b) => a - b), list.length, 0.3);
    const sharp = list.filter((s) => s.sharp >= sharpCut);
    const calmGlare = sharp.filter((s) => s.clip <= GLARE);
    const ks = (j: 0 | 1) => { const v = list.map((s) => s.ks[j]).sort((a, b) => a - b); return quantile(v, v.length, 0.9) - quantile(v, v.length, 0.1); };
    const tilt = Math.max(ks(0), ks(1));
    const nms = sharp.map((s) => s.nm!).filter((x) => x > 0).sort((a, b) => a - b);
    const p50 = quantile(nms, nms.length, 0.5), p85 = quantile(nms, nms.length, 0.85);
    const lit = p50 > 0 ? p85 / p50 : 1;
    const ready = sharp.length >= TILT_MIN_USED && tilt >= TILT_SPREAD;
    const progress = Math.min(1, 0.4 * Math.min(1, sharp.length / TILT_MIN_USED) + 0.6 * Math.min(1, tilt / TILT_SPREAD));
    const out: TiltResult = { n: list.length, used: sharp.length, tilt, lit, art: null, frame: null, ratio: null, swing: null, tex: null,
      holo: null, rh: null, ready, progress, kind: null };
    if (sharp.length < 8) return out;

    // je Punkt: Helligkeit relativ zum Rahmen (log), Mitte und Spannen p10–p90
    const buf = new Float32Array(KEEP);
    const glareFree = new Set(calmGlare);
    const LGs = sharp.map((s) => s.LG!), LGg = sharp.filter((s) => glareFree.has(s)).map((s) => s.LG!);
    const sel = { art: [] as number[], frame: [] as number[], swing: [] as number[] };
    for (let i = 0; i < N; i++) {
      if (!calm[i] || !(ART[i] || FRAME[i])) continue;
      let n = 0;
      for (const lg of LGs) { const v = lg[i]; if (v === v) buf[n++] = v; }
      if (n < 5) continue;
      const a = buf.subarray(0, n).sort();
      if (quantile(a, n, 0.5) < BRIGHT) continue;   // nur gleich helle Stellen
      if (FRAME[i]) sel.swing.push(quantile(a, n, 0.9) - quantile(a, n, 0.1));
      if (calmGlare.length >= 10) {
        let m = 0;
        for (const lg of LGg) { const v = lg[i]; if (v === v) buf[m++] = v; }
        if (m >= 5) {
          const b = buf.subarray(0, m).sort(), sp = quantile(b, m, 0.9) - quantile(b, m, 0.1);
          (ART[i] ? sel.art : sel.frame).push(sp);
        }
      }
    }
    out.swing = sel.swing.length >= 50 ? median(sel.swing) : null;
    if (sel.art.length >= 30 && sel.frame.length >= 50) {
      out.art = median(sel.art); out.frame = median(sel.frame); out.ratio = out.art / Math.max(out.frame, 1e-3);
    }
    // feines Muster im Rahmen: hellste Momente (oberste 15 %) gegen die übliche Hälfte
    const withTex = sharp.filter((s) => Number.isFinite(s.tex) && s.nm! > 0);
    if (lit >= LIT_RANGE && withTex.length >= 10) {
      const base = withTex.filter((s) => s.nm! <= p50).map((s) => s.tex!), hi = withTex.filter((s) => s.nm! >= p85).map((s) => s.tex!);
      if (base.length >= 3 && hi.length >= 2) out.tex = median(hi) / median(base);
    }
    if (out.ratio != null) {
      out.holo = out.ratio >= HOLO_RATIO && out.art! >= HOLO_ART ? "yes" : out.ratio < HOLO_RATIO_NO ? "no" : null;
    }
    if (out.swing != null) {
      const texYes = out.tex != null && out.tex >= RH_TEX, texNo = out.tex == null || out.tex < RH_TEX_NO;
      out.rh = out.swing >= RH_SWING || texYes ? "yes" : out.swing < RH_SWING_NO && texNo ? "no" : null;
    }
    out.kind = tiltKind(out, options);
    return out;
  }
}
