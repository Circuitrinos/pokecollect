// Neige-Test (lib/tiltFoil): jedes Bild der Karte an einem Bezugsbild ausrichten — aus dem Bild
// selbst, nicht nur nach dem verfolgten Umriss. Brian 17.11.: der Umriss aus dem Live-Scan lag
// zwischen zwei Bildern bis zu ~10 % der Kartenbreite daneben und bis zu 6° verdreht, und er
// kennt keine Perspektive (ein gedrehtes Rechteck) — die Neigung blieb so bei 0.02–0.04, der Test
// wartete vergeblich, und Druckkanten im Bild schwankten wie Folie.
// Vorgehen: Kanten (Gradient des log. Grauwerts — unempfindlich gegen Helligkeit) in Blöcken
// gegen das Bezugsbild verschieben, grob → fein (32×44: Ähnlichkeit, 63×88: affin, 126×176:
// Perspektive), jeweils robust angepasst; der verbesserte Umriss wird neu aus dem Kamerabild
// entzerrt. Die Verzerrung dieses Umrisses misst die Neigung.

export type Pixels = { data: Uint8ClampedArray; width: number; height: number; ox: number; oy: number };

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

const mapUV = (H: number[], u: number, v: number): [number, number] => {
  const w = H[6] * u + H[7] * v + 1;
  return [(H[0] * u + H[1] * v + H[2]) / w, (H[3] * u + H[4] * v + H[5]) / w];
};

/** Die Karte perspektivisch entzerren: w×h RGBA, je Punkt 2×2 Proben (bilinear) aus `src`
 *  (ein Ausschnitt des Kamerabilds ab ox/oy, in denselben Koordinaten wie `quad`). Außerhalb: Alpha 0. */
export function warpCard(src: Pixels, quad: number[][], w: number, h: number, ss: 1 | 2 = 2): Uint8ClampedArray | null {
  const H = quadHomography(quad);
  if (!H) return null;
  const [a, b, c, d, e, f, g, hh] = H;
  const out = new Uint8ClampedArray(w * h * 4);
  const { data, width, height, ox, oy } = src;
  let r = 0, gg = 0, bb = 0;
  for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
    r = gg = bb = 0;
    const K = ss * ss;
    let inside = K;
    for (let k = 0; k < K; k++) {
      const u = ss === 1 ? (x + 0.5) / w : (x + 0.25 + 0.5 * (k & 1)) / w, v = ss === 1 ? (y + 0.5) / h : (y + 0.25 + 0.5 * (k >> 1)) / h;
      const ww = g * u + hh * v + 1;
      const px = (a * u + b * v + c) / ww - ox - 0.5, py = (d * u + e * v + f) / ww - oy - 0.5;
      if (!(px >= 0 && py >= 0 && px < width - 1 && py < height - 1)) { inside -= 1; continue; }
      const ix = px | 0, iy = py | 0, fx = px - ix, fy = py - iy;
      const p00 = (iy * width + ix) * 4, p10 = p00 + 4, p01 = p00 + width * 4, p11 = p01 + 4;
      const w00 = (1 - fx) * (1 - fy), w10 = fx * (1 - fy), w01 = (1 - fx) * fy, w11 = fx * fy;
      r += data[p00] * w00 + data[p10] * w10 + data[p01] * w01 + data[p11] * w11;
      gg += data[p00 + 1] * w00 + data[p10 + 1] * w10 + data[p01 + 1] * w01 + data[p11 + 1] * w11;
      bb += data[p00 + 2] * w00 + data[p10 + 2] * w10 + data[p01 + 2] * w01 + data[p11 + 2] * w11;
    }
    if (inside < K) continue;   // Alpha 0: nicht im Bild
    const o = (y * w + x) * 4;
    out[o] = r / K; out[o + 1] = gg / K; out[o + 2] = bb / K; out[o + 3] = 255;
  }
  return out;
}

