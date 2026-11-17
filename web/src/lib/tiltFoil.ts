// Einzelkarte: Folie beim Neigen erkennen. Die Karte wird mehrmals pro Sekunde aus dem
// Kamerabild entzerrt (126×176, perspektivisch), am Bild selbst auf ein festes Bezugsbild
// ausgerichtet (lib/cardAlign — der verfolgte Umriss allein lag zu weit daneben) und über die
// Zeit verglichen. Zwei Prüfungen, an Brians Videos
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
// Geurteilt wird erst, wenn die Karte genug geneigt wurde (Verzerrung des ausgerichteten Umrisses);
// nur unter den Folierungen, die es
// von der Karte gibt. Die Schwellen sind vorläufig (drei Karten) — „live.tiltfoil" protokolliert
// die Werte, um sie an mehr Karten nachzustellen. Reine Rechnung ohne DOM (prüfbar für sich).

import type { FoilKind } from "./liveFoil";
import { alignToCatalog, alignToRef, catalogEdges, keystone, pyramid, quadFromUV, quadToUV, warpCard, type Aligned, type Pixels,
  type Pyramid } from "./cardAlign";

export { keystone, quadHomography, warpCard, type Pixels } from "./cardAlign";

export const TILT_W = 126;
export const TILT_H = 176;
const N = TILT_W * TILT_H;

const KEEP = 60;               // je Karte die letzten Bilder (~8 s)
const REF_PICK = 6;            // Bezugsbild: frühestens nach so vielen Bildern am Katalogbild angelegt (das am besten passende
const REF_TRY = 5;             // der so vielen schärfsten); passt keins, mit jedem weiteren Bild neu versucht …
const REF_WAIT = 24;           // … ohne Katalogbild (oder ohne Treffer) nach so vielen das schärfste mit dem verfolgten Umriss
const MIN_INL = 16, MAX_RMS = 2.0;   // ausgerichtetes Bild gilt nur mit so vielen passenden Blöcken (von 48) und so wenig Rest (Punkte)
const LOG_EVERY = 6;           // Debug: jedes so-vielte ausgerichtete Bild verkleinert protokollieren
const CLIP = 245;              // so hell: überstrahlt (nach dem Verkleinern)
const GLARE = 0.02;            // Anteil überstrahlter Punkte: ab hier Blendung (Holo-Prüfung ohne dieses Bild)
const BRIGHT = Math.log(0.6);  // gleich hell: mindestens 60 % des Rahmens
const SIGMA = 2;               // feines Muster: Abweichung vom Mittel der Umgebung (~2 mm)
export const TILT_MIN_USED = 20;    // so viele scharfe Bilder mindestens
export const TILT_EARLY = 14;       // schon ab so vielen: nur ein deutliches Ja (Holo; Reverse Holo am schwankenden Rahmen) —
                                    // „keine Folie" und Muster im Rahmen brauchen mehr (16.11., Lampe: sonst früh falsch)
export const TILT_SPREAD = 0.10;    // Neigung: Spanne der Verzerrung (p10–p90) in einer Richtung
export const LIT_RANGE = 1.3;       // hellste Momente mindestens so viel heller als üblich (zum Licht gedreht)
export const HOLO_RATIO = 2.1, HOLO_RATIO_NO = 1.6, HOLO_ART = 0.15;   // 17.11.: Brock's Scouting (normal) bis 1.8–2.15
export const RH_SWING = 0.30, RH_SWING_NO = 0.22;
export const RH_TEX = 1.05, RH_TEX_NO = 0.95;

const LIN = new Float32Array(256);
for (let i = 0; i < 256; i++) { const c = i / 255; LIN[i] = c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4; }

// Bereiche (Anteile der Karte): innen ohne Außenrand; Bildfenster eng (passt bei Pokémon und Trainern);
// Rahmen = innen ohne ein großzügiges Bildfenster
const INNER = new Uint8Array(N), ART = new Uint8Array(N), FRAME = new Uint8Array(N);
for (let y = 0; y < TILT_H; y++) for (let x = 0; x < TILT_W; x++) {
  const fx = x / TILT_W, fy = y / TILT_H, i = y * TILT_W + x;
  INNER[i] = fx > 0.06 && fx < 0.94 && fy > 0.045 && fy < 0.955 ? 1 : 0;   // Rand großzügig: der Bezug liegt evtl. etwas daneben
  ART[i] = fx > 0.10 && fx < 0.90 && fy > 0.16 && fy < 0.48 ? 1 : 0;
  FRAME[i] = INNER[i] && !(fx > 0.07 && fx < 0.93 && fy > 0.12 && fy < 0.53) ? 1 : 0;
}

