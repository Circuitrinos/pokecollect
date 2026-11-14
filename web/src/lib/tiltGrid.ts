/**
 * Tilted binder grid: the phone's orientation sensor turns the camera guide
 * the way a flat binder page looks when the phone is tilted against glare.
 *
 * Model: at the reference moment ("straight") the page sits square in the
 * guide. The photographer then tilts the phone while keeping the page in
 * view, i.e. the camera swings around the page's centre — so in the camera's
 * view the page turns about its own centre by the phone's rotation since the
 * reference. A flat page under that turn is a homography of the straight
 * guide; its corners go to the matcher, which straightens the page before
 * splitting it into pockets.
 *
 * Sensor: `deviceorientation` (alpha, beta, gamma: R = Rz(α)·Rx(β)·Ry(γ),
 * device → earth). Only the change since the reference counts, so an
 * arbitrary compass heading (alpha) is fine.
 *
 * Pure functions (no DOM).
 */
import type { Pt, Rect } from "./cardLock";

export type Mat3 = number[];   // row-major 3×3
export type Quad = [Pt, Pt, Pt, Pt];

const RAD = Math.PI / 180;

export const IDENTITY: Mat3 = [1, 0, 0, 0, 1, 0, 0, 0, 1];

export function mul(a: Mat3, b: Mat3): Mat3 {
  const r = new Array<number>(9);
  for (let i = 0; i < 3; i++)
    for (let j = 0; j < 3; j++)
      r[i * 3 + j] = a[i * 3] * b[j] + a[i * 3 + 1] * b[3 + j] + a[i * 3 + 2] * b[6 + j];
  return r;
}

export function transpose(a: Mat3): Mat3 {
  return [a[0], a[3], a[6], a[1], a[4], a[7], a[2], a[5], a[8]];
}

/** deviceorientation angles (degrees) → rotation device frame → earth frame. */
export function deviceToEarth(alpha: number, beta: number, gamma: number): Mat3 {
  const [ca, sa] = [Math.cos(alpha * RAD), Math.sin(alpha * RAD)];
  const [cb, sb] = [Math.cos(beta * RAD), Math.sin(beta * RAD)];
  const [cg, sg] = [Math.cos(gamma * RAD), Math.sin(gamma * RAD)];
  const rz: Mat3 = [ca, -sa, 0, sa, ca, 0, 0, 0, 1];
  const rx: Mat3 = [1, 0, 0, 0, cb, -sb, 0, sb, cb];
  const ry: Mat3 = [cg, 0, sg, 0, 1, 0, -sg, 0, cg];
  return mul(mul(rz, rx), ry);
}

/**
 * Earth → camera frame (x right, y down in the picture, z where the rear
 * camera looks). screenAngle: screen.orientation.angle (0, 90, 180, 270) —
 * the camera picture turns with the screen.
 */
export function earthToCamera(deviceToEarthM: Mat3, screenAngle: number): Mat3 {
  const t = screenAngle * RAD;
  const [c, s] = [Math.cos(t), Math.sin(t)];
  // device → screen axes (screen x = right, screen y = up), then y and z flipped for the camera
  const screen: Mat3 = [c, -s, 0, s, c, 0, 0, 0, 1];
  const flip: Mat3 = [1, 0, 0, 0, -1, 0, 0, 0, -1];
  return mul(mul(flip, screen), transpose(deviceToEarthM));
}

/** How the camera turned since the reference, in the camera frame. */
export function relativeTurn(now: Mat3, ref: Mat3): Mat3 {
  return mul(now, transpose(ref));
}

/** Angle of a rotation, degrees. */
export function turnDegrees(m: Mat3): number {
  const c = Math.min(1, Math.max(-1, (m[0] + m[4] + m[8] - 1) / 2));
  return Math.acos(c) / RAD;
}

/** Focal length in px of a phone's main camera (≈ 70° across the long side). */
export function focalPx(w: number, h: number): number {
  return 0.72 * Math.max(w, h);
}

/**
 * The guide's corners (TL, TR, BR, BL) as the flat page looks after the turn,
 * in frame px; null when the page would turn edge-on or behind the camera.
 * A near side swinging off screen is pulled in around the frame centre until
 * the whole guide fits `fit` of the frame — the shape stays, as if the phone
 * stepped back (which is what one does to keep the page in view).
 */
