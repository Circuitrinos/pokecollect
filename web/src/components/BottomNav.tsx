"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useRef } from "react";
import { useI18n } from "@/lib/i18n";
import { NavTarget, isNavActive, navHref, visibleNavTargets } from "@/lib/navRegistry";
import { useMe } from "@/lib/useMe";
import { useBottomNavConfig } from "@/lib/useBottomNav";
import { useScanDrafts } from "@/lib/useScanDrafts";
import DraftCount from "@/components/DraftCount";

export default function BottomNav() {
  const { t } = useI18n();
  const pathname = usePathname() ?? "/";
  // Konfigurierbare Schnellzugriffe (localStorage, Issue #34). Alle Ziele lesen
  // aus der zentralen Registry — nichts hier hartkodiert (DRY).
  const { keys } = useBottomNavConfig();
  // Mehr Ziele als Platz: die Leiste scrollt seitlich; das aktive Ziel wird
  // beim Seitenwechsel in die Mitte geholt.
  const barRef = useRef<HTMLElement>(null);
  const isAdmin = useMe()?.is_admin ?? false;
  const pendingScans = useScanDrafts()?.length ?? 0;
  useEffect(() => {
    const bar = barRef.current;
    const el = bar?.querySelector<HTMLElement>("[data-active]");
    if (bar && el) bar.scrollLeft = el.offsetLeft - (bar.clientWidth - el.clientWidth) / 2;
  }, [pathname, keys.length, isAdmin]);
  const targets = visibleNavTargets(isAdmin);

  // Scan-Seite hat eine eigene fixierte Aktionsleiste → Bottom-Nav ausblenden;
  // vor dem Login (Issue #1) ebenfalls kein App-Chrome
  if (pathname.replace(/\/$/, "") === "/scan" || pathname === "/login" || pathname === "/auth/callback") return null;

  const items = keys
    .map((k) => targets.find((target) => target.key === k))
    .filter((x): x is NavTarget => Boolean(x));

  // Leere Auswahl → keine Leiste (alles bleibt über das Burger-Menü erreichbar).
  if (items.length === 0) return null;

  return (
    <nav ref={barRef} className="md:hidden fixed bottom-0 inset-x-0 z-40 bg-pokemon-card border-t border-gray-800 flex items-stretch overflow-x-auto overscroll-x-contain [scrollbar-width:none] [&::-webkit-scrollbar]:hidden pb-[env(safe-area-inset-bottom)]">
      {items.map((target) => {
        const { key, Icon, labelKey, shortLabelKey } = target;
        const href = navHref(target, pathname);
        const active = isNavActive(pathname, target);
        // Scan behält seinen hervorgehobenen "primären" Look, wo immer er sitzt.
        const primary = key === "scan";
        return (
          <Link
            key={key}
            href={href}
            aria-current={active ? "page" : undefined}
            aria-label={shortLabelKey ? t[labelKey] : undefined}
            data-active={active || undefined}
            className={`flex-1 shrink-0 min-w-[64px] flex flex-col items-center justify-center gap-0.5 py-2 min-h-[56px] text-[10px] ${
              active ? "text-pokemon-yellow" : "text-gray-400"
            }`}
          >
            <span className={`relative ${primary ? "bg-pokemon-blue text-white rounded-full p-1 shadow-lg" : ""}`}>
              <Icon size={20} />
              {key === "scandrafts" && <DraftCount n={pendingScans} className="absolute -right-2.5 -top-1.5" />}
            </span>
            <span className="truncate max-w-[64px]">{t[shortLabelKey ?? labelKey]}</span>
          </Link>
        );
      })}
    </nav>
  );
}