// ein Bild der Karte, schon am Bezug ausgerichtet: Helligkeit (linear), unbrauchbare Punkte, Helligkeit
// relativ zum Rahmen (log, NaN = unbrauchbar), Rahmen-Median, feines Muster im Rahmen
type Sample = {
  L: Float32Array; B: Uint8Array; LG: Float32Array; nm: number; tex: number;
  sharp: number; clip: number; ks: [number, number]; t: number; inl: number; rms: number;
};
type Raw = { px: Pixels; quad: number[][]; t: number; sharp: number; tried?: boolean };
type Card = {
  cat: Float32Array | null;                           // Kanten des Katalogbilds (Stufe 0), wenn bekannt
  snap: number | null;                                // Bezug am Katalog verschoben (größte Ecke, Anteil der Breite); null: nicht angelegt
  raws: Raw[];                                        // bis das Bezugsbild feststeht
  ref: { pyr: Pyramid; grad: Float32Array; calm: Uint8Array } | null;
  // wo die Karte zuletzt wirklich lag, relativ zum verfolgten Umriss (u, v): Startpunkt fürs nächste Bild —
  // Brian 17.11.: der Umriss lag über Sekunden gleichbleibend daneben (bis ~45 % zu groß, ~25 % verschoben)
  corr: number[][] | null;
  list: Sample[]; dropped: number; seen: number;
};

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
  const s = Float32Array.from(xs.filter((x) => Number.isFinite(x))).sort();
  return quantile(s, s.length, 0.5);
}

/** Ein Bild der Karte (RGBA TILT_W×TILT_H aus warpCard) → Helligkeit (linear), unbrauchbare Punkte, Kanten, Schärfe. */
function basics(rgba: Uint8ClampedArray) {
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
  return { lum, bad, grad, sharp: s2 / m - (s / m) ** 2, clip: clip / Math.max(1, inner) };
}

/** Ausgerichtete Helligkeit verkleinert (63×88, sRGB-kodiert wie lumaB64) für das Debug-Protokoll. */
function small(L: Float32Array): { w: number; h: number; b64: string } {
  const w = TILT_W >> 1, h = TILT_H >> 1, bytes = new Uint8Array(w * h);
  for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
    const i = 2 * y * TILT_W + 2 * x, v = (L[i] + L[i + 1] + L[i + TILT_W] + L[i + TILT_W + 1]) / 4;
    const e = v <= 0.0031308 ? 12.92 * v : 1.055 * v ** (1 / 2.4) - 0.055;
    bytes[y * w + x] = Math.max(0, Math.min(255, Math.round(e * 255)));
  }
  let bin = "";
  for (let i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i]);
  return { w, h, b64: btoa(bin) };
}

/** Schärfe allein (für die Wahl des Bezugsbilds). */
function sharpness(rgba: Uint8ClampedArray): number {
  let s = 0, s2 = 0, m = 0;
  const gray = (i: number) => 0.299 * rgba[4 * i] + 0.587 * rgba[4 * i + 1] + 0.114 * rgba[4 * i + 2];
  for (let y = 1; y < TILT_H - 1; y++) for (let x = 1; x < TILT_W - 1; x++) {
    const i = y * TILT_W + x;
    const l = gray(i - 1) + gray(i + 1) + gray(i - TILT_W) + gray(i + TILT_W) - 4 * gray(i);
    s += l; s2 += l * l; m += 1;
  }
  return s2 / m - (s / m) ** 2;
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
  early: boolean;                   // erst genug für ein deutliches Ja
  kind: FoilKind | null;            // nur unter den Folierungen der Karte
  dropped: number;                  // Bilder, die sich nicht ausrichten ließen
  rms: number | null;               // Ausrichtung: Restabweichung der Blöcke (Punkte, Median)
  snap: number | null;              // Bezug am Katalogbild verschoben (Anteil der Breite); null: nicht angelegt
};

