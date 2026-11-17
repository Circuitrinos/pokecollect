// Blitz-Test im Live-Scan: sind alle Karten erkannt, blinkt die Lampe, bis jede Karte ihre
// Folierung hat. Jedes Kamerabild: die noch offenen Karten ausschneiden (Umrisse folgen der
// Bewegungsverfolgung).
//
// Geschaltet wird nach dem, was im Bild passiert, nicht nach der Uhr: um das Umschalten bitten, warten,
// bis es im Bild zu sehen ist (die Helligkeit springt in die erwartete Richtung und bleibt im nächsten
// Bild mindestens halb so weit dort — mindestens EVENT_STEP, nach zwei gesehenen Umschaltern 40 % des
// üblichen Sprungs dieser Kamera), dann noch PHASE_MS stehen lassen und erst dann um das nächste bitten. Verglichen werden die Enden zweier
// Phasen: je die letzten beiden Bilder vor dem nächsten sichtbaren Umschalten (das Bild direkt davor
// ausgelassen — es kann schon halb umgeschaltet sein), frühestens SETTLE_MS nach dem eigenen.
//
// Warum: Brians Fairphone im /e/OS-Browser (Protokoll 17.11.) schaltet die Lampe erst ~0.5 s nach der
// Bitte, und die Belichtung schießt im ersten Bild über: Lampe an 120 → 225 → 164, Lampe aus 150 → 55 → 116
// (Helligkeit des ganzen Bilds), nach 2–3 Bildern ruhig. Mit festem Takt (0.4–0.6 s) lag das Umschalten
// fast am Ende der Phase, als „Umschalten" wurde das Nachregeln danach erkannt, und verglichen wurden
// genau die über- und unterbelichteten Bilder — alle Karten lasen sich nach Holo (Reverse Holo +0.34,
// Normal +0.38…+0.40). Andere Kameras schalten in ~0.1 s; der Ablauf passt sich an beide an.
// Nur Karten, über denen die Kamera in beiden Phasen nah genug steht (FLASH_REACH); je Messung zählt,
// wo sie stand (lib/flashCheck: das Urteil vergleicht mit dem dort erwarteten Messwert).
import {
  FLASH_H, FLASH_W, FlashTally, alignPatch, atlasPatch, averagePatch, cardOffset, flashFocal, flashReachable,
  catalogLuma, fitToCatalog, flashLift, flashRoom, flashShare, flashShareFlat, flashTexture, flashTransform, litLevel, type CatalogFit,
} from "./flashCheck";
import { fixCanvasColors } from "./cameraColors";
import { FLASH_ROOM_SPLIT } from "./flashTable";
import { debugEnabled, dlog } from "./debugLog";

/** Eine noch offene Karte: Schlüssel und Umriss jetzt (TL, TR, BR, BL; Anteile des Kamerabilds). */
export type FlashCard = { key: string; quad: number[][]; id?: string | null };   // id: Karten-ID (Katalogbild für flashTexture)

type Patches = Map<string, { px: Uint8ClampedArray; deg: number | null; pos?: { tx: number; ty: number } | null; q?: number[][]; cat?: Float32Array | null; catWait?: boolean }>;
type Shot = { n: number; t: number; mean: number; cards: Patches; turn?: number[] | null };
type Phase = { lit: boolean; endN: number; means: number[]; cards: Patches };
/** Bitte ums Umschalten: wann, wohin, und wann es im Bild zu sehen war. */
type Request = { on: boolean; at: number; seenAt: number | null; seenN: number | null };

