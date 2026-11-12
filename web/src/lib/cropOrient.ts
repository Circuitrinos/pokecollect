// Ausrichtung im Ecken-Editor: was auf dem Bildschirm oben ist, ist oben.
// Die Ecken eines Kartenvierecks stehen in der Reihenfolge Oben-links, Oben-rechts,
// Unten-rechts, Unten-links DER KARTE — die Reihenfolge trägt die Ausrichtung, ein
// gedrehtes/gespiegeltes Bild in der Ansicht ordnet sie nur neu.

export type ViewOrient = { rotate: number; flipH: boolean; flipV: boolean };
export const UPRIGHT: ViewOrient = { rotate: 0, flipH: false, flipV: false };

/** Normierter Bildpunkt → Lage auf dem Bildschirm relativ zur Bildmitte (Pixel), wie die Ansicht (CSS rotate·scale) ihn zeigt. */
function viewPoint(p: number[], o: ViewOrient, w: number, h: number): number[] {
  let x = (p[0] - 0.5) * w;
  let y = (p[1] - 0.5) * h;
  if (o.flipH) x = -x;
  if (o.flipV) y = -y;
  const r = (o.rotate * Math.PI) / 180;
  return [x * Math.cos(r) - y * Math.sin(r), x * Math.sin(r) + y * Math.cos(r)];
}

/**
 * Ecken-Indizes in Bildschirm-Reihenfolge: im Uhrzeigersinn, beginnend mit der
 * Ecke, an der die oberste Kante anfängt (oben links).
 */
export function screenOrderIdx(quad: number[][], o: ViewOrient, w: number, h: number): number[] {
  const p = quad.map((q) => viewPoint(q, o, w, h));
  let area = 0;
  for (let i = 0; i < 4; i++) area += p[i][0] * p[(i + 1) % 4][1] - p[(i + 1) % 4][0] * p[i][1];
  const ring = area >= 0 ? [0, 1, 2, 3] : [0, 3, 2, 1];   // y nach unten: im Uhrzeigersinn = positive Fläche
  const tol = 0.5;
  let best = 0;
  let bestMid = [Infinity, Infinity];
  for (let k = 0; k < 4; k++) {
    const a = p[ring[k]], b = p[ring[(k + 1) % 4]];
    const mid = [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2];
    if (mid[1] < bestMid[1] - tol || (Math.abs(mid[1] - bestMid[1]) <= tol && mid[0] < bestMid[0])) {
      best = k;
      bestMid = mid;
    }
  }
  return [0, 1, 2, 3].map((k) => ring[(best + k) % 4]);
}

/** Ecken so umsortiert, dass die Karte so herum zugeschnitten wird, wie die Ansicht sie zeigt. */
export function screenOrder(quad: number[][], o: ViewOrient, w: number, h: number): number[][] {
  return screenOrderIdx(quad, o, w, h).map((i) => [...quad[i]]);
}

/** Ansicht, in der die Ecken in ihrer eigenen Reihenfolge oben links, oben rechts, … liegen (Karte aufrecht). */
export function viewFor(quad: number[][], w: number, h: number): ViewOrient {
  for (const flipH of [false, true]) {
    for (const rotate of [0, 90, 180, 270]) {
      const o = { rotate, flipH, flipV: false };
      if (screenOrderIdx(quad, o, w, h).every((v, i) => v === i)) return o;
    }
  }
  return UPRIGHT;
}

/** Nach Bildposition: oben links, oben rechts, unten rechts, unten links. */
export function orderByPosition(pts: number[][]): number[][] {
  const byY = [...pts].sort((a, b) => a[1] - b[1]);
  const top = byY.slice(0, 2).sort((a, b) => a[0] - b[0]);
  const bottom = byY.slice(2, 4).sort((a, b) => a[0] - b[0]);
  return [top[0], top[1], bottom[1], bottom[0]];
}

/**
 * Ecken in der Reihenfolge, mit der der Zuschnitt sie nimmt (w×h = Bildmaße):
 * byPosition → erst nach Bildposition sortieren (erkannte Ecken ohne verlässliche
 * Reihenfolge); liegt das Viereck quer, um eins weiter (Karten sind Hochformat).
 */
export function cardOrder(quad: number[][], w: number, h: number, byPosition = false): number[][] {
  let q = byPosition ? orderByPosition(quad) : quad.map((p) => [...p]);
  const d = (a: number[], b: number[]) => Math.hypot((a[0] - b[0]) * w, (a[1] - b[1]) * h);
  if (d(q[0], q[1]) > d(q[0], q[3])) q = [q[1], q[2], q[3], q[0]];
  return q;
}

/** Ecken rund um die Mitte sortiert — zum Zeichnen (eine Erkennung liefert sie nicht immer der Reihe nach). */
export function ringOrder(quad: number[][]): number[][] {
  const cx = quad.reduce((a, p) => a + p[0], 0) / quad.length;
  const cy = quad.reduce((a, p) => a + p[1], 0) / quad.length;
  return [...quad].sort((a, b) => Math.atan2(a[1] - cy, a[0] - cx) - Math.atan2(b[1] - cy, b[0] - cx));
}
