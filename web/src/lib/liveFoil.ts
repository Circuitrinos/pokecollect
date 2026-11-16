// Live-Scan: Folie erkennen, während sich die Kamera bewegt. Gedruckte Farbe sieht
// aus jedem Winkel gleich aus, Folie nicht: sie wird je nach Winkel heller, dunkler
// und schillert. Die App nimmt dazu mehrmals pro Sekunde einen kleinen Farbausschnitt
// jeder verfolgten Karte (mit der Kamera mitgeführt) und misst je Bildpunkt, wie stark
// sich Helligkeit und Farbton über die Zeit ändern — nach Ausgleich der Belichtung.
// Wo das passiert, sagt die Art: im Bildfenster → Holo, im Rest der Karte → Reverse
// Holo; Basis-Energien sind eine Fläche: Holo oder nicht. Weiße, überstrahlte Stellen
// (Spiegelung der Hülle, Lampe) zählen nicht — dort ist keine Farbe zu messen.
// Ränder (Text, Umrisse) zittern durch kleine Ungenauigkeiten der Verfolgung; gezählt
// wird daher nur die ruhigere Hälfte der Bildpunkte eines Bereichs.
// Reine Rechnung ohne DOM, damit sie sich für sich prüfen lässt.

export const PATCH_W = 24;
export const PATCH_H = 34;
/** Das Bildfenster einer normalen Karte (Anteile): wie matcher/detect.py ART_*. */
export const ART_BOX = { x0: 0.085, y0: 0.12, x1: 0.915, y1: 0.5 };
const BORDER = 0.05;          // Außenrand der Karte: zählt nicht (Hintergrund, Hüllenkante)
const CLIP = 248;             // so hell: überstrahlt
const MIN_SAMPLES = 8;        // so viele Ausschnitte je Karte mindestens
const MIN_SPREAD = 6;         // Grad Neigung zwischen den Ausschnitten (mit Lagesensor)
const MIN_SECONDS = 2.5;      // ohne Lagesensor: so lange mindestens beobachtet
const KEEP = 40;              // je Karte die letzten Ausschnitte
export const FOIL_MIN = 0.18; // Antwort eines Bereichs ab hier: Folie (vorläufig — die Protokolle eichen es)
export const FOIL_LEAD = 1.7; // … und so viel stärker als der andere Bereich
// Brians Protokolle (15.11.): beim Scannen einer ganzen Seite antworten auch Karten ohne Folie mit
// 0.2–0.4 (die Rahmen wackeln um einige Prozent, gemustertes Artwork ändert sich damit: Rare Candy
// 1.0 auf der Seite, 0.05 allein). Folie heißt darum auch: deutlich stärker als die übliche Karte
// desselben Scans (Median über alle) — bei ruhigen Scans bleibt es bei FOIL_MIN.
export const FOIL_REL = 1.6;
// Brians Testkarten mit bekannter Folie (15.11., Umrisse exakt aus dem Video nachgerechnet):
// Firebreather und Tool Scrapper (Holo) 0.04 wie die vier normalen Karten daneben (0.03–0.11),
// Rayquaza EX (ganze Karte glitzernd, bis 15° geneigt) 0.08–0.17. Das Maß trennt Folie nicht —
// die Hinweise bleiben aus, gemessen und protokolliert („live.foil.final") wird weiter.
export const LIVE_FOIL_HINTS = false;
const REL_MIN_CARDS = 6;
const CHROMA = 5;             // Gewicht des Farbtons gegenüber der Helligkeit

export type FoilKind = "Holo" | "Reverse Holo" | "Normal";
export type FoilScore = { art: number; frame: number; whole: number; n: number; spread: number | null; seconds: number };
export type LiveFoil = FoilScore & { kind: FoilKind };

type Sample = { lum: Float32Array; r: Float32Array; g: Float32Array; ok: Uint8Array; angle: [number, number] | null; t: number };

