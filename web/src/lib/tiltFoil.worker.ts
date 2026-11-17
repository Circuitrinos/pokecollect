// Folie beim Neigen (lib/tiltFoil) im Hintergrund: Ausrichten, Entzerren und Vergleichen kosten
// einige Millisekunden je Bild und bis zu ~100 ms je Urteil — nicht im Takt der Kamera-Vorschau.
import { FoilTilt, type Pixels } from "./tiltFoil";

type In =
  | { type: "add"; key: string; px: Pixels; quad: number[][]; t: number; log: boolean }
  | { type: "result"; id: number; key: string; options: string[] }
  | { type: "forget"; keep: string[] }
  | { type: "clear" };

const ctx = self as unknown as { onmessage: ((e: MessageEvent<In>) => void) | null; postMessage: (m: unknown) => void };
const tilt = new FoilTilt();
let logOn = false;
tilt.onSample = (s) => { if (logOn) ctx.postMessage({ sample: s }); };

ctx.onmessage = (e) => {
  const m = e.data;
  if (m.type === "add") {
    logOn = m.log;
    try { tilt.add(m.key, m.px, m.quad, m.t); } catch { /* unbrauchbares Bild */ }
    ctx.postMessage({ added: true });   // fertig: der nächste Ausschnitt darf kommen
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