/** Verzerrung des Umrisses (oben gegen unten, links gegen rechts): ändert sich beim Neigen. */
export function keystone(q: number[][]): [number, number] {
  const d = (i: number, j: number) => Math.hypot(q[i][0] - q[j][0], q[i][1] - q[j][1]);
  const top = d(0, 1), bot = d(3, 2), lef = d(0, 3), rig = d(1, 2);
  return [(2 * (top - bot)) / (top + bot || 1), (2 * (lef - rig)) / (lef + rig || 1)];
}

/** Kantenbild (Betrag des Gradienten des log. Grauwerts); außerhalb des Kamerabilds 0. */
export function edges(rgba: Uint8ClampedArray, w: number, h: number): Float32Array {
  const lg = new Float32Array(w * h), out = new Float32Array(w * h);
  for (let i = 0; i < w * h; i++) {
    lg[i] = rgba[4 * i + 3] < 250 ? NaN : Math.log(0.299 * rgba[4 * i] + 0.587 * rgba[4 * i + 1] + 0.114 * rgba[4 * i + 2] + 4);
  }
  for (let y = 1; y < h - 1; y++) for (let x = 1; x < w - 1; x++) {
    const i = y * w + x;
    const gx = lg[i - w + 1] + 2 * lg[i + 1] + lg[i + w + 1] - lg[i - w - 1] - 2 * lg[i - 1] - lg[i + w - 1];
    const gy = lg[i + w - 1] + 2 * lg[i + w] + lg[i + w + 1] - lg[i - w - 1] - 2 * lg[i - w] - lg[i - w + 1];
    const m = Math.hypot(gx, gy);
    out[i] = m === m ? m : 0;
  }
  return out;
}

type Level = { w: number; h: number; block: number; cols: number; rows: number };
export const LEVELS: Level[] = [
  { w: 32, h: 44, block: 8, cols: 5, rows: 6 },
  { w: 63, h: 88, block: 12, cols: 5, rows: 7 },
  { w: 126, h: 176, block: 16, cols: 6, rows: 8 },
];
// Schritte grob → fein: Stufe, Suchweite (Punkte der Stufe), Modell (4 Ähnlichkeit, 6 affin, 8 Perspektive).
// Grob weit suchen (±6 von 32 ≈ ±19 % der Breite) und schon dort Perspektive zulassen — ein stark
// geneigte Karte weicht vom Rechteck des Live-Scans an den Ecken um 10–20 % ab.
type Step = { level: number; search: number; model: 4 | 6 | 8 };
export let STEPS: Step[] = [
  { level: 0, search: 6, model: 6 },
  { level: 0, search: 3, model: 8 },
  { level: 1, search: 3, model: 8 },
  { level: 2, search: 2, model: 8 },
];
export function setSteps(steps: Step[]) { STEPS = steps; }

export type Pyramid = Float32Array[];
export function pyramid(src: Pixels, quad: number[][]): Pyramid | null {
  const out: Pyramid = [];
  for (const L of LEVELS) {
    const rgba = warpCard(src, quad, L.w, L.h);
    if (!rgba) return null;
    out.push(edges(rgba, L.w, L.h));
  }
  return out;
}

type Pair = [number, number, number, number];   // Bezug x, y → Bild x, y (in Punkten der vollen Auflösung)

