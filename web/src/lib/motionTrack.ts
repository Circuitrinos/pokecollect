// Bewegung der Kamera zwischen zwei Prüfungen des Live-Scans: aus dem Bild selbst
// (der Lagesensor kennt Drehung, aber weder Verschiebung noch Abstand). Etwa zehnmal
// je Sekunde auf einem kleinen Graubild: markante Punkte suchen, jeden im nächsten
// Bild wiederfinden (Ausschnitt-Vergleich, auf Bruchteile eines Pixels genau) und
// daraus Verschiebung, Drehung und Zoom schätzen (RANSAC, Ausreißer zählen nicht).
// Aufaddiert ergibt das, wo ein Punkt eines früheren Bilds jetzt im Kamerabild liegt.
// Reine Rechnung ohne DOM, damit sie sich für sich prüfen lässt.

export type Mat3 = number[][];
export const IDENTITY: Mat3 = [[1, 0, 0], [0, 1, 0], [0, 0, 1]];

export function mul(a: Mat3, b: Mat3): Mat3 {
  return a.map((row) => [0, 1, 2].map((j) => row[0] * b[0][j] + row[1] * b[1][j] + row[2] * b[2][j]));
}

export function inv(m: Mat3): Mat3 | null {
  const [[a, b, c], [d, e, f], [g, h, i]] = m;
  const A = e * i - f * h, B = -(d * i - f * g), C = d * h - e * g;
  const det = a * A + b * B + c * C;
  if (!det || !Number.isFinite(det)) return null;
  return [
    [A / det, -(b * i - c * h) / det, (b * f - c * e) / det],
    [B / det, (a * i - c * g) / det, -(a * f - c * d) / det],
    [C / det, -(a * h - b * g) / det, (a * e - b * d) / det],
  ];
}

export function apply(m: Mat3, pts: number[][]): number[][] {
  return pts.map(([x, y]) => {
    const w = m[2][0] * x + m[2][1] * y + m[2][2];
    return [(m[0][0] * x + m[0][1] * y + m[0][2]) / w, (m[1][0] * x + m[1][1] * y + m[1][2]) / w];
  });
}

/** Pixel-Matrix (Bild w×h) ↔ Anteile (0..1). */
export const toFractions = (m: Mat3, w: number, h: number): Mat3 =>
  mul(mul([[1 / w, 0, 0], [0, 1 / h, 0], [0, 0, 1]], m), [[w, 0, 0], [0, h, 0], [0, 0, 1]]);
export const toPixels = (m: Mat3, w: number, h: number): Mat3 =>
  mul(mul([[w, 0, 0], [0, h, 0], [0, 0, 1]], m), [[1 / w, 0, 0], [0, 1 / h, 0], [0, 0, 1]]);

const PATCH = 4;        // Ausschnitt 9×9
const MARGIN = 16;

/** Markante Punkte (Ecken, Kanten-Kreuzungen) — je Feld eines Rasters der stärkste. */
export function pickPoints(g: Float32Array, w: number, h: number, cols = 8, rows = 12): [number, number][] {
  const out: [number, number][] = [];
  const cw = (w - 2 * MARGIN) / cols, ch = (h - 2 * MARGIN) / rows;
  for (let r = 0; r < rows; r++) {
    for (let c = 0; c < cols; c++) {
      let best = 0, bx = -1, by = -1;
      const x0 = Math.floor(MARGIN + c * cw), x1 = Math.floor(MARGIN + (c + 1) * cw);
      const y0 = Math.floor(MARGIN + r * ch), y1 = Math.floor(MARGIN + (r + 1) * ch);
      for (let y = y0; y < y1; y += 2) {
        for (let x = x0; x < x1; x += 2) {
          // Strukturtensor über 5×5: kleinster Eigenwert (Shi-Tomasi)
          let sxx = 0, syy = 0, sxy = 0;
          for (let dy = -2; dy <= 2; dy++) {
            const row = (y + dy) * w;
            for (let dx = -2; dx <= 2; dx++) {
              const i = row + x + dx;
              const gx = g[i + 1] - g[i - 1], gy = g[i + w] - g[i - w];
              sxx += gx * gx; syy += gy * gy; sxy += gx * gy;
            }
          }
          const tr = sxx + syy, det = sxx * syy - sxy * sxy;
          const minEig = tr / 2 - Math.sqrt(Math.max(0, (tr * tr) / 4 - det));
          if (minEig > best) { best = minEig; bx = x; by = y; }
        }
      }
      if (bx >= 0 && best > 2500) out.push([bx, by]);
    }
  }
  return out;
}

