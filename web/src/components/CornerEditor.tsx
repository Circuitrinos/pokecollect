"use client";
import { useEffect, useRef, useState } from "react";
import { clamp, CropTransform } from "@/lib/cardCrop";
import { useI18n } from "@/lib/i18n";

/**
 * Manuelle Eck-Korrektur eines Kartenfotos.
 * - 4 ziehbare Punkte (TL,TR,BR,BL, normiert 0..1) + Lupe beim Ziehen
 * - Kanten ziehen (Griff in der Kantenmitte; die Kante gleitet an ihren
 *   Nachbarkanten entlang, die Perspektive bleibt) und den ganzen Rahmen
 *   verschieben (innen greifen oder ✥ in der Mitte)
 * - Zoom & Verschieben per Fingergesten (Pinch/1-Finger-Drag) und am Desktop
 *   per Mausrad / Linksklick-halten
 * - Flip/Rotate als manuelle Ausrichtungs-Korrektur
 * Wird im Scan-Review und auf der Detailseite verwendet.
 */
export type CornerSuggestion = { quad: number[][]; ok: boolean; label: string };

// Was gerade gezogen wird: ein Eckpunkt, eine Kante (zwischen Ecke i und i+1) oder der ganze Rahmen
type Drag =
  | { kind: "corner"; i: number; start: number[][] }
  | { kind: "edge"; i: number; start: number[][]; from: number[] }
  | { kind: "move"; start: number[][]; from: number[] };

/** Schnittpunkt der Geraden p+s·u und r+t·v (null, wenn fast parallel). */
function intersect(p: number[], u: number[], r: number[], v: number[]): number[] | null {
  const den = u[0] * v[1] - u[1] * v[0];
  if (Math.abs(den) < 1e-9 * Math.hypot(...u) * Math.hypot(...v)) return null;
  const s = ((r[0] - p[0]) * v[1] - (r[1] - p[1]) * v[0]) / den;
  return [p[0] + s * u[0], p[1] + s * u[1]];
}

/**
 * Kante i um `delta` (Bild-Pixel) verschieben: die Kante wandert senkrecht zu sich
 * selbst, ihre Endpunkte gleiten auf den Nachbarkanten (die Perspektive bleibt).
 * Rechnet in Pixeln (w×h), Ein-/Ausgabe normiert.
 */
export function moveEdge(start: number[][], i: number, delta: number[], w: number, h: number): number[][] {
  const px = start.map(([x, y]) => [x * w, y * h]);
  const A = px[i], B = px[(i + 1) % 4], P = px[(i + 3) % 4], Q = px[(i + 2) % 4];
  const d = [B[0] - A[0], B[1] - A[1]];
  const len = Math.hypot(d[0], d[1]) || 1;
  const n = [-d[1] / len, d[0] / len];
  const t = delta[0] * n[0] + delta[1] * n[1];
  const A0 = [A[0] + t * n[0], A[1] + t * n[1]];
  const B0 = [B[0] + t * n[0], B[1] + t * n[1]];
  const reach = 3 * Math.max(w, h);
  const slide = (fallback: number[], from: number[], to: number[]) => {
    const hit = intersect(A0, d, from, [to[0] - from[0], to[1] - from[1]]);
    return hit && Math.hypot(hit[0] - fallback[0], hit[1] - fallback[1]) < reach ? hit : fallback;
  };
  const out = px.map((p) => [...p]);
  out[i] = slide(A0, P, A);
  out[(i + 1) % 4] = slide(B0, Q, B);
  return out.map(([x, y]) => [clamp(x / w, 0, 1), clamp(y / h, 0, 1)]);
}

/** Ganzen Rahmen um (dx, dy) verschieben, ohne das Bild zu verlassen. */
export function moveQuad(start: number[][], dx: number, dy: number): number[][] {
  const xs = start.map((p) => p[0]), ys = start.map((p) => p[1]);
  const mx = clamp(dx, -Math.min(...xs), 1 - Math.max(...xs));
  const my = clamp(dy, -Math.min(...ys), 1 - Math.max(...ys));
  return start.map(([x, y]) => [x + mx, y + my]);
}

