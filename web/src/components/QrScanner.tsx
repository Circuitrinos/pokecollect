"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import toast from "react-hot-toast";
import { Flashlight, X } from "lucide-react";
import { useI18n } from "@/lib/i18n";
import { lightSet } from "@/lib/cameraZoom";
import type { QrHit, QrResponse } from "@/lib/qrWorker";

// Hüllen-Aufkleber in PokéCollect selbst scannen (backend/app/services/sleeve_tags.py).
// Erkannt wird mit BarcodeDetector, wo der Browser ihn wirklich hat (Chrome auf Android
// bekommt ihn von den Google-Diensten — auf Brians /e/OS-Handy also womöglich nicht),
// sonst mit zxing-cpp als WebAssembly in einem Worker (lib/qrWorker.ts).
// Brian 24.11.: „the scanner is way slower than the default camera app" — darum:
// Bild für Bild ohne Pause (sobald der Leser fertig ist, kommt das nächste), und
// abwechselnd das Mittelquadrat in voller Auflösung (kleine Codes aus ~20 cm) und das
// ganze Bild verkleinert (Code muss nicht genau im Quadrat liegen). Bei mehreren Codes
// im Bild (Nachbar auf dem Bogen) gewinnt der zur Bildmitte nächste.
const KEY = "qr_camera";
const SQUARE = 0.62;            // Kantenlänge des Lesequadrats, Anteil der kürzeren Bildseite
const CROP_MAX = 900;           // Mittelquadrat höchstens so groß (px) an den Leser
const FULL_MAX = 1280;          // ganzes Bild: längere Seite höchstens so groß

/** Code aus dem Inhalt eines QR-Codes: Aufkleber-Link (…/T/K7M2) oder der Code allein. */
export function stickerCode(text: string): string | null {
  const s = text.trim();
  const m = s.match(/\/t\/([0-9a-z]{4})\/?$/i) ?? s.match(/^([0-9a-z]{4})$/i);
  return m ? m[1].toUpperCase() : null;
}

type Detector = {
  detect: (src: CanvasImageSource) => Promise<{ rawValue: string; boundingBox: DOMRectReadOnly }[]>;
};

async function makeDetector(): Promise<Detector | null> {
  const BD = (window as unknown as {
    BarcodeDetector?: { new (o: { formats: string[] }): Detector; getSupportedFormats?: () => Promise<string[]> };
  }).BarcodeDetector;
  if (!BD) return null;
  try {
    const formats = (await BD.getSupportedFormats?.()) ?? [];
    return formats.includes("qr_code") ? new BD({ formats: ["qr_code"] }) : null;
  } catch {
    return null;
  }
}

type Diag = { engine: string; fps: number };

