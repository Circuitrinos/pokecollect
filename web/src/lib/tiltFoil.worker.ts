// Folie beim Neigen (lib/tiltFoil) im Hintergrund: Entzerren, Ausrichten und Vergleichen kosten
// einige Millisekunden je Bild und bis zu ~100 ms je Urteil — nicht im Takt der Kamera-Vorschau.
import { FoilTilt, warpCard, type Pixels } from "./tiltFoil";

type In =
  | { type: "add"; key: string; px: Pixels; quad: number[][]; t: number }
  | { type: "result"; id: number; key: string; options: string[] }
  | { type: "forget"; keep: string[] }
  | { type: "clear" };

const ctx = self as unknown as { onmessage: ((e: MessageEvent<In>) => void) | null; postMessage: (m: unknown) => void };
const tilt = new FoilTilt();

ctx.onmessage = (e) => {
  const m = e.data;
  if (m.type === "add") {
    const card = warpCard(m.px, m.quad);
    if (card) tilt.add(m.key, card, m.quad, m.t);
  } else if (m.type === "result") {
    let result = null;
    try { result = tilt.result(m.key, m.options); } catch { /* unbrauchbar */ }
    ctx.postMessage({ id: m.id, result });
  } else if (m.type === "forget") {
    tilt.forget(new Set(m.keep));
  } else {
    tilt.clear();
  }
};