export const EVENT_STEP = 0.06;      // so viel heller/dunkler von einem Bild zum nächsten (mindestens): die Lampe hat geschaltet
const JUMP_CAP = 0.25;              // die gelernte Mindestschwelle höchstens so hoch (|ln|, ≈ +28 %)
const FULL_FITS = 3;                // so viele Karten je Umschalten neu am Katalogbild suchen (fitToCatalog ohne Startwert)
const AE_JUMP = 0.3;                // so groß (|ln|, ≈ +35 %) zählt ein Sprung auch, wenn das nächste Bild schon nachgeregelt ist
export const PHASE_MS = 450;         // so lange bleibt die Lampe, nachdem das Umschalten zu sehen war
const SETTLE_MS = 300;               // bis dahin regelt die Belichtung nach dem Umschalten noch nach
const STEADY = 0.08;                 // |ln| Abstand zum Median der Phase: so ruhig muss ein verwendetes Bild sein
const EVENT_TIMEOUT_MS = 1500;       // so lange auf das Umschalten warten, dann gilt es als verpasst
const TICK_MS = 50;
const SHOTS_KEPT = 50;               // Bilder (~1.7 s) — eine ganze Phase muss hineinpassen
const ANGLE_MS = 250;

const B64 = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
const LIN8 = new Float32Array(256).map((_, i) => { const c = i / 255; return c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4; });
const lumLin = (px: Uint8ClampedArray, i: number) => 0.2126 * LIN8[px[4 * i]] + 0.7152 * LIN8[px[4 * i + 1]] + 0.0722 * LIN8[px[4 * i + 2]];

/** Ausschnitt fürs Protokoll: Helligkeit (lineare Luma, wieder sRGB-kodiert, 0–255; 255 = überstrahlt), base64. */
export function lumaB64(px: Uint8ClampedArray): string {
  const n = px.length >> 2, b = new Uint8Array(n);
  for (let i = 0; i < n; i++) {
    const r = px[4 * i], g = px[4 * i + 1], bl = px[4 * i + 2];
    if (Math.max(r, g, bl) >= 250) { b[i] = 255; continue; }
    const y = 0.2126 * LIN8[r] + 0.7152 * LIN8[g] + 0.0722 * LIN8[bl];
    b[i] = Math.min(254, Math.round(255 * (y <= 0.0031308 ? 12.92 * y : 1.055 * y ** (1 / 2.4) - 0.055)));
  }
  let out = "";
  for (let i = 0; i < n; i += 3) {
    const v = (b[i] << 16) | ((b[i + 1] ?? 0) << 8) | (b[i + 2] ?? 0);
    out += B64[(v >> 18) & 63] + B64[(v >> 12) & 63] + (i + 1 < n ? B64[(v >> 6) & 63] : "=") + (i + 2 < n ? B64[v & 63] : "=");
  }
  return out;
}

/** Ein Kamerabild für die Messung: Zeit, mittlere Helligkeit des ganzen Bilds, die Karten-Ausschnitte. */
export type FlashFrame = { t: number; mean: number; cards: Patches; turn?: number[] | null };

/**
 * Der Ablauf des Blitz-Tests ohne Browser: wann um das Umschalten gebeten wird, wann es im Bild zu sehen
 * ist, welche Bilder je Phase verglichen werden, was gezählt wird. FlashMonitor liefert die Bilder aus der
 * Kamera und schaltet die Lampe; ein Test (oder die Nachbildung) kann ihn mit erzeugten Bildern füttern.
 */
export class FlashLoop {
  readonly tally = new FlashTally();
  torchOn = false;
  private req: Request | null = null;          // letzte Bitte ums Umschalten
  private lastSeen: { n: number; t: number; on: boolean } | null = null;   // letztes sichtbares Umschalten
  private shots: Shot[] = [];
  private count = 0;
  private phase: Phase | null = null; // Ende der letzten Phase (Lampe an oder aus)
  private jumps: number[] = [];       // |ln| der zuletzt gesehenen Sprünge beim Umschalten (diese Kamera)
  private rooms = new Map<string, number[]>();   // je Karte: Lampe / Raum (die letzten Umschalter)
  private near: { j1: number; j2: number; means: number[] } | null = null;   // größter Sprung seit der Bitte (Protokoll, wenn verpasst)
  private fits = new Map<string, CatalogFit>();   // je Karte: zuletzt gefundene Lage am Katalogbild
  /** Ausschnitte (Lampe aus / an) je Messung mitprotokollieren — zum Nachrechnen anderer Maße (Debug). */
  dumpPatches = false;
  /** Protokoll (im Browser: dlog) */
  log: (event: string, data: Record<string, unknown>) => void = () => {};

