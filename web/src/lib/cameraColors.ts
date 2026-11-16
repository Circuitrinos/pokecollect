// Kamerafarben reparieren: Brave auf Brians Fairphone 6 liefert das Kamerabild mit vertauschten
// Farbdifferenz-Ebenen (Cb ↔ Cr, wie NV12 statt NV21 gelesen) — Rot wird Blau, die orangen Hüllen
// hellblau (16.11.; Firefox auf demselben Handy zeigt richtige Farben). Rückgängig macht es dieselbe
// Vertauschung noch einmal: als Matrix auf den sRGB-Werten (Y bleibt, Cb und Cr tauschen; die Matrix
// ist ihre eigene Umkehrung). Gemessen an Brians Aufnahme: Hüllen-Orange (86, 135, 193) → (179, 111, 75),
// in der Kamera-App (181, 103, 75).

/** Zeilen: neues R, G, B aus altem R, G, B (sRGB 0..255). */
export const SWAP_CHROMA: number[][] = [
  [0.06243, 0.12257, 0.815],
  [0.24743, 0.96765, -0.21509],
  [1.185, -0.15492, -0.03008],
];

export const SWAP_FILTER_ID = "pc-swap-chroma";

/** Werte für <feColorMatrix type="matrix"> (color-interpolation-filters="sRGB"). */
export const swapFilterValues = SWAP_CHROMA.map((r) => `${r.join(" ")} 0 0`).concat(["0 0 0 1 0"]).join("  ");

/** Pixel eines Canvas umrechnen (nach drawImage des Kamerabilds). */
export function fixCanvasColors(ctx: CanvasRenderingContext2D, w: number, h: number): void {
  const img = ctx.getImageData(0, 0, w, h);
  const d = img.data;
  const [[a, b, c], [e, f, g], [i, j, k]] = SWAP_CHROMA;
  for (let p = 0; p < d.length; p += 4) {
    const r = d[p], gr = d[p + 1], bl = d[p + 2];
    d[p] = a * r + b * gr + c * bl;          // Uint8ClampedArray rundet und klemmt auf 0..255
    d[p + 1] = e * r + f * gr + g * bl;
    d[p + 2] = i * r + j * gr + k * bl;
  }
  ctx.putImageData(img, 0, 0);
}
