// Live-Scan: die Kamera läuft, der Bild-Abgleich prüft immer wieder ein ruhiges,
// scharfes Bild. Jede Karte (Mappe: jedes Fach) sammelt dabei Befunde über
// mehrere Bilder — grün, sobald sie sicher ist, gelb bei einer wahrscheinlichen
// Karte, rot ohne brauchbaren Treffer oder bei widersprüchlichen Bildern.
// Mappe und lose Karten: das erste Bild mit Karten wird die Seitenkarte; jedes
// weitere Bild — auch eine Nahaufnahme weniger Karten — ordnet der Bild-Abgleich
// darauf ein (matcher/pagemap.py), so zählt eine Nahaufnahme für ihr Fach.
// Alle Umrisse der Karten liegen in Anteilen dieser Seitenkarte vor.
// Reine Logik ohne React/DOM, damit sie sich für sich prüfen lässt.

import type { ScanAlternative, ScanCandidate, ScanMode } from "./api";

export type Mat3 = number[][];
/** Wo ein Bild auf der Seitenkarte liegt (Antwort des Bild-Abgleichs). */
export type LiveMapInfo = { id: string; located: boolean; unknown?: boolean; whole?: boolean; area?: number | null; h?: Mat3 | null };

const IDENTITY: Mat3 = [[1, 0, 0], [0, 1, 0], [0, 0, 1]];

