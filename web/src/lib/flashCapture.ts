// Blitz-Test im Browser: die Lampe schalten lassen, jede Karte in jedem Kamerabild ausschneiden
// (Umrisse fest, das Handy wird ruhig gehalten) und je Umschalten vergleichen (lib/flashCheck).
import {
  FLASH_H, FLASH_MAX_DEG, FLASH_MIN_DEG, FLASH_STEP_MS, FLASH_SWITCHES, FLASH_W, atlasPatch, flashPairs, flashShare, flashTransform,
  patchMean, summarize, viewAngle, type FlashResult,
} from "./flashCheck";

export type FlashRun = { results: (FlashResult | null)[]; frames: number; fps: number | null; switches: number; ms: number };

const pause = (ms: number) => new Promise((r) => window.setTimeout(r, ms));

/**
 * quads: je Karte vier Ecken (TL, TR, BR, BL) als Anteile des Kamerabilds (0..1).
 * torch(on): schaltet die Lampe (Fehler fängt der Aufrufer ab). Ergebnis je Karte in derselben
 * Reihenfolge — null, wo kein Umschalten messbar war (überstrahlt, außerhalb des Bilds).
 */
export async function captureFlash(video: HTMLVideoElement, quads: number[][][], torch: (on: boolean) => Promise<void>,
  quadsNow: (() => number[][][] | null) | null = null, switches = FLASH_SWITCHES, stepMs = FLASH_STEP_MS): Promise<FlashRun> {
  const t0 = performance.now();
  const k = Math.min(1, 720 / Math.max(video.videoWidth, video.videoHeight));
  const vw = Math.round(video.videoWidth * k), vh = Math.round(video.videoHeight * k);
  const inside = quads.map((q) => q.every(([x, y]) => x >= 0 && x <= 1 && y >= 0 && y <= 1));
  const toMat = (q: number[][]) => flashTransform(q.map(([x, y]) => [x * vw, y * vh]));
  const mats = quads.map((q, i) => (inside[i] ? toMat(q) : null));
  const cur = quads.map((q) => q.map(([x, y]) => [x * vw, y * vh]));          // Umrisse jetzt (Pixel)
  const use = mats.map((m, i) => (m ? i : -1)).filter((i) => i >= 0);
  const empty: FlashRun = { results: quads.map(() => null), frames: 0, fps: null, switches: 0, ms: 0 };
  if (!use.length || !vw || !vh) return empty;
  const src = document.createElement("canvas");
  src.width = vw;
  src.height = vh;
  const atlas = document.createElement("canvas");
  atlas.width = use.length * FLASH_W;
  atlas.height = FLASH_H;
  const sctx = src.getContext("2d");
  const actx = atlas.getContext("2d", { willReadFrequently: true });
  if (!sctx || !actx) return empty;
  actx.imageSmoothingEnabled = true;
  actx.imageSmoothingQuality = "medium";
  const frames: Uint8ClampedArray[] = [], means: number[] = [], times: number[] = [];
  const degs: (number | null)[][] = [];                                         // Blickwinkel je Bild und Karte
  const grab = () => {
    // die Umrisse wandern mit der Kamera mit (Bewegungsverfolgung), wo es sie gibt
    const now = quadsNow?.();
    if (now) now.forEach((q, i) => {
      if (!mats[i] || !q.every(([x, y]) => x >= 0 && x <= 1 && y >= 0 && y <= 1)) return;
      mats[i] = toMat(q) ?? mats[i];
      cur[i] = q.map(([x, y]) => [x * vw, y * vh]);
    });
    sctx.drawImage(video, 0, 0, vw, vh);
    use.forEach((ci, i) => {
      const m = mats[ci]!;
      actx.save();
      actx.beginPath();
      actx.rect(i * FLASH_W, 0, FLASH_W, FLASH_H);
      actx.clip();
      actx.setTransform(m[0], m[1], m[2], m[3], m[4] + i * FLASH_W, m[5]);
      actx.drawImage(src, 0, 0);
      actx.restore();
    });
    const px = actx.getImageData(0, 0, atlas.width, atlas.height).data;
    frames.push(px);
    degs.push(use.map((ci) => viewAngle(cur[ci], vw, vh)));
    means.push(patchMean(px));
    times.push(performance.now());
  };
  let capturing = true;
  const next = () => {
    if (!capturing) return;
    grab();
    // jedes neue Kamerabild (wo es das gibt), sonst mit der Bildwiederholung
    if (typeof video.requestVideoFrameCallback === "function") video.requestVideoFrameCallback(next);
    else requestAnimationFrame(next);
  };
  next();
  try {
    await pause(300);
    for (let i = 0; i < switches; i++) {
      await torch(i % 2 === 0);
      await pause(stepMs);
    }
  } finally {
    await torch(false);
    await pause(150);
    capturing = false;
  }
  const pairs = flashPairs(means, times);
  // nur Umschalter, bei denen die Karte fast gerade unter dem Handy lag (FLASH_MIN_DEG…FLASH_MAX_DEG)
  const per = use.map(() => [] as number[]), deg = use.map(() => [] as number[]), outside = use.map(() => 0);
  for (const [off, on] of pairs) {
    use.forEach((_, i) => {
      const d = degs[on][i];
      if (d == null || d < FLASH_MIN_DEG || d > FLASH_MAX_DEG) { outside[i] += 1; return; }
      const s = flashShare(atlasPatch(frames[off], i, use.length), atlasPatch(frames[on], i, use.length));
      if (s == null) return;
      per[i].push(s);
      deg[i].push(Math.round(d * 10) / 10);
    });
  }
  const results: (FlashResult | null)[] = quads.map(() => null);
  use.forEach((ci, i) => {
    const r = summarize(per[i]);
    results[ci] = r ? { ...r, deg: deg[i], outside: outside[i] } : null;
  });
  const secs = times.length > 1 ? (times[times.length - 1] - times[0]) / 1000 : 0;
  return { results, frames: frames.length, fps: secs ? +(frames.length / secs).toFixed(1) : null, switches: pairs.length,
    ms: Math.round(performance.now() - t0) };
}
