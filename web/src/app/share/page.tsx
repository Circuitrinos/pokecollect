"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import toast from "react-hot-toast";
import { cardApi } from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import { clearLinkTarget, looksLikePokemonComLink, pendingLinkTarget } from "@/lib/pokemonComLink";

// Ziel von „Teilen → PokéCollect" (Web Share Target, manifest.webmanifest):
// ein auf pokemon.com geteilter Kartenlink geht an die Karte, für die zuletzt
// „Auf pokemon.com suchen" geöffnet wurde.
export default function SharePage() {
  const { t } = useI18n();
  const router = useRouter();
  const [msg, setMsg] = useState<string | null>(null);

  useEffect(() => {
    const p = new URLSearchParams(window.location.search);
    const shared = [p.get("url"), p.get("text"), p.get("title")].filter(Boolean).join(" ");
    const cardId = pendingLinkTarget();
    if (!looksLikePokemonComLink(shared)) { setMsg(t.share_no_link); return; }
    if (cardId == null) { setMsg(t.share_no_card); return; }
    cardApi.linkPokemonCom(cardId, shared)
      .then(() => {
        clearLinkTarget();
        toast.success(t.finder_linked);
        router.replace(`/cards/${cardId}`);
      })
      .catch((e) => {
        const status = (e as { response?: { status?: number } })?.response?.status;
        setMsg(status === 404 ? t.finder_link_no_image : status === 422 ? t.finder_link_invalid : t.form_save_error);
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div className="max-w-md mx-auto py-10 text-center space-y-4">
      <h1 className="text-white text-lg font-semibold">{t.share_title}</h1>
      <p className="text-gray-400 text-sm" data-testid="share-msg">{msg ?? t.share_working}</p>
      {msg && <Link href="/" className="text-pokemon-blue text-sm hover:underline">{t.nav_collection}</Link>}
    </div>
  );
}