  /** Alles vergessen (Blinken aus). */
  reset(): void {
    this.shots = []; this.phase = null; this.lastSeen = null; this.req = null; this.jumps = []; this.rooms.clear(); this.fits.clear();
  }

  /** Mindestsprung (|ln|) fürs Umschalten: EVENT_STEP, nach zwei gesehenen 40 % des üblichen Sprungs dieser Kamera. */
  private eventThreshold(): number {
    const base = Math.log(1 + EVENT_STEP);
    if (this.jumps.length < 2) return base;
    const s = [...this.jumps].sort((a, b) => a - b);
    return Math.max(base, Math.min(JUMP_CAP, 0.4 * s[s.length >> 1]));
  }

  /** Jetzt um das Umschalten bitten? → der gewünschte Zustand der Lampe, sonst null. */
  wanted(now: number): boolean | null {
    const r = this.req;
    if (r && r.seenAt == null) {
      // noch nicht im Bild: warten — zu lange, dann gilt es als verpasst (die Kette der Phasen reißt)
      if (now - r.at < EVENT_TIMEOUT_MS) return null;
      this.log("live.flash.missed", { on: r.on, ms: Math.round(now - r.at), need: Math.round(this.eventThreshold() * 100) / 100,
        best: this.near ? { j1: Math.round(this.near.j1 * 100) / 100, j2: Math.round(this.near.j2 * 100) / 100, means: this.near.means } : null });
      this.phase = null;
      this.lastSeen = null;
    } else if (r && r.seenAt != null && now - r.seenAt < PHASE_MS) {
      return null;
    }
    return !this.torchOn;
  }

  /** Um das Umschalten wurde gebeten (vor dem Schalten aufrufen). */
  asked(on: boolean, at: number): void {
    this.req = { on, at, seenAt: null, seenN: null };
    this.near = null;
  }

