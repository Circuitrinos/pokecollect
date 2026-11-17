// Test-Video (Debug): wie ist das Video zu drehen? Handys nehmen hochkant als liegende Bilder auf und
// schreiben die Drehung in die Datei (MP4: Matrix im „tkhd" der Videospur). Der Player beachtet sie,
// ein daraus abgegriffener Stream (captureStream) aber nicht — Brian 17.11.: das Testvideo lag quer.

export type VideoRotation = { angle: 0 | 90 | 180 | 270; w: number; h: number };   // im Uhrzeigersinn; gespeicherte Größe

const type4 = (v: DataView, o: number) => String.fromCharCode(v.getUint8(o), v.getUint8(o + 1), v.getUint8(o + 2), v.getUint8(o + 3));

/** Kästen (size, type) in v[from, to) durchgehen. */
function* boxes(v: DataView, from: number, to: number): Generator<{ type: string; start: number; end: number }> {
  let o = from;
  while (o + 8 <= to) {
    let len = v.getUint32(o);
    let hdr = 8;
    if (len === 1) { len = v.getUint32(o + 8) * 2 ** 32 + v.getUint32(o + 12); hdr = 16; }
    else if (len === 0) len = to - o;
    if (len < hdr || o + len > to) return;
    yield { type: type4(v, o + 4), start: o + hdr, end: o + len };
    o += len;
  }
}

/** Drehung aus dem moov-Kasten: die Spur mit Bildgröße (Video). */
export function rotationFromMoov(v: DataView): VideoRotation | null {
  for (const trak of Array.from(boxes(v, 0, v.byteLength))) {
    if (trak.type !== "trak") continue;
    for (const b of Array.from(boxes(v, trak.start, trak.end))) {
      if (b.type !== "tkhd") continue;
      const ver = v.getUint8(b.start);
      const m = b.start + 4 + (ver === 1 ? 32 : 20) + 8 + 8;   // Zeiten, Spur, Dauer; reserviert; Ebene, Gruppe, Lautstärke
      if (m + 44 > b.end) continue;
      const w = v.getUint32(m + 36) / 65536, h = v.getUint32(m + 40) / 65536;
      if (!w || !h) continue;   // Tonspur
      const a = v.getInt32(m) / 65536, bb = v.getInt32(m + 4) / 65536;
      const deg = ((Math.round((Math.atan2(bb, a) * 180) / Math.PI / 90) * 90) % 360 + 360) % 360;
      return { angle: deg as VideoRotation["angle"], w, h };
    }
  }
  return null;
}

/** Drehung einer MP4-Datei (liest nur die Kastenköpfe und den moov-Kasten); null, wenn unbekannt. */
export async function mp4Rotation(file: Blob): Promise<VideoRotation | null> {
  try {
    let off = 0;
    for (let guard = 0; guard < 64 && off + 8 <= file.size; guard++) {
      const h = new DataView(await file.slice(off, off + 16).arrayBuffer());
      let len = h.getUint32(0), hdr = 8;
      if (len === 1) { len = h.getUint32(8) * 2 ** 32 + h.getUint32(12); hdr = 16; }
      else if (len === 0) len = file.size - off;
      if (len < hdr) return null;
      if (type4(h, 4) === "moov") {
        if (len > 64 * 1024 * 1024) return null;
        return rotationFromMoov(new DataView(await file.slice(off + hdr, off + len).arrayBuffer()));
      }
      off += len;
    }
  } catch { /* keine MP4 */ }
  return null;
}
