"use client";
// Sammlung mit Freunden teilen (nur ansehen): Freunde per Häkchen wählen.
import Link from "next/link";
import { useEffect, useState } from "react";
import toast from "react-hot-toast";
import { X } from "lucide-react";
import { collectionApi, friendsApi, Friend } from "@/lib/api";
import { useI18n } from "@/lib/i18n";

export default function ShareDialog({ collectionId, name, onClose, onSaved }: {
  collectionId: number;
  name: string;
  onClose: () => void;
  onSaved: (count: number) => void;
}) {
  const { t } = useI18n();
  const [friends, setFriends] = useState<Friend[] | null>(null);
  const [picked, setPicked] = useState<Set<number>>(new Set());
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    Promise.all([friendsApi.overview(), collectionApi.shares(collectionId)])
      .then(([f, s]) => { setFriends(f.data.friends); setPicked(new Set(s.data.map((u) => u.id))); })
      .catch(() => { toast.error(t.form_save_error); onClose(); });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [collectionId]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const toggle = (id: number) => setPicked((prev) => {
    const next = new Set(prev);
    if (next.has(id)) next.delete(id); else next.add(id);
    return next;
  });

  const save = async () => {
    setSaving(true);
    try {
      const r = await collectionApi.setShares(collectionId, Array.from(picked));
      toast.success(t.share_saved);
      onSaved(r.data.length);
      onClose();
    } catch {
      toast.error(t.form_save_error);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4" onClick={onClose}>
      <div role="dialog" aria-modal="true" aria-labelledby="share-title" onClick={(e) => e.stopPropagation()}
        className="w-full max-w-md rounded-lg bg-pokemon-card p-5 space-y-4 shadow-xl">
        <div className="flex items-start justify-between gap-3">
          <h2 id="share-title" className="text-white font-semibold">{t.share_dialog_title(name)}</h2>
          <button type="button" onClick={onClose} aria-label={t.nav_menu_close} className="text-gray-400 hover:text-white">
            <X className="h-5 w-5" aria-hidden />
          </button>
        </div>
        <p className="text-gray-400 text-xs">{t.share_hint}</p>
        {friends === null ? (
          <p className="text-gray-500 text-sm">{t.detail_loading}</p>
        ) : friends.length === 0 ? (
          <p className="text-gray-400 text-sm">
            {t.share_no_friends}{" "}
            <Link href="/friends" className="text-blue-400 hover:underline">{t.nav_friends} →</Link>
          </p>
        ) : (
          <ul className="max-h-72 overflow-y-auto divide-y divide-gray-700">
            {friends.map((f) => (
              <li key={f.id}>
                <label className="flex items-center gap-3 py-2 text-sm text-white cursor-pointer">
                  <input type="checkbox" checked={picked.has(f.id)} onChange={() => toggle(f.id)} className="h-4 w-4" />
                  <span>{f.display_name || f.username}</span>
                  {f.display_name && <span className="text-gray-500 text-xs">{f.username}</span>}
                </label>
              </li>
            ))}
          </ul>
        )}
        <div className="flex justify-end gap-2 pt-1">
          <button type="button" onClick={onClose} className="bg-gray-700 text-white text-sm px-4 py-1.5 rounded hover:bg-gray-600">
            {t.form_cancel}
          </button>
          {friends && friends.length > 0 && (
            <button type="button" onClick={save} disabled={saving}
              className="bg-blue-700 text-white text-sm px-4 py-1.5 rounded hover:bg-blue-600 disabled:opacity-50">
              {t.form_save}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
