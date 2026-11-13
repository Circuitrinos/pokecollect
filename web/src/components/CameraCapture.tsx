"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { X } from "lucide-react";
import { useI18n } from "@/lib/i18n";

// „Foto aufnehmen" auf der Kartenseite: Kamera im Browser (auch Webcam am PC, wo
// <input capture> nur die Dateiauswahl öffnet), mit Kartenrahmen. Aufnehmen liefert
// das volle Kamerabild und den Rahmen (normiert 0..1) als Startecken für den Eck-Editor.
const CARD = 63 / 88;
const KEY = "photo_camera";

export function cameraAvailable(): boolean {
  return typeof window !== "undefined" && window.isSecureContext && !!navigator.mediaDevices?.getUserMedia;
}

function guide(aspect: number): number[][] {
  let gh = 0.82;
  let gw = (gh * CARD) / aspect;
  if (gw > 0.9) { gw = 0.9; gh = (gw * aspect) / CARD; }
  const x0 = 0.5 - gw / 2, y0 = 0.5 - gh / 2;
  return [[x0, y0], [x0 + gw, y0], [x0 + gw, y0 + gh], [x0, y0 + gh]];
}

export default function CameraCapture({ onCapture, onClose }: {
  onCapture: (file: File, quad: number[][]) => void;
  onClose: () => void;
}) {
  const { t } = useI18n();
  const videoRef = useRef<HTMLVideoElement>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const [devices, setDevices] = useState<MediaDeviceInfo[]>([]);
  const [deviceId, setDeviceId] = useState<string>(() => {
    try { return localStorage.getItem(KEY) ?? ""; } catch { return ""; }
  });
  const [size, setSize] = useState<{ w: number; h: number } | null>(null);
  const [error, setError] = useState<string | null>(null);

  const stop = useCallback(() => {
    streamRef.current?.getTracks().forEach((tr) => tr.stop());
    streamRef.current = null;
  }, []);

  useEffect(() => {
    let alive = true;
    setError(null);
    setSize(null);
    const video: MediaTrackConstraints = { width: { ideal: 3840 }, height: { ideal: 2160 } };
    if (deviceId) video.deviceId = { exact: deviceId };
    else video.facingMode = { ideal: "environment" };
    navigator.mediaDevices.getUserMedia({ video, audio: false })
      .then(async (stream) => {
        if (!alive) { stream.getTracks().forEach((tr) => tr.stop()); return; }
        stop();
        streamRef.current = stream;
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

  const capture = () => {
    const v = videoRef.current;
    if (!v || !v.videoWidth) return;
    const c = document.createElement("canvas");
    c.width = v.videoWidth;
    c.height = v.videoHeight;
    c.getContext("2d")?.drawImage(v, 0, 0);
    const quad = guide(v.videoWidth / v.videoHeight);
    c.toBlob((blob) => {
      if (!blob) return;
      stop();
      onCapture(new File([blob], "photo.jpg", { type: "image/jpeg" }), quad);
    }, "image/jpeg", 0.92);
  };

  const g = size ? guide(size.w / size.h) : null;
  return (
    <div className="fixed inset-0 z-50 flex flex-col bg-black" role="dialog" aria-label={t.camera_title} data-testid="camera-capture">
      <div className="flex items-center gap-2 p-2 text-white">
        <span className="flex-1 text-sm font-medium">{t.camera_title}</span>
        {devices.length > 1 && (
          <select value={deviceId} aria-label={t.camera_choose} data-testid="camera-device"
            onChange={(e) => { setDeviceId(e.target.value); try { localStorage.setItem(KEY, e.target.value); } catch { /* egal */ } }}
            className="max-w-[55vw] truncate bg-gray-800 border border-gray-700 rounded px-1.5 py-1 text-xs">
            <option value="">{t.camera_default}</option>
            {devices.map((d, i) => <option key={d.deviceId || i} value={d.deviceId}>{d.label || `${t.camera_choose} ${i + 1}`}</option>)}
          </select>
        )}
        <button type="button" onClick={() => { stop(); onClose(); }} aria-label={t.form_cancel} className="p-1 text-gray-300 hover:text-white"><X size={20} /></button>
      </div>
      <div className="relative flex-1 min-h-0">
        <video ref={videoRef} playsInline muted autoPlay data-testid="camera-video"
          onLoadedMetadata={(e) => setSize({ w: e.currentTarget.videoWidth, h: e.currentTarget.videoHeight })}
          className="absolute inset-0 h-full w-full object-contain" />
        {g && size && (
          <svg viewBox={`0 0 ${size.w} ${size.h}`} preserveAspectRatio="xMidYMid meet" className="pointer-events-none absolute inset-0 h-full w-full">
            <rect x={g[0][0] * size.w} y={g[0][1] * size.h} width={(g[1][0] - g[0][0]) * size.w} height={(g[2][1] - g[1][1]) * size.h}
              rx={size.w * 0.012} fill="none" stroke="#facc15" strokeWidth={Math.max(2, size.w / 300)} strokeDasharray={`${size.w / 60} ${size.w / 90}`} />
          </svg>
        )}
        {error && (
          <div className="absolute inset-x-4 top-1/3 rounded bg-gray-900/90 p-3 text-sm text-red-300" data-testid="camera-error">
            {t.camera_error(error)}
          </div>
        )}
      </div>
      <div className="flex items-center justify-center gap-4 p-3 pb-6">
        <span className="text-xs text-gray-400">{t.camera_hint}</span>
        <button type="button" onClick={capture} disabled={!size || !!error} data-testid="camera-shoot"
          aria-label={t.camera_shoot}
          className="h-16 w-16 rounded-full border-4 border-white bg-white/20 hover:bg-white/40 disabled:opacity-40" />
      </div>
    </div>
  );
}
