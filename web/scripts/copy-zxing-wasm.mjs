// QR-Leser für die Hüllen-Aufkleber (src/lib/qrWorker.ts): zxing-wasm lädt seine
// .wasm-Datei sonst vom jsDelivr-CDN. Darum vor jedem Build/Dev-Start aus
// node_modules nach public/zxing/ kopieren — selbst ausgeliefert, immer passend
// zur installierten Version (package-lock.json).
import { copyFileSync, mkdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const web = join(dirname(fileURLToPath(import.meta.url)), "..");
const src = join(web, "node_modules", "zxing-wasm", "dist", "reader", "zxing_reader.wasm");
const dst = join(web, "public", "zxing", "zxing_reader.wasm");
mkdirSync(dirname(dst), { recursive: true });
copyFileSync(src, dst);
console.log("zxing_reader.wasm → public/zxing/");
