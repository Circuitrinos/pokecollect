// Folie beim Neigen: lib/tiltFoil im Web Worker (sonst, falls der Browser keinen startet, direkt hier).
import { FoilTilt, type Pixels, type TiltResult, type TiltSampleLog } from "./tiltFoil";

export class TiltFoilRunner {
  private worker: Worker | null = null;
  private inline: FoilTilt | null = null;
  private seq = 0;
  private waiting = new Map<number, (r: TiltResult | null) => void>();
  /** Debug: verkleinerte, ausgerichtete Bilder (nur wenn beim Hinzufügen log = true) */
  onSample: ((s: TiltSampleLog) => void) | null = null;
  private logOn = false;
  /** Ausschnitte, die der Worker noch nicht verarbeitet hat — bei langsamen Handys nicht nachschieben */
  pending = 0;

  constructor() {
    try {
      this.worker = new Worker(new URL("./tiltFoil.worker.ts", import.meta.url));
      this.worker.onmessage = (e: MessageEvent<{ id: number; result: TiltResult | null; sample?: TiltSampleLog }>) => {
        if (e.data.sample) { this.onSample?.(e.data.sample); return; }
        if ((e.data as { added?: boolean }).added) { this.pending = Math.max(0, this.pending - 1); return; }
        const done = this.waiting.get(e.data.id);
        this.waiting.delete(e.data.id);
        done?.(e.data.result);
      };
      this.worker.onerror = () => this.fallBack();
    } catch {
      this.fallBack();
    }
  }

  private fallBack() {
    this.worker?.terminate();
    this.worker = null;
    this.pending = 0;
    if (!this.inline) {
      this.inline = new FoilTilt();
      this.inline.onSample = (s) => { if (this.logOn) this.onSample?.(s); };
    }
    this.waiting.forEach((done) => done(null));
    this.waiting.clear();
  }

  /** Ein Ausschnitt des Kamerabilds (ab ox/oy) mit dem Umriss der Karte darin; die Pixel gehen an den Worker über. */
  add(key: string, px: Pixels, quad: number[][], t: number, log = false) {
    this.logOn = log;
    if (this.worker) {
      this.pending += 1;
      this.worker.postMessage({ type: "add", key, px, quad, t, log }, [px.data.buffer]);
      return;
    }
    this.inline!.add(key, px, quad, t);
  }

  result(key: string, options: string[]): Promise<TiltResult | null> {
    if (!this.worker) return Promise.resolve(this.inline!.result(key, options));
    const id = ++this.seq;
    return new Promise((done) => {
      this.waiting.set(id, done);
      this.worker!.postMessage({ type: "result", id, key, options });
    });
  }

  forget(keep: string[]) {
    if (this.worker) this.worker.postMessage({ type: "forget", keep });
    else this.inline!.forget(new Set(keep));
  }

  clear() {
    if (this.worker) this.worker.postMessage({ type: "clear" });
    else this.inline!.clear();
  }

  stop() {
    this.worker?.terminate();
    this.worker = null;
    this.waiting.forEach((done) => done(null));
    this.waiting.clear();
  }
}