/** Punkt im nächsten Bild wiederfinden: Ausschnitt-Vergleich (mittelwertfrei) im Umkreis radius. */
export function findPoint(a: Float32Array, b: Float32Array, w: number, h: number, px: number, py: number,
  radius: number, gx = 0, gy = 0): [number, number, number] | null {
  const n = (2 * PATCH + 1) ** 2;
  let ma = 0;
  for (let dy = -PATCH; dy <= PATCH; dy++) for (let dx = -PATCH; dx <= PATCH; dx++) ma += a[(py + dy) * w + px + dx];
  ma /= n;
  const cx = Math.round(px + gx), cy = Math.round(py + gy);
  const size = 2 * radius + 1;
  const cost = new Float32Array(size * size).fill(Infinity);
  let best = Infinity, bi = -1, bj = -1;
  for (let j = -radius; j <= radius; j++) {
    const y = cy + j;
    if (y - PATCH < 0 || y + PATCH >= h) continue;
    for (let i = -radius; i <= radius; i++) {
      const x = cx + i;
      if (x - PATCH < 0 || x + PATCH >= w) continue;
      let mb = 0;
      for (let dy = -PATCH; dy <= PATCH; dy++) for (let dx = -PATCH; dx <= PATCH; dx++) mb += b[(y + dy) * w + x + dx];
      mb /= n;
      let s = 0;
      for (let dy = -PATCH; dy <= PATCH && s < best; dy++) {
        const ra = (py + dy) * w + px, rb = (y + dy) * w + x;
        for (let dx = -PATCH; dx <= PATCH; dx++) s += Math.abs(a[ra + dx] - ma - (b[rb + dx] - mb));
      }
      cost[(j + radius) * size + i + radius] = s;
      if (s < best) { best = s; bi = i; bj = j; }
    }
  }
  if (bi === -radius - 1 || !Number.isFinite(best)) return null;
  if (Math.abs(bi) === radius || Math.abs(bj) === radius) return null;   // am Rand des Suchfelds: lag wohl weiter weg
  // auf Bruchteile eines Pixels: Parabel durch die Nachbarn des Minimums (deren Kosten
  // ganz ausgerechnet — oben bricht die Summe für die Geschwindigkeit früh ab)
  const full = (i: number, j: number): number => {
    const x = cx + i, y = cy + j;
    if (x - PATCH < 0 || x + PATCH >= w || y - PATCH < 0 || y + PATCH >= h) return Infinity;
    let mb = 0;
    for (let dy = -PATCH; dy <= PATCH; dy++) for (let dx = -PATCH; dx <= PATCH; dx++) mb += b[(y + dy) * w + x + dx];
    mb /= n;
    let s = 0;
    for (let dy = -PATCH; dy <= PATCH; dy++) {
      const ra = (py + dy) * w + px, rb = (y + dy) * w + x;
      for (let dx = -PATCH; dx <= PATCH; dx++) s += Math.abs(a[ra + dx] - ma - (b[rb + dx] - mb));
    }
    return s;
  };
  const near = new Map<string, number>();
  const at = (i: number, j: number) => {
    const k = `${i},${j}`;
    if (!near.has(k)) near.set(k, full(i, j));
    return near.get(k)!;
  };
  const sub = (l: number, c: number, r: number) => {
    const d = l - 2 * c + r;
    return Number.isFinite(l) && Number.isFinite(r) && d > 0 ? Math.max(-0.5, Math.min(0.5, (l - r) / (2 * d))) : 0;
  };
  const fx = bi > -radius && bi < radius ? sub(at(bi - 1, bj), best, at(bi + 1, bj)) : 0;
  const fy = bj > -radius && bj < radius ? sub(at(bi, bj - 1), best, at(bi, bj + 1)) : 0;
  return [cx + bi + fx, cy + bj + fy, best / n];
}

