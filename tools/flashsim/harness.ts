// Runs the app's own flash test (FlashLoop + FlashTally + viewAngle from web/src/lib) on frames from the
// simulator. One JSON message per line on stdin, one answer per line on stdout.
//   {op:"tick", t}                       → {want: true|false|null}
//   {op:"asked", on, t}                  → {}
//   {op:"frame", t, mean, w, h, cards:[{key, quad, px(base64 RGBA 40×55)}]}
//                                         → {added, cards:{key:[n, tentative, settled, deg]}, log:[...]}
//   {op:"result"}                         → {cards:{key: FlashTally.result}}
import { createInterface } from "node:readline";
import { FlashLoop } from "@lib/flashCapture";
import * as FC from "@lib/flashCheck";

const loop = new FlashLoop();
let logs: unknown[] = [];
loop.log = (event, data) => { logs.push([event, data]); };
const keys = new Set<string>();

const rl = createInterface({ input: process.stdin });
rl.on("line", (line) => {
  const m = JSON.parse(line);
  let out: unknown = {};
  if (m.op === "tick") {
    out = { want: loop.wanted(m.t) };
  } else if (m.op === "asked") {
    loop.asked(m.on, m.t);
    loop.torchOn = m.on;   // the torch call resolves (the simulator says when it is asked)
  } else if (m.op === "frame") {
    const cards = new Map<string, { px: Uint8ClampedArray; deg: number | null; pos?: { tx: number; ty: number } | null }>();
    const focal = FC.flashFocal(m.w, m.h, 1);
    const cc0 = (FC as Record<string, unknown>).cardCamera as ((q: number[][], w: number, h: number, f: number) => { tx: number; ty: number; deg: number } | null) | undefined;
    const co = (FC as Record<string, unknown>).cardOffset as ((q: number[][], w: number, h: number, f: number, t: number[] | null) => { tx: number; ty: number; deg: number } | null) | undefined;
    const cc = co ? (q: number[][], w: number, h: number, f: number) => co(q, w, h, f, m.turn ?? null) : cc0;
    for (const c of m.cards) {
      const px = new Uint8ClampedArray(Buffer.from(c.px, "base64"));
      if (cc) {
        const p = cc(c.quad, m.w, m.h, focal);
        cards.set(c.key, { px, deg: p?.deg ?? null, pos: p ? { tx: p.tx, ty: p.ty } : null });
      } else {
        cards.set(c.key, { px, deg: FC.viewAngle(c.quad, m.w, m.h, focal) });
      }
      keys.add(c.key);
    }
    const added = loop.frame({ t: m.t, mean: m.mean, cards, turn: m.turn ?? null });
    const st: Record<string, unknown> = {};
    for (const k of keys) { const c = cards.get(k); st[k] = [loop.tally.n(k), loop.tally.kind(k), loop.tally.settled(k), c?.deg ?? null, c?.pos ?? null]; }
    out = { added, cards: st, log: logs };
    logs = [];
  } else if (m.op === "result") {
    const r: Record<string, unknown> = {};
    for (const k of keys) r[k] = loop.tally.result(k);
    out = { cards: r };
  } else if (m.op === "reset") {
    loop.reset(); loop.tally.clear(); keys.clear(); loop.torchOn = false;
  }
  process.stdout.write(JSON.stringify(out) + "\n");
});