  /** Neues Bild → true, wenn dabei mindestens eine Karte eine Messung bekommen hat. */
  frame(f: FlashFrame): boolean {
    const shot: Shot = { n: this.count++, t: f.t, mean: f.mean, cards: f.cards, turn: f.turn ?? null };
    this.shots.push(shot);
    if (this.shots.length > SHOTS_KEPT) this.shots.shift();
    // ist das erbetene Umschalten im vorletzten Bild zu sehen? (Sprung in die erwartete Richtung gegenüber
    // dem Bild davor, und im letzten Bild noch mindestens halb so weit dort)
    const r = this.req, nShots = this.shots.length;
    if (!r || r.seenAt != null || nShots < 3) return false;
    const ev = this.shots[nShots - 2], pre = this.shots[nShots - 3];
    if (ev.t <= r.at || ev.n !== pre.n + 1 || shot.n !== ev.n + 1) return false;
    const dir = r.on ? 1 : -1;
    const j1 = Math.log(Math.max(ev.mean, 1) / Math.max(pre.mean, 1)), j2 = Math.log(Math.max(shot.mean, 1) / Math.max(pre.mean, 1));
    const minJump = this.eventThreshold();
    if (dir * j1 > (this.near?.j1 ?? 0)) this.near = { j1: dir * j1, j2: dir * j2, means: [pre, ev, shot].map((x) => Math.round(x.mean)) };
    // bestätigt, wenn das nächste Bild noch ein Viertel des Sprungs hält (mindestens EVENT_STEP): die Kamera
    // regelt im dunklen Raum schon im Bild danach kräftig nach (Brian 17.11.: 120 → 177 → 136; mit
    // „halbe Mindestschwelle" nach großen Sprüngen 24 Umschalter hintereinander verpasst, 50 s ohne Messung)
    // Oder: ein großer Sprung, den die Belichtung schon im nächsten Bild ganz ausgleicht (17.11., zweite Matte:
    // 24 von 184 verpasst, z. B. 119 → 172 → 127 und 119 → 79 → 113) — die Bilder danach sind trotzdem mit
    // Lampe; gemessen wird ohnehin nur relativ (Aufhellen gegen den Median der Karte, Körnung).
    const held = dir * j2 >= Math.max(Math.log(1 + EVENT_STEP), 0.25 * dir * j1);
    const evened = dir * j1 >= Math.max(minJump + 0.05, AE_JUMP) && dir * j2 > -0.05;
    if (dir * j1 < minJump || !(held || evened)) return false;
    this.jumps.push(Math.abs(j1));
    if (this.jumps.length > 8) this.jumps.shift();
    r.seenAt = ev.t;
    r.seenN = ev.n;
    // wie hell der Raum gegen die Lampe ist: das Bild davor und das Sprungbild sind gleich belichtet
    ev.cards.forEach((c, key) => {
      const p = pre.cards.get(key);
      if (!p) return;
      const k = r.on ? flashRoom(p.px, c.px) : flashRoom(c.px, p.px);
      if (k == null || !Number.isFinite(k)) return;
      const list = this.rooms.get(key) ?? [];
      list.push(k);
      if (list.length > 6) list.shift();
      this.rooms.set(key, list);
    });
    const before = this.lastSeen;
    this.lastSeen = { n: ev.n, t: ev.t, on: r.on };
    const seenLog = { on: r.on, ms: Math.round(ev.t - r.at), jump: [pre, ev, shot].map((x) => Math.round(x.mean)) };
    // die Phase, die jetzt zu Ende ging: Lampe so, wie sie beim vorigen Umschalten wurde
    if (!before || before.on === r.on) { this.phase = null; this.log("live.flash.seen", seenLog); return false; }
    const window = this.shots.filter((x) => x.n > before.n && x.n <= ev.n - 2 && x.t >= before.t + SETTLE_MS);
    // ruhige Bilder: Helligkeit nah am Median der Phase (ein Ausreißer kurz vor dem Umschalten bleibt draußen)
    const sorted = window.map((x) => x.mean).sort((a, b) => a - b);
    const mid = sorted.length ? sorted[sorted.length >> 1] : 0;
    const usable = window.filter((x) => Math.abs(Math.log(Math.max(x.mean, 1) / Math.max(mid, 1))) <= STEADY);
    const [prev, last] = usable.length >= 2 ? usable.slice(-2) : [undefined, usable[0]];
    if (!last) { this.phase = null; this.log("live.flash.seen", { ...seenLog, frames: 0 }); return false; }
    const ended: Phase = { lit: before.on, endN: ev.n, means: [], cards: new Map() };
    last.cards.forEach((c, key) => {
      const p = prev?.cards.get(key);
      const pos = c.pos && p?.pos ? { tx: (c.pos.tx + p.pos.tx) / 2, ty: (c.pos.ty + p.pos.ty) / 2 } : c.pos ?? null;
      ended.cards.set(key, { px: p ? averagePatch(p.px, c.px) : c.px, deg: c.deg, pos, q: c.q, cat: c.cat ?? p?.cat ?? null, catWait: !!c.catWait && !(p?.cat) });
    });
    ended.means = [prev, last].filter((x): x is Shot => !!x).map((x) => Math.round(x.mean));
    const older = this.phase;
    this.phase = ended;
    // nur zwei Phasen direkt hintereinander (die ältere endete mit dem Umschalten, mit dem diese begann)
    if (!older || older.lit === ended.lit || older.endN !== before.n) { this.log("live.flash.seen", { ...seenLog, frames: usable.length }); return false; }
    const darkPh = ended.lit ? older : ended, litPh = ended.lit ? ended : older;
    let added = false;
    const seen: Record<string, unknown[]> = {};
    let fullFits = FULL_FITS;
    const patches: Record<string, string[]> = {};   // Debug: je Karte [aus, an] (lumaB64, 40×55)   // Protokoll: je Karte [Messwert, Helligkeit an/aus, Winkel, tx, ty, Raum, Umriss aus, Umriss an, flashShare, flat, flat mit Raum-Schwelle, Körnung, Lage am Katalog, Bild hellt mehr auf als Rahmen]
    litPh.cards.forEach((b, key) => {
      const a = darkPh.cards.get(key);
      if (!a || b.deg == null || a.deg == null) return;
      const ok = flashReachable(b.pos) && flashReachable(a.pos);
      const lit = ok ? alignPatch(a.px, b.px) : b.px;
      const rl0 = this.rooms.get(key);
      const room0 = rl0 && rl0.length ? [...rl0].sort((x, y) => x - y)[rl0.length >> 1] : null;
      // dunkler Raum (Lampe ≥ FLASH_ROOM_SPLIT × Raumlicht): ohne den Verlauf über die Karte (Schatten), sonst wie bisher
      const shNow = ok ? flashShare(a.px, lit) : null;
      const shFlat = ok ? flashShareFlat(a.px, lit) : null;
      const shRoom = ok ? flashShareFlat(a.px, lit, FLASH_W, FLASH_H, flashLift(room0)) : null;
      const dark = room0 != null && room0 >= FLASH_ROOM_SPLIT;
      const sh = dark ? shFlat : shNow;   // shRoom: nur protokolliert (Vergleich)
      // dunkler Raum mit Katalogbild: Körnung (flashTexture), am Katalogbild ausgerichtet
      let fit: CatalogFit | null = null, tex: number | null = null, artUp: number | null = null;
      const before = this.fits.get(key) ?? null;
      // die weite Suche kostet ~30 ms am Rechner (am Handy mehr) — höchstens FULL_FITS Karten je Umschalten,
      // die übrigen kommen beim nächsten dran (sonst stockt das Bild beim ersten Umschalten mit 12 Karten)
      if (ok && dark && a.cat && (before || fullFits-- > 0)) {
        const lum = new Float32Array(FLASH_W * FLASH_H);
        for (let i = 0; i < lum.length; i++) lum[i] = lumLin(a.px, i);
        fit = fitToCatalog(lum, a.cat, before);
        // nahe der letzten Lage nur, wenn es dort gut passt — sonst neu suchen (17.11.: die nahe Suche blieb
        // an halb passenden Lagen hängen, Holo las sich dann wie normal)
        if (before && fit.corr < 0.6 && fullFits-- > 0) fit = fitToCatalog(lum, a.cat);
        if (fit.corr >= 0.6) this.fits.set(key, fit); else this.fits.delete(key);
        const tx0 = fit.corr >= 0.35 ? flashTexture(a.px, lit, a.cat, fit) : null;
        tex = tx0?.ratio ?? null;
        artUp = tx0?.lift ?? null;
      }
      const deg = (a.deg + b.deg) / 2;
      const tx = a.pos && b.pos ? (a.pos.tx + b.pos.tx) / 2 : null, ty = a.pos && b.pos ? (a.pos.ty + b.pos.ty) / 2 : null;
      const room = room0;
      const r2 = (x: number | null) => (x == null ? null : Math.round(x * 100) / 100);
      const q3 = (q?: number[][]) => (q ? q.map(([x, y]) => [Math.round(x * 1000) / 1000, Math.round(y * 1000) / 1000]) : null);
      const r3 = (x: number | null) => (x == null ? null : Math.round(x * 1000) / 1000);
      seen[key] = [r3(sh), Math.round(litLevel(lit)), Math.round(litLevel(a.px)), Math.round(deg), r2(tx), r2(ty), r2(room),
        q3(a.q), q3(b.q), r3(shNow), r3(shFlat), r3(shRoom), r3(tex),
        fit ? [r2(fit.dx), r2(fit.dy), r3(fit.sx), r3(fit.sy), r2(fit.corr)] : null, r3(artUp)];
      if (this.dumpPatches) { patches[key] = [lumaB64(a.px), lumaB64(lit)]; }
      if (sh == null || tx == null || ty == null) return;
      // dunkler Raum: Katalogbild kommt noch, oder es ist da und die Körnung nicht messbar — nicht mit dem alten Maß raten
      if (dark && (a.catWait || (a.cat && tex == null))) return;
      this.tally.add(key, { share: sh, tx, ty, room, tex }, deg, r.on);
      added = true;
    });
    const turn = ev.turn ? ev.turn.map((x) => Math.round(x * 10000) / 10000) : null;
    this.log("live.flash.switch", { ...seenLog, ends: { dark: darkPh.means, lit: litPh.means }, frames: [usable.length, window.length], cards: seen, turn });
    // je Karte und Phase ein Ereignis (≤ 3000 Zeichen): {n, k, p: "d" aus / "l" an, b64}
    if (this.dumpPatches) for (const [k, [d, l]] of Object.entries(patches)) {
      this.log("live.flash.px", { n: ev.n, k, p: "d", b64: d });
      this.log("live.flash.px", { n: ev.n, k, p: "l", b64: l });
    }
    return added;
  }
}

