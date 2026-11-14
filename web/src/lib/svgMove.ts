// Live-Scan: eine SVG-Zeichnung mit der Kamera mitführen, auch perspektivisch (die
// SVG-transform-Matrix kann keine Perspektive). Die Elemente tragen ihre Lage im
// geprüften Bild als data-pts ("x,y x,y …" → points) bzw. data-at ("x,y" → x/y, Text
// bleibt aufrecht und lesbar). React zeichnet weiter die Ausgangslage; moveSvg rechnet
// nur neu, wenn sich die Matrix oder die Ausgangslage eines Elements geändert hat.
import { apply, type Mat3 } from "./motionTrack";

const applied = new WeakMap<Element, string>();   // Element → zuletzt angewandt ("Matrix|Ausgangslage")

const parse = (s: string): number[][] => s.trim().split(/\s+/).map((p) => p.split(",").map(Number));
const fmt = (v: number) => (Number.isFinite(v) ? v.toFixed(1) : "0");

/** Matrix als Schlüssel (gleiche Matrix → nichts zu tun). */
export function matrixKey(m: Mat3 | null): string {
  return m ? m.map((r) => r.map((v) => v.toPrecision(6)).join(",")).join(";") : "-";
}

/** Alle data-pts/data-at-Elemente unter root mit m (Bildpunkte) verschieben; m = null → Ausgangslage. */
export function moveSvg(root: Element | null, m: Mat3 | null, key = matrixKey(m)): void {
  if (!root) return;
  root.querySelectorAll("[data-pts],[data-at]").forEach((el) => {
    const pts = el.getAttribute("data-pts");
    const src = pts ?? el.getAttribute("data-at") ?? "";
    const stamp = `${key}|${src}`;
    if (applied.get(el) === stamp) return;
    applied.set(el, stamp);
    const p = parse(src);
    const out = m ? apply(m, p) : p;
    if (pts != null) el.setAttribute("points", out.map(([x, y]) => `${fmt(x)},${fmt(y)}`).join(" "));
    else if (out[0]) { el.setAttribute("x", fmt(out[0][0])); el.setAttribute("y", fmt(out[0][1])); }
  });
}

/** Punkte als data-pts-Text. */
export const ptsAttr = (pts: number[][]): string => pts.map(([x, y]) => `${x},${y}`).join(" ");
