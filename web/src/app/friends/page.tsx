"use client";
// Freunde: per genauem Benutzernamen anfragen, Anfragen annehmen/ablehnen,
// Freunde entfernen — und Sammlungen, die Freunde mit dir teilen (nur ansehen).
import Link from "next/link";
import { useEffect, useState } from "react";
import toast from "react-hot-toast";
import { friendsApi, sharedApi, FriendsOverview, FriendUser, SharedCollection } from "@/lib/api";
import { useMe } from "@/lib/useMe";
import { useI18n } from "@/lib/i18n";

const SECTION = "bg-pokemon-card rounded-lg p-5 space-y-3";
const H2 = "text-white font-semibold text-sm border-b border-gray-700 pb-2";
const BTN = "text-white text-xs px-3 py-1 rounded disabled:opacity-50";

const nameOf = (u: FriendUser) => u.display_name || u.username;

function Person({ user, children }: { user: FriendUser; children?: React.ReactNode }) {
  return (
    <li className="py-2 flex items-center gap-3">
      <div className="min-w-0 flex-1 text-sm">
        <span className="text-white">{nameOf(user)}</span>
        {user.display_name && <span className="text-gray-500 text-xs"> · {user.username}</span>}
      </div>
      <div className="flex gap-2">{children}</div>
    </li>
  );
}

export default function FriendsPage() {
  const { t } = useI18n();
  const me = useMe();
  const [data, setData] = useState<FriendsOverview | null>(null);
  const [shared, setShared] = useState<SharedCollection[] | null>(null);
  const [username, setUsername] = useState("");
  const [busy, setBusy] = useState(false);

  const loadShared = () => sharedApi.list().then((r) => setShared(r.data)).catch(() => setShared([]));
  useEffect(() => {
    friendsApi.overview().then((r) => setData(r.data)).catch(() => toast.error(t.settings_load_error));
    loadShared();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const run = async (call: () => Promise<{ data: FriendsOverview }>, ok?: string) => {
    setBusy(true);
    try {
      const r = await call();
      setData(r.data);
      if (ok) toast.success(ok);
      loadShared();
      return true;
    } catch (e: unknown) {
      const status = (e as { response?: { status?: number } })?.response?.status;
      toast.error(status === 404 ? t.friends_not_found : status === 409 ? t.friends_already : status === 422 ? t.friends_self : t.form_save_error);
      return false;
    } finally {
      setBusy(false);
    }
  };

  const send = async (e: React.FormEvent) => {
    e.preventDefault();
    const name = username.trim();
    if (!name) return;
    const before = data?.friends.length ?? 0;
    let after = before;
    const ok = await run(async () => {
      const r = await friendsApi.request(name);
      after = r.data.friends.length;
      return r;
    });
    if (!ok) return;
    // Hatte die andere Seite schon angefragt, seid ihr jetzt direkt befreundet.
    toast.success(after > before ? t.friends_now_friends(name) : t.friends_sent);
    setUsername("");
  };

  if (!data) return <div className="text-gray-500 p-8">{t.detail_loading}</div>;

  return (
    <div className="max-w-2xl mx-auto space-y-6">
      <h1 className="text-2xl font-bold text-white">{t.friends_title}</h1>

      <form onSubmit={send} className={SECTION}>
        <h2 className={H2}>{t.friends_add_label}</h2>
        <div className="flex gap-2">
          <input value={username} onChange={(e) => setUsername(e.target.value)} placeholder={t.friends_add_placeholder}
            aria-label={t.friends_add_placeholder} autoCapitalize="none" autoCorrect="off" spellCheck={false}
            className="min-w-0 flex-1 bg-gray-900 border border-gray-700 rounded px-3 py-2 text-white text-sm focus:outline-none focus:border-blue-500" />
          <button type="submit" disabled={busy || !username.trim()} className="bg-blue-700 text-white text-sm px-4 py-2 rounded hover:bg-blue-600 disabled:opacity-50">
            {t.friends_add_button}
          </button>
        </div>
        {me && <p className="text-gray-500 text-xs">{t.friends_add_hint(me.username)}</p>}
      </form>

      {data.incoming.length > 0 && (
        <section className={SECTION} data-testid="friends-incoming">
          <h2 className={H2}>{t.friends_incoming}</h2>
          <ul className="divide-y divide-gray-700">
            {data.incoming.map((r) => (
              <Person key={r.id} user={r.user}>
                <button type="button" disabled={busy} onClick={() => run(() => friendsApi.accept(r.id), t.friends_now_friends(nameOf(r.user)))}
                  className={`${BTN} bg-green-800 hover:bg-green-700`}>{t.friends_accept}</button>
                <button type="button" disabled={busy} onClick={() => run(() => friendsApi.dropRequest(r.id))}
                  className={`${BTN} bg-gray-700 hover:bg-gray-600`}>{t.friends_decline}</button>
              </Person>
            ))}
          </ul>
        </section>
      )}

      <section className={SECTION} data-testid="friends-list">
        <h2 className={H2}>{t.friends_list}</h2>
        {data.friends.length === 0 ? (
          <p className="text-gray-500 text-sm">{t.friends_none}</p>
        ) : (
          <ul className="divide-y divide-gray-700">
            {data.friends.map((f) => (
              <Person key={f.id} user={f}>
                <button type="button" disabled={busy}
                  onClick={() => { if (confirm(t.friends_remove_confirm(nameOf(f)))) run(() => friendsApi.remove(f.id)); }}
                  className={`${BTN} bg-red-900 hover:bg-red-800`}>{t.friends_remove}</button>
              </Person>
            ))}
          </ul>
        )}
      </section>

      {data.outgoing.length > 0 && (
        <section className={SECTION} data-testid="friends-outgoing">
          <h2 className={H2}>{t.friends_outgoing}</h2>
          <ul className="divide-y divide-gray-700">
            {data.outgoing.map((r) => (
              <Person key={r.id} user={r.user}>
                <button type="button" disabled={busy} onClick={() => run(() => friendsApi.dropRequest(r.id))}
                  className={`${BTN} bg-gray-700 hover:bg-gray-600`}>{t.friends_cancel}</button>
              </Person>
            ))}
          </ul>
        </section>
      )}

      <section className={SECTION} data-testid="friends-shared">
        <h2 className={H2}>{t.friends_shared_title}</h2>
        {!shared ? (
          <p className="text-gray-500 text-sm">{t.detail_loading}</p>
        ) : shared.length === 0 ? (
          <p className="text-gray-500 text-sm">{t.friends_shared_none}</p>
        ) : (
          <ul className="divide-y divide-gray-700">
            {shared.map((c) => (
              <li key={c.id}>
                <Link href={`/shared/${c.id}`} className="flex items-center justify-between gap-3 py-2 hover:bg-gray-800/50 rounded px-1">
                  <span className="min-w-0">
                    <span className="block text-white text-sm truncate">{c.name}</span>
                    <span className="block text-gray-500 text-xs">{t.shared_by(nameOf(c.owner))} · {t.collections_card_count(c.karten_anzahl)}</span>
                  </span>
                  <span className="text-gray-400 text-sm">→</span>
                </Link>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