/** Ähnlichkeit (Zoom, Drehung, Verschiebung) aus Punktpaaren, mit Ausreißern (RANSAC). */
export function fitSimilarity(src: number[][], dst: number[][], tol = 1.5, rounds = 80):
  { m: Mat3; inliers: number } | null {
  const n = src.length;
  if (n < 4) return null;
  const solve = (idx: number[]): Mat3 | null => {
    // x' = a x − b y + tx, y' = b x + a y + ty — kleinste Quadrate
    let sx = 0, sy = 0, sx2 = 0, sy2 = 0;
    for (const i of idx) { sx += src[i][0]; sy += src[i][1]; sx2 += dst[i][0]; sy2 += dst[i][1]; }
    const k = idx.length;
    const mx = sx / k, my = sy / k, mx2 = sx2 / k, my2 = sy2 / k;
    let num1 = 0, num2 = 0, den = 0;
    for (const i of idx) {
      const x = src[i][0] - mx, y = src[i][1] - my, u = dst[i][0] - mx2, v = dst[i][1] - my2;
      num1 += x * u + y * v; num2 += x * v - y * u; den += x * x + y * y;
    }
    if (den < 1e-6) return null;
    const a = num1 / den, b = num2 / den;
    return [[a, -b, mx2 - a * mx + b * my], [b, a, my2 - b * mx - a * my], [0, 0, 1]];
  };
  const inliersOf = (m: Mat3) => {
    const out: number[] = [];
    for (let i = 0; i < n; i++) {
      const [x, y] = src[i];
      const u = m[0][0] * x + m[0][1] * y + m[0][2], v = m[1][0] * x + m[1][1] * y + m[1][2];
      if (Math.hypot(u - dst[i][0], v - dst[i][1]) <= tol) out.push(i);
    }
    return out;
  };
  let best: number[] = [];
  let seed = 12345;
  const rnd = () => { seed = (seed * 1103515245 + 12345) & 0x7fffffff; return seed / 0x7fffffff; };
  for (let r = 0; r < rounds; r++) {
    const i = Math.floor(rnd() * n);
    let j = Math.floor(rnd() * n);
    if (j === i) j = (j + 1) % n;
    if (Math.hypot(src[i][0] - src[j][0], src[i][1] - src[j][1]) < 8) continue;
    const m = solve([i, j]);
    if (!m) continue;
    const inl = inliersOf(m);
    if (inl.length > best.length) best = inl;
  }
  if (best.length < 4) return null;
  const m = solve(best);
  if (!m) return null;
  return { m, inliers: inliersOf(m).length };
}

/** Halbe Auflösung (Mittel aus 2×2). */
function half(g: Float32Array, w: number, h: number): [Float32Array, number, number] {
  const w2 = w >> 1, h2 = h >> 1;
  const out = new Float32Array(w2 * h2);
  for (let y = 0; y < h2; y++) {
    for (let x = 0; x < w2; x++) {
      const i = 2 * y * w + 2 * x;
      out[y * w2 + x] = (g[i] + g[i + 1] + g[i + w] + g[i + w + 1]) / 4;
    }
  }
  return [out, w2, h2];
}

/** Grobe Verschiebung bei schneller Bewegung: auf halber Auflösung, Median der Punkte. */
function coarseShift(a: Float32Array, b: Float32Array, w: number, h: number, radius: number): [number, number] | null {
  const [a2, w2, h2] = half(a, w, h);
  const [b2] = half(b, w, h);
  const pts = pickPoints(a2, w2, h2, 4, 6);
  const dx: number[] = [], dy: number[] = [];
  for (const [x, y] of pts) {
    const f = findPoint(a2, b2, w2, h2, x, y, radius);
    if (f && f[2] < 18) { dx.push(f[0] - x); dy.push(f[1] - y); }
  }
  if (dx.length < 5) return null;
  const med = (v: number[]) => v.sort((p, q) => p - q)[v.length >> 1];
  return [2 * med(dx), 2 * med(dy)];
}

