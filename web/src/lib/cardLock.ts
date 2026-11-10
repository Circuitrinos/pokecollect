/**
 * Live-camera helpers for the scan page: the card-shaped guide and the
 * "is a card sitting in the guide?" check that drives auto-capture.
 *
 * The check looks for four straight edges near the guide's sides (a card's
 * border, or its art frame — either says "a card is there and aligned"). It
 * tolerates hand shake, a slight tilt and a card somewhat smaller than the
 * guide; an empty desk doesn't produce four consistent edges. It is only the
 * trigger: the captured full-resolution photo still goes through the server's
 * own card detection.
 *
 * Pure functions (no DOM), so they can be exercised outside the browser.
 */

export type Rect = { x: number; y: number; w: number; h: number };
export type Pt = [number, number];
export type Lock = { quad: [Pt, Pt, Pt, Pt]; scale: number; score: number };

export const CARD_ASPECT = 63 / 88; // width / height of a Pokémon card

/** Guide aspect (w/h) for a scan mode; null = no guide (several loose cards). */
export function guideAspect(mode: string, cols: number, rows: number): number | null {
  if (mode === "single") return CARD_ASPECT;
  if (mode === "binder") return (Math.max(1, cols) * 63) / (Math.max(1, rows) * 88);
  return null;
}

/** Largest rect with the given aspect that fits `fill` of the frame, centred. */
export function guideRect(frameW: number, frameH: number, aspect: number, fill = 0.86): Rect {
  let w = frameW * fill;
  let h = w / aspect;
  if (h > frameH * fill) {
    h = frameH * fill;
    w = h * aspect;
  }
  return { x: (frameW - w) / 2, y: (frameH - h) / 2, w, h };
}

/** RGBA → luminance (0..255). */
export function toGray(rgba: Uint8ClampedArray | Uint8Array, w: number, h: number): Float32Array {
  const g = new Float32Array(w * h);
  for (let i = 0, p = 0; p < w * h; i += 4, p++) g[p] = 0.299 * rgba[i] + 0.587 * rgba[i + 1] + 0.114 * rgba[i + 2];
  return g;
}

type Grad = { gx: Float32Array; gy: Float32Array; mean: number };

function sobel(g: Float32Array, w: number, h: number): Grad {
  const gx = new Float32Array(w * h);
  const gy = new Float32Array(w * h);
  let sum = 0;
  for (let y = 1; y < h - 1; y++) {
    for (let x = 1; x < w - 1; x++) {
      const i = y * w + x;
      const a = g[i - w - 1], b = g[i - w], c = g[i - w + 1];
      const d = g[i - 1], f = g[i + 1];
      const p = g[i + w - 1], q = g[i + w], r = g[i + w + 1];
      const vx = Math.abs(c + 2 * f + r - a - 2 * d - p);
      const vy = Math.abs(p + 2 * q + r - a - 2 * b - c);
      gx[i] = vx;
      gy[i] = vy;
      sum += vx + vy;
    }
  }
  return { gx, gy, mean: sum / ((w - 2) * (h - 2)) };
}

type Line = { a: number; c: number; m: number; score: number }; // pos = c + a * (t - m)

/**
 * Best straight edge near one side of rect R. "v" sides (left/right) are
 * x = f(y) lines found in |gx|; "h" sides (top/bottom) are y = f(x) in |gy|.
 * Small Hough over (slope, offset), counting scanlines with an edge pixel
 * on the line, so a second parallel edge (the art frame) doesn't split votes.
 */
