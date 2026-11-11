"use client";
import { useEffect, useRef, useState } from "react";
import Image from "@/components/AppImage";
import toast from "react-hot-toast";
import { API_BASE, Card, ScanCheck, cardApi } from "@/lib/api";
import CornerEditor, { CornerSuggestion } from "@/components/CornerEditor";
import { settleCrop } from "@/lib/settleCrop";
import CardFinder from "@/components/carddetail/CardFinder";
import { cropToCardPhoto, normalizeOrientation, CropTransform } from "@/lib/cardCrop";
import { imageUrl, cardImageSrc } from "@/lib/utils";
import { useI18n } from "@/lib/i18n";
import { useMe } from "@/lib/useMe";
import { scanExportProblems } from "@/lib/scanExport";
import { IMAGE_LANGS } from "@/lib/imageLang";
import PictureFinder from "@/components/PictureFinder";
import StampEditor from "@/components/StampEditor";

// Foto-Spalte der Kartendetailseite (Issue #14): Anzeige, Aufnahme/Upload,
// Eck-Editor (Zuschnitt/Entzerrung), Bild-URL und Löschen. Herausgeschnitten
// aus cards/[id]/page.tsx — Verhalten 1:1.

type Props = {
  card: Card;
  /** Kartenseite mit ?fixcrop=1 (Werkzeug „Schwache Scans"): Zuschnitt-Editor gleich öffnen. */
  autoFixCrop?: boolean;
  /** Server-Antwort übernehmen (nur Kartenzustand). */
  onCardUpdated: (card: Card) => void;
  /** Server-Antwort übernehmen UND Formular synchronisieren (URL-Änderungen). */
  onCardSaved: (card: Card) => void;
  /** Klick/Tap aufs Kartenbild → Vollbild-Lightbox öffnen (Issue #24). */
  onImageClick?: (src: string) => void;
  /** Horizontales Wischen aufs Kartenbild → Vor/Zurück blättern (Issue #24, Mobil). */
  onSwipeCard?: (dir: "prev" | "next") => void;
};

