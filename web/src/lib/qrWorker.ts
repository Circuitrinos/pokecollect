// QR-Leser im Hintergrund (components/QrScanner.tsx): zxing-cpp als WebAssembly —
// gemessen 4–10× schneller als jsQR und toleranter bei Unschärfe. Läuft in einem
// Worker, damit das Kamerabild flüssig bleibt; die .wasm-Datei liefert PokéCollect
// selbst aus (public/zxing/, scripts/copy-zxing-wasm.mjs), nicht das CDN.
import { prepareZXingModule, readBarcodes } from "zxing-wasm/reader";

prepareZXingModule({
  overrides: {
    locateFile: (path: string, prefix: string) => (path.endsWith(".wasm") ? "/zxing/zxing_reader.wasm" : prefix + path),
  },
});

export type QrRequest = { id: number; image: ImageData; multi: boolean };
export type QrHit = { text: string; cx: number; cy: number };
export type QrResponse = { id: number; hits: QrHit[] } | { id: number; error: string };

self.onmessage = async (e: MessageEvent<QrRequest>) => {
  const { id, image, multi } = e.data;
  try {
    const results = await readBarcodes(image, {
      formats: ["QRCode"],
      tryHarder: true,
      tryRotate: true,
      tryInvert: false,
      tryDownscale: true,
      maxNumberOfSymbols: multi ? 4 : 1,
    });
    const hits: QrHit[] = results.filter((r) => r.isValid && r.text).map((r) => {
      const p = r.position;
      return {
        text: r.text,
        cx: (p.topLeft.x + p.topRight.x + p.bottomRight.x + p.bottomLeft.x) / 4 / image.width,
        cy: (p.topLeft.y + p.topRight.y + p.bottomRight.y + p.bottomLeft.y) / 4 / image.height,
      };
    });
    (self as unknown as Worker).postMessage({ id, hits } satisfies QrResponse);
  } catch (err) {
    (self as unknown as Worker).postMessage({ id, error: String(err) } satisfies QrResponse);
  }
};
