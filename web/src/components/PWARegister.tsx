"use client";
import { useEffect, useRef, useState } from "react";
import { usePathname } from "next/navigation";
import { useI18n } from "@/lib/i18n";

const BUILD = process.env.NEXT_PUBLIC_BUILD_STAMP ?? "";
const CHECK_EVERY_MS = 5 * 60 * 1000;
// Hier nie von selbst neu laden — man ist womöglich mitten in einem Scan oder einer neuen Karte.
const BUSY = /^\/(scan|cards\/new)(\/|$)/;

/**
 * Registriert den Service Worker (nur im sicheren Kontext: HTTPS oder localhost)
 * und sorgt dafür, dass nach einem Server-Update die neue Fassung geladen wird:
 * Beim Zurückkehren zur App (und alle 5 Minuten) wird die Build-Kennung des
 * Servers mit der eigenen verglichen. Ist sie neu, lädt die Seite beim nächsten
 * Seitenwechsel neu — oder sofort beim Zurückkehren, außer mitten in Scan oder
 * neuer Karte; bis dahin steht unten ein Hinweis mit „Neu laden“.
 */
export default function PWARegister() {
  const { t } = useI18n();
  const pathname = usePathname() ?? "/";
  const [updateReady, setUpdateReady] = useState(false);
  const firstPath = useRef(pathname);

  useEffect(() => {
    document.documentElement.dataset.build = BUILD;
    let reg: ServiceWorkerRegistration | undefined;
    if (typeof navigator !== "undefined" && "serviceWorker" in navigator) {
      const register = () => {
        navigator.serviceWorker.register("/sw.js", { updateViaCache: "none" })
          .then((r) => { reg = r; })
          .catch(() => {/* SW optional */});
      };
      if (document.readyState === "complete") register();
      else window.addEventListener("load", register, { once: true });
    }

    let stopped = false;
    const check = async (resumed: boolean) => {
      reg?.update().catch(() => {});
      try {
        const r = await fetch("/version.json", { cache: "no-store" });
        const { build } = await r.json();
        if (stopped || !build || !BUILD) return;
        if (build === BUILD) {
          // Diese Seite ist aktuell: Caches anderer Builds aufräumen
          const keys = await caches.keys();
          await Promise.all(keys.filter((k) => k.startsWith("pokecollect-") && k !== `pokecollect-${BUILD}`).map((k) => caches.delete(k)));
          return;
        }
        if (resumed && !BUSY.test(window.location.pathname)) window.location.reload();
        else setUpdateReady(true);
      } catch { /* offline: später erneut */ }
    };
    const onVisible = () => { if (document.visibilityState === "visible") void check(true); };
    const onFocus = () => void check(true);
    document.addEventListener("visibilitychange", onVisible);
    window.addEventListener("focus", onFocus);
    const timer = window.setInterval(() => void check(false), CHECK_EVERY_MS);
    const initial = window.setTimeout(() => void check(false), 3000);
    return () => {
      stopped = true;
      document.removeEventListener("visibilitychange", onVisible);
      window.removeEventListener("focus", onFocus);
      window.clearInterval(timer);
      window.clearTimeout(initial);
    };
  }, []);

  // Update bereit: der nächste Seitenwechsel lädt die neue Fassung
  useEffect(() => {
    if (updateReady && pathname !== firstPath.current && !BUSY.test(pathname)) window.location.reload();
    firstPath.current = pathname;
  }, [pathname, updateReady]);

  if (!updateReady) return null;
  return (
    <div role="status" data-testid="update-banner"
      className="fixed left-1/2 -translate-x-1/2 bottom-20 md:bottom-4 z-50 flex items-center gap-3 rounded-lg bg-pokemon-card border border-pokemon-yellow/50 px-4 py-2 text-sm text-white shadow-lg">
      <span>{t.update_available}</span>
      <button type="button" onClick={() => window.location.reload()}
        className="bg-pokemon-yellow text-black font-medium rounded px-3 py-1 hover:opacity-90">
        {t.update_reload}
      </button>
    </div>
  );
}
