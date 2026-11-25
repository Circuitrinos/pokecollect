"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import toast from "react-hot-toast";
import { Flashlight, X } from "lucide-react";
import { useI18n } from "@/lib/i18n";
import { lightSet } from "@/lib/cameraZoom";

// Hüllen-Aufkleber in PokéCollect selbst scannen (backend/app/services/sleeve_tags.py).
// Erkannt wird mit BarcodeDetector, wo der Browser ihn hat — in Chrome auf Android
// hängt er an den Google-Diensten, auf Brians /e/OS-Handy also womöglich nicht —,
// sonst mit jsQR (reines JavaScript, wird erst dann nachgeladen).
// Gelesen wird nur das Quadrat in der Bildmitte: schneller, und ein Aufkleber daneben
// (der Nachbar auf dem Bogen, die nächste Hülle) wird nicht versehentlich erkannt.
const KEY = "qr_camera";
const SQUARE = 0.62;            // Kantenlänge des Lesequadrats, Anteil der kürzeren Bildseite
const MAX_PX = 720;             // größer bringt beim 0,38-mm-Modul nichts, kostet nur Zeit
const EVERY_MS = 120;

/** Code aus dem Inhalt eines QR-Codes: Aufkleber-Link (…/T/K7M2) oder der Code allein. */
export function stickerCode(text: string): string | null {
  const s = text.trim();
  const m = s.match(/\/t\/([0-9a-z]{4})\/?$/i) ?? s.match(/^([0-9a-z]{4})$/i);
  return m ? m[1].toUpperCase() : null;
}

type Detector = { detect: (src: CanvasImageSource) => Promise<{ rawValue: string }[]> };
type JsQR = (data: Uint8ClampedArray, w: number, h: number, o?: { inversionAttempts?: string }) => { data: string } | null;

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
        // Nahfokus: Aufkleber werden auf ~10 cm gehalten
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

  // Lesen: alle EVERY_MS das Mittelquadrat, nie zwei Durchläufe gleichzeitig
  useEffect(() => {
    if (!size) return;
    let alive = true;
    let timer: ReturnType<typeof setTimeout>;
    let detector: Detector | null | undefined;          // undefined = noch nicht geprüft
    let jsqr: JsQR | null = null;
    let lastForeign = "";
    const canvas = document.createElement("canvas");
    const ctx = canvas.getContext("2d", { willReadFrequently: true });

    const tick = async () => {
      const v = videoRef.current;
      if (!alive || doneRef.current) return;
      if (v && ctx && v.videoWidth) {
        const side = Math.round(Math.min(v.videoWidth, v.videoHeight) * SQUARE);
        const out = Math.min(side, MAX_PX);
        canvas.width = canvas.height = out;
        ctx.drawImage(v, (v.videoWidth - side) / 2, (v.videoHeight - side) / 2, side, side, 0, 0, out, out);
        let text: string | null = null;
        if (detector === undefined) detector = await makeDetector();
        if (detector) {
          try {
            text = (await detector.detect(canvas))[0]?.rawValue ?? null;
          } catch {
            detector = null;                              // kaputt → ab jetzt jsQR
          }
        }
        if (!detector) {
          if (!jsqr) jsqr = (await import("jsqr")).default as unknown as JsQR;
          const img = ctx.getImageData(0, 0, out, out);
          text = jsqr(img.data, out, out, { inversionAttempts: "dontInvert" })?.data ?? null;
        }
        if (text && alive && !doneRef.current) {
          const code = stickerCode(text);
          if (code) {
            doneRef.current = true;
            navigator.vibrate?.(40);
            stop();
            onResultRef.current(code);
            return;
          }
          if (text !== lastForeign) { lastForeign = text; toast.error(t.qr_not_tag); }
        }
      }
      if (alive) timer = setTimeout(tick, EVERY_MS);
    };
    timer = setTimeout(tick, EVERY_MS);
    return () => { alive = false; clearTimeout(timer); };
  }, [size, stop, t]);

  const toggleTorch = () => {
    const track = streamRef.current?.getVideoTracks()[0];
    if (!track || torch === null) return;
    lightSet(track, !torch, null).then(() => setTorch(!torch)).catch(() => setTorch(null));
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
      <div className="relative flex-1 min-h-0">
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
      <p className="p-3 pb-6 text-center text-xs text-gray-300">{t.qr_hint}</p>
    </div>
  );
}