/** Ein Ausschnitt (RGBA, PATCH_W×PATCH_H) → Helligkeit relativ zur Karte, Farbanteile, gültige Punkte. */
export function toSample(rgba: Uint8ClampedArray, angle: [number, number] | null, t: number): Sample | null {
  const n = PATCH_W * PATCH_H;
  const lum = new Float32Array(n), r = new Float32Array(n), g = new Float32Array(n), ok = new Uint8Array(n);
  const vals: number[] = [];
  for (let i = 0; i < n; i++) {
    const R = rgba[4 * i], G = rgba[4 * i + 1], B = rgba[4 * i + 2];
    const s = R + G + B;
    lum[i] = 0.299 * R + 0.587 * G + 0.114 * B;
    r[i] = s ? R / s : 1 / 3;
    g[i] = s ? G / s : 1 / 3;
    ok[i] = Math.max(R, G, B) < CLIP && s > 30 ? 1 : 0;   // überstrahlt oder fast schwarz: nicht messbar
    if (ok[i]) vals.push(lum[i]);
  }
  if (vals.length < n * 0.3) return null;
  vals.sort((a, b) => a - b);
  const med = vals[vals.length >> 1] || 1;
  for (let i = 0; i < n; i++) lum[i] /= med;             // Belichtung der Kamera ausgleichen
  return { lum, r, g, ok, angle, t };
}

/** Kleine Neigungswinkel (Grad, um x und y) aus der Drehung des Lagesensors (3×3, zeilenweise). */
export function tiltAngles(turn: number[] | null): [number, number] | null {
  if (!turn) return null;
  const ax = Math.atan2(turn[7], turn[8]), ay = Math.atan2(-turn[6], Math.hypot(turn[7], turn[8]));
  return [ax * 180 / Math.PI, ay * 180 / Math.PI];
}

const median = (a: number[]) => {
  if (!a.length) return 0;
  const s = [...a].sort((x, y) => x - y);
  return s[s.length >> 1];
};

const inArt = (u: number, v: number) => u >= ART_BOX.x0 && u <= ART_BOX.x1 && v >= ART_BOX.y0 && v <= ART_BOX.y1;

const BLOCK = 3;              // Bildpunkte je Block: kleine Ungenauigkeiten der Verfolgung mitteln sich heraus
const BW = Math.floor(PATCH_W / BLOCK), BH = Math.floor(PATCH_H / BLOCK);

/** Blockmittel eines Ausschnitts (Helligkeit, Farbanteile); NaN, wo zu wenig messbar war. */
function blocks(s: Sample): { lum: Float32Array; r: Float32Array; g: Float32Array } {
  const lum = new Float32Array(BW * BH), r = new Float32Array(BW * BH), g = new Float32Array(BW * BH);
  for (let by = 0; by < BH; by++) {
    for (let bx = 0; bx < BW; bx++) {
      let k = 0, sl = 0, sr = 0, sg = 0;
      for (let y = by * BLOCK; y < (by + 1) * BLOCK; y++) {
        for (let x = bx * BLOCK; x < (bx + 1) * BLOCK; x++) {
          const i = y * PATCH_W + x;
          if (!s.ok[i]) continue;
          k++; sl += s.lum[i]; sr += s.r[i]; sg += s.g[i];
        }
      }
      const j = by * BW + bx;
      const enough = k * 2 > BLOCK * BLOCK;
      lum[j] = enough ? sl / k : NaN; r[j] = enough ? sr / k : NaN; g[j] = enough ? sg / k : NaN;
    }
  }
  return { lum, r, g };
}

