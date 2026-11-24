"use client";
import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import toast from "react-hot-toast";
import { QrCode, Search } from "lucide-react";
import Image from "@/components/AppImage";
import { API_BASE, Card, SleeveTag, TagErrorReason, tagError, tagsApi } from "@/lib/api";
import { cardImageSrc, cardName, extractSetCode } from "@/lib/utils";
import { useI18n } from "@/lib/i18n";
import { valueLabel } from "@/lib/enumLabels";

// Ziel des QR-Codes auf einem Hüllen-Aufkleber (backend/app/services/sleeve_tags.py).
// Der Link ist groß geschrieben (…/T/K7M2 — kleiner QR-Code), darum heißt der
// Ordner „T". Klebt der Aufkleber schon auf einer Karte → gleich zur Karte;
// sonst die eigenen Karten ohne Aufkleber zur Auswahl, die neuesten zuerst
// (Ablauf: Karte scannen, in die Hülle, Aufkleber drauf, Aufkleber scannen).
export default function TagLandingPage() {
  const { code } = useParams<{ code: string }>();
  const router = useRouter();
  const { t, lang } = useI18n();
  const [tag, setTag] = useState<SleeveTag | null>(null);
  const [err, setErr] = useState<TagErrorReason | null>(null);
  const [search, setSearch] = useState("");
  const [cands, setCands] = useState<Card[] | null>(null);
  const [conflict, setConflict] = useState<{ card: Card; other: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const seq = useRef(0);

  useEffect(() => {
    tagsApi.get(code).then((r) => {
      if (r.data.card_id) router.replace(`/cards/${r.data.card_id}`);
      else setTag(r.data);
    }).catch((e) => setErr(tagError(e).reason ?? "unknown"));
  }, [code, router]);

  useEffect(() => {
    if (!tag) return;
    const n = ++seq.current;
    const timer = setTimeout(() => {
      tagsApi.candidates(search).then((r) => { if (n === seq.current) setCands(r.data); })
        .catch(() => { if (n === seq.current) setCands([]); });
    }, search ? 250 : 0);
    return () => clearTimeout(timer);
  }, [tag, search]);

  const pick = async (card: Card, replace = false) => {
    if (!tag || busy) return;
    setBusy(true);
    try {
      await tagsApi.link(tag.code, card.id, replace);
      toast.success(t.tag_linked_ok(tag.code, cardName(card, lang)));
      router.replace(`/cards/${card.id}`);
    } catch (e) {
      const { reason, other } = tagError(e);
      if (reason === "card_tagged") setConflict({ card, other: String(other) });
      else toast.error(reason === "tag_linked" ? t.tag_already_used : t.tags_error);
      setBusy(false);
    }
  };

  if (err) {
    const msg = err === "format" ? t.tag_err_format : t.tag_err_unknown;
    return (
      <div className="max-w-md mx-auto mt-10 bg-pokemon-card rounded-lg p-6 text-center space-y-3">
        <QrCode className="w-10 h-10 mx-auto text-gray-500" aria-hidden />
        <p className="text-white text-sm">{msg}</p>
        <Link href="/tags" className="inline-block text-sky-300 text-sm hover:underline">{t.tags_title}</Link>
      </div>
    );
  }
  if (!tag) return null;

  return (
    <div className="max-w-2xl mx-auto space-y-4">
      <div className="flex items-center gap-3">
        <div className="w-11 h-11 rounded-lg bg-white flex items-center justify-center shrink-0">
          <QrCode className="w-7 h-7 text-gray-900" aria-hidden />
        </div>
        <div>
          <h1 className="text-xl font-bold text-white">{t.tag_title(tag.code)}</h1>
          <p className="text-gray-400 text-sm">{t.tag_free}</p>
        </div>
      </div>

      <label className="relative block">
        <Search className="w-4 h-4 text-gray-500 absolute left-3 top-1/2 -translate-y-1/2" aria-hidden />
        <input value={search} onChange={(e) => setSearch(e.target.value)} placeholder={t.tag_search}
          className="w-full bg-gray-900 border border-gray-700 rounded-lg pl-9 pr-3 py-2.5 text-white text-sm focus:outline-none focus:border-blue-500" />
      </label>
      {!search && <p className="text-gray-500 text-xs -mt-2">{t.tag_recent}</p>}

      {cands !== null && cands.length === 0 && <p className="text-gray-500 text-sm">{t.tag_no_candidates}</p>}
      <ul className="space-y-2">
        {(cands ?? []).map((card) => {
          const { src, fallbacks } = cardImageSrc(card, API_BASE, true);
          return (
            <li key={card.id}>
              <button type="button" onClick={() => pick(card)} disabled={busy}
                className="w-full flex items-center gap-3 bg-pokemon-card hover:bg-gray-700/60 rounded-lg p-2 text-left disabled:opacity-60">
                <div className="relative w-12 aspect-[63/88] shrink-0 rounded overflow-hidden bg-gray-800">
                  {src && <Image src={src} fallbacks={fallbacks} alt={card.kartenname} fill className="object-cover" sizes="48px" />}
                </div>
                <div className="min-w-0 flex-1">
                  <div className="text-white text-sm font-medium truncate">{cardName(card, lang)}</div>
                  <div className="text-gray-400 text-xs font-mono">
                    {extractSetCode(card.set_edition) || "–"} {card.karten_nr ?? ""}
                  </div>
                  <div className="text-gray-500 text-xs truncate">
                    {[card.folierung, card.sprache, card.zustand].map((v) => valueLabel(v, lang)).filter(Boolean).join(" · ")}
                  </div>
                </div>
              </button>
            </li>
          );
        })}
      </ul>

      {conflict && (
        <div className="fixed inset-0 bg-black/70 flex items-center justify-center z-50 p-4">
          <div className="bg-gray-900 border border-gray-700 rounded-xl p-5 max-w-sm w-full space-y-4">
            <p className="text-white text-sm">{t.tag_card_tagged(conflict.other)}</p>
            <div className="flex justify-end gap-2">
              <button type="button" onClick={() => setConflict(null)}
                className="bg-gray-700 hover:bg-gray-600 text-white text-sm px-3 py-1.5 rounded">{t.tag_cancel}</button>
              <button type="button" onClick={() => { const c = conflict.card; setConflict(null); pick(c, true); }}
                className="bg-blue-700 hover:bg-blue-600 text-white text-sm px-3 py-1.5 rounded">{t.tag_replace}</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