/** Folierung aus den beiden Prüfungen, nur unter `options` (leer: alle). */
export function tiltKind(r: Pick<TiltResult, "holo" | "rh" | "ready"> & Partial<Pick<TiltResult, "early" | "swing">>,
  options: string[]): FoilKind | null {
  const has = (k: FoilKind) => !options.length || options.includes(k);
  if (!r.ready) {
    if (!r.early) return null;
    if (r.swing != null && r.swing >= RH_SWING && r.holo !== "yes" && has("Reverse Holo")) return "Reverse Holo";
    if (r.holo === "yes" && r.rh !== "yes" && has("Holo")) return "Holo";
    return null;
  }
  if (r.rh === "yes" && has("Reverse Holo")) return "Reverse Holo";
  if (r.holo === "yes" && r.rh !== "yes" && has("Holo")) return "Holo";
  const noRh = r.rh === "no" || !has("Reverse Holo");
  const noHolo = r.holo === "no" || !has("Holo");
  if (noRh && noHolo && has("Normal")) return "Normal";
  if (noRh && has("Holo") && !has("Normal") && r.holo !== "no") return "Holo";   // Holo oder Reverse Holo: nicht Reverse Holo
  return null;
}

export type TiltSampleLog = { key: string; i: number; w: number; h: number; b64: string; ks: [number, number]; sharp: number;
  clip: number; inl: number; rms: number };

export class FoilTilt {
  private cards = new Map<string, Card>();
  /** zum Nachrechnen (Debug-Protokoll): jedes LOG_EVERY-te ausgerichtete Bild verkleinert (63×88) */
  onSample: ((s: TiltSampleLog) => void) | null = null;

  /** Ein Ausschnitt des Kamerabilds mit dem (ungefähren) Umriss der Karte darin. */
  add(key: string, px: Pixels, quad: number[][], t: number) {
    const c = this.card(key);
    if (!c.ref) {
      // Bezugsbild: das schärfste der ersten REF_PICK — bis dahin die Ausschnitte aufheben; mit Katalogbild
      // wird es daran angelegt (Bild- und Rahmenbereich sitzen dann), ohne nach REF_WAIT Bildern so
      const rgba = warpCard(px, quad, TILT_W, TILT_H);
      if (!rgba) return;
      c.raws.push({ px, quad, t, sharp: sharpness(rgba) });
      if (c.raws.length < REF_PICK || (!c.cat && c.raws.length < REF_WAIT)) return;
      let best = c.raws.reduce((a, b) => (b.sharp > a.sharp ? b : a));
      let refQuad = best.quad;
      if (c.cat) {
        // die schärfsten paar (noch nicht versuchten) am Katalog anlegen; das am besten passende wird der Bezug
        let pick: { raw: Raw; quad: number[][]; inl: number; rms: number } | null = null;
        for (const raw of [...c.raws].sort((a, b) => b.sharp - a.sharp).slice(0, REF_TRY)) {
          if (raw.tried) continue;
          raw.tried = true;
          const sn = alignToCatalog(raw.px, raw.quad, c.cat);
          if (sn && (!pick || sn.inliers > pick.inl || (sn.inliers === pick.inl && sn.rms < pick.rms))) pick = { raw, quad: sn.quad, inl: sn.inliers, rms: sn.rms };
        }
        if (pick) {
          best = pick.raw;
          refQuad = pick.quad;
          const w = Math.hypot(best.quad[1][0] - best.quad[0][0], best.quad[1][1] - best.quad[0][1]);
          c.snap = Math.max(...pick.quad.map((p, i) => Math.hypot(p[0] - best.quad[i][0], p[1] - best.quad[i][1]))) / Math.max(w, 1);
        } else if (c.raws.length < REF_WAIT) {
          return;   // keins passte: mit dem nächsten Bild noch einmal (der Umriss wird oft erst später besser)
        }
      }
      c.corr = quadToUV(best.quad, refQuad);
      const pyr = pyramid(best.px, refQuad), refRgba = warpCard(best.px, refQuad, TILT_W, TILT_H);
      if (!pyr || !refRgba) { c.raws = []; return; }
      const grad = basics(refRgba).grad;
      // ruhige Stellen (keine Druckkanten): Kanten des Bezugsbilds unter dem 70. Perzentil
      const gIn: number[] = [];
      for (let k = 0; k < N; k++) if (INNER[k]) gIn.push(grad[k]);
      gIn.sort((a, b) => a - b);
      const gCut = quantile(gIn, gIn.length, 0.7);
      const calm = new Uint8Array(N);
      for (let k = 0; k < N; k++) calm[k] = INNER[k] && grad[k] < gCut ? 1 : 0;
      c.ref = { pyr, grad, calm };
      const raws = c.raws;
      c.raws = [];
      for (const r of raws) this.process(key, c, r.px, r === best ? refQuad : r.quad, r.t, r === best);
      return;
    }
    this.process(key, c, px, quad, t, false);
  }