/** Wie stark sich ein Bereich über die Ausschnitte ändert (Blöcke, ruhigere Hälfte, Median). */
export function scoreSamples(list: Sample[]): FoilScore | null {
  if (list.length < MIN_SAMPLES) return null;
  const bl = list.map(blocks);
  const n = BW * BH;
  const resp = new Float32Array(n).fill(NaN), mean = new Float32Array(n).fill(NaN), edge = new Float32Array(n);
  for (let j = 0; j < n; j++) {
    let k = 0, sl = 0, sl2 = 0, sr = 0, sr2 = 0, sg = 0, sg2 = 0;
    for (const b of bl) {
      if (!Number.isFinite(b.lum[j])) continue;
      k++; sl += b.lum[j]; sl2 += b.lum[j] ** 2; sr += b.r[j]; sr2 += b.r[j] ** 2; sg += b.g[j]; sg2 += b.g[j] ** 2;
    }
    if (k < Math.max(4, bl.length * 0.5)) continue;     // meist überstrahlt: nicht messbar
    const v = (s: number, s2: number) => Math.max(0, s2 / k - (s / k) ** 2);
    mean[j] = sl / k;
    resp[j] = Math.sqrt(v(sl, sl2)) + CHROMA * Math.sqrt(v(sr, sr2) + v(sg, sg2));
  }
  for (let by = 0; by < BH; by++) {       // Kanten des mittleren Bilds (dort zittert die Verfolgung am meisten)
    for (let bx = 0; bx < BW; bx++) {
      const j = by * BW + bx;
      const gx = bx > 0 && bx < BW - 1 ? mean[j + 1] - mean[j - 1] : 0;
      const gy = by > 0 && by < BH - 1 ? mean[j + BW] - mean[j - BW] : 0;
      edge[j] = Math.hypot(gx || 0, gy || 0);
    }
  }
  const region = (want: (u: number, v: number) => boolean) => {
    const pts: { r: number; e: number }[] = [];
    for (let by = 0; by < BH; by++) {
      for (let bx = 0; bx < BW; bx++) {
        const u = (bx + 0.5) / BW, v = (by + 0.5) / BH;
        if (u < BORDER || u > 1 - BORDER || v < BORDER || v > 1 - BORDER || !want(u, v)) continue;
        const j = by * BW + bx;
        if (Number.isFinite(resp[j])) pts.push({ r: resp[j], e: edge[j] });
      }
    }
    if (pts.length < 4) return 0;
    const cut = median(pts.map((p) => p.e));
    return median(pts.filter((p) => p.e <= cut).map((p) => p.r));
  };
  const angles = list.map((s) => s.angle).filter((a): a is [number, number] => !!a);
  let spread: number | null = null;
  if (angles.length >= list.length / 2) {
    const xs = angles.map((a) => a[0]), ys = angles.map((a) => a[1]);
    spread = Math.hypot(Math.max(...xs) - Math.min(...xs), Math.max(...ys) - Math.min(...ys));
  }
  return {
    art: region(inArt), frame: region((u, v) => !inArt(u, v)), whole: region(() => true),
    n: list.length, spread, seconds: (list[list.length - 1].t - list[0].t) / 1000,
  };
}

/**
 * Die Art der Folie aus den Werten — oder null, solange zu wenig gesehen wurde
 * (zu wenige Ausschnitte, zu wenig geneigt). wholeCard: Basis-Energie (eine Fläche).
 */
export function judgeFoil(s: FoilScore | null, wholeCard: boolean, bar = FOIL_MIN): FoilKind | null {
  if (!s) return null;
  if (s.spread != null ? s.spread < MIN_SPREAD : s.seconds < MIN_SECONDS) return null;
  if (wholeCard) return s.whole >= bar ? "Holo" : "Normal";
  if (s.art >= bar && s.art >= FOIL_LEAD * s.frame) return "Holo";
  if (s.frame >= bar && s.frame >= FOIL_LEAD * s.art) return "Reverse Holo";
  if (s.art < bar && s.frame < bar) return "Normal";
  return null;            // beides glänzt ähnlich: unklar (Vollbild-Karte, Spiegelung) — keine Aussage
}

