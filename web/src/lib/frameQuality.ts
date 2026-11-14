// Bildgüte fürs Live-Scannen: ist das Bild scharf, und steht das Handy still?
// Beides auf einem kleinen Graubild (toGray aus cardLock), ein paar Mal pro Sekunde.

/** Schärfe: Varianz des Laplace-Filters im inneren Bildbereich (größer = schärfer). */
export function sharpness(g: Float32Array, w: number, h: number): number {
  const x0 = Math.max(1, Math.round(w * 0.1)), x1 = Math.min(w - 1, Math.round(w * 0.9));
  const y0 = Math.max(1, Math.round(h * 0.1)), y1 = Math.min(h - 1, Math.round(h * 0.9));
  let n = 0, sum = 0, sq = 0;
  for (let y = y0; y < y1; y++) {
    const row = y * w;
    for (let x = x0; x < x1; x++) {
      const i = row + x;
      const l = 4 * g[i] - g[i - 1] - g[i + 1] - g[i - w] - g[i + w];
      sum += l;
      sq += l * l;
      n++;
    }
  }
  if (!n) return 0;
  const mean = sum / n;
  return sq / n - mean * mean;
}

/** Bewegung zwischen zwei Graubildern gleicher Größe: mittlere Helligkeitsänderung (0–255). */
export function frameDiff(a: Float32Array, b: Float32Array): number {
  if (a.length !== b.length || !a.length) return Infinity;
  let s = 0;
  for (let i = 0; i < a.length; i += 2) s += Math.abs(a[i] - b[i]);
  return s / Math.ceil(a.length / 2);
}

/** Bildfolge beobachten: ein Bild ist gut, wenn es kaum vom vorigen abweicht und fast so
 *  scharf ist wie die schärfsten der letzten Sekunde. */
export class FrameJudge {
  private prev: Float32Array | null = null;
  private recent: { at: number; s: number }[] = [];
  private readonly stillDiff: number;
  private readonly sharpShare: number;
  private readonly windowMs: number;

  constructor(stillDiff = 4.5, sharpShare = 0.85, windowMs = 1500) {
    this.stillDiff = stillDiff;
    this.sharpShare = sharpShare;
    this.windowMs = windowMs;
  }

  /** → still (kaum Bewegung zum vorigen Bild), sharp (fast so scharf wie die besten zuletzt). */
  judge(g: Float32Array, w: number, h: number, now: number): { still: boolean; sharp: boolean; diff: number; s: number } {
    const diff = this.prev ? frameDiff(this.prev, g) : Infinity;
    this.prev = g;
    const s = sharpness(g, w, h);
    this.recent = this.recent.filter((r) => now - r.at < this.windowMs);
    this.recent.push({ at: now, s });
    const top = Math.max(...this.recent.map((r) => r.s));
    return { still: diff <= this.stillDiff, sharp: s >= this.sharpShare * top, diff, s };
  }

  reset() {
    this.prev = null;
    this.recent = [];
  }
}
