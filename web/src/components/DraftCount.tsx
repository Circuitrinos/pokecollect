"use client";
/** Zähler offener Scans an einem Menüpunkt (nichts bei 0). */
export default function DraftCount({ n, className = "" }: { n: number; className?: string }) {
  if (!n) return null;
  return (
    <span data-testid="scan-drafts-count"
      className={`inline-flex min-w-[1.1rem] h-[1.1rem] items-center justify-center rounded-full bg-pokemon-red px-1 text-[10px] font-semibold leading-none text-white ${className}`}>
      {n > 99 ? "99+" : n}
    </span>
  );
}
