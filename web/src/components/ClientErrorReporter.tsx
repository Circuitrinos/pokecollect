"use client";
import { useEffect } from "react";
import { api } from "@/lib/api";
import { APP_VERSION } from "@/lib/version";
import { debugFromUrl, dlog } from "@/lib/debugLog";

// Unbehandelte Fehler und abgelehnte Promises im Browser ans Server-Log melden
// (backend/app/api/v1/client_log.py → api-errors.log). Höchstens 20 je Seitenaufruf,
// gleiche nur einmal; Antworten der API zählen nicht (die protokolliert der Server).
const MAX_REPORTS = 20;
const IGNORE = [/ResizeObserver loop/i, /^Script error\.?$/i];
let sent = 0;
const seen = new Set<string>();

export function reportClientError(kind: string, error: unknown, fallback = "") {
  if (typeof window !== "undefined") {
    const e = error as { message?: string } | null;
    dlog("js-error", { kind, message: String(e?.message ?? error ?? fallback).slice(0, 300) });
  }
  if (typeof window === "undefined" || sent >= MAX_REPORTS || !localStorage.getItem("token")) return;
  const e = error as { message?: string; stack?: string; isAxiosError?: boolean } | null;
  if (e?.isAxiosError) return;
  const message = String(e?.message ?? (typeof error === "string" ? error : fallback) ?? "").slice(0, 2000) || "(ohne Text)";
  if (IGNORE.some((re) => re.test(message))) return;
  const key = `${kind}:${message}`;
  if (seen.has(key)) return;
  seen.add(key);
  sent += 1;
  api.post("/client-log", {
    kind, message, stack: e?.stack ? String(e.stack).slice(0, 8000) : null,
    url: (window.location.pathname + window.location.search).slice(0, 500), version: APP_VERSION,
  }).catch(() => {});
}

export default function ClientErrorReporter() {
  useEffect(() => {
    debugFromUrl();
    dlog("page", { path: window.location.pathname, version: APP_VERSION });
    const onError = (ev: ErrorEvent) => reportClientError("error", ev.error ?? ev.message, ev.message);
    const onRejection = (ev: PromiseRejectionEvent) => reportClientError("unhandledrejection", ev.reason);
    window.addEventListener("error", onError);
    window.addEventListener("unhandledrejection", onRejection);
    return () => {
      window.removeEventListener("error", onError);
      window.removeEventListener("unhandledrejection", onRejection);
    };
  }, []);
  return null;
}
