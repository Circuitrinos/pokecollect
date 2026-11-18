"use client";
import { useCallback, useEffect, useMemo, useState } from "react";
import toast from "react-hot-toast";
import { ManualCard, ManualCardInput, PokemonSet, catalogApi, setsApi } from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import { INPUT } from "@/components/settings/SettingsUI";
import PictureFinder from "@/components/PictureFinder";

// Admin: Katalogkarten von Hand anlegen, die TCGdex noch nicht kennt (Brian 18.11.:
// „MEP 113 Enamorus"). Die Karte bekommt TCGdex' künftige ID; danach gleich ein Bild
// (Bild finden: einfügen/hochladen/zuschneiden) — daraus lernt auch der Scanner.
type Category = ManualCardInput["category"];
const EMPTY = { set: "", number: "", name: "", category: "Pokemon" as Category, dex: "", trainerType: "",
  energyType: "", rarity: "", mark: "", illustrator: "", normal: true, reverse: false, holo: false };

export default function ManualCatalog() {
  const { t } = useI18n();
  const [sets, setSets] = useState<PokemonSet[]>([]);
  const [items, setItems] = useState<ManualCard[]>([]);
  const [rarities, setRarities] = useState<string[]>([]);
  const [f, setF] = useState(EMPTY);
  const [saving, setSaving] = useState(false);
  const [picture, setPicture] = useState<{ cardId: string; name: string } | null>(null);

  const load = useCallback(() => {
    catalogApi.manualList().then((r) => { setItems(r.data.items); setRarities(r.data.rarities); }).catch(() => {});
  }, []);
  useEffect(() => {
    load();
    setsApi.list().then((r) => setSets(r.data.filter((s) => s.set_id))).catch(() => {});
  }, [load]);

  const byCode = useMemo(() => new Map(sets.map((s) => [s.code.toUpperCase(), s])), [sets]);
  const chosenSet = byCode.get(f.set.trim().toUpperCase()) ?? null;
  const put = <K extends keyof typeof EMPTY>(k: K, v: (typeof EMPTY)[K]) => setF((x) => ({ ...x, [k]: v }));

  const submit = async () => {
    if (!chosenSet?.set_id) { toast.error(t.manual_unknown_set); return; }
    const data: ManualCardInput = {
      set_id: chosenSet.set_id, number: f.number.trim(), name: f.name.trim(), category: f.category,
      rarity: f.rarity.trim() || null, regulation_mark: f.mark.trim().toUpperCase() || null,
      illustrator: f.illustrator.trim() || null,
      dex_id: f.category === "Pokemon" && f.dex ? Number(f.dex) : null,
      trainer_type: f.category === "Trainer" && f.trainerType ? f.trainerType as ManualCardInput["trainer_type"] : null,
      energy_type: f.category === "Energy" && f.energyType ? f.energyType as ManualCardInput["energy_type"] : null,
      normal: f.normal, reverse: f.reverse, holo: f.holo,
    };
    setSaving(true);
    try {
      const r = await catalogApi.manualAdd(data);
      toast.success(t.manual_added(`${chosenSet.code} ${f.number.trim()}`));
      setF({ ...EMPTY, set: f.set });
      load();
      setPicture({ cardId: r.data.card_id, name: data.name });
    } catch (e) {
      const res = (e as { response?: { status?: number; data?: { detail?: { card_id?: string } } } })?.response;
      if (res?.status === 409) toast.error(t.manual_exists(res.data?.detail?.card_id ?? ""));
      else toast.error(t.form_save_error);
    } finally {
      setSaving(false);
    }
  };

  const remove = async (it: ManualCard) => {
    if (!window.confirm(t.manual_remove_confirm(`${it.name ?? it.card_id}`))) return;
    try {
      await catalogApi.manualRemove(it.card_id);
      load();
    } catch {
      toast.error(t.form_save_error);
    }
  };

  const ready = !!chosenSet && !!f.number.trim() && !!f.name.trim() && (f.normal || f.reverse || f.holo);
  const label = "block text-xs text-gray-400 mb-1";

  return (
    <div className="space-y-3" data-testid="manual-catalog">
      <p className="text-gray-400 text-xs">{t.manual_hint}</p>

      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <label className="col-span-1">
          <span className={label}>{t.manual_set}</span>
          <input list="manual-sets" value={f.set} onChange={(e) => put("set", e.target.value)} className={INPUT}
            placeholder="MEP" data-testid="manual-set" />
          <datalist id="manual-sets">
            {sets.map((s) => <option key={s.code} value={s.code}>{s.name_en || s.name}</option>)}
          </datalist>
        </label>
        <label className="col-span-1">
          <span className={label}>{t.manual_number}</span>
          <input value={f.number} onChange={(e) => put("number", e.target.value)} className={INPUT}
            placeholder="113" data-testid="manual-number" />
        </label>
        <label className="col-span-2">
          <span className={label}>{t.manual_name}</span>
          <input value={f.name} onChange={(e) => put("name", e.target.value)} className={INPUT}
            placeholder="Enamorus" data-testid="manual-name" />
        </label>
        <p className="col-span-2 sm:col-span-4 -mt-2 text-[11px] text-gray-500">
          {chosenSet ? `${chosenSet.name_en || chosenSet.name}${f.number.trim() ? ` · ${chosenSet.code} ${f.number.trim()}` : ""}`
            : f.set.trim() ? t.manual_unknown_set : " "}
        </p>

        <label>
          <span className={label}>{t.manual_category}</span>
          <select value={f.category} onChange={(e) => put("category", e.target.value as Category)} className={INPUT}>
            <option value="Pokemon">{t.manual_cat_pokemon}</option>
            <option value="Trainer">{t.manual_cat_trainer}</option>
            <option value="Energy">{t.manual_cat_energy}</option>
          </select>
        </label>
        {f.category === "Pokemon" && (
          <label>
            <span className={label}>{t.manual_dex}</span>
            <input type="number" min={1} max={2000} value={f.dex} onChange={(e) => put("dex", e.target.value)}
              className={INPUT} placeholder="905" />
          </label>
        )}
        {f.category === "Trainer" && (
          <label>
            <span className={label}>{t.manual_trainer_type}</span>
            <select value={f.trainerType} onChange={(e) => put("trainerType", e.target.value)} className={INPUT}>
              <option value="">–</option>
              <option value="Item">{t.manual_tt_item}</option>
              <option value="Supporter">{t.manual_tt_supporter}</option>
              <option value="Tool">{t.manual_tt_tool}</option>
              <option value="Stadium">{t.manual_tt_stadium}</option>
            </select>
          </label>
        )}
        {f.category === "Energy" && (
          <label>
            <span className={label}>{t.manual_energy_type}</span>
            <select value={f.energyType} onChange={(e) => put("energyType", e.target.value)} className={INPUT}>
              <option value="">–</option>
              <option value="Basic">{t.manual_et_basic}</option>
              <option value="Special">{t.manual_et_special}</option>
            </select>
          </label>
        )}
        <label>
          <span className={label}>{t.manual_rarity}</span>
          <input list="manual-rarities" value={f.rarity} onChange={(e) => put("rarity", e.target.value)} className={INPUT} />
          <datalist id="manual-rarities">{rarities.map((r) => <option key={r} value={r} />)}</datalist>
        </label>
        <label>
          <span className={label}>{t.manual_mark}</span>
          <input value={f.mark} maxLength={1} onChange={(e) => put("mark", e.target.value.toUpperCase())}
            className={INPUT} placeholder="I" />
        </label>
        <label className="col-span-2">
          <span className={label}>{t.manual_illustrator}</span>
          <input value={f.illustrator} onChange={(e) => put("illustrator", e.target.value)} className={INPUT} />
        </label>
        <div className="col-span-2">
          <span className={label}>{t.manual_finishes}</span>
          <div className="flex flex-wrap gap-3 text-sm text-gray-200 py-2">
            {(["normal", "reverse", "holo"] as const).map((k) => (
              <label key={k} className="flex items-center gap-1.5">
                <input type="checkbox" checked={f[k]} onChange={(e) => put(k, e.target.checked)} />
                {k === "normal" ? "Normal" : k === "reverse" ? "Reverse Holo" : "Holo"}
              </label>
            ))}
          </div>
        </div>
      </div>

      <button type="button" onClick={() => void submit()} disabled={!ready || saving} data-testid="manual-add"
        className="bg-blue-700 text-white text-sm px-4 py-1.5 rounded hover:bg-blue-600 disabled:opacity-50">
        ➕ {t.manual_add}
      </button>

      {items.length > 0 && (
        <div className="pt-3 border-t border-gray-700 space-y-2">
          <p className="text-sm text-white">{t.manual_list(items.length)}</p>
          <ul className="space-y-2">
            {items.map((it) => (
              <li key={it.card_id} className="flex items-center gap-3 rounded border border-gray-800 p-2"
                data-testid="manual-item">
                <div className="w-10 h-14 shrink-0 rounded bg-gray-900 overflow-hidden flex items-center justify-center">
                  {it.image_url
                    // eslint-disable-next-line @next/next/no-img-element
                    ? <img src={it.image_url} alt="" className="w-full h-full object-cover" />
                    : <span className="text-[10px] text-gray-600 text-center leading-tight">{t.manual_no_picture}</span>}
                </div>
                <div className="flex-1 min-w-0 text-xs">
                  <div className="text-sm text-white truncate">{it.name}</div>
                  <div className="text-gray-400 truncate">
                    {[it.set_code, it.local_id].filter(Boolean).join(" ")} · {it.set_name} · <code>{it.card_id}</code>
                  </div>
                  {it.linked > 0 && <div className="text-gray-500">{t.manual_linked(it.linked)}</div>}
                </div>
                <button type="button" onClick={() => setPicture({ cardId: it.card_id, name: it.name ?? it.card_id })}
                  className="rounded bg-gray-700 px-2 py-1 text-xs text-white hover:bg-gray-600">🖼 {t.manual_picture}</button>
                <button type="button" onClick={() => void remove(it)} disabled={it.linked > 0}
                  title={it.linked > 0 ? t.manual_linked(it.linked) : undefined}
                  className="rounded bg-gray-800 px-2 py-1 text-xs text-gray-300 hover:bg-red-800 disabled:opacity-40">
                  {t.manual_remove}
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}

      {picture && (
        <PictureFinder cardId={picture.cardId} name={picture.name}
          onClose={() => { setPicture(null); load(); }}
          onApplied={() => { setPicture(null); load(); }} />
      )}
    </div>
  );
}