export class FlashMonitor {
  readonly loop = new FlashLoop();
  readonly tally = this.loop.tally;
  blinking = false;                  // die Lampe wird gerade geschaltet (der Live-Scan schickt solange keine Bilder)
  private running = false;
  private switching = false;
  private timer = 0;
  private src = document.createElement("canvas");
  private tiny = document.createElement("canvas");
  private atlas = document.createElement("canvas");
  /** Kamerafarben reparieren (lib/cameraColors) — die Seite setzt das. */
  colorFix: () => boolean = () => false;
  /** Zoom der Kamera gegenüber dem Start (für den Blickwinkel) — die Seite setzt das. */
  zoom: () => number = () => 1;
  /** Drehung des Handys seit „Gerade" (Lagesensor, lib/tiltGrid) oder null — die Seite setzt das. */
  turn: () => number[] | null = () => null;
  private catalog = new Map<string, Float32Array | null>();   // Karten-ID → Katalogbild (40×55, linear), null: keins
  private catalogPending = new Set<string>();
  private catalogFails = new Map<string, number>();

  /** Katalogbild einer Karte (Server /scan/catalog-luma), einmal je ID geholt; null: keins, undefined: kommt noch. */
  private catalogFor(id: string | null | undefined): Float32Array | null | undefined {
    if (!id) return null;
    if (this.catalog.has(id)) return this.catalog.get(id) ?? null;
    if (!this.catalogPending.has(id)) {
      this.catalogPending.add(id);
      void (async () => {
        try {
          const { api } = await import("./api");
          const r = await api.get<{ b64: string | null }>(`/scan/catalog-luma/${encodeURIComponent(id)}`);
          this.catalog.set(id, r.data.b64 ? catalogLuma(r.data.b64) : null);
          dlog("live.flash.catalog", { id, ok: !!r.data.b64 });
        } catch {
          // noch einmal versuchen; nach dem dritten Fehlschlag ohne Katalogbild weiter (altes Maß)
          const n = (this.catalogFails.get(id) ?? 0) + 1;
          this.catalogFails.set(id, n);
          if (n >= 3) { this.catalog.set(id, null); dlog("live.flash.catalog", { id, ok: false, fails: n }); }
        } finally {
          this.catalogPending.delete(id);
        }
      })();
    }
    return undefined;
  }
  private catalogOf(id: string | null | undefined): { cat: Float32Array | null; catWait: boolean } {
    const c = this.catalogFor(id);
    return { cat: c ?? null, catWait: c === undefined };
  }