  private process(key: string, c: Card, px: Pixels, quad: number[][], t: number, isRef: boolean) {
    const ref = c.ref!;
    const good = (a: Aligned | null): a is Aligned => !!a && a.inliers >= MIN_INL && a.rms <= MAX_RMS;
    let al: Aligned | null = isRef ? { quad, inliers: 0, rms: 0 } : null;
    if (!isRef) {
      // erst dort, wo die Karte relativ zum Umriss zuletzt lag; sonst am Umriss selbst; sonst grob am Katalogbild
      const prior = c.corr ? quadFromUV(quad, c.corr) : null;
      for (const q0 of prior ? [prior, quad] : [quad]) {
        const a = alignToRef(px, q0, ref.pyr);
        if (good(a)) { al = a; break; }
      }
      if (!al && c.cat) {
        const sn = alignToCatalog(px, prior ?? quad, c.cat);
        const a = sn ? alignToRef(px, sn.quad, ref.pyr) : null;
        if (good(a)) al = a;
      }
      if (!al) { c.dropped += 1; return; }
      c.corr = quadToUV(quad, al.quad) ?? c.corr;
    }
    if (!al) return;
    const rgba = warpCard(px, al.quad, TILT_W, TILT_H);
    if (!rgba) { c.dropped += 1; return; }
    const { lum: L, bad: B, sharp, clip } = basics(rgba);
    const fr: number[] = [];
    for (let i = 0; i < N; i++) if (FRAME[i] && !B[i]) fr.push(L[i]);
    const nm = median(fr);
    // feines Muster im Rahmen: Abweichung vom geglätteten Bild, ohne Ränder und Punkte neben Überstrahltem
    const lg = new Float32Array(N), ok = new Uint8Array(N), sm = new Float32Array(N);
    for (let i = 0; i < N; i++) { lg[i] = Math.log(Math.max(L[i], 1e-4)); ok[i] = B[i] ? 0 : 1; }
    blurMasked(lg, ok, sm);
    const res: number[] = [];
    for (let y = 2; y < TILT_H - 2; y++) for (let x = 2; x < TILT_W - 2; x++) {
      const i = y * TILT_W + x;
      if (!FRAME[i] || !ref.calm[i]) continue;
      let near = false;
      for (let k = -2; k <= 2 && !near; k++) for (let m = -2; m <= 2; m++) if (B[i + k * TILT_W + m]) { near = true; break; }
      if (!near && Number.isFinite(sm[i])) res.push(Math.abs(lg[i] - sm[i]));
    }
    // Helligkeit relativ zum Rahmen (log), unbrauchbar: NaN
    const LG = new Float32Array(N);
    for (let i = 0; i < N; i++) LG[i] = B[i] || !(nm > 0) ? NaN : Math.log(Math.max(L[i] / nm, 1e-4));
    const s: Sample = { L, B, LG, nm, tex: res.length >= 50 ? median(res) : NaN, sharp, clip, ks: keystone(al.quad), t,
      inl: al.inliers, rms: al.rms };
    c.list.push(s);
    if (c.list.length > KEEP) c.list.shift();
    c.seen += 1;
    if (this.onSample && c.seen % LOG_EVERY === 1) this.onSample({ key, i: c.seen, ...small(L), ks: s.ks, sharp, clip, inl: al.inliers, rms: al.rms });
  }

  private card(key: string): Card {
    let c = this.cards.get(key);
    if (!c) { c = { cat: null, snap: null, raws: [], ref: null, corr: null, list: [], dropped: 0, seen: 0 }; this.cards.set(key, c); }
    return c;
  }

  /** Katalogbild der Karte (Grauwerte w×h, sRGB-kodiert wie /scan/catalog-luma): legt das Bezugsbild daran an
   *  (und fängt danach Bilder auf, die sich am Bezug nicht ausrichten ließen). */
  setCatalog(key: string, gray: Uint8Array, w: number, h: number) {
    this.card(key).cat = catalogEdges(gray, w, h);
  }