/** Blöcke des Bezugs im Bild suchen (normierte Korrelation der Kanten), mit Subpixel-Spitze. */
function matchBlocks(G: Float32Array, R: Float32Array, L: Level, s: number): Pair[] {
  const { w, h, block: b } = L;
  const sx = 126 / w, sy = 176 / h;
  const x0 = Math.max(s, Math.round(0.06 * w)), x1 = Math.min(w - b - s, Math.round(0.94 * w) - b);
  const y0 = Math.max(s, Math.round(0.05 * h)), y1 = Math.min(h - b - s, Math.round(0.95 * h) - b);
  const blocks: { bx: number; by: number; sd: number }[] = [];
  for (let r = 0; r < L.rows; r++) for (let c = 0; c < L.cols; c++) {
    const bx = Math.round(x0 + ((x1 - x0) * c) / Math.max(1, L.cols - 1)), by = Math.round(y0 + ((y1 - y0) * r) / Math.max(1, L.rows - 1));
    let s1 = 0, s2 = 0;
    for (let y = 0; y < b; y++) for (let x = 0; x < b; x++) { const v = R[(by + y) * w + bx + x]; s1 += v; s2 += v * v; }
    const n = b * b;
    blocks.push({ bx, by, sd: Math.sqrt(Math.max(0, s2 / n - (s1 / n) ** 2)) });
  }
  const sds = blocks.map((k) => k.sd).sort((a, b2) => a - b2), sdMed = sds[sds.length >> 1];
  const pairs: Pair[] = [];
  const span = 2 * s + 1, cc = new Float32Array(span * span);
  for (const { bx, by, sd } of blocks) {
    if (!(sd > 0.35 * sdMed) || sd <= 1e-6) continue;   // zu wenig Zeichnung
    let rs = 0;
    for (let y = 0; y < b; y++) for (let x = 0; x < b; x++) rs += R[(by + y) * w + bx + x];
    const rm = rs / (b * b);
    let best = -2, bi = -1;
    for (let dy = -s; dy <= s; dy++) for (let dx = -s; dx <= s; dx++) {
      let s1 = 0, s2 = 0, sr = 0, rr = 0;
      for (let y = 0; y < b; y++) {
        const ro = (by + y) * w + bx, go = (by + y + dy) * w + bx + dx;
        for (let x = 0; x < b; x++) {
          const g = G[go + x], rv = R[ro + x] - rm;
          s1 += g; s2 += g * g; sr += g * rv; rr += rv * rv;
        }
      }
      const n = b * b, vg = s2 - (s1 * s1) / n;
      const c = vg > 1e-9 && rr > 1e-9 ? sr / Math.sqrt(vg * rr) : -1;
      cc[(dy + s) * span + dx + s] = c;
      if (c > best) { best = c; bi = (dy + s) * span + dx + s; }
    }
    if (best < 0.45 || bi < 0) continue;
    const px = bi % span, py = (bi / span) | 0;
    if (px === 0 || py === 0 || px === span - 1 || py === span - 1) continue;   // am Rand der Suche: vielleicht weiter weg
    const sub = (m: number, c0: number, p: number) => { const d = m - 2 * c0 + p; return d < 0 ? (0.5 * (m - p)) / d : 0; };
    const fx = sub(cc[py * span + px - 1], best, cc[py * span + px + 1]), fy = sub(cc[(py - 1) * span + px], best, cc[(py + 1) * span + px]);
    const dx = px - s - fx, dy = py - s - fy;
    const cx = bx + b / 2, cy = by + b / 2;
    pairs.push([cx * sx, cy * sy, (cx + dx) * sx, (cy + dy) * sy]);
  }
  return pairs;
}

/** Lineares Gleichungssystem (klein) lösen; null, wenn singulär. */
function solve(A: number[][], y: number[]): number[] | null {
  const n = y.length, M = A.map((row, i) => [...row, y[i]]);
  for (let c = 0; c < n; c++) {
    let p = c;
    for (let r = c + 1; r < n; r++) if (Math.abs(M[r][c]) > Math.abs(M[p][c])) p = r;
    if (Math.abs(M[p][c]) < 1e-12) return null;
    [M[c], M[p]] = [M[p], M[c]];
    for (let r = 0; r < n; r++) {
      if (r === c) continue;
      const f = M[r][c] / M[c][c];
      for (let k = c; k <= n; k++) M[r][k] -= f * M[c][k];
    }
  }
  return M.map((row, i) => row[n] / row[i]);
}

/** Abbildung Bezug → Bild aus Paaren: 4 = Ähnlichkeit, 6 = affin, 8 = Perspektive. Ergebnis als
 *  [a b c d e f g h] (x' = (a x + b y + c) / (g x + h y + 1)). */