export type TrackStep = { ok: boolean; points: number; inliers: number; m: Mat3 | null };

/**
 * Folgt der Kamera: push() mit jedem kleinen Graubild (immer gleich groß). cum ist
 * die Matrix vom Bild beim Start (bzw. letzten Verlust) ins aktuelle Bild, in
 * Anteilen (0..1). epoch zählt die Verluste: Matrizen aus verschiedenen Epochen
 * passen nicht zusammen.
 */
export class MotionTracker {
  cum: Mat3 = IDENTITY;
  epoch = 0;
  lostFor = 0;
  private prev: Float32Array | null = null;
  private w = 0;
  private h = 0;
  private lastStep: Mat3 = IDENTITY;    // letzte Bewegung je Bild: Startwert fürs nächste
  private readonly radius: number;

  constructor(radius = 10) {
    this.radius = radius;
  }

  reset() {
    this.cum = IDENTITY;
    this.prev = null;
    this.lastStep = IDENTITY;
    this.lostFor = 0;
    this.epoch += 1;
  }

  push(g: Float32Array, w: number, h: number): TrackStep {
    if (!this.prev || w !== this.w || h !== this.h) {
      this.prev = g; this.w = w; this.h = h;
      return { ok: true, points: 0, inliers: 0, m: IDENTITY };
    }
    const pts = pickPoints(this.prev, w, h);
    const attempt = (guess: Mat3) => {
      const src: number[][] = [], dst: number[][] = [];
      for (const [x, y] of pts) {
        const gx = guess[0][0] * x + guess[0][1] * y + guess[0][2] - x;
        const gy = guess[1][0] * x + guess[1][1] * y + guess[1][2] - y;
        const f = findPoint(this.prev!, g, w, h, x, y, this.radius, gx, gy);
        if (f && f[2] < 18) { src.push([x, y]); dst.push([f[0], f[1]]); }
      }
      const fit = fitSimilarity(src, dst);
      const scale = fit ? Math.hypot(fit.m[0][0], fit.m[1][0]) : 0;
      const angle = fit ? Math.abs(Math.atan2(fit.m[1][0], fit.m[0][0])) : 0;
      const good = !!fit && fit.inliers >= Math.max(8, 0.35 * src.length) && scale > 0.8 && scale < 1.25 && angle < 0.25;
      return { fit, good, n: src.length };
    };
    // Startwert: dieselbe Bewegung wie beim letzten Mal (die Hand bewegt sich gleichmäßig);
    // passt das nicht, eine grobe Suche auf halber Auflösung (schneller Schwenk)
    let res = attempt(this.lastStep);
    if (!res.good) {
      const shift = coarseShift(this.prev, g, w, h, this.radius);
      if (shift) res = attempt([[1, 0, shift[0]], [0, 1, shift[1]], [0, 0, 1]]);
    }
    const { fit, good: ok } = res;
    const src = { length: res.n };
    if (!ok) {
      // das alte Bild bleibt Bezug: beim nächsten Mal wird die Bewegung seitdem gesucht
      this.lostFor += 1;
      this.lastStep = IDENTITY;
      if (this.lostFor >= 3) {          // mehrmals nicht wiedergefunden: neu anfangen
        this.cum = IDENTITY;
        this.lostFor = 0;
        this.epoch += 1;
        this.prev = g;
      }
      return { ok: false, points: src.length, inliers: fit?.inliers ?? 0, m: null };
    }
    this.prev = g;
    this.lostFor = 0;
    this.lastStep = fit!.m;
    this.cum = mul(toFractions(fit!.m, w, h), this.cum);
    return { ok: true, points: src.length, inliers: fit!.inliers, m: fit!.m };
  }
}