export function invert3(m: Mat3): Mat3 | null {
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

export function applyH(m: Mat3, q: number[][]): number[][] {
  return q.map(([x, y]) => {
    const w = m[2][0] * x + m[2][1] * y + m[2][2];
    return [(m[0][0] * x + m[0][1] * y + m[0][2]) / w, (m[1][0] * x + m[1][1] * y + m[1][2]) / w];
  });
}

export type Level = "red" | "yellow" | "green";
/** Warum die Farbe so ist (für den Hinweis am Rahmen). */
export type Why = "picked" | "sure" | "agree" | "print" | "close" | "weak" | "conflict";

export type LiveObs = {
  frame: number;
  id: string | null;        // bester Treffer (TCGdex-ID)
  sure: boolean;            // der Abgleich hat allein entschieden (Artwork eindeutig oder Nummer gelesen)
  printOpen: boolean;       // die Karte ist klar, nur der Druck nicht (gleiche Grafik in mehreren Sets)
  close: boolean;           // wahrscheinliche Karte, aber nicht sicher
  art: number;              // Artwork-Treffer des besten Kandidaten
  rival: number;            // … und des besten anderen
  quad: number[][] | null;  // Umriss auf der Seitenkarte (Anteile)
  vq: number[][] | null;    // Umriss in diesem Bild
  zoom: number;             // wie nah: 1 = ganze Seite, 2 = doppelt so groß
  cand: ScanCandidate;
};

export type LiveTrack = {
  key: string;
  position: number | null;  // Mappe: Fach auf der Seite (0-basiert)
  obs: LiveObs[];
  quad: number[][];         // zuletzt gesehener Umriss
  seen: number;             // zuletzt gesehen in Bild Nr.
  picked?: { id: string; cand: ScanCandidate } | null;   // von Hand gewählt
};

export type LiveState = {
  frames: number;           // geprüfte Bilder
  tracks: LiveTrack[];
  lastNew: number;          // Bild, in dem zuletzt eine neue Karte auftauchte
  seq: number;
  mapId: string | null;     // Seitenkarte beim Bild-Abgleich
  view: Mat3 | null;        // letztes Bild → Seitenkarte (für die Rahmen im Kamerabild)
  frameH: Record<number, Mat3>;   // je geprüftem Bild: Bild → Seitenkarte
  lost: number;             // so viele Bilder hintereinander nicht auf der Seitenkarte gefunden
};

export type Verdict = { level: Level; why: Why; id: string | null; cand: ScanCandidate | null };

export const emptyLive = (): LiveState => ({ frames: 0, tracks: [], lastNew: 0, seq: 0, mapId: null, view: null, frameH: {}, lost: 0 });

const CLOSE_CONF = 0.4;     // „close" liefert 0.45, „weak" 0.25 (matcher/decide)
const AGREE_MIN_ART = 20;   // mehrere unsichere Bilder zusammen: so viele Artwork-Treffer …
const AGREE_LEAD = 1.6;     // … und so viel Vorsprung vor dem besten anderen
const MAX_KEEP_OBS = 12;    // je Karte die letzten Befunde
const DROP_AFTER = 3;       // einmal gesehen, nicht sicher, seit so vielen Bildern weg → Fehlfund
const CLOSE_ZOOM = 1.5;     // ab hier eine Nahaufnahme: ihr sicherer Befund zählt doppelt

const centre = (q: number[][]): [number, number] =>
  [q.reduce((a, p) => a + p[0], 0) / q.length, q.reduce((a, p) => a + p[1], 0) / q.length];

const width = (q: number[][]): number => Math.max(...q.map((p) => p[0])) - Math.min(...q.map((p) => p[0]));
const height = (q: number[][]): number => Math.max(...q.map((p) => p[1])) - Math.min(...q.map((p) => p[1]));
const size = (q: number[][]): number => (width(q) + height(q)) / 2;

export function bboxOf(q: number[][]): number[] {
  const xs = q.map((p) => p[0]), ys = q.map((p) => p[1]);
  const x0 = Math.max(0, Math.min(...xs)), y0 = Math.max(0, Math.min(...ys));
  return [x0, y0, Math.min(1, Math.max(...xs)) - x0, Math.min(1, Math.max(...ys)) - y0];
}

const quadOf = (c: ScanCandidate): number[][] | null => {
  if (c.raw?.quad?.length === 4) return c.raw.quad;
  const b = c.raw?.bbox;
  return b?.length === 4 ? [[b[0], b[1]], [b[0] + b[2], b[1]], [b[0] + b[2], b[1] + b[3]], [b[0], b[1] + b[3]]] : null;
};

export function obsOf(c: ScanCandidate, frame: number, h: Mat3 | null = null, zoom = 1): LiveObs {
  const id = c.match?.tcgdex_card_id ?? c.raw?.tcgdex_card_id ?? null;
  const alts: ScanAlternative[] = c.alternatives ?? [];
  const mine = alts.find((a) => a.tcgdex_card_id === id) ?? alts[0];
  const art = mine?.art_inliers ?? 0;
  const rival = Math.max(0, ...alts.filter((a) => a.tcgdex_card_id !== id).map((a) => a.art_inliers ?? 0));
  const sure = !!id && !c.needs_choice && !c.print_choice;
  const printOpen = !!id && !c.needs_choice && !!c.print_choice;
  const close = !!id && !sure && !printOpen && (c.confidence ?? 0) >= CLOSE_CONF;
  const vq = quadOf(c);
  const quad = c.map_quad?.length === 4 ? c.map_quad : vq && h ? applyH(h, vq) : vq;
  return { frame, id, sure, printOpen, close, art, rival, quad, vq, zoom, cand: c };
}

/** Fach unter einem Punkt der Seitenkarte, geschätzt aus den Fächern, die schon bekannt sind. */
export function estimatePocket(tracks: LiveTrack[], pt: [number, number], cols: number, rows: number): number | null {
  const known = tracks.filter((t) => t.position != null);
  if (!known.length || cols < 1 || rows < 1) return null;
  const fit = (pts: { i: number; v: number }[], pitch: number): { a: number; b: number } => {
    const n = pts.length, mi = pts.reduce((s, p) => s + p.i, 0) / n, mv = pts.reduce((s, p) => s + p.v, 0) / n;
    const vi = pts.reduce((s, p) => s + (p.i - mi) ** 2, 0);
    const b = vi > 0 ? pts.reduce((s, p) => s + (p.i - mi) * (p.v - mv), 0) / vi : pitch;
    return { a: mv - b * mi, b };
  };
  const xs = known.map((t) => ({ i: (t.position as number) % cols, v: centre(t.quad)[0] }));
  const ys = known.map((t) => ({ i: Math.floor((t.position as number) / cols), v: centre(t.quad)[1] }));
  const w = known.reduce((s, t) => s + width(t.quad), 0) / known.length;
  const h = known.reduce((s, t) => s + height(t.quad), 0) / known.length;
  const fx = fit(xs, w * 1.08), fy = fit(ys, h * 1.06);
  if (!fx.b || !fy.b) return null;
  const col = Math.round((pt[0] - fx.a) / fx.b), row = Math.round((pt[1] - fy.a) / fy.b);
  return col >= 0 && col < cols && row >= 0 && row < rows ? row * cols + col : null;
}

export type FrameOpts = { map?: LiveMapInfo | null; cols?: number; rows?: number };

/**
 * Befunde eines geprüften Bilds den Karten zuordnen (Mappe: nach Fach, sonst nach
 * Lage auf der Seitenkarte). Ein Bild, das nicht auf der Seitenkarte liegt, zählt nicht.
 */
export function addFrame(state: LiveState, cands: ScanCandidate[], mode: ScanMode, opts: FrameOpts = {}): LiveState {
  const map = mode === "single" ? null : opts.map ?? null;
  if (map && state.mapId && map.id === state.mapId && !map.located) return { ...state, lost: state.lost + 1 };
  const frame = state.frames + 1;
  const h = map?.located && map.h ? map.h : IDENTITY;
  const zoom = map?.located && map.area ? Math.max(1, 1 / Math.sqrt(map.area)) : 1;
  let seq = state.seq;
  let lastNew = state.lastNew;
  const tracks = state.tracks.map((t) => ({ ...t }));
  const obs = (mode === "single" ? cands.slice(0, 1) : cands).map((c) => obsOf(c, frame, h, zoom)).filter((o) => o.quad);
  const add = (t: LiveTrack, o: LiveObs) => {
    t.obs = [...t.obs, o].slice(-MAX_KEEP_OBS);
    t.quad = o.quad!;
    t.seen = frame;
  };
  const fresh = (o: LiveObs, position: number | null) => {
    tracks.push({ key: `t${++seq}`, position, obs: [o], quad: o.quad!, seen: frame });
    lastNew = frame;
  };
  const loose: LiveObs[] = [];
  for (const o of obs) {
    const pos = mode === "binder" ? o.cand.position ?? o.cand.raw?.position ?? null : null;
    if (mode === "single") {
      if (tracks.length) add(tracks[0], o); else fresh(o, null);
    } else if (pos != null) {
      const t = tracks.find((x) => x.position === pos);
      if (t) add(t, o); else fresh(o, pos);
    } else {
      loose.push(o);
    }
  }
  // Lose Karten und Nahaufnahmen: nächster Umriss auf der Seitenkarte, solange die
  // Mitte weniger als eine halbe Karte daneben liegt (Mappe: auch Fächer mit Nummer)
  const free = tracks.filter((t) => (mode === "binder" || t.position == null) && t.seen !== frame);
  const pairs: { o: LiveObs; t: LiveTrack; d: number }[] = [];
  for (const o of loose) {
    const [x, y] = centre(o.quad!);
    for (const t of free) {
      const [tx, ty] = centre(t.quad);
      const d = Math.hypot(x - tx, y - ty);
      if (d < 0.5 * size(t.quad)) pairs.push({ o, t, d });
    }
  }
  pairs.sort((a, b) => a.d - b.d);
  const usedO = new Set<LiveObs>(), usedT = new Set<LiveTrack>();
  for (const { o, t } of pairs) {
    if (usedO.has(o) || usedT.has(t)) continue;
    usedO.add(o); usedT.add(t);
    add(t, o);
  }
  for (const o of loose) {
    if (usedO.has(o)) continue;
    // Mappe: Nahaufnahme einer Karte, die noch keinen Rahmen hat → Fach aus dem Raster schätzen
    const pos = mode === "binder" ? estimatePocket(tracks, centre(o.quad!), opts.cols ?? 0, opts.rows ?? 0) : null;
    const t = pos != null ? tracks.find((x) => x.position === pos && x.seen !== frame) : undefined;
    if (t) add(t, o); else fresh(o, pos);
  }
  // Fehlfunde (ein einziges Mal gesehen, nicht sicher, seitdem weg) fallen wieder heraus
  const kept = tracks.filter((t) => t.picked || t.obs.length > 1 || frame - t.seen < DROP_AFTER
    || verdict(t).level === "green");
  return {
    frames: frame, tracks: kept, lastNew, seq,
    mapId: map?.located ? map.id : state.mapId,
    view: h, frameH: { ...state.frameH, [frame]: h }, lost: 0,
  };
}

/** Rahmen einer Karte im aktuellen Kamerabild (von der Seitenkarte zurückgerechnet). */
export function viewQuad(state: LiveState, t: LiveTrack): number[][] {
  if (t.seen === state.frames) {
    const o = t.obs.find((x) => x.frame === t.seen);
    if (o?.vq) return o.vq;
  }
  const inv = state.view ? invert3(state.view) : null;
  return inv ? applyH(inv, t.quad) : t.quad;
}

const latest = (list: LiveObs[]) => list.reduce<LiveObs | null>((a, o) => (!a || o.frame >= a.frame ? o : a), null);

/** Stimmen je Karte; weighted: eine sichere Nahaufnahme zählt doppelt. */
function votes(list: LiveObs[], weighted = false): [string, number][] {
  const m = new Map<string, number>();
  for (const o of list) if (o.id) m.set(o.id, (m.get(o.id) ?? 0) + (weighted && o.zoom >= CLOSE_ZOOM ? 2 : 1));
  return Array.from(m.entries()).sort((a, b) => b[1] - a[1]);
}

/** Farbe einer Karte aus allen ihren Befunden. */
export function verdict(t: LiveTrack): Verdict {
  if (t.picked) return { level: "green", why: "picked", id: t.picked.id, cand: t.picked.cand };
  const sure = t.obs.filter((o) => o.sure);
  if (sure.length) {
    const v = votes(sure, true);
    const [id, n] = v[0];
    const second = v[1]?.[1] ?? 0;
    const cand = latest(sure.filter((o) => o.id === id))!.cand;
    if (second === 0 || n >= 2 * second) return { level: "green", why: "sure", id, cand };
    return { level: "red", why: "conflict", id, cand };
  }
  const likely = t.obs.filter((o) => o.close || o.printOpen);
  if (likely.length) {
    const [id] = votes(likely)[0];
    const mine = likely.filter((o) => o.id === id);
    const others = likely.filter((o) => o.id !== id);
    const cand = latest(mine)!.cand;
    if (mine.some((o) => o.printOpen)) return { level: "yellow", why: "print", id, cand };
    const art = mine.reduce((a, o) => a + o.art, 0);
    const rival = mine.reduce((a, o) => a + o.rival, 0) + others.reduce((a, o) => a + o.art, 0);
    if (mine.length >= 2 && !others.length && art >= AGREE_MIN_ART && art >= AGREE_LEAD * Math.max(rival, 1)) {
      return { level: "green", why: "agree", id, cand };
    }
    return { level: "yellow", why: "close", id, cand };
  }
  const last = latest(t.obs);
  return { level: "red", why: "weak", id: last?.id ?? null, cand: last?.cand ?? null };
}

export function counts(state: LiveState): Record<Level, number> {
  const out: Record<Level, number> = { red: 0, yellow: 0, green: 0 };
  for (const t of state.tracks) out[verdict(t).level]++;
  return out;
}

/** Mappe: Fächer, die schon sicher sind — der Abgleich lässt sie im nächsten Bild aus. */
export function skipPockets(state: LiveState): number[] {
  return state.tracks.filter((t) => t.position != null && verdict(t).level === "green")
    .map((t) => t.position as number).sort((a, b) => a - b);
}

/** Homographie Einheitsquadrat → Viereck (TL, TR, BR, BL). */
function squareTo(q: number[][]): Mat3 {
  const [[x0, y0], [x1, y1], [x2, y2], [x3, y3]] = q;
  const dx1 = x1 - x2, dx2 = x3 - x2, dy1 = y1 - y2, dy2 = y3 - y2;
  const sx = x0 - x1 + x2 - x3, sy = y0 - y1 + y2 - y3;
  const den = dx1 * dy2 - dx2 * dy1;
  const g = den ? (sx * dy2 - dx2 * sy) / den : 0;
  const h = den ? (dx1 * sy - sx * dy1) / den : 0;
  return [[x1 - x0 + g * x1, x3 - x0 + h * x3, x0], [y1 - y0 + g * y1, y3 - y0 + h * y3, y0], [g, h, 1]];
}

/**
 * Lose Karten im Raster: Felder (zeilenweise ab oben links), in deren Mitte eine schon
 * sichere Karte liegt — der Abgleich lässt sie aus, wie die Fächer der Mappe. toNow:
 * letztes geprüftes Bild → Bild, das gleich geschickt wird (Kamera-Bewegung, Anteile);
 * grid: das Raster in diesem Bild (Anteile, TL TR BR BL).
 */
export function skipSquares(state: LiveState, toNow: Mat3, grid: number[][], cols: number, rows: number): number[] {
  const back = invert3(squareTo(grid));
  if (!back || cols < 1 || rows < 1) return [];
  const sure = new Set<number>(), open = new Set<number>();
  for (const t of state.tracks) {
    const q = applyH(toNow, viewQuad(state, t));
    const [[s, u]] = applyH(back, [[q.reduce((a, p) => a + p[0], 0) / 4, q.reduce((a, p) => a + p[1], 0) / 4]]);
    if (!(s >= 0 && s < 1 && u >= 0 && u < 1)) continue;
    const sq = Math.floor(u * rows) * cols + Math.floor(s * cols);
    (verdict(t).level === "green" ? sure : open).add(sq);
  }
  // ein Feld mit noch einer unsicheren Karte darin wird weiter geprüft
  return Array.from(sure).filter((sq) => !open.has(sq)).sort((a, b) => a - b);
}

/** Fertig: jede gefundene Karte grün, und seit der letzten neuen kam keine dazu. */
export function allSure(state: LiveState, mode: ScanMode): boolean {
  if (!state.tracks.length || state.tracks.some((t) => verdict(t).level !== "green")) return false;
  return mode === "single" || state.frames - state.lastNew >= 1;
}

/** Was gefunden wurde, als Text (Fach:Karte) — gleich wie beim letzten Abschluss = dieselbe Seite noch einmal. */
export function signature(state: LiveState): string {
  return state.tracks.map((t) => `${t.position ?? ""}:${verdict(t).id ?? "?"}`).sort().join(",");
}

/** Eine Karte von Hand festlegen (Tippen auf den Rahmen → Kandidat gewählt). */
export function pick(state: LiveState, key: string, id: string, cand: ScanCandidate): LiveState {
  return { ...state, tracks: state.tracks.map((t) => (t.key === key ? { ...t, picked: { id, cand } } : t)) };
}

/** Kandidaten zum Antippen: die Treffer aller Bilder dieser Karte, beste zuerst. */
export function choices(t: LiveTrack): ScanAlternative[] {
  const best = new Map<string, ScanAlternative & { n: number }>();
  for (const o of t.obs) {
    for (const a of o.cand.alternatives ?? []) {
      const had = best.get(a.tcgdex_card_id);
      if (had) { had.n++; had.art_inliers = Math.max(had.art_inliers, a.art_inliers ?? 0); }
      else best.set(a.tcgdex_card_id, { ...a, n: 1 });
    }
  }
  return Array.from(best.values()).sort((a, b) => b.n * b.art_inliers - a.n * a.art_inliers || b.n - a.n)
    .map(({ n: _n, ...a }) => a).slice(0, 8);
}

export type FinalItem = { cand: ScanCandidate; chosenId: string | null };

/**
 * Ergebnis für die Prüfseite: ein Foto (das Bild, in dem die meisten Karten mit
 * ihrem Endergebnis zu sehen sind) und je Karte der beste Befund, mit dem Umriss
 * aus genau diesem Foto. frames: Bilder, die noch da sind.
 */
export function finish(state: LiveState, frames: number[]): { frame: number | null; items: FinalItem[] } {
  const verdicts = new Map(state.tracks.map((t) => [t.key, verdict(t)]));
  let base: number | null = null;
  let bestScore = -1;
  for (const f of frames) {
    let score = 0;
    for (const t of state.tracks) {
      const o = t.obs.find((x) => x.frame === f);
      if (o) score += o.id && o.id === verdicts.get(t.key)!.id ? 2 : 1;
    }
    if (score > bestScore || (score === bestScore && base !== null && f > base)) { bestScore = score; base = f; }
  }
  const order = [...state.tracks].sort((a, b) => {
    if (a.position != null && b.position != null) return a.position - b.position;
    const [ax, ay] = centre(a.quad), [bx, by] = centre(b.quad);
    return Math.abs(ay - by) > 0.5 * Math.min(size(a.quad), size(b.quad)) ? ay - by : ax - bx;
  });
  const items = order.map((t): FinalItem => {
    const v = verdicts.get(t.key)!;
    const last = latest(t.obs)!;
    const inBase = base != null ? t.obs.find((o) => o.frame === base) ?? null : null;
    // Umriss in genau diesem Foto: dort gesehen, sonst von der Seitenkarte hineingerechnet
    const toBase = base != null && state.frameH[base] ? invert3(state.frameH[base]) : null;
    const quad = inBase?.vq ?? (toBase ? applyH(toBase, t.quad) : t.quad);
    const from = v.cand && !t.picked ? v.cand : last.cand;
    let cand: ScanCandidate = {
      ...from,
      position: t.position ?? from.position,
      raw: { ...from.raw, quad, bbox: bboxOf(quad), position: t.position ?? from.raw?.position ?? null },
    };
    if (t.picked) {
      const p = t.picked.cand;
      cand = { ...cand, match: p.match, suggested: p.suggested, foil_options: p.foil_options,
        uncertain_fields: p.uncertain_fields, confidence: p.confidence, needs_choice: false, print_choice: false };
    } else if (v.why === "agree") {
      cand = { ...cand, needs_choice: false };
    } else if (v.level === "red") {
      cand = { ...cand, needs_choice: true };
    }
    // Play!-Stempel: der stärkste Befund dieser Karte
    const stamps = t.obs.filter((o) => (!v.id || o.id === v.id) && o.cand.stamp).map((o) => o.cand.stamp!);
    if (stamps.length) cand = { ...cand, stamp: stamps.reduce((a, s) => (s.score > a.score ? s : a)) };
    return { cand, chosenId: t.picked?.id ?? null };
  });
  return { frame: base, items };
}
