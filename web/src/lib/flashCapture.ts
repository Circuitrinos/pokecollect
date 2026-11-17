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
// Nur Karten, die in beiden Phasen fast gerade unter dem Handy liegen (FLASH_MIN_DEG…FLASH_MAX_DEG).
import {
  FLASH_H, FLASH_MAX_DEG, FLASH_MIN_DEG, FLASH_W, FlashTally, alignPatch, atlasPatch,
  averagePatch, flashFocal, flashShare, flashTransform, litLevel, viewAngle,
} from "./flashCheck";
import { fixCanvasColors } from "./cameraColors";
import { dlog } from "./debugLog";

/** Eine noch offene Karte: Schlüssel und Umriss jetzt (TL, TR, BR, BL; Anteile des Kamerabilds). */
export type FlashCard = { key: string; quad: number[][] };

type Patches = Map<string, { px: Uint8ClampedArray; deg: number | null }>;
type Shot = { n: number; t: number; mean: number; cards: Patches };
type Phase = { lit: boolean; endN: number; means: number[]; cards: Patches };
/** Bitte ums Umschalten: wann, wohin, und wann es im Bild zu sehen war. */
type Request = { on: boolean; at: number; seenAt: number | null; seenN: number | null };

export const EVENT_STEP = 0.06;      // so viel heller/dunkler von einem Bild zum nächsten (mindestens): die Lampe hat geschaltet
export const PHASE_MS = 450;         // so lange bleibt die Lampe, nachdem das Umschalten zu sehen war
const SETTLE_MS = 300;               // bis dahin regelt die Belichtung nach dem Umschalten noch nach
const STEADY = 0.08;                 // |ln| Abstand zum Median der Phase: so ruhig muss ein verwendetes Bild sein
const EVENT_TIMEOUT_MS = 1500;       // so lange auf das Umschalten warten, dann gilt es als verpasst
const TICK_MS = 50;
const SHOTS_KEPT = 50;               // Bilder (~1.7 s) — eine ganze Phase muss hineinpassen
const ANGLE_MS = 250;

export class FlashMonitor {
  readonly tally = new FlashTally();
  blinking = false;                  // die Lampe wird gerade geschaltet (der Live-Scan schickt solange keine Bilder)
  private running = false;
  private torchOn = false;
  private switching = false;
  private req: Request | null = null;          // letzte Bitte ums Umschalten
  private lastSeen: { n: number; t: number; on: boolean } | null = null;   // letztes sichtbares Umschalten
  private shots: Shot[] = [];
  private count = 0;
  private timer = 0;
  private src = document.createElement("canvas");
  private tiny = document.createElement("canvas");
  private atlas = document.createElement("canvas");
  private phase: Phase | null = null; // Ende der letzten Phase (Lampe an oder aus)
  private jumps: number[] = [];       // |ln| der zuletzt gesehenen Sprünge beim Umschalten (diese Kamera)
  /** Kamerafarben reparieren (lib/cameraColors) — die Seite setzt das. */
  colorFix: () => boolean = () => false;
  /** Zoom der Kamera gegenüber dem Start (für den Blickwinkel) — die Seite setzt das. */
  zoom: () => number = () => 1;