function findSide(G: Grad, w: number, h: number, pos: number, from: number, to: number,
                  band: number, vertical: boolean, thr: number): Line {
  const mag = vertical ? G.gx : G.gy;
  const lim = vertical ? w : h;
  const lo = Math.max(1, Math.round(pos - band));
  const hi = Math.min(lim - 2, Math.round(pos + band));
  const t0 = Math.max(1, Math.round(from));
  const t1 = Math.min((vertical ? h : w) - 2, Math.round(to));
  const n = t1 - t0 + 1;
  const m = (t0 + t1) / 2;
  if (hi <= lo || n < 8) return { a: 0, c: pos, m, score: 0 };

  // candidate edge positions per scanline: local maxima above threshold, strongest few
  const cands: number[][] = [];
  for (let t = t0; t <= t1; t++) {
    const row: [number, number][] = [];
    for (let s = lo; s <= hi; s++) {
      const i = vertical ? t * w + s : s * w + t;
      const step = vertical ? 1 : w;
      const v = mag[i];
      if (v >= thr && v >= mag[i - step] && v >= mag[i + step]) row.push([v, s]);
    }
    row.sort((p, q) => q[0] - p[0]);
    cands.push(row.slice(0, 8).map((r) => r[1]));
  }

  const slopes: number[] = [];
  for (let k = -10; k <= 10; k++) slopes.push(k * 0.02); // ±0.2 ≈ ±11°
  const span = (n / 2) * 0.2 + 2;
  const base = Math.floor(lo - span);
  const nb = Math.ceil(hi + span) - base + 1;
  let best: Line = { a: 0, c: pos, m, score: 0 };
  const acc = new Int32Array(nb);
  const stamp = new Int32Array(nb);
  for (const a of slopes) {
    acc.fill(0);
    stamp.fill(-1);
    for (let j = 0; j < n; j++) {
      const t = t0 + j;
      for (const s of cands[j]) {
        const b = Math.round(s - a * (t - m)) - base;
        for (let d = -1; d <= 1; d++) {
          const k = b + d;
          if (k >= 0 && k < nb && stamp[k] !== j) {
            stamp[k] = j;
            acc[k]++;
          }
        }
      }
    }
    for (let k = 0; k < nb; k++) {
      const sc = acc[k] / n;
      if (sc > best.score) best = { a, c: k + base, m, score: sc };
    }
  }
  return best;
}

function intersect(v: Line, hz: Line): Pt {
  // v: x = cv + av (y - mv);  hz: y = ch + ah (x - mh)
  const x = (v.c + v.a * (hz.c - hz.a * hz.m - v.m)) / (1 - v.a * hz.a);
  return [x, hz.c + hz.a * (x - hz.m)];
}

const dist = (p: Pt, q: Pt) => Math.hypot(p[0] - q[0], p[1] - q[1]);

/**
 * Find a card (or card grid) in the guide. Gray frame w×h, guide in the same
 * pixel units. Returns the lock (quad corners TL, TR, BR, BL) or null.
 */
export function findCardInGuide(gray: Float32Array, w: number, h: number, guide: Rect,
                                minScore = 0.55): Lock | null {
  const G = sobel(gray, w, h);
  const thr = Math.max(40, 2.5 * G.mean);
  const aspect = guide.w / guide.h;
  for (const scale of [1.0, 0.84, 0.7]) {
    const R = { w: guide.w * scale, h: guide.h * scale, x: 0, y: 0 };
    R.x = guide.x + (guide.w - R.w) / 2;
    R.y = guide.y + (guide.h - R.h) / 2;
    // ±9 % covers the gap to the next scale step (each side moves 8 % per step)
    const bx = 0.09 * R.w;
    const by = 0.09 * R.h;
    const left = findSide(G, w, h, R.x, R.y + 0.18 * R.h, R.y + 0.82 * R.h, bx, true, thr);
    if (left.score < minScore) continue;
    const right = findSide(G, w, h, R.x + R.w, R.y + 0.18 * R.h, R.y + 0.82 * R.h, bx, true, thr);
    if (right.score < minScore) continue;
    const top = findSide(G, w, h, R.y, R.x + 0.18 * R.w, R.x + 0.82 * R.w, by, false, thr);
    if (top.score < minScore) continue;
    const bottom = findSide(G, w, h, R.y + R.h, R.x + 0.18 * R.w, R.x + 0.82 * R.w, by, false, thr);
    if (bottom.score < minScore) continue;
    const quad: [Pt, Pt, Pt, Pt] = [intersect(left, top), intersect(right, top),
                                    intersect(right, bottom), intersect(left, bottom)];
    const qw = (dist(quad[0], quad[1]) + dist(quad[3], quad[2])) / 2;
    const qh = (dist(quad[0], quad[3]) + dist(quad[1], quad[2])) / 2;
    if (qh < 1 || Math.abs(qw / qh / aspect - 1) > 0.16) continue; // not card-shaped
    if (qw * qh < 0.35 * guide.w * guide.h) continue;              // too small
    return { quad, scale, score: Math.min(left.score, right.score, top.score, bottom.score) };
  }
  return null;
}

/** Largest corner movement between two locks, as a fraction of the guide diagonal. */
export function lockMovement(a: Lock, b: Lock, guide: Rect): number {
  let mv = 0;
  for (let i = 0; i < 4; i++) mv = Math.max(mv, dist(a.quad[i], b.quad[i]));
  return mv / Math.hypot(guide.w, guide.h);
}
