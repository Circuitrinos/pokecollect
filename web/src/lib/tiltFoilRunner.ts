// Folie beim Neigen: lib/tiltFoil im Web Worker (sonst, falls der Browser keinen startet, direkt hier).
import { FoilTilt, warpCard, type Pixels, type TiltResult } from "./tiltFoil";

export class TiltFoilRunner {
  private worker: Worker | null = null;
  private inline: FoilTilt | null = null;
  private seq = 0;
  private waiting = new Map<number, (r: TiltResult | null) => void>();

  constructor() {
    try {
      this.worker = new Worker(new URL("./tiltFoil.worker.ts", import.meta.url));
      this.worker.onmessage = (e: MessageEvent<{ id: number; result: TiltResult | null }>) => {
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
    this.inline = this.inline ?? new FoilTilt();
    this.waiting.forEach((done) => done(null));
    this.waiting.clear();
  }

  /** Ein Ausschnitt des Kamerabilds (ab ox/oy) mit dem Umriss der Karte darin; die Pixel gehen an den Worker über. */
  add(key: string, px: Pixels, quad: number[][], t: number) {
    if (this.worker) {
      this.worker.postMessage({ type: "add", key, px, quad, t }, [px.data.buffer]);
      return;
    }
    const card = warpCard(px, quad);
    if (card) this.inline!.add(key, card, quad, t);
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