  /**
   * cards(): die Karten, die gemessen werden — null oder leer: nicht blinken.
   * onMeasure(): eine Karte hat eine Messung mehr. onBlink(on): blinkt ab jetzt / nicht mehr.
   * onAngles(pos): wo die Kamera je Karte steht (höchstens alle ANGLE_MS), für die Anleitung auf der Karte.
   */
  constructor(private video: HTMLVideoElement, private torch: (on: boolean) => Promise<void>,
    private cards: () => FlashCard[] | null, private onMeasure: () => void, private onBlink: (on: boolean) => void,
    private onAngles: (pos: Record<string, { tx: number; ty: number } | null>) => void = () => {}) {
    this.loop.log = (event, data) => dlog(event, data);
    this.loop.dumpPatches = debugEnabled();
  }
  private anglesAt = 0;

  start(): void {
    if (this.running) return;
    this.running = true;
    this.next();
    this.timer = window.setInterval(() => { void this.tick(); }, TICK_MS);
  }

  async stop(): Promise<void> {
    this.running = false;
    window.clearInterval(this.timer);
    this.setBlinking(false);
    if (this.loop.torchOn) await this.set(false);
  }

  private setBlinking(on: boolean) {
    if (this.blinking === on) return;
    this.blinking = on;
    this.onBlink(on);
    if (!on) this.loop.reset();
  }

