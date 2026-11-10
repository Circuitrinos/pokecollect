// Turnier-Legalität (Standard) aus dem Regulation Mark — Gegenstück zu
// backend/app/services/regulation.py. legal_mark ist das für Standard
// maßgebliche Zeichen (eigenes oder, bei Trainer/Spezial-Energie, das jüngste
// gleichnamige Reprint); "-" = geprüft, kein Zeichen; null = unbekannt.

export const DEFAULT_MIN_MARK = "H";
export const MARK_LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ".split("");

export type StandardStatus = "legal" | "reprint" | "basic" | "not_legal" | "unknown";

type Regulated = {
  regulation_mark?: string | null;
  legal_mark?: string | null;
  basic_energy?: boolean;
};

export function standardStatus(card: Regulated, minMark: string | null | undefined): StandardStatus {
  const min = (minMark || DEFAULT_MIN_MARK).toUpperCase();
  if (card.basic_energy) return "basic";
  const legal = card.legal_mark;
  if (!legal) return "unknown";
  if (legal === "-" || legal < min) return "not_legal";
  const own = card.regulation_mark;
  return own && own >= min ? "legal" : "reprint";
}

export const isStandardLegal = (s: StandardStatus) => s === "legal" || s === "reprint" || s === "basic";
