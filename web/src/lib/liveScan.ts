// Live-Scan: die Kamera läuft, der Bild-Abgleich prüft immer wieder ein ruhiges,
// scharfes Bild. Jede Karte (Mappe: jedes Fach) sammelt dabei Befunde über
// mehrere Bilder — grün, sobald sie sicher ist, gelb bei einer wahrscheinlichen
// Karte, rot ohne brauchbaren Treffer oder bei widersprüchlichen Bildern.
// Reine Logik ohne React/DOM, damit sie sich für sich prüfen lässt.

import type { ScanAlternative, ScanCandidate, ScanMode } from "./api";

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
  quad: number[][] | null;  // Umriss im Bild (Anteile)
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
};

export type Verdict = { level: Level; why: Why; id: string | null; cand: ScanCandidate | null };

export const emptyLive = (): LiveState => ({ frames: 0, tracks: [], lastNew: 0, seq: 0 });

const CLOSE_CONF = 0.4;     // „close" liefert 0.45, „weak" 0.25 (matcher/decide)
const AGREE_MIN_ART = 20;   // mehrere unsichere Bilder zusammen: so viele Artwork-Treffer …
const AGREE_LEAD = 1.6;     // … und so viel Vorsprung vor dem besten anderen
const MAX_KEEP_OBS = 12;    // je Karte die letzten Befunde
const DROP_AFTER = 3;       // einmal gesehen, nicht sicher, seit so vielen Bildern weg → Fehlfund

const centre = (q: number[][]): [number, number] =>
  [q.reduce((a, p) => a + p[0], 0) / q.length, q.reduce((a, p) => a + p[1], 0) / q.length];

const size = (q: number[][]): number => {
  const xs = q.map((p) => p[0]), ys = q.map((p) => p[1]);
  return (Math.max(...xs) - Math.min(...xs) + Math.max(...ys) - Math.min(...ys)) / 2;
};

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

export function obsOf(c: ScanCandidate, frame: number): LiveObs {
  const id = c.match?.tcgdex_card_id ?? c.raw?.tcgdex_card_id ?? null;
  const alts: ScanAlternative[] = c.alternatives ?? [];
  const mine = alts.find((a) => a.tcgdex_card_id === id) ?? alts[0];
  const art = mine?.art_inliers ?? 0;
  const rival = Math.max(0, ...alts.filter((a) => a.tcgdex_card_id !== id).map((a) => a.art_inliers ?? 0));
  const sure = !!id && !c.needs_choice && !c.print_choice;
  const printOpen = !!id && !c.needs_choice && !!c.print_choice;
  const close = !!id && !sure && !printOpen && (c.confidence ?? 0) >= CLOSE_CONF;
  return { frame, id, sure, printOpen, close, art, rival, quad: quadOf(c), cand: c };
}

/** Befunde eines geprüften Bilds den Karten zuordnen (Mappe: nach Fach, sonst nach Lage). */
export function addFrame(state: LiveState, cands: ScanCandidate[], mode: ScanMode): LiveState {
  const frame = state.frames + 1;
  let seq = state.seq;
  let lastNew = state.lastNew;
  const tracks = state.tracks.map((t) => ({ ...t }));
  const obs = (mode === "single" ? cands.slice(0, 1) : cands).map((c) => obsOf(c, frame)).filter((o) => o.quad);
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
  // Lose Karten: nächster Umriss, solange die Mitte weniger als eine halbe Karte daneben liegt
  const free = tracks.filter((t) => t.position == null && t.seen !== frame);
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
  for (const o of loose) if (!usedO.has(o)) fresh(o, null);
  // Fehlfunde (ein einziges Mal gesehen, nicht sicher, seitdem weg) fallen wieder heraus
  const kept = tracks.filter((t) => t.picked || t.obs.length > 1 || frame - t.seen < DROP_AFTER
    || verdict(t).level === "green");
  return { frames: frame, tracks: kept, lastNew, seq };
}

const latest = (list: LiveObs[]) => list.reduce<LiveObs | null>((a, o) => (!a || o.frame >= a.frame ? o : a), null);

function votes(list: LiveObs[]): [string, number][] {
  const m = new Map<string, number>();
  for (const o of list) if (o.id) m.set(o.id, (m.get(o.id) ?? 0) + 1);
  return Array.from(m.entries()).sort((a, b) => b[1] - a[1]);
}

/** Farbe einer Karte aus allen ihren Befunden. */
export function verdict(t: LiveTrack): Verdict {
  if (t.picked) return { level: "green", why: "picked", id: t.picked.id, cand: t.picked.cand };
  const sure = t.obs.filter((o) => o.sure);
  if (sure.length) {
    const v = votes(sure);
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
    const quad = inBase?.quad ?? t.quad;
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
