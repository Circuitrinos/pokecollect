// „Einschwingen" eines Zuschnitts (Fix crop): entlang der Ecken aus dem
// Original schneiden, vom Bild-Abgleich prüfen lassen (ohne zu speichern) und
// seinem nächsten Vorschlag folgen, bis der Zuschnitt besteht oder sich die
// Ecken kaum noch bewegen. Der Abgleich bessert selbst schon mehrmals nach
// (matcher recrop.refine); hier kommen höchstens ein paar Runden dazu.
import { cropToCardPhoto, CropTransform } from "@/lib/cardCrop";

export type CropCheck = { status: string; inliers: number; corner_err: number | null; suggested_quad: number[][] | null };
export type Settled = { file: File; quad: number[][]; ok: boolean; inliers: number; err: number | null };

// Unter so vielen Treffern ist die Lage der Ecken nicht messbar (matcher MIN_GOOD)
export const MIN_FOR_CORNERS = 12;
export const cornerErr = (inliers: number, err: number | null) => (inliers >= MIN_FOR_CORNERS ? err : null);

const SETTLED = 0.003;
const moved = (a: number[][], b: number[][]) => Math.max(...a.flatMap((p, i) => p.map((v, k) => Math.abs(v - b[i][k]))));

export async function settleCrop(
  blob: Blob,
  start: number[][],
  check: (file: File, quad: number[][]) => Promise<CropCheck>,
  // includeStart: false = die Start-Ecken sind grob (von Hand) — mindestens dem
  // ersten Vorschlag folgen, statt sie selbst als Ergebnis zu nehmen
  opts: { transform?: CropTransform; rounds?: number; includeStart?: boolean } = {},
): Promise<Settled | null> {
  let quad: number[][] | null = start;
  let transform: CropTransform | undefined = opts.transform;
  let best: Settled | null = null;
  const includeStart = opts.includeStart ?? true;
  for (let round = 0; round < (opts.rounds ?? 3) && quad; round++) {
    const file = await cropToCardPhoto(blob, { quad, ...(transform ?? {}) });
    const r = await check(file, quad);
    // Vorschläge des Abgleichs sind schon richtig herum (Karten-Ecke oben links zuerst)
    transform = undefined;
    const cand: Settled = { file, quad, ok: r.status === "ok", inliers: r.inliers, err: cornerErr(r.inliers, r.corner_err) };
    const counts = round > 0 || includeStart;
    const better = !best || (cand.ok && !best.ok)
      || (cand.ok === best.ok && (cand.err ?? 1) <= (best.err ?? 1) && cand.inliers >= best.inliers * 0.8);
    if (counts && better) best = cand;
    if (counts && cand.ok) break;
    const next: number[][] | null = r.suggested_quad;
    if (!next || moved(next, quad) < SETTLED) {
      if (!best && next) best = cand;      // die eigenen Ecken sitzen schon
      break;
    }
    quad = next;
  }
  return best;
}