function fitModel(P: Pair[], model: 4 | 6 | 8): number[] | null {
  if (P.length < model / 2 + 1) return null;
  // Koordinaten normieren (Schwerpunkt, mittlerer Abstand √2) — sonst ist die Perspektive schlecht konditioniert
  const norm = (k: 0 | 2) => {
    let mx = 0, my = 0;
    for (const p of P) { mx += p[k]; my += p[k + 1]; }
    mx /= P.length; my /= P.length;
    let d = 0;
    for (const p of P) d += Math.hypot(p[k] - mx, p[k + 1] - my);
    const s = Math.SQRT2 / Math.max(d / P.length, 1e-9);
    return { mx, my, s };
  };
  const A = norm(0), B = norm(2);
  const pts = P.map((p) => [(p[0] - A.mx) * A.s, (p[1] - A.my) * A.s, (p[2] - B.mx) * B.s, (p[3] - B.my) * B.s]);
  const n = model;
  const AtA = Array.from({ length: n }, () => new Array(n).fill(0)), Aty = new Array(n).fill(0);
  const add = (row: number[], y: number) => {
    for (let i = 0; i < n; i++) { Aty[i] += row[i] * y; for (let j = 0; j < n; j++) AtA[i][j] += row[i] * row[j]; }
  };
  for (const [x, y, u, v] of pts) {
    if (model === 4) { add([x, -y, 1, 0], u); add([y, x, 0, 1], v); }
    else if (model === 6) { add([x, y, 1, 0, 0, 0], u); add([0, 0, 0, x, y, 1], v); }
    else { add([x, y, 1, 0, 0, 0, -x * u, -y * u], u); add([0, 0, 0, x, y, 1, -x * v, -y * v], v); }
  }
  const s = solve(AtA, Aty);
  if (!s) return null;
  const Hn = model === 4 ? [s[0], -s[1], s[2], s[1], s[0], s[3], 0, 0] : model === 6 ? [...s, 0, 0] : s;
  // zurück: H = T_B⁻¹ · Hn · T_A  (3×3, h33 = 1)
  const m = [[Hn[0], Hn[1], Hn[2]], [Hn[3], Hn[4], Hn[5]], [Hn[6], Hn[7], 1]];
  const TA = [[A.s, 0, -A.s * A.mx], [0, A.s, -A.s * A.my], [0, 0, 1]];
  const TBi = [[1 / B.s, 0, B.mx], [0, 1 / B.s, B.my], [0, 0, 1]];
  const mul = (X: number[][], Y: number[][]) => X.map((r) => [0, 1, 2].map((j) => r[0] * Y[0][j] + r[1] * Y[1][j] + r[2] * Y[2][j]));
  const H = mul(TBi, mul(m, TA));
  const k = H[2][2];
  if (!k) return null;
  return [H[0][0] / k, H[0][1] / k, H[0][2] / k, H[1][0] / k, H[1][1] / k, H[1][2] / k, H[2][0] / k, H[2][1] / k];
}

const apply = (H: number[], x: number, y: number): [number, number] => mapUV(H, x, y);

/** Robust anpassen: zweimal die Ausreißer weglassen (Abweichung über max(2.5 × Median, `floor`)). */
function fitRobust(P: Pair[], model: 4 | 6 | 8, floor: number): { H: number[]; inliers: number; rms: number } | null {
  let use = P, H = fitModel(use, model);
  for (let round = 0; round < 2 && H; round++) {
    const res = P.map((p) => { const [u, v] = apply(H!, p[0], p[1]); return Math.hypot(u - p[2], v - p[3]); });
    const med = [...res].sort((a, b) => a - b)[res.length >> 1];
    const cut = Math.max(2.5 * med, floor);
    use = P.filter((_, i) => res[i] <= cut);
    H = fitModel(use, model);
  }
  if (!H) return null;
  let s2 = 0;
  for (const p of use) { const [u, v] = apply(H, p[0], p[1]); s2 += (u - p[2]) ** 2 + (v - p[3]) ** 2; }
  return { H, inliers: use.length, rms: Math.sqrt(s2 / Math.max(1, use.length)) };
}

/** Neuer Umriss im Kamerabild: die Ecken des Bezugs (volle Auflösung) über H ins jetzige Kartenbild,
 *  von dort über den jetzigen Umriss ins Kamerabild. */