export function tiltedGuide(g: Rect, frameW: number, frameH: number, turn: Mat3, f = focalPx(frameW, frameH),
  fit = 0.96): Quad | null {
  const cx = frameW / 2, cy = frameH / 2;
  const corners: Pt[] = [[g.x, g.y], [g.x + g.w, g.y], [g.x + g.w, g.y + g.h], [g.x, g.y + g.h]];
  const out: Pt[] = [];
  for (const [x, y] of corners) {
    const u = x - cx, v = y - cy;
    const qx = turn[0] * u + turn[1] * v;
    const qy = turn[3] * u + turn[4] * v;
    const qz = f + turn[6] * u + turn[7] * v;
    if (qz < 0.25 * f) return null;
    out.push([cx + (f * qx) / qz, cy + (f * qy) / qz]);
  }
  const hx = (cx * fit), hy = (cy * fit);   // half the allowed width / height around the centre
  let k = 1;
  for (const [x, y] of out) {
    if (Math.abs(x - cx) > hx) k = Math.min(k, hx / Math.abs(x - cx));
    if (Math.abs(y - cy) > hy) k = Math.min(k, hy / Math.abs(y - cy));
  }
  return (k < 1 ? out.map(([x, y]) => [cx + (x - cx) * k, cy + (y - cy) * k]) : out) as Quad;
}

/** Homography of the unit square onto a quad (TL, TR, BR, BL): (s, t) → point. */
export function squareToQuad(q: Quad): (s: number, t: number) => Pt {
  const [[x0, y0], [x1, y1], [x2, y2], [x3, y3]] = q;
  const dx1 = x1 - x2, dx2 = x3 - x2, dy1 = y1 - y2, dy2 = y3 - y2;
  const sx = x0 - x1 + x2 - x3, sy = y0 - y1 + y2 - y3;
  const den = dx1 * dy2 - dx2 * dy1;
  const g = den ? (sx * dy2 - dx2 * sy) / den : 0;
  const h = den ? (dx1 * sy - sx * dy1) / den : 0;
  const a = x1 - x0 + g * x1, b = x3 - x0 + h * x3;
  const d = y1 - y0 + g * y1, e = y3 - y0 + h * y3;
  return (s, t) => {
    const w = g * s + h * t + 1;
    return [(a * s + b * t + x0) / w, (d * s + e * t + y0) / w];
  };
}

/**
 * Grey image resampled so the tilted quad fills the guide rect (nearest
 * neighbour, same size) — the straight-on view the edge finder expects.
 */
export function straightenToGuide(gray: Float32Array, w: number, h: number, quad: Quad, g: Rect): Float32Array {
  const map = squareToQuad(quad);
  const out = new Float32Array(w * h);
  for (let y = 0; y < h; y++) {
    const t = (y + 0.5 - g.y) / g.h;
    for (let x = 0; x < w; x++) {
      const [sx, sy] = map((x + 0.5 - g.x) / g.w, t);
      const xi = Math.round(sx - 0.5), yi = Math.round(sy - 0.5);
      out[y * w + x] = xi >= 0 && yi >= 0 && xi < w && yi < h ? gray[yi * w + xi] : 0;
    }
  }
  return out;
}

/**
 * The turn as a homography of the whole frame (px, straight view → turned view),
 * the same model as tiltedGuide but without its fit: the live scan's motion
 * tracker takes the perspective from here and the shift, zoom and in-plane turn
 * from the picture. Rows of a 3×3 matrix.
 */
export function tiltHomography(turn: Mat3, frameW: number, frameH: number, f = focalPx(frameW, frameH)): number[][] {
  const cx = frameW / 2, cy = frameH / 2;
  // around the centre: (u, v) → (R00 u + R01 v, R10 u + R11 v) / (1 + (R20 u + R21 v) / f)
  const a = [turn[0], turn[1], turn[3], turn[4], turn[6] / f, turn[7] / f];
  const [p, q, r, s, g, h] = a;
  const w0 = 1 - g * cx - h * cy;            // the bottom row after moving the origin to the centre
  return [
    [p + cx * g, q + cx * h, -(p * cx + q * cy) + cx * w0],
    [r + cy * g, s + cy * h, -(r * cx + s * cy) + cy * w0],
    [g, h, w0],
  ];
}

/** A point of the straightened view (see straightenToGuide) back in the frame. */
export function fromGuide(p: Pt, quad: Quad, g: Rect): Pt {
  return squareToQuad(quad)((p[0] - g.x) / g.w, (p[1] - g.y) / g.h);
}