export default function QrScanner({ onResult, onClose, title }: {
  onResult: (code: string) => void;
  onClose: () => void;
  title?: string;
}) {
  const { t } = useI18n();
  const videoRef = useRef<HTMLVideoElement>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const doneRef = useRef(false);
  const onResultRef = useRef(onResult);          // neuer Callback startet das Lesen nicht neu
  useEffect(() => { onResultRef.current = onResult; }, [onResult]);
  const [devices, setDevices] = useState<MediaDeviceInfo[]>([]);
  const [deviceId, setDeviceId] = useState<string>(() => {
    try { return localStorage.getItem(KEY) ?? ""; } catch { return ""; }
  });
  const [size, setSize] = useState<{ w: number; h: number } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [torch, setTorch] = useState<boolean | null>(null);   // null = Kamera hat keine Lampe
  const [diag, setDiag] = useState<Diag | null>(null);

  const stop = useCallback(() => {
    streamRef.current?.getTracks().forEach((tr) => tr.stop());
    streamRef.current = null;
  }, []);

  // Kamera
  useEffect(() => {
    let alive = true;
    setError(null);
    setSize(null);
    const video: MediaTrackConstraints = { width: { ideal: 1920 }, height: { ideal: 1080 } };
    if (deviceId) video.deviceId = { exact: deviceId };
    else video.facingMode = { ideal: "environment" };
    navigator.mediaDevices.getUserMedia({ video, audio: false })
      .then(async (stream) => {
        if (!alive) { stream.getTracks().forEach((tr) => tr.stop()); return; }
        stop();
        streamRef.current = stream;
        const track = stream.getVideoTracks()[0];
        await track?.applyConstraints({ advanced: [{ focusMode: "continuous" } as MediaTrackConstraintSet] }).catch(() => {});
        const caps = track?.getCapabilities?.() as { torch?: boolean } | undefined;
        setTorch(caps?.torch ? false : null);
        const v = videoRef.current;
        if (v) { v.srcObject = stream; await v.play().catch(() => {}); }
        const all = await navigator.mediaDevices.enumerateDevices().catch(() => []);
        if (alive) setDevices(all.filter((d) => d.kind === "videoinput"));
      })
      .catch((e: unknown) => {
        if (!alive) return;
        if (deviceId) { setDeviceId(""); return; }     // gemerkte Kamera weg → Standard
        setError(String((e as { message?: string })?.message ?? e));
      });
    return () => { alive = false; stop(); };
  }, [deviceId, stop]);

  // Lesen: ein Bild nach dem anderen, ohne Pause dazwischen
  useEffect(() => {
    if (!size) return;
    let alive = true;
    let detector: Detector | null | undefined;          // undefined = noch nicht geprüft
    let worker: Worker | null = null;
    let reqId = 0;
    const pending = new Map<number, (hits: QrHit[] | null) => void>();
    const canvas = document.createElement("canvas");
    const ctx = canvas.getContext("2d", { willReadFrequently: true });
    let frame = 0;
    let reads = 0;
    let since = performance.now();
    let engine = "";
    let lastForeign = "";

    const viaWorker = (image: ImageData, multi: boolean) => new Promise<QrHit[] | null>((resolve) => {
      if (!worker) {
        worker = new Worker(new URL("../lib/qrWorker.ts", import.meta.url), { type: "module" });
        worker.onmessage = (e: MessageEvent<QrResponse>) => {
          const cb = pending.get(e.data.id);
          pending.delete(e.data.id);
          cb?.("hits" in e.data ? e.data.hits : null);
        };
        worker.onerror = () => {
          pending.forEach((cb) => cb(null));
          pending.clear();
          if (alive) setError(t.qr_reader_failed);
          alive = false;
        };
      }
      const id = ++reqId;
      pending.set(id, resolve);
      worker.postMessage({ id, image, multi }, [image.data.buffer]);
    });

    const grab = (v: HTMLVideoElement, full: boolean): ImageData | null => {
      if (!ctx) return null;
      const W = v.videoWidth, H = v.videoHeight;
      if (full) {
        const k = Math.min(1, FULL_MAX / Math.max(W, H));
        canvas.width = Math.round(W * k);
        canvas.height = Math.round(H * k);
        ctx.drawImage(v, 0, 0, canvas.width, canvas.height);
      } else {
        const side = Math.round(Math.min(W, H) * SQUARE);
        const out = Math.min(side, CROP_MAX);
        canvas.width = canvas.height = out;
        ctx.drawImage(v, (W - side) / 2, (H - side) / 2, side, side, 0, 0, out, out);
      }
      return ctx.getImageData(0, 0, canvas.width, canvas.height);
    };

    // Aufkleber-Code nächst der Bildmitte; fremde QR-Codes einmal melden
    const pick = (hits: QrHit[]): string | null => {
      let best: string | null = null;
      let bestD = Infinity;
      for (const h of hits) {
        const code = stickerCode(h.text);
        if (!code) {
          if (h.text !== lastForeign) { lastForeign = h.text; toast.error(t.qr_not_tag); }
          continue;
        }
        const d = (h.cx - 0.5) ** 2 + (h.cy - 0.5) ** 2;
        if (d < bestD) { bestD = d; best = code; }
      }
      return best;
    };

    const next = (v: HTMLVideoElement) => {
      if (!alive || doneRef.current) return;
      const rvfc = (v as HTMLVideoElement & { requestVideoFrameCallback?: (cb: () => void) => number }).requestVideoFrameCallback;
      if (rvfc) rvfc.call(v, () => void step());
      else requestAnimationFrame(() => void step());
    };

    const step = async () => {
      const v = videoRef.current;
      if (!alive || doneRef.current || !v) return;
      if (v.videoWidth) {
        frame++;
        if (detector === undefined) {
          detector = await makeDetector();
          engine = detector ? "native" : "zxing";
        }
        let hits: QrHit[] | null = null;
        if (detector) {
          try {
            hits = (await detector.detect(v)).map((r) => ({
              text: r.rawValue,
              cx: (r.boundingBox.x + r.boundingBox.width / 2) / v.videoWidth,
              cy: (r.boundingBox.y + r.boundingBox.height / 2) / v.videoHeight,
            }));
          } catch {
            detector = null;                              // kaputt → ab jetzt zxing
            engine = "zxing";
          }
        }
        if (!detector) {
          const full = frame % 2 === 0;
          const image = grab(v, full);
          hits = image ? await viaWorker(image, full) : null;
        }
        reads++;
        const now = performance.now();
        if (now - since > 1000 && alive) {
          setDiag({ engine, fps: Math.round((reads * 1000) / (now - since)) });
          reads = 0;
          since = now;
        }
        const code = hits ? pick(hits) : null;
        if (code && alive && !doneRef.current) {
          doneRef.current = true;
          navigator.vibrate?.(40);
          stop();
          onResultRef.current(code);
          return;
        }
      }
      next(v);
    };

    if (videoRef.current) next(videoRef.current);
    return () => {
      alive = false;
      worker?.terminate();
      pending.forEach((cb) => cb(null));
      pending.clear();
    };
  }, [size, stop, t]);

  const toggleTorch = () => {
    const track = streamRef.current?.getVideoTracks()[0];
    if (!track || torch === null) return;
    lightSet(track, !torch, null).then(() => setTorch(!torch)).catch(() => setTorch(null));
  };

  // Antippen: dort scharfstellen (wo der Browser das kann), danach wieder Dauer-Fokus
  const focusAt = (e: React.MouseEvent<HTMLDivElement>) => {
    const track = streamRef.current?.getVideoTracks()[0];
    if (!track) return;
    const r = e.currentTarget.getBoundingClientRect();
    const x = (e.clientX - r.left) / r.width, y = (e.clientY - r.top) / r.height;
    track.applyConstraints({ advanced: [{ focusMode: "single-shot", pointsOfInterest: [{ x, y }] } as MediaTrackConstraintSet] })
      .then(() => setTimeout(() => {
        track.applyConstraints({ advanced: [{ focusMode: "continuous" } as MediaTrackConstraintSet] }).catch(() => {});
      }, 1500))
      .catch(() => {});
  };

  const sq = size ? Math.min(size.w, size.h) * SQUARE : 0;
  return (
    <div className="fixed inset-0 z-50 flex flex-col bg-black" role="dialog" aria-label={title ?? t.qr_title} data-testid="qr-scanner">
      <div className="flex items-center gap-2 p-2 text-white">
        <span className="flex-1 text-sm font-medium">{title ?? t.qr_title}</span>
        {torch !== null && (
          <button type="button" onClick={toggleTorch} aria-pressed={torch} title={t.qr_light}
            className={`p-1.5 rounded ${torch ? "bg-pokemon-yellow text-gray-900" : "text-gray-300 hover:text-white"}`}>
            <Flashlight size={18} />
          </button>
        )}
        {devices.length > 1 && (
          <select value={deviceId} aria-label={t.camera_choose}
            onChange={(e) => { setDeviceId(e.target.value); try { localStorage.setItem(KEY, e.target.value); } catch { /* egal */ } }}
            className="max-w-[45vw] truncate bg-gray-800 border border-gray-700 rounded px-1.5 py-1 text-xs">
            <option value="">{t.camera_default}</option>
            {devices.map((d, i) => <option key={d.deviceId || i} value={d.deviceId}>{d.label || `${t.camera_choose} ${i + 1}`}</option>)}
          </select>
        )}
        <button type="button" onClick={() => { stop(); onClose(); }} aria-label={t.form_cancel} className="p-1 text-gray-300 hover:text-white">
          <X size={20} />
        </button>
      </div>
      <div className="relative flex-1 min-h-0" onClick={focusAt}>
        <video ref={videoRef} playsInline muted autoPlay
          onLoadedMetadata={(e) => setSize({ w: e.currentTarget.videoWidth, h: e.currentTarget.videoHeight })}
          className="absolute inset-0 h-full w-full object-contain" />
        {size && (
          <svg viewBox={`0 0 ${size.w} ${size.h}`} preserveAspectRatio="xMidYMid meet" className="pointer-events-none absolute inset-0 h-full w-full">
            <defs>
              <mask id="qr-hole">
                <rect width={size.w} height={size.h} fill="white" />
                <rect x={(size.w - sq) / 2} y={(size.h - sq) / 2} width={sq} height={sq} rx={sq * 0.06} fill="black" />
              </mask>
            </defs>
            <rect width={size.w} height={size.h} fill="black" opacity={0.45} mask="url(#qr-hole)" />
            <rect x={(size.w - sq) / 2} y={(size.h - sq) / 2} width={sq} height={sq} rx={sq * 0.06}
              fill="none" stroke="#facc15" strokeWidth={Math.max(3, size.w / 250)} />
          </svg>
        )}
        {error && (
          <div className="absolute inset-x-4 top-1/3 rounded bg-gray-900/90 p-3 text-sm text-red-300">{t.camera_error(error)}</div>
        )}
      </div>
      <div className="p-3 pb-6 text-center space-y-1">
        <p className="text-xs text-gray-300">{t.qr_hint}</p>
        {/* Für die Fehlersuche: welcher Leser, welche Kamera-Auflösung, wie viele Bilder je Sekunde */}
        {size && diag && (
          <p className="text-[10px] text-gray-600 font-mono" data-testid="qr-diag">
            {diag.engine} · {size.w}×{size.h} · {diag.fps}/s
          </p>
        )}
      </div>
    </div>
  );
}
