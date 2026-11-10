"use client";
import { useState } from "react";

// Katalogbild mit Rückfall: TCGdex-Bild, sonst die (ungeprüfte) Adresse in der
// offiziellen Datenbank; lädt beides nicht, erscheint der Name als Platzhalter.
export default function CatalogCardImage({ url, fallback, alt, className = "", lazy = true }: {
  url?: string | null; fallback?: string | null; alt: string; className?: string; lazy?: boolean;
}) {
  const sources = [url, fallback].filter((s): s is string => !!s);
  const [idx, setIdx] = useState(0);
  if (idx >= sources.length) {
    return <div className="flex items-center justify-center h-full text-gray-600 text-[11px] text-center p-2">{alt}</div>;
  }
  return (
    // eslint-disable-next-line @next/next/no-img-element
    <img src={sources[idx]} alt={alt} className={className} loading={lazy ? "lazy" : undefined}
      onError={() => setIdx((i) => i + 1)} />
  );
}
