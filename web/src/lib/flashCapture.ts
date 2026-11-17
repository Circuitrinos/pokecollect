// Blitz-Test im Live-Scan: sind alle Karten erkannt, blinkt die Lampe, bis jede Karte ihre
// Folierung hat. Jedes Kamerabild: die noch offenen Karten ausschneiden (Umrisse folgen der
// Bewegungsverfolgung). Verglichen werden die Enden zweier Phasen (Lampe aus / an): je die beiden
// letzten Bilder vor einem Umschalten, gemittelt — dann hat die Belichtung nachgeregelt. Direkt nach
// dem Umschalten ist das Bild über- oder unterbelichtet (Brave/Fairphone: Median 157 → 204 → 163 in
// ~0.35 s), und das verzerrt Bild gegen Rahmen. Nur Karten, die in beiden Phasen fast gerade unter dem
// Handy liegen (FLASH_MIN_DEG…FLASH_MAX_DEG).
import {
  FLASH_H, FLASH_MAX_DEG, FLASH_MIN_DEG, FLASH_STEP_MS, FLASH_W, FlashTally, SwitchFinder, alignPatch, atlasPatch,
  averagePatch, flashFocal, flashShare, flashTransform, litLevel, viewAngle,
} from "./flashCheck";
import { fixCanvasColors } from "./cameraColors";
import { dlog } from "./debugLog";

/** Eine noch offene Karte: Schlüssel und Umriss jetzt (TL, TR, BR, BL; Anteile des Kamerabilds). */
export type FlashCard = { key: string; quad: number[][] };

type Shot = { n: number; cards: Map<string, { px: Uint8ClampedArray; deg: number | null }> };
type Phase = { lit: boolean; at: number; cards: Map<string, { px: Uint8ClampedArray; deg: number | null }> };
const ANGLE_MS = 250;

export class FlashMonitor {
  readonly tally = new FlashTally();
  blinking = false;                  // die Lampe wird gerade geschaltet (der Live-Scan schickt solange keine Bilder)
  private running = false;
  private torchOn = false;
  private switching = false;
  private asked: { at: number; on: boolean } | null = null;
  private finder = new SwitchFinder();
  private shots: Shot[] = [];
  private count = 0;
  private timer = 0;
  private src = document.createElement("canvas");
  private tiny = document.createElement("canvas");
  private atlas = document.createElement("canvas");
  private means: number[] = [];       // Helligkeit der letzten Bilder (fürs Protokoll)
  private phase: Phase | null = null; // Ende der letzten Phase (Lampe an oder aus)
  /** Kamerafarben reparieren (lib/cameraColors) — die Seite setzt das. */
  colorFix: () => boolean = () => false;
  /** Zoom der Kamera gegenüber dem Start (für den Blickwinkel) — die Seite setzt das. */
  zoom: () => number = () => 1;

  /**
   * cards(): die Karten, deren Folierung noch offen ist — null oder leer: nicht blinken.
   * onMeasure(): eine Karte hat ein Umschalten mehr. onBlink(on): blinkt ab jetzt / nicht mehr.
   * onAngles(deg): Blickwinkel je offener Karte (höchstens alle ANGLE_MS), für die Anleitung auf der Karte.
   */
  constructor(private video: HTMLVideoElement, private torch: (on: boolean) => Promise<void>,
    private cards: () => FlashCard[] | null, private onMeasure: () => void, private onBlink: (on: boolean) => void,
    private onAngles: (deg: Record<string, number | null>) => void = () => {}) {}
  private anglesAt = 0;

  start(): void {
    if (this.running) return;
    this.running = true;
    this.next();
    this.timer = window.setInterval(() => { void this.tick(); }, FLASH_STEP_MS);
  }

  async stop(): Promise<void> {
    this.running = false;
    window.clearInterval(this.timer);
    this.setBlinking(false);
    if (this.torchOn) await this.set(false);
  }

  private setBlinking(on: boolean) {
    if (this.blinking === on) return;
    this.blinking = on;
    this.onBlink(on);
    if (!on) { this.finder.reset(); this.shots = []; this.means = []; this.phase = null; }
  }

  private async set(on: boolean) {
    this.switching = true;
    this.asked = { at: performance.now(), on };
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
    const shot: Shot = { n: this.count++, cards: new Map() };
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
    if (this.shots.length > 5) this.shots.shift();
    this.means.push(Math.round(mean));
    if (this.means.length > 4) this.means.shift();
    const sw = this.finder.push(mean, performance.now());
    if (!sw) return;
    // nur ein Umschalten, um das gebeten wurde (richtige Richtung, kurz danach) — kein Schwenk ins Helle
    const asked = this.asked;
    if (!asked || asked.on !== sw.lightOn || performance.now() - asked.at > 1.5 * FLASH_STEP_MS) return;
    this.asked = null;
    // die Phase, die gerade zu Ende ging: die beiden letzten Bilder vor dem Sprung (davor das halb belichtete)
    const pre = sw.lightOn ? sw.off : sw.on;
    const last = this.shots.find((x) => x.n === pre), prev = this.shots.find((x) => x.n === pre - 1);
    if (!last) { this.phase = null; return; }
    const ended: Phase = { lit: !sw.lightOn, at: performance.now(), cards: new Map() };
    last.cards.forEach((c, key) => {
      const p = prev?.cards.get(key);
      ended.cards.set(key, { px: p ? averagePatch(p.px, c.px) : c.px, deg: c.deg });
    });
    const before = this.phase;
    this.phase = ended;
    // ein Umschalten verpasst (gleicher Zustand oder zu lange her): erst mit der nächsten Phase vergleichen
    if (!before || before.lit === ended.lit || ended.at - before.at > 2.5 * FLASH_STEP_MS) return;
    const darkPh = ended.lit ? before : ended, litPh = ended.lit ? ended : before;
    let added = false;
    const seen: Record<string, (number | null)[]> = {};   // Protokoll: je Karte [Anteil, Helligkeit an/aus, Winkel]
    litPh.cards.forEach((b, key) => {
      const a = darkPh.cards.get(key);
      if (!a || b.deg == null || a.deg == null) return;
      const inBand = (d: number) => d >= FLASH_MIN_DEG && d <= FLASH_MAX_DEG;
      const ok = inBand(b.deg) && inBand(a.deg);
      const lit = ok ? alignPatch(a.px, b.px) : b.px;
      const s = ok ? flashShare(a.px, lit) : null;
      const deg = (a.deg + b.deg) / 2;
      seen[key] = [s == null ? null : Math.round(s * 1000) / 1000, Math.round(litLevel(lit)), Math.round(litLevel(a.px)), Math.round(deg)];
      if (s == null) return;
      this.tally.add(key, s, deg, sw.lightOn);
      added = true;
    });
    dlog("live.flash.switch", { on: sw.lightOn, zoom: this.zoom(), means: this.means.slice(), cards: seen });
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