export default function PhotoPanel({ card, onCardUpdated, onCardSaved, onImageClick, onSwipeCard, autoFixCrop }: Props) {
  const { t } = useI18n();
  // Nur Admins: Hinweis, wenn das Foto nicht in den Scan-Trainingsdatensatz kommt
  const isAdmin = !!useMe()?.is_admin;
  const exportProblems = isAdmin ? scanExportProblems(card) : [];
  const [pictureOpen, setPictureOpen] = useState(false);
  // Play!-Stempel markieren (Prize Pack mit eigenem Foto)
  const [stampOpen, setStampOpen] = useState(false);
  const stampPhoto = card.bild_scan_pfad ?? card.bild_karte_pfad;
  const fileRef = useRef<HTMLInputElement>(null);
  const cameraRef = useRef<HTMLInputElement>(null);
  const [urlInput, setUrlInput] = useState("");
  const [showUrlInput, setShowUrlInput] = useState(false);
  // Foto-Zuschnitt/Entzerrung vor dem Upload (CornerEditor)
  const [editorUrl, setEditorUrl] = useState<string | null>(null);
  const [editorInitQuad, setEditorInitQuad] = useState<number[][]>(
    [[0.06, 0.05], [0.94, 0.05], [0.94, 0.95], [0.06, 0.95]]);
  const editorBlobRef = useRef<Blob | null>(null);
  const editorOriginalRef = useRef<Blob | null>(null);  // Original mit-hochladen (nur bei neuem Foto)
  // Zuschnitt aus dem gespeicherten Original korrigieren (POST /recrop statt neuem Foto)
  const editorRecropRef = useRef(false);
  // Gespeichertes Foto mit Original → Zuschnitt lässt sich korrigieren
  const canRecrop = !!card.bild_original_pfad && !!(card.bild_karte_pfad || card.bild_scan_pfad);
  // Letzte Prüfung des Scan-Fotos (Werkzeug „Schwache Scans"): Hinweis + vorgeschlagene Ecken
  const [check, setCheck] = useState<ScanCheck | null>(null);
  const checkRef = useRef<ScanCheck | null>(null);
  const [checkLoaded, setCheckLoaded] = useState(false);
  useEffect(() => {
    const done = (c: ScanCheck | null) => { checkRef.current = c; setCheck(c); setCheckLoaded(true); };
    if (!canRecrop) { done(null); return; }
    cardApi.scanCheck(card.id).then((r) => done(r.data)).catch(() => done(null));
  }, [card.id, canRecrop, card.bild_quad]);
  const [uploadingPhoto, setUploadingPhoto] = useState(false);
  // Tap-vs-Wisch aufs Kartenbild (Issue #24): Tap öffnet die Lightbox, ein
  // horizontaler Wisch blättert zur Nachbarkarte und unterdrückt den Folge-Klick.
  const swipeStart = useRef<{ x: number; y: number } | null>(null);
  const swipeSuppressClick = useRef(false);

  const onPhotoTouchStart = (e: React.TouchEvent) => {
    if (editorUrl) return;               // Foto-Editor offen → nicht blättern
    const tch = e.touches[0];
    swipeStart.current = { x: tch.clientX, y: tch.clientY };
  };
  const onPhotoTouchEnd = (e: React.TouchEvent) => {
    const start = swipeStart.current;
    swipeStart.current = null;
    if (!start || editorUrl || !onSwipeCard) return;
    const tch = e.changedTouches[0];
    const dx = tch.clientX - start.x;
    const dy = tch.clientY - start.y;
    if (Math.abs(dx) > 50 && Math.abs(dx) > Math.abs(dy) * 1.5) {
      swipeSuppressClick.current = true; // folgenden Zoom-Klick unterdrücken
      onSwipeCard(dx < 0 ? "next" : "prev");
    }
  };
  // Bild-Prioritätskette zentral in cardImageSrc (Vertrag mit dem Backend,
  // siehe backend/app/services/card_image_service.py:14-18) — Issue #10
  const { src: imgSrc, isPlaceholder, fallbacks } = cardImageSrc(card, API_BASE, true, "full");
  // #120: nach dem, was angezeigt wird (Spiegel), nicht nach den Rohfeldern
  const isAutoCard = !card.bild_karte_pfad && !card.bild_pokedex_spiegel && !!card.bild_karte_spiegel;
  // Woher das automatische Bild kommt (TCGdex, offizielle Datenbank, TCGplayer)
  const autoSource = card.bild_karte_url?.includes("assets.pokemon.com") ? "pokemon.com"
    : card.bild_karte_url?.includes("tcgplayer") ? "TCGplayer"
    : card.bild_karte_url?.includes("pokemontcg.io") ? "pokemontcg.io"
    : card.bild_karte_url?.startsWith("katalog:") ? (t.detail_provenance_image("katalog") ?? "PokéCollect") : "TCGdex";
  // Tap/Klick aufs Bild öffnet die Lightbox — außer direkt nach einem Wisch.
  const handleImageClick = () => {
    if (swipeSuppressClick.current) { swipeSuppressClick.current = false; return; }
    if (imgSrc && onImageClick) onImageClick(imgSrc);
  };

  // Foto gewählt/aufgenommen → erst im Eck-Editor zuschneiden/entzerren,
  // dann hochladen. So lässt sich das Foto vor dem Speichern bearbeiten.
  const handleImageUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file) return;
    // EXIF einbacken → Editor-Anzeige == gespeicherter Zuschnitt
    const norm = await normalizeOrientation(file);
    editorBlobRef.current = norm;
    editorOriginalRef.current = norm;   // neues Foto → Original mitspeichern
    setEditorInitQuad([[0.06, 0.05], [0.94, 0.05], [0.94, 0.95], [0.06, 0.95]]);
    setEditorUrl((prev) => {
      if (prev) URL.revokeObjectURL(prev);
      return URL.createObjectURL(norm);
    });
  };

  // Zuschnitt korrigieren: das Original mit den gespeicherten (oder von der
  // Scan-Prüfung vorgeschlagenen) Ecken öffnen; Übernehmen ersetzt das Foto
  // und die Ecken (Trainingsdaten für die Kartenerkennung).
  const handleFixCrop = async () => {
    const src = imageUrl(card.bild_original_pfad, API_BASE, card.aktualisiert_am);
    if (!src) return;
    try {
      const blob = await (await fetch(src)).blob();
      editorBlobRef.current = blob;
      editorOriginalRef.current = null;
      editorRecropRef.current = true;
      setEditorSuggests(!!card.tcgdex_card_id);
      setEditorInitQuad(checkRef.current?.suggested_quad ?? card.bild_quad ?? [[0.1, 0.08], [0.9, 0.08], [0.9, 0.92], [0.1, 0.92]]);
      setEditorUrl((prev) => {
        if (prev) URL.revokeObjectURL(prev);
        return URL.createObjectURL(blob);
      });
    } catch {
      toast.error(t.detail_upload_error);
    }
  };
  // Aus der Liste „Schwache Scans" kommend: Editor einmal von selbst öffnen
  const autoOpened = useRef(false);
  useEffect(() => {
    if (!autoFixCrop || autoOpened.current || !canRecrop || !checkLoaded) return;   // Vorschlag erst laden
    autoOpened.current = true;
    void handleFixCrop();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [autoFixCrop, canRecrop, checkLoaded]);

  // Bereits hochgeladenes eigenes Foto nachträglich bearbeiten (Zuschnitt /
  // Flip / Drehen). Mit Original: wie „Zuschnitt korrigieren" (Ecken bleiben
  // gespeichert); ohne Original nur den vorhandenen Zuschnitt drehen/spiegeln.
  const handleEditExisting = async () => {
    if (!card.bild_karte_pfad) return;
    if (canRecrop) { await handleFixCrop(); return; }
    const fromOriginal = !!card.bild_original_pfad;
    const src = imageUrl(card.bild_original_pfad ?? card.bild_karte_pfad, API_BASE, card.aktualisiert_am);
    if (!src) return;
    try {
      const resp = await fetch(src);
      const blob = await resp.blob();
      editorBlobRef.current = blob;
      editorOriginalRef.current = null;   // Bearbeitung → Original NICHT überschreiben
      // Original: Karte freistellen (Ecken setzen). Bereits-Zuschnitt: nur Flip/Drehen.
      setEditorInitQuad(fromOriginal
        ? [[0.1, 0.08], [0.9, 0.08], [0.9, 0.92], [0.1, 0.92]]
        : [[0.01, 0.01], [0.99, 0.01], [0.99, 0.99], [0.01, 0.99]]);
      setEditorUrl((prev) => {
        if (prev) URL.revokeObjectURL(prev);
        return URL.createObjectURL(blob);
      });
    } catch {
      toast.error(t.detail_upload_error);
    }
  };

  // Fix crop: grobe Ecken genügen — der Bild-Abgleich schlägt die genauen vor
  const [editorSuggests, setEditorSuggests] = useState(false);
  const suggestCorners = async (quad: number[][], transform: CropTransform): Promise<CornerSuggestion | null> => {
    const blob = editorBlobRef.current;
    if (!blob) return null;
    const best = await settleCrop(blob, quad, async (file, q) => (await cardApi.cropSuggest(card.id, file, q)).data,
      { transform, includeStart: false });
    return best && { quad: best.quad, ok: best.ok, label: t.weak_stats(best.inliers, best.err) };
  };

  const closePhotoEditor = () => {
    setEditorUrl((prev) => {
      if (prev) URL.revokeObjectURL(prev);
      return null;
    });
    editorBlobRef.current = null;
    editorOriginalRef.current = null;
    editorRecropRef.current = false;
    setEditorSuggests(false);
  };

  const handleApplyPhoto = async (quad: number[][], transform: CropTransform) => {
    const blob = editorBlobRef.current;
    if (!blob) { closePhotoEditor(); return; }
    setUploadingPhoto(true);
    try {
      const file = await cropToCardPhoto(blob, { quad, ...transform });
      if (editorRecropRef.current) {
        const r = await cardApi.recrop(card.id, file, quad);
        onCardUpdated(r.data);
        checkRef.current = null;
        setCheck(null);
        toast.success(t.fix_crop_saved);
        closePhotoEditor();
        return;
      }
      const original = editorOriginalRef.current
        ? new File([editorOriginalRef.current], "orig.jpg", { type: "image/jpeg" })
        : undefined;
      // Ecken mitsenden: sie gehören zum Originalfoto (Trainingsdaten)
      const r = await cardApi.uploadImage(card.id, file, original, original ? quad : null);
      onCardUpdated(r.data);
      toast.success(t.detail_photo_saved);
      closePhotoEditor();
    } catch {
      toast.error(t.detail_upload_error);
    } finally {
      setUploadingPhoto(false);
    }
  };

  const handleSaveUrl = async () => {
    const url = urlInput.trim();
    if (!url) return;
    try {
      const r = await cardApi.update(card.id, { bild_pokedex_url: url });
      onCardSaved(r.data);
      setShowUrlInput(false);
      setUrlInput("");
      toast.success(t.detail_url_saved);
    } catch {
      toast.error(t.detail_url_save_error);
    }
  };

  const handleClearPokedexUrl = async () => {
    if (!confirm(t.detail_delete_url_confirm)) return;
    try {
      const r = await cardApi.update(card.id, { bild_pokedex_url: null });
      onCardSaved(r.data);
      toast.success(t.detail_url_deleted);
    } catch {
      toast.error(t.detail_delete_error);
    }
  };

  // Anzeige umschalten: eigenes Foto ↔ Katalogbild (das Foto bleibt dabei
  // erhalten und lernt weiter mit — dieselben Aktionen wie die Sammelauswahl).
  // Beim eigenen Foto immer anbieten — fehlt ein Katalogbild, sucht der
  // Server eins (TCGdex, offizielle Datenbank).
  const showsOwn = !!card.bild_karte_pfad;
  const canShowCatalog = showsOwn;
  const canShowOwn = !showsOwn && !!card.bild_scan_pfad;
  const [switching, setSwitching] = useState(false);
  const [finderOpen, setFinderOpen] = useState(false);
  const [langBusy, setLangBusy] = useState(false);
  const changeImageLang = async (lang: string) => {
    setLangBusy(true);
    try {
      const r = await cardApi.setImageLanguage(card.id, lang);
      onCardUpdated(r.data);
    } catch (e) {
      const status = (e as { response?: { status?: number } })?.response?.status;
      toast.error(status === 404 ? t.image_lang_missing : t.form_save_error);
    } finally {
      setLangBusy(false);
    }
  };
  const switchPicture = async (to: "catalog_image" | "own_photo") => {
    setSwitching(true);
    try {
      if (to === "catalog_image") {
        const r = await cardApi.useCatalogImage(card.id);
        onCardUpdated(r.data);
      } else {
        await cardApi.bulk(to, [card.id]);
        const r = await cardApi.get(card.id);
        onCardUpdated(r.data);
      }
    } catch (e) {
      const status = (e as { response?: { status?: number } })?.response?.status;
      toast.error(status === 404 ? t.detail_picture_no_catalog : t.form_save_error);
      if (status === 404) cardApi.get(card.id).then((r) => onCardUpdated(r.data)).catch(() => {});
    } finally {
      setSwitching(false);
    }
  };

  const handleDeleteImage = async () => {
    if (!confirm(t.detail_delete_photo_confirm)) return;
    try {
      const r = await cardApi.deleteImage(card.id);
      onCardUpdated(r.data);
      toast.success(t.detail_photo_saved);
      // Server stößt direkt nach der Antwort den TCGdex-Nachladejob an →
      // Karte explizit neu laden statt setTimeout-Polling (Issue #14).
      const r2 = await cardApi.get(card.id);
      onCardUpdated(r2.data);
    } catch {
      toast.error(t.detail_delete_error);
    }
  };

  return (
    <div className="shrink-0 w-full max-w-[13rem] mx-auto md:mx-0 md:w-52">
      <div
        className="aspect-[63/88] relative bg-gray-800 rounded-lg overflow-hidden"
        onTouchStart={onPhotoTouchStart}
        onTouchEnd={onPhotoTouchEnd}
      >
        {imgSrc ? (
          <>
            <Image
              src={imgSrc}
              fallbacks={fallbacks}
              alt={card.kartenname}
              fill
              className={isPlaceholder ? "object-contain p-4 opacity-75" : "object-cover"}
            />
            {isAutoCard && (
              <div className="absolute bottom-0 inset-x-0 bg-black/60 text-center text-blue-400 text-xs py-1">
                {autoSource}
              </div>
            )}
            {isPlaceholder && (
              <div className="absolute bottom-0 inset-x-0 bg-black/60 text-center text-gray-400 text-xs py-1">
                {t.detail_placeholder}
              </div>
            )}
            {onImageClick && (
              // Transparente Ebene über dem Bild: Tap/Klick öffnet die Lightbox
              // (Wisch-Gesten laufen über die onTouch-Handler des Containers).
              <button
                type="button"
                onClick={handleImageClick}
                aria-label={t.detail_zoom_image}
                className="absolute inset-0 z-10 cursor-zoom-in"
              />
            )}
          </>
        ) : (
          <div className="flex items-center justify-center h-full text-gray-600">{t.detail_no_image}</div>
        )}
      </div>
      {(canShowCatalog || canShowOwn) && (
        <div className="mt-2 grid grid-cols-2 gap-1 rounded-lg bg-gray-800/70 p-1" role="radiogroup"
          aria-label={t.detail_picture_label}>
          <button type="button" role="radio" aria-checked={showsOwn} disabled={switching || showsOwn}
            onClick={() => void switchPicture("own_photo")}
            className={`rounded-md px-2 py-1 text-xs ${showsOwn ? "bg-pokemon-blue text-white" : "text-gray-300 hover:bg-gray-700"}`}>
            {t.detail_picture_own}
          </button>
          <button type="button" role="radio" aria-checked={!showsOwn} disabled={switching || !showsOwn}
            onClick={() => void switchPicture("catalog_image")}
            className={`rounded-md px-2 py-1 text-xs ${!showsOwn ? "bg-pokemon-blue text-white" : "text-gray-300 hover:bg-gray-700"}`}>
            {t.detail_picture_catalog}
          </button>
        </div>
      )}
      {card.bild_sprache && (
        <label className="mt-1 flex items-center justify-between gap-2 text-xs text-gray-400" title={t.image_lang_hint}>
          <span>🌐 {t.image_lang_label}</span>
          <select value={card.bild_sprache} disabled={langBusy} data-testid="image-lang"
            onChange={(e) => void changeImageLang(e.target.value)}
            className="bg-gray-800 border border-gray-700 rounded px-1.5 py-0.5 text-white text-xs disabled:opacity-50">
            {IMAGE_LANGS.map((l) => <option key={l} value={l}>{l}</option>)}
          </select>
        </label>
      )}
      <button type="button" onClick={() => setFinderOpen(true)}
        className="w-full mt-1 text-sm bg-gray-800 text-gray-300 hover:text-white rounded px-3 py-1.5">
        🔍 {t.finder_open}
      </button>
      {finderOpen && (
        <CardFinder card={card} onClose={() => setFinderOpen(false)} onLinked={(c) => onCardSaved(c)} />
      )}
      {isAdmin && card.tcgdex_card_id && (
        <button type="button" onClick={() => setPictureOpen(true)} data-testid="card-find-picture"
          className="w-full mt-1 text-sm bg-gray-800 text-gray-300 hover:text-white rounded px-3 py-1.5">
          🖼 {t.pic_find}
        </button>
      )}
      {pictureOpen && card.tcgdex_card_id && (
        <PictureFinder cardId={card.tcgdex_card_id} name={card.englischer_name || card.kartenname}
          onClose={() => setPictureOpen(false)}
          onApplied={() => { setPictureOpen(false); cardApi.get(card.id).then((r) => onCardUpdated(r.data)).catch(() => {}); }} />
      )}
      {exportProblems.length > 0 && (
        <div className="mt-2 rounded border border-amber-700/60 bg-amber-900/20 px-3 py-2 text-xs text-amber-200" data-testid="scan-export-warning">
          <p className="font-medium">⚠ {t.scan_export_card_warn}</p>
          <ul className="mt-1 list-disc pl-4 space-y-0.5 text-amber-100/90">
            {exportProblems.includes("no_card") && <li>{t.scan_export_no_card}</li>}
            {exportProblems.includes("no_original") && <li>{t.scan_export_no_original}</li>}
          </ul>
        </div>
      )}
      <input ref={fileRef} type="file" accept="image/*" className="hidden" onChange={handleImageUpload} />
      <input ref={cameraRef} type="file" accept="image/*" capture="environment" className="hidden" onChange={handleImageUpload} />
      <div className="mt-2 grid grid-cols-2 gap-1">
        <button type="button"
          onClick={() => cameraRef.current?.click()}
          className="text-sm bg-gray-800 text-gray-300 hover:text-white rounded px-3 py-1.5"
        >
          {t.detail_take_photo}
        </button>
        <button type="button"
          onClick={() => fileRef.current?.click()}
          className="text-sm bg-gray-800 text-gray-300 hover:text-white rounded px-3 py-1.5"
        >
          {card.bild_karte_pfad ? t.detail_replace_photo : t.detail_upload_photo}
        </button>
      </div>
      {check?.status === "low" && (
        <div className="mt-2 rounded border border-amber-700/60 bg-amber-900/20 px-3 py-2 text-xs text-amber-200" data-testid="weak-scan-warning">
          ⚠ {t.weak_card_warn(check.inliers, check.inliers >= 12 ? check.corner_err : null)}{check.suggested_quad ? ` ${t.weak_card_suggest}` : ""}
        </div>
      )}
      {canRecrop ? (
        <button type="button"
          onClick={() => void handleFixCrop()}
          data-testid="fix-crop"
          className="w-full mt-1 text-sm bg-gray-800 text-pokemon-blue hover:text-white rounded px-3 py-1.5"
        >
          ✥ {t.fix_crop}
        </button>
      ) : card.bild_karte_pfad && (
        <button type="button"
          onClick={() => void handleEditExisting()}
          className="w-full mt-1 text-sm bg-gray-800 text-pokemon-blue hover:text-white rounded px-3 py-1.5"
        >
          ✥ {t.detail_edit_photo}
        </button>
      )}
      {card.prize_pack && stampPhoto && (
        <button type="button" onClick={() => setStampOpen(true)} data-testid="stamp-mark"
          className="w-full mt-1 text-sm bg-gray-800 text-gray-300 hover:text-white rounded px-3 py-1.5">
          🎯 {card.stamp_quad ? `${t.stamp_marked} ✓` : t.stamp_mark}
        </button>
      )}
      {stampOpen && stampPhoto && (
        <StampEditor cardId={card.id} photo={stampPhoto} version={card.aktualisiert_am} initialQuad={card.stamp_quad}
          onSaved={onCardUpdated} onClose={() => setStampOpen(false)} />
      )}
      {!card.bild_karte_pfad && (
        <button type="button"
          onClick={() => { setShowUrlInput((v) => !v); setUrlInput(card.bild_pokedex_url ?? ""); }}
          className="w-full mt-1 text-sm bg-gray-800 text-gray-300 hover:text-white rounded px-3 py-1.5"
        >
          {card.bild_pokedex_url ? t.detail_change_url : t.detail_set_url}
        </button>
      )}
      {showUrlInput && (
        <div className="mt-1 flex gap-1">
          <input
            type="url"
            value={urlInput}
            onChange={(e) => setUrlInput(e.target.value)}
            placeholder="https://…"
            className="flex-1 min-w-0 bg-gray-900 border border-gray-600 rounded px-2 py-1 text-white text-xs"
            onKeyDown={(e) => e.key === "Enter" && handleSaveUrl()}
          />
          <button type="button" onClick={handleSaveUrl} className="text-xs bg-green-700 text-white rounded px-2 py-1 hover:bg-green-600">OK</button>
        </div>
      )}
      {/* #120 (Owner-Entscheid A): nur drei Bild-Hosts werden angezeigt */}
      {showUrlInput && <p className="mt-1 text-[11px] text-gray-500">{t.detail_url_hosts}</p>}
      {card.bild_pokedex_url && !card.bild_pokedex_spiegel && (
        <p className="mt-1 text-[11px] text-amber-400">{t.detail_url_not_shown}</p>
      )}
      {(card.bild_karte_pfad || card.bild_pokedex_url) && (
        <button type="button"
          onClick={card.bild_karte_pfad ? handleDeleteImage : handleClearPokedexUrl}
          className="w-full mt-1 text-sm bg-red-950 text-red-400 hover:text-red-200 rounded px-3 py-1.5"
        >
          {card.bild_karte_pfad ? t.detail_delete_photo : t.detail_delete_url}
        </button>
      )}

      {editorUrl && (
        <CornerEditor
          imageUrl={editorUrl}
          initialQuad={editorInitQuad}
          onCancel={closePhotoEditor}
          onApply={(quad, transform) => { if (!uploadingPhoto) void handleApplyPhoto(quad, transform); }}
          suggest={editorSuggests ? suggestCorners : undefined}
        />
      )}
    </div>
  );
}
