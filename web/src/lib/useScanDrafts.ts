"use client";
// Offene Scan-Entwürfe (Prüfseite gespeichert, noch nicht fertig bestätigt):
// eine geteilte Liste für Menü-Zähler, die Seite „Offene Scans" und die
// Scan-Seite. Die Navbar hält sie aktuell (Seitenwechsel, Tab wieder vorne).
import { useEffect } from "react";
import { usePathname } from "next/navigation";
import { ScanDraftSummary, scanDraftApi } from "@/lib/api";
import { createCachedResource } from "@/lib/apiCache";

// Gerade gelöscht: ein Abruf, der das Löschen überholt, zeigt sie nicht wieder an
const deleting = new Set<number>();

const resource = createCachedResource<ScanDraftSummary[]>(() => {
  const token = typeof window !== "undefined" ? localStorage.getItem("token") : null;
  if (!token) return Promise.resolve([]);
  return scanDraftApi.list().then((r) => r.data.filter((d) => !deleting.has(d.id)));
});

export function useScanDrafts(): ScanDraftSummary[] | null {
  return resource.useResource().data;
}

export function refreshScanDrafts(): void {
  resource.refresh().catch(() => {});
}

/** Entwurf löschen: sofort aus der Liste, dann auf dem Server. */
export async function removeScanDraft(id: number): Promise<void> {
  deleting.add(id);
  resource.update((ds) => (ds ?? []).filter((d) => d.id !== id));
  try {
    await scanDraftApi.remove(id);
  } catch { /* schon weg */ }
  try {
    await resource.refresh();
  } catch { /* nächster Seitenwechsel */ }
  deleting.delete(id);
}

/** In der Navbar: bei jedem Seitenwechsel und wenn der Tab wieder vorne ist neu laden. */
export function useScanDraftsLive(): void {
  const pathname = usePathname();
  useEffect(() => {
    if (pathname === "/login" || pathname === "/auth/callback") return;
    refreshScanDrafts();
  }, [pathname]);
  useEffect(() => {
    const onVis = () => { if (document.visibilityState === "visible") refreshScanDrafts(); };
    document.addEventListener("visibilitychange", onVis);
    return () => document.removeEventListener("visibilitychange", onVis);
  }, []);
}