/** Ab welcher Antwort ein Bereich in diesem Scan als Folie gilt: FOIL_MIN, oder FOIL_REL × die
 *  übliche Antwort (Median „whole") der Karten, wenn genug genau genug gesehen wurden. */
export function foilBar(scores: (FoilScore | null)[]): number {
  const w = scores.filter((s): s is FoilScore => !!s && s.n >= MIN_SAMPLES).map((s) => s.whole);
  return w.length >= REL_MIN_CARDS ? Math.max(FOIL_MIN, FOIL_REL * median(w)) : FOIL_MIN;
}

/** Basis-Energie: das Bild ist die ganze Karte — nur Holo oder nicht. Mega Evolution (MEE)
 *  und manche andere Drucke heißen ohne „Basic" („Fighting Energy"); Spezial-Energien („Jet Energy") nicht. */
const BASIC_ENERGY = /^(basic\s+\S+|grass|fire|water|lightning|psychic|fighting|darkness|metal|fairy)\s+energy$/i;
export const isBasicEnergy = (name: string | null | undefined) => BASIC_ENERGY.test((name ?? "").trim());

/** Ausschnitte je Karte (Schlüssel der Live-Karte). */
/**
 * Einen Ausschnitt weglassen, wenn es zu viele sind. Mit Lagesensor den, dessen
 * Neigung einem anderen am nächsten liegt (bei Gleichstand den älteren): so
 * bleiben die stark geneigten Blicke vom Anfang erhalten, auch wenn das Handy
 * danach lange ruhig gehalten wird. Ohne Sensor den ältesten.
 */
function dropOne(list: Sample[]): void {
  if (!list.every((s) => s.angle)) { list.shift(); return; }
  let drop = 0, nearest = Infinity;
  for (let i = 0; i < list.length - 1; i++) {        // der neueste bleibt immer
    const [ax, ay] = list[i].angle!;
    let near = Infinity;
    for (let j = 0; j < list.length; j++) {
      if (j === i) continue;
      const [bx, by] = list[j].angle!;
      near = Math.min(near, Math.hypot(ax - bx, ay - by));
    }
    if (near < nearest) { nearest = near; drop = i; }
  }
  list.splice(drop, 1);
}

export class FoilWatch {
  private samples = new Map<string, Sample[]>();

  add(key: string, rgba: Uint8ClampedArray, angle: [number, number] | null, t: number): void {
    const s = toSample(rgba, angle, t);
    if (!s) return;
    const list = this.samples.get(key) ?? [];
    list.push(s);
    if (list.length > KEEP) dropOne(list);
    this.samples.set(key, list);
  }

  score(key: string): FoilScore | null {
    return scoreSamples(this.samples.get(key) ?? []);
  }

  keys(): string[] {
    return Array.from(this.samples.keys());
  }

  forget(keep: Set<string>): void {
    for (const k of Array.from(this.samples.keys())) if (!keep.has(k)) this.samples.delete(k);
  }

  clear(): void {
    this.samples.clear();
  }
}

/** Ausschnitt einer Karte: affine Abbildung Ausschnitt (PATCH_W×PATCH_H) → Bild aus drei Ecken (TL, TR, BL; Pixel). */
export function patchTransform(quad: number[][]): [number, number, number, number, number, number] | null {
  const [tl, tr, , bl] = quad;
  // A: Ausschnitt → Bild;  Canvas braucht Bild → Ausschnitt (die Umkehrung)
  const a = (tr[0] - tl[0]) / PATCH_W, c = (bl[0] - tl[0]) / PATCH_H, e = tl[0];
  const b = (tr[1] - tl[1]) / PATCH_W, d = (bl[1] - tl[1]) / PATCH_H, f = tl[1];
  const det = a * d - b * c;
  if (!det || !Number.isFinite(det)) return null;
  const ia = d / det, ib = -b / det, ic = -c / det, id = a / det;
  return [ia, ib, ic, id, -(ia * e + ic * f), -(ib * e + id * f)];
}