export default function CornerEditor({
  imageUrl, initialQuad, onCancel, onApply, title, hint, orientable = true, extra, suggest, suggestOnOpen = false,
  suggestTexts,
}: {
  imageUrl: string;
  initialQuad: number[][];
  onCancel: () => void;
  onApply: (quad: number[][], transform: CropTransform) => void;
  // Andere Verwendungen (z. B. Play!-Stempel markieren): eigener Titel/Hinweis,
  // ohne Drehen/Spiegeln, zusätzliche Knöpfe links unten
  title?: string;
  hint?: string;
  orientable?: boolean;
  extra?: React.ReactNode;
  // Nach jedem losgelassenen Eckpunkt: eingeschwungene Ecken vorschlagen (Fix crop
  // mit Bild-Abgleich). null = Karte in diesen Ecken nicht erkannt.
  suggest?: (quad: number[][], transform: CropTransform) => Promise<CornerSuggestion | null>;
  // gleich beim Öffnen einen Vorschlag holen (z. B. Bild aus der Websuche zuschneiden)
  suggestOnOpen?: boolean;
  // eigene Texte für den Vorschlag (Standard: Kartenkanten)
  suggestTexts?: { hint?: string; busy?: string; none?: string };
}) {
  const { t } = useI18n();
  const [quad, setQuadState] = useState<number[][]>(initialQuad);
  const quadRef = useRef<number[][]>(initialQuad);
  const setQuad = (q: number[][]) => { quadRef.current = q; setQuadState(q); };
  const [sugg, setSugg] = useState<{ state: "busy" } | { state: "none" } | ({ state: "done" } & CornerSuggestion) | null>(null);
  const suggestRef = useRef(suggest);
  useEffect(() => { suggestRef.current = suggest; }, [suggest]);
  const seqRef = useRef(0);
  const [zoom, setZoom] = useState(1);
  const [pan, setPan] = useState<{ x: number; y: number }>({ x: 0, y: 0 });
  const [active, setActive] = useState<Drag | null>(null);
  const [aspect, setAspect] = useState(88 / 63);
  const [flipH, setFlipH] = useState(false);
  const [flipV, setFlipV] = useState(false);
  const [rotate, setRotate] = useState(0);
  const [imgSize, setImgSize] = useState({ w: 0, h: 0 });

  const vpRef = useRef<HTMLDivElement | null>(null);
  const imgRef = useRef<HTMLImageElement | null>(null);
  const zoomRef = useRef(1);
  const panRef = useRef({ x: 0, y: 0 });
  const activeRef = useRef<Drag | null>(null);
  const dragPtrRef = useRef<{ id: number; x: number; y: number } | null>(null);
  const pointersRef = useRef<Map<number, { x: number; y: number }>>(new Map());
  const pinchRef = useRef<{ dist: number; mid: { x: number; y: number } } | null>(null);
  // Refs für die Zeiger-Zuordnung im globalen Listener (keine veralteten Closures)
  const rotateRef = useRef(0);
  const flipHRef = useRef(false);
  const flipVRef = useRef(false);
  const imgSizeRef = useRef({ w: 0, h: 0 });

  const setView = (z: number, p: { x: number; y: number }) => {
    zoomRef.current = z; panRef.current = p;
    setZoom(z); setPan(p);
  };
  const doRotate = () => { const r = (rotateRef.current + 90) % 360; rotateRef.current = r; setRotate(r); };
  const doFlipH = () => { flipHRef.current = !flipHRef.current; setFlipH(flipHRef.current); };
  const doFlipV = () => { flipVRef.current = !flipVRef.current; setFlipV(flipVRef.current); };
  const resetOrient = () => {
    rotateRef.current = 0; flipHRef.current = false; flipVRef.current = false;
    setRotate(0); setFlipH(false); setFlipV(false);
  };
  // Vorschlag holen; ältere Antworten werden verworfen, wenn inzwischen neu gezogen wurde
  const runSuggest = () => {
    const fn = suggestRef.current;
    if (!fn) return;
    const seq = ++seqRef.current;
    setSugg({ state: "busy" });
    fn(quadRef.current, { flipH: flipHRef.current, flipV: flipVRef.current, rotate: rotateRef.current })
      .then((r) => { if (seq === seqRef.current) setSugg(r ? { state: "done", ...r } : { state: "none" }); })
      .catch(() => { if (seq === seqRef.current) setSugg({ state: "none" }); });
  };
  const runSuggestRef = useRef(runSuggest);
  runSuggestRef.current = runSuggest;
  useEffect(() => {
    if (suggestOnOpen) runSuggestRef.current();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  const applySuggestion = () => {
    if (sugg?.state !== "done") return;
    setQuad(sugg.quad);
    resetOrient();          // vorgeschlagene Ecken sind schon richtig herum
    seqRef.current++;
    setSugg(null);
  };

  // CSS-Transform der Bild-/Overlay-Ebene: Pan/Zoom + Drehen/Spiegeln um die
  // Bildmitte (transform-origin 0 0). Identisch zur Matrix in der Zeiger-Zuordnung.
  const layerTransform = (() => {
    const cx = imgSize.w / 2, cy = imgSize.h / 2;
    const fx = flipH ? -1 : 1, fy = flipV ? -1 : 1;
    return `translate(${pan.x}px, ${pan.y}px) scale(${zoom}) translate(${cx}px, ${cy}px) rotate(${rotate}deg) scale(${fx}, ${fy}) translate(${-cx}px, ${-cy}px)`;
  })();

  // Bild mittig in den Viewport setzen (Ausgangszustand).
  const centerView = () => {
    const vp = vpRef.current, im = imgRef.current;
    if (!vp || !im) return;
    setView(1, {
      x: (vp.clientWidth - im.offsetWidth) / 2,
      y: (vp.clientHeight - im.offsetHeight) / 2,
    });
  };

  const zoomAround = (px: number, py: number, factor: number) => {
    const z0 = zoomRef.current;
    const z = clamp(z0 * factor, 1, 8);
    const f = z / z0;
    const p0 = panRef.current;
    setView(z, { x: px - f * (px - p0.x), y: py - f * (py - p0.y) });
  };

  const vpCenterZoom = (factor: number) => {
    const vp = vpRef.current;
    if (!vp) return;
    zoomAround(vp.clientWidth / 2, vp.clientHeight / 2, factor);
  };

  useEffect(() => {
    const dist2 = (a: { x: number; y: number }, b: { x: number; y: number }) =>
      Math.hypot(a.x - b.x, a.y - b.y);

    const move = (e: PointerEvent) => {
      // 1) Ecke / Kante / Rahmen ziehen → Viewport-Punkt über die inverse
      //    Transform-Matrix in Bild-Pixel zurückrechnen (auch bei Drehung/Spiegelung).
      const drag = activeRef.current;
      if (drag != null) {
        const { w, h } = imgSizeRef.current;
        if (w === 0 || h === 0) return;
        if (dragPtrRef.current?.id === e.pointerId) dragPtrRef.current = { id: e.pointerId, x: e.clientX, y: e.clientY };
        const [lx, ly] = toLocalRef.current(e.clientX, e.clientY);
        if (drag.kind === "corner") {
          const p = [clamp(lx, 0, 1), clamp(ly, 0, 1)];
          setQuad(quadRef.current.map((q, k) => (k === drag.i ? p : q)));
        } else if (drag.kind === "edge") {
          setQuad(moveEdge(drag.start, drag.i, [(lx - drag.from[0]) * w, (ly - drag.from[1]) * h], w, h));
        } else {
          setQuad(moveQuad(drag.start, lx - drag.from[0], ly - drag.from[1]));
        }
        return;
      }
      // 2) Pan / Pinch
      const ptrs = pointersRef.current;
      if (!ptrs.has(e.pointerId)) return;
      const prev = ptrs.get(e.pointerId)!;
      const cur = { x: e.clientX, y: e.clientY };
      ptrs.set(e.pointerId, cur);
      if (ptrs.size >= 2) {
        const [a, b] = Array.from(ptrs.values());
        const off = vpRef.current?.getBoundingClientRect();
        const offLeft = off?.left ?? 0, offTop = off?.top ?? 0;
        const mid = { x: (a.x + b.x) / 2 - offLeft, y: (a.y + b.y) / 2 - offTop };
        const d = dist2(a, b);
        if (pinchRef.current) {
          const factor = d / (pinchRef.current.dist || d);
          zoomAround(mid.x, mid.y, factor);
          const dmx = mid.x - pinchRef.current.mid.x;
          const dmy = mid.y - pinchRef.current.mid.y;
          setView(zoomRef.current, { x: panRef.current.x + dmx, y: panRef.current.y + dmy });
        }
        pinchRef.current = { dist: d, mid };
      } else {
        const dx = cur.x - prev.x, dy = cur.y - prev.y;
        setView(zoomRef.current, { x: panRef.current.x + dx, y: panRef.current.y + dy });
      }
    };
    const up = (e: PointerEvent) => {
      pointersRef.current.delete(e.pointerId);
      if (pointersRef.current.size < 2) pinchRef.current = null;
      const drag = activeRef.current;
      activeRef.current = null;
      dragPtrRef.current = null;
      setActive(null);
      // nur nach einer echten Änderung neu vorschlagen (nicht bei bloßem Antippen)
      if (drag && JSON.stringify(drag.start) !== JSON.stringify(quadRef.current)) runSuggestRef.current();
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
    window.addEventListener("pointercancel", up);
    return () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      window.removeEventListener("pointercancel", up);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Viewport-Punkt (clientX/Y) → normierte Bildkoordinaten (ungeklemmt)
  const toLocal = (clientX: number, clientY: number): number[] => {
    const { w, h } = imgSizeRef.current;
    const off = vpRef.current?.getBoundingClientRect();
    const cx = w / 2, cy = h / 2;
    const fx = flipHRef.current ? -1 : 1, fy = flipVRef.current ? -1 : 1;
    const M = new DOMMatrix()
      .translate(panRef.current.x, panRef.current.y)
      .scale(zoomRef.current)
      .translate(cx, cy)
      .rotate(rotateRef.current)
      .scale(fx, fy)
      .translate(-cx, -cy);
    const local = M.inverse().transformPoint(new DOMPoint(clientX - (off?.left ?? 0), clientY - (off?.top ?? 0)));
    return [local.x / (w || 1), local.y / (h || 1)];
  };
  const toLocalRef = useRef(toLocal);
  toLocalRef.current = toLocal;

  const beginDrag = (e: React.PointerEvent, kind: "corner" | "edge" | "move", i = 0) => {
    e.stopPropagation();
    if (activeRef.current) {
      // zweiter Finger während eines Zugs: das wird ein Pinch-Zoom, kein zweiter Zug
      const d = dragPtrRef.current;
      activeRef.current = null;
      dragPtrRef.current = null;
      setActive(null);
      if (d) pointersRef.current.set(d.id, { x: d.x, y: d.y });
      startPanPointer(e);
      return;
    }
    e.preventDefault();
    const start = quadRef.current.map((p) => [...p]);
    const from = toLocal(e.clientX, e.clientY);
    const drag: Drag = kind === "corner" ? { kind, i, start } : kind === "edge" ? { kind, i, start, from } : { kind, start, from };
    activeRef.current = drag;
    dragPtrRef.current = { id: e.pointerId, x: e.clientX, y: e.clientY };
    setActive(drag);
  };
  const startPanPointer = (e: React.PointerEvent) => {
    pointersRef.current.set(e.pointerId, { x: e.clientX, y: e.clientY });
    if (pointersRef.current.size === 2) {
      const [a, b] = Array.from(pointersRef.current.values());
      const off = vpRef.current?.getBoundingClientRect();
      const offLeft = off?.left ?? 0, offTop = off?.top ?? 0;
      pinchRef.current = {
        dist: Math.hypot(a.x - b.x, a.y - b.y),
        mid: { x: (a.x + b.x) / 2 - offLeft, y: (a.y + b.y) / 2 - offTop },
      };
    }
  };

  // Lupe (oben, gegenüber dem aktiven Punkt; direkt aus dem Originalbild).
  // FRAC = Anteil der Bildbreite, den die Lupe bei Zoom 1 zeigt. AN DEN VIEWPORT-
  // ZOOM GEKOPPELT (× zoom): beim Reinzoomen (Mehr-Karten-Fotos) zieht die Lupe
  // mit und löst stets feiner auf als die Ansicht, statt fix auf dem ganzen Foto
  // zu bleiben (#36). Kleineres FRAC = stärker vergrößert.
  const LOUPE = 124, FRAC = 0.12;
  // Lupe: an der gezogenen Ecke bzw. Kantenmitte (beim Verschieben keine)
  const ap = active?.kind === "corner" ? quad[active.i]
    : active?.kind === "edge" ? [(quad[active.i][0] + quad[(active.i + 1) % 4][0]) / 2, (quad[active.i][1] + quad[(active.i + 1) % 4][1]) / 2]
    : null;
  const centroid = [quad.reduce((a, p) => a + p[0], 0) / 4, quad.reduce((a, p) => a + p[1], 0) / 4];
  const loupeOnRight = ap ? ap[0] < 0.5 : true;
  const bgW = (LOUPE / FRAC) * zoom;
  const bgH = bgW * aspect;

  return (
    <div
      className="fixed inset-0 z-[70] bg-black/85 flex items-center justify-center p-2 sm:p-4"
      onClick={(e) => { if (e.target === e.currentTarget) onCancel(); }}
    >
      <div className="bg-pokemon-card border border-gray-700 rounded-lg p-3 sm:p-4 w-full max-w-lg max-h-[95vh] flex flex-col">
        <h3 className="text-white text-sm font-medium mb-1">{title ?? t.scan_corners_title}</h3>
        <p className="text-gray-400 text-xs mb-2">{hint ?? t.scan_corners_hint}</p>

        <div
          ref={vpRef}
          className="relative mx-auto overflow-hidden rounded select-none touch-none bg-black w-full"
          style={{ height: "56vh" }}
          onPointerDown={startPanPointer}
          onWheel={(e) => {
            const vp = vpRef.current;
            const off = vp ? vp.getBoundingClientRect() : null;
            if (off) zoomAround(e.clientX - off.left, e.clientY - off.top, e.deltaY < 0 ? 1.12 : 0.89);
          }}
        >
          <div
            className="absolute top-0 left-0"
            style={{ transform: layerTransform, transformOrigin: "0 0" }}
          >
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img
              ref={imgRef}
              src={imageUrl}
              alt=""
              draggable={false}
              className="block"
              style={{ maxHeight: "56vh", maxWidth: "100%" }}
              onLoad={(e) => {
                const im = e.currentTarget;
                if (im.naturalWidth > 0) setAspect(im.naturalHeight / im.naturalWidth);
                const size = { w: im.offsetWidth, h: im.offsetHeight };
                imgSizeRef.current = size;
                setImgSize(size);
                centerView();
              }}
            />
            <svg
              className="absolute inset-0 w-full h-full pointer-events-none"
              viewBox="0 0 1 1" preserveAspectRatio="none"
            >
              <polygon
                points={quad.map(([x, y]) => `${x},${y}`).join(" ")}
                fill="rgba(250,204,21,0.12)" stroke="#facc15" strokeWidth={2}
                vectorEffect="non-scaling-stroke"
                // innen greifen = ganzen Rahmen verschieben (außerhalb: Ansicht verschieben)
                pointerEvents="all" className="cursor-move" data-testid="corner-frame"
                onPointerDown={(e) => beginDrag(e, "move")}
              />
              {sugg?.state === "done" && (
                <polygon
                  points={sugg.quad.map(([x, y]) => `${x},${y}`).join(" ")}
                  fill="none" stroke={sugg.ok ? "#22c55e" : "#f59e0b"} strokeWidth={2} strokeDasharray="6 4"
                  vectorEffect="non-scaling-stroke" data-testid="corner-suggestion"
                />
              )}
            </svg>
            {/* Kantengriffe in der Kantenmitte, entlang der Kante ausgerichtet */}
            {quad.map((a, i) => {
              const b = quad[(i + 1) % 4];
              const deg = (Math.atan2((b[1] - a[1]) * imgSize.h, (b[0] - a[0]) * imgSize.w) * 180) / Math.PI;
              const on = active?.kind === "edge" && active.i === i;
              return (
                <button type="button" key={`e${i}`} aria-label={t.corner_edge(i + 1)} data-testid={`corner-edge-${i}`}
                  onPointerDown={(e) => beginDrag(e, "edge", i)}
                  className="absolute flex h-5 w-9 items-center justify-center touch-none cursor-grab"
                  style={{
                    left: `${((a[0] + b[0]) / 2) * 100}%`, top: `${((a[1] + b[1]) / 2) * 100}%`,
                    transform: `translate(-50%, -50%) rotate(${deg}deg) scale(${1 / zoom})`,
                  }}>
                  <span className={`block h-2 w-7 rounded-full border ${on ? "bg-pokemon-blue border-white" : "bg-pokemon-yellow/90 border-black"}`} />
                </button>
              );
            })}
            {/* Ganzen Rahmen verschieben */}
            <button type="button" aria-label={t.corner_move} data-testid="corner-move"
              onPointerDown={(e) => beginDrag(e, "move")}
              className={`absolute flex h-7 w-7 items-center justify-center rounded-full border-2 text-sm touch-none cursor-move ${
                active?.kind === "move" ? "bg-pokemon-blue text-white border-white" : "bg-black/60 text-pokemon-yellow border-pokemon-yellow/80"}`}
              style={{
                left: `${centroid[0] * 100}%`, top: `${centroid[1] * 100}%`,
                transform: `translate(-50%, -50%) scale(${1 / zoom}) scale(${flipH ? -1 : 1}, ${flipV ? -1 : 1}) rotate(${-rotate}deg)`,
              }}>✥</button>
            {quad.map(([x, y], i) => (
              <button type="button"
                key={i}
                onPointerDown={(e) => beginDrag(e, "corner", i)}
                className={`absolute w-7 h-7 rounded-full border-2 text-[10px] font-bold flex items-center justify-center touch-none ${
                  active?.kind === "corner" && active.i === i ? "bg-pokemon-blue text-white border-white" : "bg-pokemon-yellow/90 text-black border-black"
                }`}
                style={{
                  left: `${x * 100}%`, top: `${y * 100}%`,
                  // Gegen Zoom/Drehen/Spiegeln gegensteuern → Marker bleibt rund,
                  // konstant groß und die Ziffer aufrecht/lesbar.
                  transform: `translate(-50%, -50%) scale(${1 / zoom}) scale(${flipH ? -1 : 1}, ${flipV ? -1 : 1}) rotate(${-rotate}deg)`,
                }}
              >
                {i + 1}
              </button>
            ))}
          </div>

          {ap && (
            <div
              className="absolute pointer-events-none rounded-full border-2 border-white shadow-lg overflow-hidden"
              style={{
                width: LOUPE, height: LOUPE, top: 8,
                left: loupeOnRight ? undefined : 8,
                right: loupeOnRight ? 8 : undefined,
                backgroundImage: `url(${imageUrl})`,
                backgroundRepeat: "no-repeat",
                backgroundSize: `${bgW}px ${bgH}px`,
                backgroundPosition: `${LOUPE / 2 - ap[0] * bgW}px ${LOUPE / 2 - ap[1] * bgH}px`,
              }}
            >
              <div className="absolute left-1/2 top-0 h-full w-px bg-pokemon-yellow/70" />
              <div className="absolute top-1/2 left-0 w-full h-px bg-pokemon-yellow/70" />
            </div>
          )}
        </div>

        {/* fester Platz: Hinweis → Vorschlag springt nicht, der (zentrierte) Dialog bleibt stehen */}
        {suggest && (
          <div className="mt-2 min-h-[2.25rem]">
            {sugg && (
              <div className={`flex flex-wrap items-center gap-2 rounded px-2 py-1.5 text-xs ${
                sugg.state === "done" ? (sugg.ok ? "bg-green-950/60 text-green-200" : "bg-amber-950/60 text-amber-200") : "bg-gray-800 text-gray-300"}`}
                data-testid="corner-suggest" data-state={sugg.state === "done" ? (sugg.ok ? "pass" : "weak") : sugg.state}>
                <span className="flex-1 min-w-0">
                  {sugg.state === "busy" ? (suggestTexts?.busy ?? t.corner_suggest_busy)
                    : sugg.state === "none" ? (suggestTexts?.none ?? t.corner_suggest_none)
                    : sugg.ok ? `✓ ${t.corner_suggest_pass(sugg.label)}` : t.corner_suggest_weak(sugg.label)}
                </span>
                {sugg.state === "done" && (
                  <button type="button" onClick={applySuggestion} data-testid="corner-suggest-use"
                    className={`rounded px-2 py-1 text-white ${sugg.ok ? "bg-green-700 hover:bg-green-600" : "bg-amber-700 hover:bg-amber-600"}`}>
                    {t.corner_suggest_use}
                  </button>
                )}
              </div>
            )}
            {!sugg && <p className="text-[11px] text-gray-500">{suggestTexts?.hint ?? t.corner_suggest_hint}</p>}
          </div>
        )}

        {/* Zoom + Ausrichtung */}
        <div className="flex items-center justify-center gap-1.5 mt-2 flex-wrap text-sm">
          <button type="button" onClick={() => vpCenterZoom(0.8)} disabled={zoom <= 1}
            className="w-9 h-9 rounded bg-gray-700 text-white hover:bg-gray-600 disabled:opacity-40">−</button>
          <span className="text-gray-300 text-xs w-11 text-center">{Math.round(zoom * 100)}%</span>
          <button type="button" onClick={() => vpCenterZoom(1.25)} disabled={zoom >= 8}
            className="w-9 h-9 rounded bg-gray-700 text-white hover:bg-gray-600 disabled:opacity-40">+</button>
          <span className="w-2" />
          {orientable && (
            <>
              <button type="button" onClick={doRotate} title={t.scan_corners_rotate}
                className="px-2 h-9 rounded bg-gray-700 text-white text-xs hover:bg-gray-600">⟳ 90°</button>
              <button type="button" onClick={doFlipH} title={t.scan_corners_fliph}
                className={`w-9 h-9 rounded text-white ${flipH ? "bg-pokemon-blue" : "bg-gray-700 hover:bg-gray-600"}`}>⇋</button>
              <button type="button" onClick={doFlipV} title={t.scan_corners_flipv}
                className={`w-9 h-9 rounded text-white ${flipV ? "bg-pokemon-blue" : "bg-gray-700 hover:bg-gray-600"}`}>⇅</button>
            </>
          )}
          <button type="button" onClick={centerView} disabled={zoom === 1 && pan.x >= 0}
            className="px-2 h-9 rounded bg-gray-700 text-white text-xs hover:bg-gray-600 disabled:opacity-40">
            {t.scan_corners_reset_view}
          </button>
        </div>

        <div className="flex gap-2 justify-end mt-3 flex-wrap">
          {extra && <div className="mr-auto flex items-center gap-2">{extra}</div>}
          <button type="button" onClick={() => { setQuad(initialQuad); resetOrient(); centerView(); seqRef.current++; setSugg(null); }}
            className="text-gray-400 hover:text-white text-sm px-3 py-1.5">
            {t.scan_corners_reset}
          </button>
          <button type="button" onClick={onCancel} className="bg-gray-700 text-white text-sm px-3 py-1.5 rounded hover:bg-gray-600">
            {t.scan_corners_cancel}
          </button>
          <button type="button" onClick={() => onApply(quad, { flipH, flipV, rotate })}
            className="bg-green-600 text-white text-sm px-4 py-1.5 rounded hover:bg-green-700">
            {t.scan_corners_apply}
          </button>
        </div>
      </div>
    </div>
  );
}