  private async set(on: boolean) {
    this.switching = true;
    this.loop.asked(on, performance.now());
    try {
      await this.torch(on);
      this.loop.torchOn = on;
    } finally {
      this.switching = false;
    }
  }

  private async tick() {
    if (!this.running || this.switching) return;
    const list = document.hidden ? null : this.cards();
    if (!list || !list.length) {
      if (this.loop.torchOn) await this.set(false);
      this.setBlinking(false);
      return;
    }
    this.setBlinking(true);
    const want = this.loop.wanted(performance.now());
    if (want != null) await this.set(want);
  }

  private next = () => {
    if (!this.running) return;
    try {
      this.grab();
    } finally {
      // jedes neue Kamerabild (wo es das gibt), sonst mit der Bildwiederholung
      if (typeof this.video.requestVideoFrameCallback === "function") this.video.requestVideoFrameCallback(this.next);
      else requestAnimationFrame(this.next);
    }
  };

  private grab() {
    const v = this.video;
    if (!this.blinking || !v.videoWidth) return;
    const k = Math.min(1, 720 / Math.max(v.videoWidth, v.videoHeight));
    const vw = Math.round(v.videoWidth * k), vh = Math.round(v.videoHeight * k);
    if (this.src.width !== vw || this.src.height !== vh) { this.src.width = vw; this.src.height = vh; }
    const sctx = this.src.getContext("2d");
    if (!sctx) return;
    sctx.drawImage(v, 0, 0, vw, vh);
    // Helligkeit des ganzen Bilds (klein): daran ist das Umschalten zu sehen
    const tw = 24, th = Math.max(1, Math.round((24 * vh) / vw));
    if (this.tiny.width !== tw || this.tiny.height !== th) { this.tiny.width = tw; this.tiny.height = th; }
    const tctx = this.tiny.getContext("2d", { willReadFrequently: true });
    if (!tctx) return;
    tctx.drawImage(this.src, 0, 0, tw, th);
    const tp = tctx.getImageData(0, 0, tw, th).data;
    let sum = 0;
    for (let i = 0; i < tp.length; i += 4) sum += tp[i] + tp[i + 1] + tp[i + 2];
    const mean = sum / (tp.length / 4) / 3;
    // die offenen Karten ausschneiden
    const list = (this.cards() ?? []).filter((c) => c.quad.every(([x, y]) => x >= 0 && x <= 1 && y >= 0 && y <= 1));
    const shot: FlashFrame = { t: performance.now(), mean, cards: new Map(), turn: this.turn() };
    if (list.length) {
      if (this.atlas.width !== list.length * FLASH_W || this.atlas.height !== FLASH_H) {
        this.atlas.width = list.length * FLASH_W;
        this.atlas.height = FLASH_H;
      }
      const actx = this.atlas.getContext("2d", { willReadFrequently: true });
      if (actx) {
        actx.imageSmoothingEnabled = true;
        actx.imageSmoothingQuality = "medium";
        const px = list.map((c) => c.quad.map(([x, y]) => [x * vw, y * vh]));
        const focal = flashFocal(vw, vh, this.zoom());
        px.forEach((q, i) => {
          const m = flashTransform(q);
          if (!m) return;
          actx.save();
          actx.beginPath();
          actx.rect(i * FLASH_W, 0, FLASH_W, FLASH_H);
          actx.clip();
          actx.setTransform(m[0], m[1], m[2], m[3], m[4] + i * FLASH_W, m[5]);
          actx.drawImage(this.src, 0, 0);
          actx.restore();
        });
        if (this.colorFix()) fixCanvasColors(actx, this.atlas.width, this.atlas.height);
        const data = actx.getImageData(0, 0, this.atlas.width, this.atlas.height).data;
        list.forEach((c, i) => {
          const cc = cardOffset(px[i], vw, vh, focal, shot.turn ?? null);
          shot.cards.set(c.key, { px: atlasPatch(data, i, list.length), deg: cc?.deg ?? null, pos: cc ? { tx: cc.tx, ty: cc.ty } : null, q: c.quad,
            ...this.catalogOf(c.id) });
        });
      }
    }
    const t = performance.now();
    if (t - this.anglesAt >= ANGLE_MS) {
      this.anglesAt = t;
      this.onAngles(Object.fromEntries(Array.from(shot.cards, ([key, c]) => [key, c.pos ?? null])));
    }
    if (this.loop.frame(shot)) this.onMeasure();
  }
}