  /**
   * cards(): die Karten, die gemessen werden — null oder leer: nicht blinken.
   * onMeasure(): eine Karte hat eine Messung mehr. onBlink(on): blinkt ab jetzt / nicht mehr.
   * onAngles(deg): Blickwinkel je Karte (höchstens alle ANGLE_MS), für die Anleitung auf der Karte.
   */
  constructor(private video: HTMLVideoElement, private torch: (on: boolean) => Promise<void>,
    private cards: () => FlashCard[] | null, private onMeasure: () => void, private onBlink: (on: boolean) => void,
    private onAngles: (deg: Record<string, number | null>) => void = () => {}) {}
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
    if (this.torchOn) await this.set(false);
  }

  /** Mindestsprung (|ln|) fürs Umschalten: EVENT_STEP, nach zwei gesehenen 40 % des üblichen Sprungs dieser Kamera. */
  private eventThreshold(): number {
    const base = Math.log(1 + EVENT_STEP);
    if (this.jumps.length < 2) return base;
    const s = [...this.jumps].sort((a, b) => a - b);
    return Math.max(base, 0.4 * s[s.length >> 1]);
  }

  private setBlinking(on: boolean) {
    if (this.blinking === on) return;
    this.blinking = on;
    this.onBlink(on);
    if (!on) { this.shots = []; this.phase = null; this.lastSeen = null; this.req = null; this.jumps = []; }
  }

  private async set(on: boolean) {
    this.switching = true;
    this.req = { on, at: performance.now(), seenAt: null, seenN: null };
    try {
      await this.torch(on);
      this.torchOn = on;
    } finally {
      this.switching = false;
    }
  }

  private async tick() {
    if (!this.running || this.switching) return;
    const list = document.hidden ? null : this.cards();
    if (!list || !list.length) {
      if (this.torchOn) await this.set(false);
      this.setBlinking(false);
      return;
    }
    this.setBlinking(true);
    const now = performance.now(), r = this.req;
    if (r && r.seenAt == null) {
      // noch nicht im Bild: warten — zu lange, dann gilt es als verpasst (die Kette der Phasen reißt)
      if (now - r.at < EVENT_TIMEOUT_MS) return;
      dlog("live.flash.missed", { on: r.on, ms: Math.round(now - r.at) });
      this.phase = null;
      this.lastSeen = null;
    } else if (r && r.seenAt != null && now - r.seenAt < PHASE_MS) {
      return;
    }
    await this.set(!this.torchOn);
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
    const shot: Shot = { n: this.count++, t: performance.now(), mean, cards: new Map() };
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
        list.forEach((c, i) => shot.cards.set(c.key, { px: atlasPatch(data, i, list.length), deg: viewAngle(px[i], vw, vh, focal) }));
      }
    }
    const t = performance.now();
    if (t - this.anglesAt >= ANGLE_MS) {
      this.anglesAt = t;
      this.onAngles(Object.fromEntries(Array.from(shot.cards, ([key, c]) => [key, c.deg])));
    }
    this.shots.push(shot);
    if (this.shots.length > SHOTS_KEPT) this.shots.shift();
    // ist das erbetene Umschalten im vorletzten Bild zu sehen? (Sprung in die erwartete Richtung gegenüber
    // dem Bild davor, und im letzten Bild noch mindestens halb so weit dort)
    const r = this.req, nShots = this.shots.length;
    if (!r || r.seenAt != null || nShots < 3) return;
    const ev = this.shots[nShots - 2], pre = this.shots[nShots - 3];
    if (ev.t <= r.at || ev.n !== pre.n + 1 || shot.n !== ev.n + 1) return;
    const dir = r.on ? 1 : -1;
    const j1 = Math.log(Math.max(ev.mean, 1) / Math.max(pre.mean, 1)), j2 = Math.log(Math.max(shot.mean, 1) / Math.max(pre.mean, 1));
    const minJump = this.eventThreshold();
    if (dir * j1 < minJump || dir * j2 < minJump / 2) return;
    this.jumps.push(Math.abs(j1));
    if (this.jumps.length > 8) this.jumps.shift();
    r.seenAt = ev.t;
    r.seenN = ev.n;
    const before = this.lastSeen;
    this.lastSeen = { n: ev.n, t: ev.t, on: r.on };
    const seenLog = { on: r.on, ms: Math.round(ev.t - r.at), jump: [pre, ev, shot].map((x) => Math.round(x.mean)) };
    // die Phase, die jetzt zu Ende ging: Lampe so, wie sie beim vorigen Umschalten wurde
    if (!before || before.on === r.on) { this.phase = null; dlog("live.flash.seen", seenLog); return; }
    const window = this.shots.filter((x) => x.n > before.n && x.n <= ev.n - 2 && x.t >= before.t + SETTLE_MS);
    // ruhige Bilder: Helligkeit nah am Median der Phase (ein Ausreißer kurz vor dem Umschalten bleibt draußen)
    const sorted = window.map((x) => x.mean).sort((a, b) => a - b);
    const mid = sorted.length ? sorted[sorted.length >> 1] : 0;
    const usable = window.filter((x) => Math.abs(Math.log(Math.max(x.mean, 1) / Math.max(mid, 1))) <= STEADY);
    const [prev, last] = usable.length >= 2 ? usable.slice(-2) : [undefined, usable[0]];
    if (!last) { this.phase = null; dlog("live.flash.seen", { ...seenLog, frames: 0 }); return; }
    const ended: Phase = { lit: before.on, endN: ev.n, means: [], cards: new Map() };
    last.cards.forEach((c, key) => {
      const p = prev?.cards.get(key);
      ended.cards.set(key, { px: p ? averagePatch(p.px, c.px) : c.px, deg: c.deg });
    });
    ended.means = [prev, last].filter((x): x is Shot => !!x).map((x) => Math.round(x.mean));
    const older = this.phase;
    this.phase = ended;
    // nur zwei Phasen direkt hintereinander (die ältere endete mit dem Umschalten, mit dem diese begann)
    if (!older || older.lit === ended.lit || older.endN !== before.n) { dlog("live.flash.seen", { ...seenLog, frames: usable.length }); return; }
    const darkPh = ended.lit ? older : ended, litPh = ended.lit ? ended : older;
    let added = false;
    const seen: Record<string, (number | null)[]> = {};   // Protokoll: je Karte [Anteil, Helligkeit an/aus, Winkel]
    litPh.cards.forEach((b, key) => {
      const a = darkPh.cards.get(key);
      if (!a || b.deg == null || a.deg == null) return;
      const inBand = (d: number) => d >= FLASH_MIN_DEG && d <= FLASH_MAX_DEG;
      const ok = inBand(b.deg) && inBand(a.deg);
      const lit = ok ? alignPatch(a.px, b.px) : b.px;
      const sh = ok ? flashShare(a.px, lit) : null;
      const deg = (a.deg + b.deg) / 2;
      seen[key] = [sh == null ? null : Math.round(sh * 1000) / 1000, Math.round(litLevel(lit)), Math.round(litLevel(a.px)), Math.round(deg)];
      if (sh == null) return;
      this.tally.add(key, sh, deg, r.on);
      added = true;
    });
    dlog("live.flash.switch", { ...seenLog, ends: { dark: darkPh.means, lit: litPh.means }, frames: [usable.length, window.length], cards: seen });
    if (added) this.onMeasure();
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
