"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { useI18n } from "@/lib/i18n";
import { APP_VERSION } from "@/lib/version";
import BurgerMenu from "@/components/BurgerMenu";
import { isNavActive, scanHref, visibleNavTargets } from "@/lib/navRegistry";
import { useMe } from "@/lib/useMe";

// Haben rechts eigene Knöpfe
const OWN_BUTTON = new Set(["scan", "settings"]);

export default function Navbar() {
  const { t, lang, setLang } = useI18n();
  const pathname = usePathname() ?? "/";
  const isAdmin = useMe()?.is_admin ?? false;
  // Passt die Link-Reihe nicht ganz: rechts ausblenden, damit erkennbar ist, dass sie scrollt
  const linksRef = useRef<HTMLDivElement>(null);
  const [cramped, setCramped] = useState(false);   // nicht alle Links passen → ☰ zeigen
  const [overflow, setOverflow] = useState(false);  // … und rechts ist noch mehr
  useEffect(() => {
    const el = linksRef.current;
    if (!el) return;
    const measure = () => {
      const tooWide = el.scrollWidth > el.clientWidth + 1;
      setCramped(tooWide);
      setOverflow(tooWide && el.scrollLeft + el.clientWidth < el.scrollWidth - 1);
    };
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    el.addEventListener("scroll", measure, { passive: true });
    return () => { ro.disconnect(); el.removeEventListener("scroll", measure); };
  }, [isAdmin, pathname]);

  // Vor dem Login kein App-Chrome (Issue #1)
  if (pathname === "/login" || pathname === "/auth/callback") return null;

  return (
    <nav className="border-b border-gray-800 bg-pokemon-card px-4 sm:px-6 py-3 flex items-center gap-4 sm:gap-6">
      {/* Burger (☰, oben links): vollständige Navigation aus der Registry — nur,
          wenn das Fenster zu schmal ist (Handy, oder die Links passen nicht alle);
          sonst übernimmt das obere Menü. */}
      <div className={cramped ? "" : "md:hidden"} data-testid="burger-wrap">
        <BurgerMenu />
      </div>
      <Link href="/collections" className="text-lg sm:text-xl font-bold text-pokemon-yellow shrink-0">
        PokéCollect
      </Link>
      <span className="text-gray-600 text-xs hidden sm:inline">v{APP_VERSION}</span>

      {/* Desktop-Links: alle Seiten aus der Registry (wie ☰-Menü und Bottom-Nav);
          bei schmalem Fenster scrollt die Reihe seitlich. Mobil: Bottom-Nav. */}
      <div ref={linksRef} data-testid="top-links"
        className={`hidden md:flex items-center gap-5 min-w-0 overflow-x-auto [scrollbar-width:none] [&::-webkit-scrollbar]:hidden ${
          overflow ? "[mask-image:linear-gradient(to_right,black_calc(100%-40px),transparent)]" : ""}`}>
        {visibleNavTargets(isAdmin).filter((target) => !OWN_BUTTON.has(target.key)).map((target) => {
          const active = isNavActive(pathname, target);
          return (
            <Link key={target.key} href={target.href} aria-current={active ? "page" : undefined}
              className={`whitespace-nowrap text-sm ${active ? "text-pokemon-yellow" : "text-gray-300 hover:text-white"}`}>
              {t[target.labelKey]}
            </Link>
          );
        })}
      </div>

      <div className="ml-auto shrink-0 flex items-center gap-2 sm:gap-3">
        <button type="button"
          onClick={() => setLang(lang === "DE" ? "EN" : "DE")}
          className="text-gray-400 hover:text-white text-xs border border-gray-700 rounded px-2 py-1"
          title={lang === "DE" ? "Switch to English" : "Zu Deutsch wechseln"}
        >
          {lang === "DE" ? "EN" : "DE"}
        </button>
        <Link href="/settings" className="hidden md:inline-block text-gray-400 hover:text-white text-sm">⚙ {t.nav_settings}</Link>
        <Link
          href={scanHref(pathname)}
          className="hidden md:inline-block bg-pokemon-blue text-white text-sm px-3 py-1.5 rounded hover:bg-blue-500 text-center"
        >
          {t.nav_scan}
        </Link>
        <Link
          href="/cards/new"
          className="bg-pokemon-red text-white text-sm px-3 py-1.5 rounded hover:bg-red-600 inline-block text-center"
        >
          {t.nav_add_card}
        </Link>
      </div>
    </nav>
  );
}