const nextFrame = (v: HTMLVideoElement) => new Promise<void>((r) => {
  if (typeof v.requestVideoFrameCallback === "function") v.requestVideoFrameCallback(() => r());
  else requestAnimationFrame(() => r());
});

/**
 * Lässt sich die Lampe schalten? Manche Android-Browser melden „torch" nicht als Fähigkeit,
 * obwohl es geht (Brians Fairphone 6: torch false). Also ausprobieren: Lampe an und schauen,
 * ob das Bild heller wird — kurz, bevor die Belichtung nachregelt (die Spitze zählt).
 */
export async function probeTorch(video: HTMLVideoElement, torch: (on: boolean) => Promise<void>): Promise<{ works: boolean; change: number }> {
  const c = document.createElement("canvas");
  const w = 24, h = Math.max(1, Math.round((24 * (video.videoHeight || 16)) / (video.videoWidth || 9)));
  c.width = w;
  c.height = h;
  const ctx = c.getContext("2d", { willReadFrequently: true });
  if (!ctx || !video.videoWidth) return { works: false, change: 0 };
  const mean = () => {
    ctx.drawImage(video, 0, 0, w, h);
    const d = ctx.getImageData(0, 0, w, h).data;
    let s = 0;
    for (let i = 0; i < d.length; i += 4) s += d[i] + d[i + 1] + d[i + 2];
    return s / (d.length / 4) / 3;
  };
  const sample = async (ms: number) => {
    const out: number[] = [];
    const end = performance.now() + ms;
    while (performance.now() < end) { await nextFrame(video); out.push(mean()); }
    return out;
  };
  const before = await sample(300);
  const base = before.length ? before.reduce((a, b) => a + b, 0) / before.length : 0;
  try {
    await torch(true);
    const lit = await sample(700);
    const change = lit.length && base ? Math.max(...lit.map((m) => (m - base) / base)) : 0;
    return { works: change >= 0.04, change: Math.round(change * 1000) / 1000 };
  } finally {
    await torch(false);
  }
}