  count(key: string): number { return this.cards.get(key)?.list.length ?? 0; }

  forget(keep: Set<string>) {
    for (const k of Array.from(this.cards.keys())) if (!keep.has(k)) this.cards.delete(k);
  }

  clear() { this.cards.clear(); }

  result(key: string, options: string[]): TiltResult | null {
    const card = this.cards.get(key);
    if (!card) return null;
    const { list, ref } = card;
    if (!ref || !list.length) {
      const n = card.raws.length;
      return { n, used: 0, tilt: 0, lit: 1, art: null, frame: null, ratio: null, swing: null, tex: null, holo: null, rh: null,
        ready: false, early: false, progress: Math.min(0.2, (0.4 * n) / TILT_MIN_USED), kind: null, dropped: card.dropped, rms: null, snap: null };
    }
    const calm = ref.calm;

    const sharpCut = quantile(list.map((s) => s.sharp).sort((a, b) => a - b), list.length, 0.3);
    const sharp = list.filter((s) => s.sharp >= sharpCut);
    const calmGlare = sharp.filter((s) => s.clip <= GLARE);
    const ks = (j: 0 | 1) => { const v = list.map((s) => s.ks[j]).sort((a, b) => a - b); return quantile(v, v.length, 0.9) - quantile(v, v.length, 0.1); };
    const tilt = Math.max(ks(0), ks(1));
    const nms = sharp.map((s) => s.nm).filter((x) => x > 0).sort((a, b) => a - b);
    const p50 = quantile(nms, nms.length, 0.5), p85 = quantile(nms, nms.length, 0.85);
    const lit = p50 > 0 ? p85 / p50 : 1;
    const ready = sharp.length >= TILT_MIN_USED && tilt >= TILT_SPREAD;
    const early = sharp.length >= TILT_EARLY && tilt >= TILT_SPREAD;
    const progress = Math.min(1, 0.4 * Math.min(1, sharp.length / TILT_MIN_USED) + 0.6 * Math.min(1, tilt / TILT_SPREAD));
    const rms = median(list.map((s) => s.rms).filter((x) => x > 0));
    const out: TiltResult = { n: list.length, used: sharp.length, tilt, lit, art: null, frame: null, ratio: null, swing: null, tex: null,
      holo: null, rh: null, ready, early, progress, kind: null, dropped: card.dropped, rms: Number.isFinite(rms) ? rms : null, snap: card.snap };
    if (sharp.length < 8) return out;

    // je Punkt: Helligkeit relativ zum Rahmen (log), Mitte und Spannen p10–p90
    const buf = new Float32Array(KEEP);
    const glareFree = new Set(calmGlare);
    const LGs = sharp.map((s) => s.LG), LGg = sharp.filter((s) => glareFree.has(s)).map((s) => s.LG);
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
    const withTex = sharp.filter((s) => Number.isFinite(s.tex) && s.nm > 0);
    if (lit >= LIT_RANGE && withTex.length >= 10) {
      const base = withTex.filter((s) => s.nm <= p50).map((s) => s.tex), hi = withTex.filter((s) => s.nm >= p85).map((s) => s.tex);
      if (base.length >= 3 && hi.length >= 2) out.tex = median(hi) / median(base);
    }
    if (out.ratio != null) {
      out.holo = out.ratio >= HOLO_RATIO && out.art! >= HOLO_ART ? "yes" : out.ratio < HOLO_RATIO_NO ? "no" : null;
    }
    if (out.swing != null) {
      const texYes = out.tex != null && out.tex >= RH_TEX, texNo = out.tex == null || out.tex < RH_TEX_NO;
      // Holo (Bild schwankt viel stärker als der Rahmen) ist kein Reverse Holo — dort glänzt gerade das Bild nicht;
      // eine Hülle lässt auch den Rahmen schwanken (17.11., Dawn in Hülle: 0.28 statt 0.13)
      const swingYes = out.swing >= RH_SWING && out.holo !== "yes";
      out.rh = swingYes || texYes ? "yes" : out.swing < RH_SWING_NO && texNo ? "no" : null;
    }
    out.kind = tiltKind(out, options);
    return out;
  }
}