function compose(quad: number[][], H: number[]): number[][] | null {
  const Q = quadHomography(quad);
  if (!Q) return null;
  return [[0, 0], [126, 0], [126, 176], [0, 176]].map(([x, y]) => {
    const [u, v] = apply(H, x, y);
    return mapUV(Q, u / 126, v / 176);
  });
}

export type Aligned = { quad: number[][]; inliers: number; rms: number };

/** Den Umriss `quad` so verbessern, dass die Karte im Bild auf dem Bezug (`ref`, gleiche Stufen) liegt. */
export function alignToRef(src: Pixels, quad: number[][], ref: Pyramid, steps: Step[] = STEPS): Aligned | null {
  let q = quad, last: Aligned | null = null;
  for (let si = 0; si < steps.length; si++) {
    const { level, search, model: want } = steps[si];
    const L = LEVELS[level];
    const rgba = warpCard(src, q, L.w, L.h, level === 2 ? 1 : 2);   // fein: eine Probe je Punkt reicht (Bild ~2× so groß)
    if (!rgba) return null;
    const pairs = matchBlocks(edges(rgba, L.w, L.h), ref[level], L, search);
    // zu wenige Blöcke für Perspektive: einfacheres Modell
    const model: 4 | 6 | 8 = pairs.length >= 2 * want ? want : pairs.length >= 12 ? 6 : 4;
    const fit = fitRobust(pairs, model, (1.2 * 126) / L.w);
    if (!fit || fit.inliers < Math.max(model, Math.round(0.25 * L.cols * L.rows))) return si === 0 ? null : last;
    const next = compose(q, fit.H);
    if (!next) return last;
    // Plausibel? (eine Ecke höchstens ~30 % der Breite verschoben)
    const width = Math.hypot(q[1][0] - q[0][0], q[1][1] - q[0][1]);
    if (next.some((p, i) => Math.hypot(p[0] - q[i][0], p[1] - q[i][1]) > 0.3 * width)) return si === 0 ? null : last;
    q = next;
    last = { quad: q, inliers: fit.inliers, rms: fit.rms };
  }
  return last;
}

/** Kanten des Katalogbilds (Grauwerte w×h, z. B. 40×55 von /scan/catalog-luma) in Stufe 0 (32×44). */
export function catalogEdges(gray: Uint8Array, w: number, h: number): Float32Array {
  const L = LEVELS[0], rgba = new Uint8ClampedArray(L.w * L.h * 4);
  for (let y = 0; y < L.h; y++) for (let x = 0; x < L.w; x++) {
    const sx = Math.min(w - 1.001, Math.max(0, ((x + 0.5) * w) / L.w - 0.5)), sy = Math.min(h - 1.001, Math.max(0, ((y + 0.5) * h) / L.h - 0.5));
    const ix = sx | 0, iy = sy | 0, fx = sx - ix, fy = sy - iy;
    const v = (gray[iy * w + ix] * (1 - fx) + gray[iy * w + ix + 1] * fx) * (1 - fy) + (gray[(iy + 1) * w + ix] * (1 - fx) + gray[(iy + 1) * w + ix + 1] * fx) * fy;
    const o = (y * L.w + x) * 4;
    rgba[o] = rgba[o + 1] = rgba[o + 2] = v; rgba[o + 3] = 255;
  }
  return edges(rgba, L.w, L.h);
}

/** Den Umriss grob an das Katalogbild legen (nur Stufe 0: Ähnlichkeit, dann affin) — für das Bezugsbild.
 *  Brian 17.11.: der Umriss aus dem Live-Scan lag beim Bezugsbild bis zu ~15 % daneben, so dass Bild-
 *  und Rahmenbereich verrutscht waren. */
export function alignToCatalog(src: Pixels, quad: number[][], cat: Float32Array): Aligned | null {
  const al = alignToRef(src, quad, [cat], [{ level: 0, search: 8, model: 4 }, { level: 0, search: 4, model: 6 }, { level: 0, search: 2, model: 6 }]);
  return al && al.inliers >= 10 && al.rms <= 4 ? al : null;
}
