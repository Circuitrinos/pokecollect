"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import toast from "react-hot-toast";
import {
  Collection, DeckSummary, ScanCandidate, ScanCommitItem, ScanMode, ScanStatus,
  cardApi, collectionApi, deckApi, scanApi,
  ScanAlternative,
} from "@/lib/api";
import { useEnums } from "@/lib/useEnums";
import { useSets } from "@/lib/useSets";
import { useSettings } from "@/lib/useSettings";
import SetPicker from "@/components/SetPicker";
import RaritySelect from "@/components/RaritySelect";
import CornerEditor from "@/components/CornerEditor";
import SlotPicker, { pageSizeOf } from "@/components/SlotPicker";
import { cropToCardPhoto, loadImg, normalizeOrientation, CropTransform } from "@/lib/cardCrop";
import { defaultCardLang, setCodeFromEdition } from "@/lib/utils";
import { findCardInGuide, guideAspect, guideRect, lockMovement, toGray, type Lock, type Pt } from "@/lib/cardLock";
import { safeReturnPath } from "@/lib/navRegistry";
import { useI18n } from "@/lib/i18n";
import { valueLabel } from "@/lib/enumLabels";
import { IMAGE_LANGS, imageLangOf, withImageLang } from "@/lib/imageLang";

type Step = "setup" | "review";
type ScanTarget = "pokedex" | "collection" | "wishlist" | "deck";

type EditableCandidate = ScanCandidate & {
  include: boolean;
  usePhoto: boolean;        // eigenes Foto als Kartenbild (true) vs. Katalogbild (false; Foto lernt trotzdem mit)
  cropUrl: string | null;   // Vorschau des zugeschnittenen eigenen Fotos
  imPokedex: boolean;       // je Karte: in den Pokédex
  prizePack: boolean;       // Play!-Pokémon-Stempel (Prize Pack) — vom Nutzer angehakt
  copies: number;           // so viele gleiche Exemplare anlegen (alle mit demselben Bild)
  editedQuad?: number[][] | null;  // manuell korrigierte Ecken (normiert 0..1, TL,TR,BR,BL)
  editedOrient?: CropTransform;    // manuelle Flip/Rotate-Korrektur
  chosenId?: string | null;        // im Picker gewählte Karte (Bild-Abgleich)
  rematching?: boolean;            // nach dem Ecken-Editor: Abgleich mit dem neuen Zuschnitt läuft
  key: string;                     // stabile ID — die Liste ändert sich (verwerfen, nachtragen, Exemplare)
  imageLang?: string | null;       // Bildsprache des Katalogbilds (null = wie geliefert)
  variantOf?: string | null;       // weiteres Exemplar mit eigenen Einstellungen (kein eigenes Foto im Bild)
  added?: boolean;                 // vom Nutzer nachgetragene Karte (die Erkennung hatte sie übersehen)
  saving?: boolean;
  saved?: { ids: number[]; name: string } | null;   // schon einzeln gespeichert
};

// Katalogbild in der gewählten Bildsprache (TCGdex, westliche Karten)
const matchImage = (c: EditableCandidate): string | null =>
  (c.imageLang ? withImageLang(c.match?.image_url, c.imageLang) : null) ?? c.match?.image_url ?? null;

// TCGdex liefert jede Karte auch klein (/low.webp) – reicht für Picker-Vorschauen.
function thumbUrl(url: string | null): string | null {
  return url ? url.replace(/\/high\.(webp|png|jpg)$/, "/low.$1") : null;
}

const LANGS = ["DE", "EN", "CN", "JP", "FR", "ES", "IT"];

function parseLayout(layout: string): { cols: number; rows: number } {
  const m = layout.match(/^(\d+)x(\d+)$/);
  if (!m) return { cols: 3, rows: 3 };
  return { cols: Number(m[1]), rows: Number(m[2]) };
}

/** Verkleinert das Bild vor dem Upload (schnellere Analyse), max. Kantenlänge maxDim. */
async function downscaleImage(blob: Blob, maxDim = 1600): Promise<Blob> {
  const url = URL.createObjectURL(blob);
  try {
    const img = await loadImg(url);
    const longest = Math.max(img.width, img.height);
    if (longest <= maxDim) return blob;
    const scale = maxDim / longest;
    const canvas = document.createElement("canvas");
    canvas.width = Math.round(img.width * scale);
    canvas.height = Math.round(img.height * scale);
    const ctx = canvas.getContext("2d")!;
    ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
    const out = await new Promise<Blob | null>((res) => canvas.toBlob(res, "image/jpeg", 0.88));
    return out ?? blob;
  } catch {
    return blob;
  } finally {
    URL.revokeObjectURL(url);
  }
}

export default function ScanPage() {
  const { t, lang } = useI18n();
  const router = useRouter();
  const [status, setStatus] = useState<ScanStatus | null>(null);
  const { enums } = useEnums();
  const [collections, setCollections] = useState<Collection[]>([]);
  const { sets, refresh: refreshSets } = useSets();
  const { settings: appSettings } = useSettings();

  const [mode, setMode] = useState<ScanMode>("single");
  const [target, setTarget] = useState<ScanTarget>("pokedex");
  const [collectionId, setCollectionId] = useState<number | null>(null);
  // Ziel „Deck": Karten besessen + in dieses Deck (die Deckliste wächst mit)
  const [decks, setDecks] = useState<DeckSummary[]>([]);
  const [deckId, setDeckId] = useState<number | null>(null);
  // Seite, von der aus gescannt wurde (/scan?from=…): „Zurück" führt dorthin.
  // Von einer Sammlungsseite (…&collection=<id>) ist diese Sammlung das Ziel.
  const [returnTo, setReturnTo] = useState<string | null>(null);
  // Gewähltes Fach in der Sammlung (über alle Seiten gezählt, 0-basiert);
  // null = erstes freies Fach bzw. beim Mappenseiten-Scan eine neue Seite.
  const [slot, setSlot] = useState<number | null>(null);
  const [slotRefresh, setSlotRefresh] = useState(0);
  const [layout, setLayout] = useState("3x3");
  const [setPokedexRep, setSetPokedexRep] = useState(false);
  const [priority, setPriority] = useState("");  // für Wunschkarten-Scan

  const [step, setStep] = useState<Step>("setup");
  // Standard-Sprache aus den (geteilten) Einstellungen — Issue #14
  const defaultLang = defaultCardLang(appSettings, lang);
  const [busy, setBusy] = useState(false);
  const [candidates, setCandidates] = useState<EditableCandidate[]>([]);
  const [sourceUrl, setSourceUrl] = useState<string | null>(null);
  const [editorIdx, setEditorIdx] = useState<number | null>(null);
  const sourceBlobRef = useRef<Blob | null>(null);
  const keySeq = useRef(0);
  const nextKey = () => `c${++keySeq.current}`;
  // Async-Ergebnisse per Schlüssel zuordnen (der Index kann sich inzwischen verschoben haben)
  const patchCand = (key: string, fn: (c: EditableCandidate) => EditableCandidate) =>
    setCandidates((prev) => prev.map((c) => (c.key === key ? fn(c) : c)));
  const [addingMissed, setAddingMissed] = useState(false);
  const [camMsg, setCamMsg] = useState<string | null>(null);
  const [autoCapture, setAutoCapture] = useState(true);
  const [vidSize, setVidSize] = useState<{ w: number; h: number } | null>(null);
  const [lockQuad, setLockQuad] = useState<Pt[] | null>(null);   // card found in the guide (video px)
  const [lockSteady, setLockSteady] = useState(false);
  // Nach einer Aufnahme bleibt die Kamera an; die Auto-Aufnahme wartet, bis
  // die gescannte Karte aus dem Rahmen ist (sonst nähme sie dieselbe nochmal).
  const needClearRef = useRef(false);
  const [waitingNext, setWaitingNext] = useState(false);
  // Mehrere Karten / Mappenseite: Umrisse, die der Bild-Abgleich im Live-Bild findet
  const [detQuads, setDetQuads] = useState<Pt[][] | null>(null);
  const busyRef = useRef(false);
  useEffect(() => { busyRef.current = busy; }, [busy]);

  // Kamera (nur in sicherem Kontext verfügbar – sonst Datei-Fallback)
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const [cameraOn, setCameraOn] = useState(false);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    setReturnTo(safeReturnPath(params.get("from")));
    const fromId = Number(params.get("collection"));
    const preset = Number.isInteger(fromId) && fromId > 0 ? fromId : null;
    const fromDeck = Number(params.get("deck"));
    const presetDeck = Number.isInteger(fromDeck) && fromDeck > 0 ? fromDeck : null;
    if (presetDeck) {
      setTarget("deck");
      setDeckId(presetDeck);
    }
    if (preset) {
      setTarget("collection");
      setCollectionId(preset);
      const presetSlot = Number(params.get("slot"));
      if (params.get("slot") !== null && Number.isInteger(presetSlot) && presetSlot >= 0) setSlot(presetSlot);
    }
    scanApi.status().then((r) => setStatus(r.data)).catch(() => {});
    deckApi.list().then((r) => {
      setDecks(r.data);
      if (presetDeck && !r.data.some((d) => d.id === presetDeck)) {
        setTarget("pokedex");
        setDeckId(null);
      }
    }).catch(() => {});
    collectionApi.list().then((r) => {
      setCollections(r.data);
      if (preset && !r.data.some((c) => c.id === preset)) {  // gelöscht/fremd → normaler Standard
        setTarget("pokedex");
        setCollectionId(null);
        setSlot(null);
      }
    }).catch(() => {});
    return () => stopCamera();
  }, []);
  const backHref = returnTo ?? "/";

  // Bild-Abgleich lädt nach einem Neustart eine Weile: Status nachfragen, bis er bereit ist.
  const localState = status?.provider === "local" ? status.local?.status ?? "unreachable" : null;
  useEffect(() => {
    if (!localState || localState === "ready" || localState === "error") return;
    const id = window.setTimeout(() => {
      scanApi.status().then((r) => setStatus(r.data)).catch(() => {});
    }, 5000);
    return () => window.clearTimeout(id);
  }, [localState, status]);

  const targetCollection = target === "collection" && collectionId
    ? collections.find((x) => x.id === collectionId) ?? null : null;

  // Layout aus gewählter Sammlung übernehmen
  useEffect(() => {
    if (target === "collection" && collectionId) {
      const c = collections.find((x) => x.id === collectionId);
      if (c?.binder_layout) setLayout(c.binder_layout);
    }
  }, [target, collectionId, collections]);

  const stopCamera = () => {
    streamRef.current?.getTracks().forEach((tr) => tr.stop());
    streamRef.current = null;
    setCameraOn(false);
  };

  const startCamera = async () => {
    setCamMsg(null);
    if (!navigator.mediaDevices?.getUserMedia) {
      // Unsicherer Kontext (HTTP über LAN) → getUserMedia ist gesperrt.
      setCamMsg(t.scan_camera_insecure);
      return;
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: { ideal: "environment" }, width: { ideal: 1920 }, height: { ideal: 1080 } },
        audio: false,
      });
      streamRef.current = stream;
      setCameraOn(true); // Video wird erst jetzt gerendert → Stream im Effect anhängen
    } catch {
      setCamMsg(t.scan_camera_insecure);
    }
  };

  // Stream an das Video hängen, sobald es im DOM ist (sonst bleibt das Bild schwarz)
  useEffect(() => {
    if (cameraOn && step === "setup" && videoRef.current && streamRef.current) {
      videoRef.current.srcObject = streamRef.current;
      videoRef.current.play().catch(() => {});
    }
  }, [cameraOn, step]);

  // Live-Umrisse aus dem Bild-Abgleich (mehrere Karten, Mappenseite): ~1–2 Bilder/s
  // klein an den Server, nacheinander (nie parallel). Ohne matcher-Dienst
  // (available=false) einfach keine Umrisse; erneuter Versuch nach 5 s.
  useEffect(() => {
    setDetQuads(null);
    if (!cameraOn || step !== "setup" || mode === "single") return;
    let stop = false;
    const canvas = document.createElement("canvas");
    const ctx = canvas.getContext("2d");
    const sleep = (ms: number) => new Promise((r) => window.setTimeout(r, ms));
    (async () => {
      while (!stop) {
        const v = videoRef.current;
        if (!ctx || !v || v.videoWidth === 0 || busyRef.current) { await sleep(400); continue; }
        const vw = v.videoWidth, vh = v.videoHeight;
        const k = Math.min(1, 1280 / Math.max(vw, vh));
        canvas.width = Math.round(vw * k);
        canvas.height = Math.round(vh * k);
        ctx.drawImage(v, 0, 0, canvas.width, canvas.height);
        const frame = await new Promise<Blob | null>((res) => canvas.toBlob(res, "image/jpeg", 0.7));
        const t0 = performance.now();
        try {
          if (!frame) throw new Error("no frame");
          const r = await scanApi.detect(frame);
          if (stop) break;
          if (!r.data.available) { setDetQuads(null); await sleep(5000); continue; }
          setDetQuads(r.data.cards.map((c) => c.quad.map(([x, y]) => [x * vw, y * vh] as Pt)));
        } catch {
          await sleep(3000);
          continue;
        }
        await sleep(Math.max(0, 650 - (performance.now() - t0)));
      }
    })();
    return () => { stop = true; };
  }, [cameraOn, step, mode]);

  // Rahmen in Kartenform (Einzelkarte) bzw. Seitenform (Mappe); bei losen Karten keiner.
  const { cols: layoutCols, rows: layoutRows } = parseLayout(layout);
  const gAspect = guideAspect(mode, layoutCols, layoutRows);
  const handleCaptureRef = useRef<() => void>(() => {});

  // Kartensuche im Live-Bild: ~7×/s vier gerade Kanten am Rahmen (Karte bzw.
  // Kartenraster liegt drin) → grüne Kontur, auch ohne Auto-Aufnahme. Mit
  // Auto-Aufnahme löst sie aus, sobald der Fund ~½ s ruhig steht.
  // Verwacklungstolerant; nur der Auslöser – die Erkennung macht der Server.
  useEffect(() => {
    setLockQuad(null);
    setLockSteady(false);
    if (!cameraOn || gAspect === null || step !== "setup") return;
    const canvas = document.createElement("canvas");
    const ctx = canvas.getContext("2d", { willReadFrequently: true });
    if (!ctx) return;
    let prev: Lock | null = null;
    let steady = 0;
    let clear = 0;
    const id = window.setInterval(() => {
      const v = videoRef.current;
      if (!v || v.videoWidth === 0 || busyRef.current) return;
      const k = 360 / Math.max(v.videoWidth, v.videoHeight);
      const w = Math.round(v.videoWidth * k), h = Math.round(v.videoHeight * k);
      if (canvas.width !== w || canvas.height !== h) { canvas.width = w; canvas.height = h; }
      ctx.drawImage(v, 0, 0, w, h);
      const guide = guideRect(w, h, gAspect);
      const lock = findCardInGuide(toGray(ctx.getImageData(0, 0, w, h).data, w, h), w, h, guide);
      steady = lock && prev && lockMovement(prev, lock, guide) <= 0.022 ? steady + 1 : 0;
      prev = lock;
      setLockQuad(lock ? lock.quad.map(([x, y]) => [x / k, y / k] as Pt) : null);
      setLockSteady(steady > 0);
      if (!autoCapture) return;             // nur anzeigen
      if (needClearRef.current) {           // erst die vorige Karte aus dem Rahmen nehmen
        clear = lock ? 0 : clear + 1;
        if (clear >= 2) { needClearRef.current = false; setWaitingNext(false); }
        return;
      }
      if (steady >= 3) {
        steady = 0;
        prev = null;
        handleCaptureRef.current();
      }
    }, 140);
    return () => window.clearInterval(id);
  }, [cameraOn, autoCapture, gAspect, step]);

  const blobFromVideo = (): Promise<Blob | null> =>
    new Promise((resolve) => {
      const v = videoRef.current;
      if (!v) return resolve(null);
      const canvas = document.createElement("canvas");
      canvas.width = v.videoWidth;
      canvas.height = v.videoHeight;
      const ctx = canvas.getContext("2d");
      if (!ctx) return resolve(null);
      ctx.drawImage(v, 0, 0, canvas.width, canvas.height);
      canvas.toBlob((b) => resolve(b), "image/jpeg", 0.9);
    });

  const runScan = useCallback(async (rawBlob: Blob) => {
    setBusy(true);
    try {
      // EXIF-Orientierung fest einbacken (Anzeige == Zuschnitt) und vor dem
      // Senden verkleinern → schnellere Analyse. bbox/quad beziehen sich auf
      // genau dieses Bild, daher merken wir es als Quelle für die Zuschnitte.
      const blob = await downscaleImage(await normalizeOrientation(rawBlob));
      sourceBlobRef.current = blob;
      setSourceUrl((prev) => {
        if (prev) URL.revokeObjectURL(prev);
        return URL.createObjectURL(blob);
      });
      const { cols, rows } = parseLayout(layout);
      const res = await scanApi.scan(blob, {
        mode,
        rows: mode === "binder" ? rows : 0,
        cols: mode === "binder" ? cols : 0,
        default_language: defaultLang,
      });
      const list: EditableCandidate[] = res.data.candidates.map((c) => ({
        ...c,
        include: !c.needs_choice,   // ohne eindeutigen Treffer erst nach der Auswahl übernehmen
        // Erkannt → das saubere Katalogbild anzeigen; das Foto wird trotzdem
        // gespeichert, damit der Bild-Abgleich daraus lernt. Ohne Treffer bleibt
        // das Foto das einzige Bild.
        usePhoto: !!(c.raw?.bbox || c.raw?.quad) && !c.match?.image_url,
        cropUrl: null,
        imPokedex: setPokedexRep,
        prizePack: false,
        copies: 1,
        key: nextKey(),
        imageLang: null,
      }));
      const art = res.data.hinweis_art;
      if (art) {
        const msg =
          art === "key_ungueltig" ? t.scan_fehler_key
          : art === "rate_limit" ? t.scan_fehler_rate
          : art === "gemini_fehler" ? t.scan_fehler_gemini
          : art === "reader_fehler" ? t.scan_fehler_reader
          : art === "bild_abgleich_startet" ? t.scan_fehler_local_starting
          : t.scan_limit_fallback; // tageslimit
        toast(msg, { icon: art === "key_ungueltig" ? "🔑" : "⚠️" });
      } else if (res.data.limit_erreicht) {
        toast(t.scan_limit_fallback, { icon: "⚠️" });
      }
      if (!list.length) {
        toast.error(t.scan_no_results);
        return;
      }
      setCandidates(list);
      setStep("review");
      // Foto-Zuschnitte je Karte (aus der bbox) für Vorschau erzeugen
      list.forEach(async (c) => {
        const blob = sourceBlobRef.current;
        if ((!c.raw?.bbox && !c.raw?.quad) || !blob) return;
        try {
          const f = await cropToCardPhoto(blob, { bbox: c.raw.bbox, quad: c.raw.quad, autoOrient: true });
          const u = URL.createObjectURL(f);
          patchCand(c.key, (cc) => ({ ...cc, cropUrl: u }));
        } catch { /* Vorschau optional */ }
      });
    } catch {
      toast.error(t.scan_error);
    } finally {
      setBusy(false);
    }
  }, [layout, mode, t, setPokedexRep, defaultLang]);

  const handleCapture = async () => {
    const blob = await blobFromVideo();
    // Kamera läuft weiter (Serienscan); Auto-Aufnahme erst nach Kartenwechsel wieder scharf.
    needClearRef.current = true;
    setWaitingNext(true);
    if (blob) runScan(blob);
  };
  handleCaptureRef.current = () => { void handleCapture(); };  // Auto-Aufnahme nutzt immer die aktuelle Fassung

  const handleFile = (e: React.ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0];
    if (f) runScan(f);
    e.target.value = "";
  };

  const toggleUsePhoto = (idx: number) =>
    setCandidates((prev) => prev.map((c, i) => (i === idx ? { ...c, usePhoto: !c.usePhoto } : c)));
  const togglePokedex = (idx: number) =>
    setCandidates((prev) => prev.map((c, i) => (i === idx ? { ...c, imPokedex: !c.imPokedex } : c)));
  const setCopies = (idx: number, n: number) =>
    setCandidates((prev) => prev.map((c, i) => (i === idx ? { ...c, copies: Math.min(50, Math.max(1, n)) } : c)));
  const togglePrizePack = (idx: number) =>
    setCandidates((prev) => prev.map((c, i) => (i === idx ? { ...c, prizePack: !c.prizePack } : c)));

  const updateField = (idx: number, key: string, value: unknown) => {
    setCandidates((prev) =>
      prev.map((c, i) => (i === idx ? { ...c, suggested: { ...c.suggested, [key]: value } } : c))
    );
  };
  const toggleInclude = (idx: number) =>
    setCandidates((prev) => prev.map((c, i) => (i === idx ? { ...c, include: !c.include } : c)));

  // Start-Viereck für den Eck-Editor: korrigierte Ecken › erkannte Ecken ›
  // Bounding-Box › grober Default.
  const initialQuadFor = (c: EditableCandidate): number[][] => {
    if (c.editedQuad && c.editedQuad.length === 4) return c.editedQuad;
    if (c.raw?.quad && c.raw.quad.length === 4) return c.raw.quad;
    const b = c.raw?.bbox;
    if (b && b.length === 4) {
      const [x, y, w, h] = b;
      return [[x, y], [x + w, y], [x + w, y + h], [x, y + h]];
    }
    return [[0.15, 0.1], [0.85, 0.1], [0.85, 0.9], [0.15, 0.9]];
  };

  // Crop-Optionen je Karte: manuelle Ecken (mit Flip/Rotate) bevorzugt, sonst
  // die Auto-Erkennung (Gemini) mit Eck-Sortierung nach Bildposition.
  const cropOptsFor = (c: EditableCandidate) =>
    c.editedQuad
      ? { quad: c.editedQuad, ...(c.editedOrient ?? {}) }
      : { bbox: c.raw?.bbox, quad: c.raw?.quad, autoOrient: true };

  // Manuell gesetzte Ecken/Ausrichtung übernehmen → Foto neu entzerren.
  const applyCorners = async (idx: number, quad: number[][], transform: CropTransform) => {
    setEditorIdx(null);
    const blob = sourceBlobRef.current;
    const key = candidates[idx]?.key;
    if (!blob || !key) return;
    try {
      const f = await cropToCardPhoto(blob, { quad, ...transform });
      const u = URL.createObjectURL(f);
      patchCand(key, (cc) => ({ ...cc, editedQuad: quad, editedOrient: transform, cropUrl: u,
        usePhoto: cc.usePhoto || !cc.match?.image_url, rematching: true }));
      void rematch(key, f);
    } catch { /* Entzerrung optional */ }
  };

  // Neue Ecken = neuer Zuschnitt → die Karte damit neu abgleichen (Modus „card":
  // der Bild-Abgleich überspringt die Erkennung). Treffer, Alternativen und
  // Auswahlbedarf kommen neu; Ecken, Foto-Wahl, Anzahl, Prize Pack bleiben.
  const rematch = async (key: string, crop: Blob) => {
    const before = candidates.find((c) => c.key === key)?.match?.tcgdex_card_id;
    try {
      const res = await scanApi.scan(crop, { mode: "card", default_language: defaultLang });
      const n = res.data.candidates[0];
      setCandidates((prev) => prev.map((c) => {
        if (c.key !== key) return c;
        if (!n) return { ...c, rematching: false };
        return {
          ...c, ...n,
          raw: { ...n.raw, quad: c.raw?.quad ?? null, bbox: c.raw?.bbox ?? null, position: c.raw?.position ?? null },
          position: c.position,
          include: !n.needs_choice,
          chosenId: null,
          usePhoto: c.usePhoto || !n.match?.image_url,
          rematching: false,
        };
      }));
      if (!n) toast(t.scan_rematch_none, { icon: "⚠️" });
      else if (n.match?.tcgdex_card_id && n.match.tcgdex_card_id === before && !n.needs_choice) toast.success(t.scan_rematch_same);
      else toast.success(t.scan_rematched(String((n.suggested as Record<string, unknown>)?.kartenname ?? n.raw.name ?? "?")));
    } catch {
      patchCand(key, (c) => ({ ...c, rematching: false }));
      toast.error(t.scan_error);
    }
  };

  // ↻ Diese eine Karte neu erkennen (gleicher Zuschnitt; vorher ggf. Ecken korrigieren)
  const rescan = async (idx: number) => {
    const c = candidates[idx];
    const blob = sourceBlobRef.current;
    if (!c || !blob) return;
    patchCand(c.key, (cc) => ({ ...cc, rematching: true }));
    try {
      const hasCrop = !!(c.editedQuad || c.raw?.quad || c.raw?.bbox);
      const crop = hasCrop ? await cropToCardPhoto(blob, cropOptsFor(c)) : blob;
      await rematch(c.key, crop);
    } catch {
      patchCand(c.key, (cc) => ({ ...cc, rematching: false }));
      toast.error(t.scan_error);
    }
  };

  // ✕ Nur diese Karte verwerfen
  const discardOne = (idx: number) => {
    setEditorIdx(null);
    setCandidates((prev) => prev.filter((_, i) => i !== idx));
  };

  // Weiteres Exemplar mit eigenen Einstellungen (andere Folierung, anderer Druck
  // mit gleichem Namen/Artwork): eigener Eintrag direkt darunter. Steht die Anzahl
  // über 1, wandert eines dieser Exemplare in den neuen Eintrag.
  const addVariant = (idx: number) => setCandidates((prev) => {
    const c = prev[idx];
    if (!c) return prev;
    const v: EditableCandidate = {
      ...c, key: nextKey(), copies: 1, position: null, variantOf: c.variantOf ?? c.key,
      imPokedex: false, include: true, needs_choice: false, saving: false, saved: null, rematching: false,
      // das Foto zeigt die andere Karte → Katalogbild, sofern es eins gibt
      usePhoto: c.match?.image_url ? false : c.usePhoto,
    };
    const out = [...prev];
    if (c.copies > 1) out[idx] = { ...c, copies: c.copies - 1 };
    out.splice(idx + 1, 0, v);
    return out;
  });

  // Mappenseite: Fach einer nachgetragenen Karte aus ihrer Lage schätzen — Raster
  // aus den erkannten Karten (Spalte/Zeile ~ Mittelpunkt), sonst übers ganze Foto.
  const estimatePosition = (quad: number[][]): number | null => {
    if (mode !== "binder") return null;
    const { cols, rows } = parseLayout(layout);
    const center = (q: number[][]) => [q.reduce((a, p) => a + p[0], 0) / q.length, q.reduce((a, p) => a + p[1], 0) / q.length];
    const [cx, cy] = center(quad);
    const known = candidates
      .filter((c) => c.position != null && (c.editedQuad ?? c.raw?.quad))
      .map((c) => ({ p: c.position as number, xy: center((c.editedQuad ?? c.raw!.quad)!) }));
    const fit = (pts: { i: number; v: number }[], n: number, v: number) => {
      const distinct = new Set(pts.map((q) => q.i));
      if (distinct.size < 2) return Math.min(n - 1, Math.max(0, Math.floor(v * n)));
      const mi = pts.reduce((a, q) => a + q.i, 0) / pts.length, mv = pts.reduce((a, q) => a + q.v, 0) / pts.length;
      const b = pts.reduce((a, q) => a + (q.i - mi) * (q.v - mv), 0) / pts.reduce((a, q) => a + (q.i - mi) ** 2, 0);
      return Math.min(n - 1, Math.max(0, Math.round(mi + (v - mv) / (b || 1))));
    };
    const col = fit(known.map((k) => ({ i: k.p % cols, v: k.xy[0] })), cols, cx);
    const row = fit(known.map((k) => ({ i: Math.floor(k.p / cols), v: k.xy[1] })), rows, cy);
    const pos = row * cols + col;
    return candidates.some((c) => c.position === pos) ? null : pos;
  };

  // + Übersehene Karte: im Foto umranden → zuschneiden → erkennen → neuer Eintrag
  const addMissedCard = async (quad: number[][], transform: CropTransform) => {
    setAddingMissed(false);
    const blob = sourceBlobRef.current;
    if (!blob) return;
    try {
      const f = await cropToCardPhoto(blob, { quad, ...transform });
      const key = nextKey();
      const position = estimatePosition(quad);
      const cand: EditableCandidate = {
        key, position, confidence: 0, uncertain_fields: ["match"],
        raw: { name: null, set_code: null, number: null, language: null, position, confidence: null, bbox: null, quad },
        match: null, suggested: { sprache: defaultLang }, foil_options: [], alternatives: [], needs_choice: false,
        include: false, usePhoto: true, cropUrl: URL.createObjectURL(f), imPokedex: setPokedexRep, prizePack: false,
        copies: 1, editedQuad: quad, editedOrient: transform, rematching: true, imageLang: null, added: true,
      };
      setCandidates((prev) => [...prev, cand]);
      await rematch(key, f);
    } catch {
      toast.error(t.scan_error);
    }
  };

  // Karte anhand der (geänderten) Set/Nummer neu auflösen → Live-Bild + Treffer.
  // Behält die Nutzer-Auswahl für Set/Nummer/Sprache bei.
  const refreshCandidate = async (idx: number, overrides: Record<string, unknown> = {}) => {
    const c = candidates[idx];
    if (!c) return;
    const s = { ...(c.suggested as Record<string, unknown>), ...overrides };
    try {
      const r = await scanApi.resolve({
        name: String(s.kartenname ?? c.raw.name ?? ""),
        set_code: setCodeFromEdition(s.set_edition as string),
        number: (s.karten_nr as string) ?? null,
        language: (s.sprache as string) ?? "DE",
      });
      const nc = r.data;
      setCandidates((prev) => prev.map((cc) => cc.key === c.key ? {
        ...cc,
        match: nc.match,
        foil_options: nc.foil_options,
        uncertain_fields: nc.uncertain_fields,
        confidence: nc.confidence,
        suggested: {
          ...nc.suggested,
          // Nutzer-Eingaben behalten
          set_edition: s.set_edition,
          karten_nr: s.karten_nr,
          sprache: s.sprache,
          muster: s.muster,   // gewähltes Muster überlebt das Re-Resolve (#63)
          // manuell gesetzte Pokédex-Nr. nicht verlieren, wenn die Auflösung keine findet
          pokedex_nr: (nc.suggested as Record<string, unknown>).pokedex_nr ?? s.pokedex_nr,
        },
      } : cc));
    } catch { /* Live-Auflösung ist optional */ }
  };

  // Bild-Abgleich: Nutzer wählt einen der Treffer → Karte direkt per ID auflösen.
  const pickAlternative = async (idx: number, alt: ScanAlternative) => {
    const c = candidates[idx];
    if (!c) return;
    const s = c.suggested as Record<string, unknown>;
    try {
      const r = await scanApi.resolve({
        tcgdex_card_id: alt.tcgdex_card_id,
        name: alt.name,
        language: (s.sprache as string) ?? defaultLang,
      });
      const nc = r.data;
      setCandidates((prev) => prev.map((cc) => cc.key === c.key ? {
        ...cc,
        match: nc.match,
        foil_options: nc.foil_options,
        uncertain_fields: nc.uncertain_fields,
        confidence: nc.confidence,
        needs_choice: false,
        include: true,
        chosenId: alt.tcgdex_card_id,
        suggested: { ...nc.suggested, sprache: s.sprache ?? nc.suggested.sprache, muster: s.muster },
      } : cc));
    } catch {
      toast.error(t.scan_error);
    }
  };

  // „Keine davon": Treffer verwerfen, Felder bleiben zum manuellen Korrigieren.
  const chooseNone = (idx: number) =>
    setCandidates((prev) => prev.map((c, i) => i === idx ? {
      ...c, needs_choice: false, include: true, chosenId: null, match: null, uncertain_fields: ["match"],
    } : c));

  // Karten anlegen und ihre Fotos hochladen (alle offenen oder eine einzelne).
  const commitItems = async (chosen: EditableCandidate[]) => {
    const items: ScanCommitItem[] = chosen.map((c) => {
      const s = c.suggested as Record<string, unknown>;
      return {
        kartenname: String(s.kartenname ?? c.raw.name ?? "?"),
        pokedex_nr: (s.pokedex_nr as number) ?? c.match?.dex_id ?? null,
        englischer_name: (s.englischer_name as string) ?? c.match?.englischer_name ?? null,
        set_edition: (s.set_edition as string) ?? null,
        karten_nr: (s.karten_nr as string) ?? null,
        seltenheit: (s.seltenheit as string) ?? c.match?.rarity ?? null,
        folierung: (s.folierung as string) ?? null,
        muster: (s.muster as string) || null,
        prize_pack: c.prizePack,
        sprache: (s.sprache as string) ?? defaultLang,
        tcgdex_card_id: c.match?.tcgdex_card_id ?? null,
        set_id: c.match?.set_id ?? null,
        dex_id: c.match?.dex_id ?? (s.dex_id as number) ?? null,
        // API-Bild (in der gewählten Bildsprache) nur, wenn der Nutzer NICHT sein eigenes Foto will
        bild_karte_url: c.usePhoto ? null : matchImage(c),
        position: c.position ?? null,
        im_pokedex: target !== "wishlist" && c.imPokedex,
        prioritaet: target === "wishlist" ? (priority || null) : null,
        copies: target === "wishlist" ? 1 : c.copies,
      };
    });
    const ps = pageSizeOf(targetCollection?.binder_layout);
    const res = await scanApi.commit({
      target,
      collection_id: target === "collection" ? collectionId : null,
      deck_id: target === "deck" ? deckId : null,
      start_position: target === "collection" && slot != null
        ? (mode === "binder" ? Math.floor(slot / ps) * ps : slot) : null,
      items,
    });
    // Aufnahme je Karte zuschneiden und hochladen: mit „eigenes Foto" als
    // Kartenbild (Anzeige-Vorrang), sonst nur als Scan-Foto für den Bild-
    // Abgleich (die Karte zeigt das Katalogbild). Das erste Exemplar bekommt
    // Zuschnitt + Original (lernt mit); weitere Exemplare mit „eigenes Foto"
    // nur den Zuschnitt — sonst zeigten sie den Platzhalter, wenn es kein
    // Katalogbild gibt. Ein „Exemplar mit anderen Angaben" ist eine andere
    // Karte als die im Foto: kein Original (wäre ein falsches Trainingsbeispiel).
    const ids = res.data.card_ids;
    const copyIds = res.data.copy_ids ?? [];
    await Promise.all(chosen.map(async (c, k) => {
      const blob = sourceBlobRef.current;
      const hasCrop = !!(c.editedQuad || c.raw?.quad || c.raw?.bbox);
      if (!blob || ids[k] == null || (!c.usePhoto && !hasCrop)) return;
      if (c.variantOf && !c.usePhoto) return;
      try {
        const file = await cropToCardPhoto(blob, cropOptsFor(c));
        if (c.variantOf) {
          for (const id of copyIds[k]?.length ? copyIds[k] : [ids[k]]) await cardApi.uploadImage(id, file).catch(() => {});
          return;
        }
        // Originalfoto (ganze Aufnahme) mitspeichern → spätere Nachbearbeitung
        const original = new File([blob], "orig.jpg", { type: "image/jpeg" });
        const b = c.raw?.bbox;
        const quad = c.editedQuad ?? c.raw?.quad
          ?? (b && b.length === 4 ? [[b[0], b[1]], [b[0] + b[2], b[1]], [b[0] + b[2], b[1] + b[3]], [b[0], b[1] + b[3]]] : null);
        if (c.usePhoto) await cardApi.uploadImage(ids[k], file, original, quad);
        else await cardApi.uploadScanPhoto(ids[k], file, original, quad);  // nur zum Lernen
        if (c.usePhoto) {
          for (const extra of (copyIds[k] ?? []).filter((id) => id !== ids[k])) {
            await cardApi.uploadImage(extra, file).catch(() => {});
          }
        }
      } catch { /* Foto-Upload optional */ }
    }));
    return res.data;
  };

  const finishReview = (next: boolean) => {
    if (next) {
      backToCapture();
    } else {
      stopCamera();
      router.push(backHref);
    }
  };

  const handleSave = async (next: boolean) => {
    const chosen = candidates.filter((c) => c.include && !c.saved);
    if (!chosen.length) { finishReview(next); return; }
    if (target === "deck" && !deckId) { toast.error(t.deck_scan_pick); return; }
    setBusy(true);
    try {
      const data = await commitItems(chosen);
      // Weiterscannen: nächstes freies Fach hinter dem letzten bzw. nächste Seite
      if (target === "collection") {
        const ps = pageSizeOf(targetCollection?.binder_layout);
        const placed = data.positions ?? [];
        if (slot != null && placed.length) {
          setSlot(mode === "binder" ? (Math.floor(Math.max(...placed) / ps) + 1) * ps : data.next_position ?? null);
        }
        setSlotRefresh((n) => n + 1);
      }
      toast.success(t.scan_saved(data.created));
      finishReview(next);
    } catch {
      toast.error(t.scan_save_error);
    } finally {
      setBusy(false);
    }
  };

  // ✓ Nur diese Karte speichern — die übrigen bleiben zur Prüfung stehen.
  const saveOne = async (idx: number) => {
    const c = candidates[idx];
    if (!c || c.saved || c.saving || c.rematching || c.needs_choice) return;
    if (target === "deck" && !deckId) { toast.error(t.deck_scan_pick); return; }
    patchCand(c.key, (cc) => ({ ...cc, saving: true }));
    try {
      const data = await commitItems([c]);
      if (target === "collection") {
        // Dieselbe Mappenseite bzw. Fachfolge für die restlichen Karten des Fotos
        const ps = pageSizeOf(targetCollection?.binder_layout);
        const placed = data.positions ?? [];
        if (mode === "binder" && slot == null && placed.length) setSlot(Math.floor(Math.min(...placed) / ps) * ps);
        else if (mode !== "binder" && slot != null && data.next_position != null) setSlot(data.next_position);
        setSlotRefresh((n) => n + 1);
      }
      const s = c.suggested as Record<string, unknown>;
      const name = String((lang === "EN" ? s.englischer_name : null) ?? s.kartenname ?? c.raw.name ?? "?");
      patchCand(c.key, (cc) => ({ ...cc, saving: false, saved: { ids: data.all_card_ids?.length ? data.all_card_ids : data.card_ids, name } }));
      toast.success(t.scan_saved(data.created));
    } catch {
      patchCand(c.key, (cc) => ({ ...cc, saving: false }));
      toast.error(t.scan_save_error);
    }
  };

  // Zurück zur Aufnahme (Kamera ist noch an, falls sie lief) – oben, ohne Scrollen.
  const backToCapture = () => {
    setCandidates([]);
    setStep("setup");
    window.scrollTo({ top: 0 });
  };

  // ── Render ────────────────────────────────────────────────────────────────
  const engineActive = status?.active ?? "none";

  return (
    <div className="max-w-3xl mx-auto pb-24">
      <div className="mb-1 flex items-center justify-between">
        <h1 className="text-xl font-bold text-white">{t.scan_title}</h1>
        <Link href={backHref} className="text-gray-500 hover:text-white text-sm">{t.scan_back}</Link>
      </div>
      <p className="hidden sm:block text-gray-400 text-sm mb-1">{t.scan_subtitle}</p>
      <p className={`text-xs ${localState && localState !== "ready" ? "mb-2" : "mb-3"}`}>
        {engineActive === "none"
          ? <span className="text-red-400">{t.scan_engine_none}</span>
          : <span className="text-gray-500">{t.scan_engine_active(engineActive)}</span>}
      </p>
      {localState && localState !== "ready" && (
        <p className="text-xs mb-3 rounded border border-amber-700/60 bg-amber-900/20 px-2 py-1.5 text-amber-300">
          {localState === "loading" || localState === "converting"
            ? t.scan_local_starting(status?.local?.phase)
            : t.scan_local_unreachable}
        </p>
      )}

      {targetCollection && (
        <div className="mb-3">
          <SlotPicker collection={targetCollection} value={slot} onChange={setSlot}
            pageMode={mode === "binder"} refreshKey={slotRefresh} />
        </div>
      )}

      {step === "setup" && (
        <div className="space-y-4">
          {/* Modus – kompakt, damit die Aufnahme ohne Scrollen sichtbar ist */}
          <div>
            <div className="grid grid-cols-3 gap-1 rounded-lg bg-gray-800/70 p-1" role="radiogroup" aria-label={t.scan_mode}>
              {([
                ["single", t.scan_mode_single, t.scan_mode_single_desc],
                ["multi", t.scan_mode_multi, t.scan_mode_multi_desc],
                ["binder", t.scan_mode_binder, t.scan_mode_binder_desc],
              ] as const).map(([m, label, desc]) => (
                <button type="button" key={m} role="radio" aria-checked={mode === m} title={desc}
                  onClick={() => setMode(m)}
                  className={`rounded-md px-2 py-1.5 text-sm transition ${
                    mode === m ? "bg-pokemon-blue text-white" : "text-gray-300 hover:bg-gray-700"
                  }`}
                >
                  {label}
                </button>
              ))}
            </div>
          </div>

          {/* Kamera / Datei */}
          <div className={`rounded-lg border border-gray-700 bg-pokemon-card ${cameraOn ? "p-2" : "p-4"}`}>
            {cameraOn ? (
              <div className="space-y-3">
                <div className="relative mx-auto overflow-hidden rounded"
                  style={{ maxWidth: vidSize ? `min(480px, calc(64vh * ${(vidSize.w / vidSize.h).toFixed(4)}))` : 480 }}>
                  <video ref={videoRef} autoPlay playsInline muted className="block w-full bg-black"
                    onLoadedMetadata={(e) => setVidSize({ w: e.currentTarget.videoWidth, h: e.currentTarget.videoHeight })}
                    onResize={(e) => setVidSize({ w: e.currentTarget.videoWidth, h: e.currentTarget.videoHeight })} />
                  {vidSize && (
                    <CameraGuide w={vidSize.w} h={vidSize.h} aspect={gAspect} cols={mode === "binder" ? layoutCols : 1}
                      rows={mode === "binder" ? layoutRows : 1} quad={lockQuad} steady={lockSteady} boxes={detQuads} />
                  )}
                  {vidSize && (() => {
                    const chip = busy ? t.scan_analyzing
                      : gAspect !== null && autoCapture ? (waitingNext ? t.scan_cam_next : lockQuad ? t.scan_cam_hold : t.scan_cam_searching)
                      : detQuads ? t.scan_cam_found(detQuads.length) : null;
                    return chip && (
                      <div className="pointer-events-none absolute top-2 left-1/2 -translate-x-1/2 whitespace-nowrap rounded-full bg-black/60 px-3 py-1 text-xs text-white">
                        {chip}
                      </div>
                    );
                  })()}
                  {/* Bedienung auf dem Bild (wie eine Kamera-App) – kein Scrollen nötig */}
                  <div className="absolute inset-x-0 bottom-0 grid grid-cols-3 items-center bg-gradient-to-t from-black/75 to-transparent px-3 pb-3 pt-10">
                    <button type="button" onClick={stopCamera}
                      className="justify-self-start rounded-full bg-black/50 px-3 py-1.5 text-xs text-white hover:bg-black/70">
                      {t.scan_stop_camera}
                    </button>
                    <button type="button" onClick={() => void handleCapture()} disabled={busy} aria-label={t.scan_capture}
                      title={t.scan_capture}
                      className="justify-self-center h-16 w-16 rounded-full border-4 border-white bg-white/25 shadow-lg transition hover:bg-white/40 active:scale-95 disabled:opacity-50">
                      {busy && <span className="block h-full w-full animate-pulse rounded-full bg-white/50" />}
                    </button>
                    {gAspect !== null ? (
                      <label className="justify-self-end flex items-center gap-1 rounded-full bg-black/50 px-3 py-1.5 text-xs text-white">
                        <input type="checkbox" checked={autoCapture} onChange={(e) => setAutoCapture(e.target.checked)} />
                        {t.scan_autocapture}
                      </label>
                    ) : <span />}
                  </div>
                </div>
                <p className="text-center text-gray-500 text-xs">
                  {mode === "single" ? t.scan_cam_hint_card : mode === "binder" ? t.scan_cam_hint_page : t.scan_cam_hint_multi}
                </p>
              </div>
            ) : (
              <div className="space-y-3 text-center">
                <input ref={fileInputRef} type="file" accept="image/*" capture="environment"
                  onChange={handleFile} className="hidden" />
                <button type="button" onClick={() => fileInputRef.current?.click()} disabled={busy || engineActive === "none"}
                  className="bg-pokemon-blue text-white px-5 py-3 rounded-lg hover:bg-blue-500 disabled:opacity-50 w-full sm:w-auto">
                  {busy ? t.scan_analyzing : t.scan_take_photo}
                </button>
                <div>
                  <button type="button" onClick={startCamera} disabled={engineActive === "none"}
                    className="text-pokemon-blue hover:underline text-sm disabled:opacity-50">
                    {t.scan_start_camera}
                  </button>
                </div>
                {camMsg && <p className="text-gray-400 text-xs whitespace-pre-line">{camMsg}</p>}
              </div>
            )}
          </div>
          {/* Ziel & Optionen (unter der Aufnahme) */}
          <div className="rounded-lg border border-gray-800 p-3 space-y-3">
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="text-gray-400 text-xs block mb-1">{t.scan_target}</label>
              <select
                value={target}
                onChange={(e) => setTarget(e.target.value as ScanTarget)}
                data-testid="scan-target"
                className="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1.5 text-white text-sm"
              >
                <option value="pokedex">{t.scan_target_pokedex}</option>
                <option value="collection">{t.scan_target_collection}</option>
                <option value="wishlist">{t.scan_target_wishlist}</option>
                <option value="deck">{t.scan_target_deck}</option>
              </select>
            </div>
            {target === "deck" && (
              <div>
                <label className="text-gray-400 text-xs block mb-1">{t.scan_target_deck}</label>
                <select
                  value={deckId ?? ""}
                  onChange={(e) => setDeckId(e.target.value ? Number(e.target.value) : null)}
                  className="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1.5 text-white text-sm"
                  data-testid="scan-deck"
                >
                  <option value="">–</option>
                  {decks.map((d) => (
                    <option key={d.id} value={d.id}>{d.name}</option>
                  ))}
                </select>
              </div>
            )}
            {target === "collection" && (
              <div>
                <label className="text-gray-400 text-xs block mb-1">{t.scan_target_collection}</label>
                <select
                  value={collectionId ?? ""}
                  onChange={(e) => { setCollectionId(e.target.value ? Number(e.target.value) : null); setSlot(null); }}
                  className="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1.5 text-white text-sm"
                >
                  <option value="">–</option>
                  {collections.map((c) => (
                    <option key={c.id} value={c.id}>{c.name}</option>
                  ))}
                </select>
              </div>
            )}
            {target === "wishlist" && (
              <div>
                <label className="text-gray-400 text-xs block mb-1">{t.form_priority}</label>
                <select
                  value={priority}
                  onChange={(e) => setPriority(e.target.value)}
                  className="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1.5 text-white text-sm"
                >
                  <option value="">–</option>
                  {(enums?.prioritaet ?? []).map((p) => (
                    <option key={p} value={p}>{valueLabel(p, lang)}</option>
                  ))}
                </select>
              </div>
            )}
            {mode === "binder" && (
              <div>
                <label className="text-gray-400 text-xs block mb-1">{t.scan_layout}</label>
                <input
                  type="text"
                  value={layout}
                  onChange={(e) => setLayout(e.target.value)}
                  placeholder="3x3"
                  className="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1.5 text-white text-sm font-mono"
                />
              </div>
            )}
          </div>

          {target !== "wishlist" && (
            <label className="flex items-center gap-2 text-sm text-white">
              <input type="checkbox" checked={setPokedexRep} onChange={(e) => setSetPokedexRep(e.target.checked)} />
              {t.scan_set_pokedex_rep} <span className="text-gray-500 text-xs">({t.scan_default_all})</span>
            </label>
          )}
          </div>
        </div>
      )}

      {step === "review" && (
        <div className="space-y-4">
          <div className="flex items-center justify-between">
            <h2 className="text-white font-medium">{t.scan_results_title(candidates.length)}</h2>
            <button type="button" onClick={backToCapture} className="text-gray-500 hover:text-white text-sm">{t.scan_discard_all}</button>
          </div>
          {sourceUrl && mode !== "single" && (
            <button type="button" onClick={() => setAddingMissed(true)} title={t.scan_add_missed_hint}
              data-testid="scan-add-missed"
              className="w-full rounded-lg border border-dashed border-gray-600 py-2 text-sm text-gray-300 hover:border-gray-400 hover:text-white">
              ＋ {t.scan_add_missed}
            </button>
          )}

          {candidates.map((c, idx) => {
            const s = c.suggested as Record<string, unknown>;
            const uncertain = c.uncertain_fields.length > 0;
            const foilOptions = c.foil_options.length ? c.foil_options : (enums?.folierung ?? ["Normal"]);
            const pokedexNr = (s.pokedex_nr as number) ?? c.match?.dex_id ?? null;
            const catalogImg = matchImage(c);
            const baseImgLang = imageLangOf(c.match?.image_url);
            if (c.saved) {
              return (
                <div key={c.key} data-testid="scan-card-saved"
                  className="flex items-center justify-between gap-2 rounded-lg border border-green-800 bg-green-900/20 px-3 py-2 text-sm">
                  <span className="text-green-300 truncate">✓ {t.scan_card_saved(c.saved.name, c.saved.ids.length)}</span>
                  <Link href={`/cards/${c.saved.ids[0]}`} className="shrink-0 text-xs text-pokemon-blue hover:underline">{t.scan_open_card}</Link>
                </div>
              );
            }
            return (
              <div key={c.key} data-testid="scan-card"
                className={`rounded-lg border p-3 ${
                  c.needs_choice ? "border-pokemon-yellow"
                  : !c.include ? "border-gray-800 opacity-50" : uncertain ? "border-pokemon-red" : "border-gray-700"
                } bg-pokemon-card`}>
                {/* Kopfzeile: Pokédex-Nr. + Übernehmen / Im Pokédex */}
                <div className="flex items-center justify-between gap-2 mb-2 flex-wrap">
                  <span className="text-xs text-gray-400">
                    {pokedexNr != null ? `#${String(pokedexNr).padStart(4, "0")}` : <span className="text-gray-600">{t.scan_no_dex}</span>}
                    {c.variantOf && <span className="ml-2 rounded bg-gray-700 px-1.5 py-0.5 text-[10px] text-gray-200">↳ {t.scan_variant_badge}</span>}
                    {c.added && <span className="ml-2 rounded bg-gray-700 px-1.5 py-0.5 text-[10px] text-gray-200">{t.scan_added_badge}</span>}
                  </span>
                  <div className="flex items-center gap-3 text-xs flex-wrap justify-end">
                    {target !== "wishlist" && pokedexNr != null && (
                      <label className="flex items-center gap-1 text-gray-300">
                        <input type="checkbox" checked={c.imPokedex} onChange={() => togglePokedex(idx)} />
                        {t.detail_pokedex_flag}
                      </label>
                    )}
                    <label className="flex items-center gap-1 text-gray-300" title={t.prize_pack_hint}>
                      <input type="checkbox" checked={c.prizePack} onChange={() => togglePrizePack(idx)} />
                      {t.scan_prize_pack}
                    </label>
                    {target !== "wishlist" && (
                      <div className="flex items-center gap-1 text-gray-300" title={t.scan_copies_hint}>
                        <span>{t.scan_copies}</span>
                        <button type="button" aria-label="−1" onClick={() => setCopies(idx, c.copies - 1)} disabled={c.copies <= 1}
                          className="h-6 w-6 rounded bg-gray-700 text-white leading-none hover:bg-gray-600 disabled:opacity-40">−</button>
                        <span className={`w-5 text-center tabular-nums ${c.copies > 1 ? "text-white font-semibold" : ""}`}
                          aria-live="polite">{c.copies}</span>
                        <button type="button" aria-label="+1" onClick={() => setCopies(idx, c.copies + 1)} disabled={c.copies >= 50}
                          className="h-6 w-6 rounded bg-gray-700 text-white leading-none hover:bg-gray-600 disabled:opacity-40">+</button>
                      </div>
                    )}
                    <label className="flex items-center gap-1 text-gray-300">
                      <input type="checkbox" checked={c.include} onChange={() => toggleInclude(idx)} />
                      {t.scan_include}
                    </label>
                  </div>
                </div>

                {/* Bild-Abgleich: Treffer zur Auswahl (Pflicht, wenn nicht eindeutig) */}
                {(c.alternatives?.length ?? 0) > 1 && (
                  <div className={`mb-2 rounded p-2 ${c.needs_choice ? "border border-pokemon-yellow bg-yellow-500/10" : "bg-gray-800/40"}`}>
                    <div className={`text-xs mb-1 ${c.needs_choice ? "text-pokemon-yellow" : "text-gray-400"}`}>
                      {c.needs_choice ? t.scan_pick_title : t.scan_pick_others}
                    </div>
                    <div className="flex gap-2 overflow-x-auto pb-1">
                      {c.alternatives!.map((a) => {
                        const current = c.chosenId ?? (c.needs_choice ? null : c.match?.tcgdex_card_id);
                        const active = current === a.tcgdex_card_id;
                        const thumb = thumbUrl(a.image_url);
                        return (
                          <button key={a.tcgdex_card_id} type="button" onClick={() => void pickAlternative(idx, a)}
                            title={`${a.name ?? ""} · ${a.set_name ?? ""} ${a.local_id ?? ""}`}
                            className={`shrink-0 w-20 text-left rounded border ${
                              active ? "border-pokemon-yellow ring-2 ring-pokemon-yellow" : "border-gray-700 hover:border-gray-500"}`}>
                            {thumb ? (
                              // eslint-disable-next-line @next/next/no-img-element
                              <img src={thumb} alt="" loading="lazy" className="w-full rounded-t" />
                            ) : (
                              <div className="aspect-[63/88] bg-gray-800 rounded-t" />
                            )}
                            <div className="px-1 pt-0.5 text-[10px] leading-tight text-gray-200 truncate">{a.name}</div>
                            <div className="px-1 pb-0.5 text-[9px] leading-tight text-gray-500 truncate">
                              {a.set_name}{a.local_id ? ` · ${a.local_id}` : ""}{active ? ` · ${t.scan_pick_chosen}` : ""}
                            </div>
                          </button>
                        );
                      })}
                      {c.needs_choice && (
                        <button type="button" onClick={() => chooseNone(idx)}
                          className="shrink-0 w-20 rounded border border-dashed border-gray-600 text-[10px] text-gray-400 hover:text-white">
                          {t.scan_pick_none}
                        </button>
                      )}
                    </div>
                  </div>
                )}

                <div className="flex flex-col sm:flex-row gap-3">
                  {/* Bild – eigenes Foto (Zuschnitt) oder TCGdex-Treffer, je nach Wahl. */}
                  <div className="w-32 mx-auto sm:mx-0 sm:w-36 shrink-0">
                    {(() => {
                      const own = c.cropUrl ?? (mode === "single" ? sourceUrl : null);
                      const big = c.usePhoto ? (own ?? catalogImg ?? null)
                                             : (catalogImg ?? own ?? null);
                      return big ? (
                        <a href={big} target="_blank" rel="noreferrer" title={t.scan_view_large} className="relative block">
                          {/* eslint-disable-next-line @next/next/no-img-element */}
                          <img src={big} alt="" className="w-full rounded hover:opacity-90"
                            onError={() => {
                              // Bild in dieser Sprache gibt es nicht → zurück zur gelieferten Fassung
                              if (c.imageLang && big === catalogImg) {
                                toast(t.image_lang_missing, { icon: "🖼" });
                                patchCand(c.key, (cc) => ({ ...cc, imageLang: null }));
                              }
                            }} />
                          {c.rematching && (
                            <span className="absolute inset-0 flex items-center justify-center rounded bg-black/60 text-xs text-white animate-pulse">
                              {t.scan_rechecking}
                            </span>
                          )}
                        </a>
                      ) : (
                        <div className="aspect-[63/88] bg-gray-800 rounded flex items-center justify-center text-gray-600 text-xs">?</div>
                      );
                    })()}
                    <div className="mt-1 text-center text-[10px] text-gray-500">
                      {t.scan_confidence}: {Math.round(c.confidence * 100)}%
                    </div>
                    {(c.cropUrl || (mode === "single" && sourceUrl)) && catalogImg && (
                      <label className="mt-1 flex items-center justify-center gap-1 text-[10px] text-gray-300">
                        <input type="checkbox" checked={c.usePhoto} onChange={() => toggleUsePhoto(idx)} />
                        {t.scan_use_photo}
                      </label>
                    )}
                    {sourceUrl && (
                      <button type="button"
                        onClick={() => setEditorIdx(idx)}
                        className="mt-1 w-full text-[10px] text-pokemon-blue hover:underline"
                      >
                        ✥ {t.scan_edit_corners}
                      </button>
                    )}
                  </div>

                  {/* Felder */}
                  <div className="flex-1 min-w-0 space-y-1.5">
                    {uncertain && (
                      <div className="text-pokemon-red text-xs">⚠ {t.scan_uncertain} ({c.uncertain_fields.join(", ")})</div>
                    )}
                    <input
                      value={String(s.kartenname ?? "")}
                      onChange={(e) => updateField(idx, "kartenname", e.target.value)}
                      placeholder={t.scan_field_name}
                      className="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1 text-white text-sm"
                    />
                    {/* Set-Auswahl (Dropdown wie beim manuellen Anlegen) */}
                    <SetPicker
                      value={String(s.set_edition ?? "")}
                      sets={sets}
                      onChange={(setEdition) => {
                        updateField(idx, "set_edition", setEdition || null);
                        void refreshCandidate(idx, { set_edition: setEdition || null });
                      }}
                      onSetAdded={() => { void refreshSets(); }}
                    />
                    <div className="grid grid-cols-2 gap-1.5">
                      <input
                        type="number"
                        value={String(s.pokedex_nr ?? "")}
                        onChange={(e) => updateField(idx, "pokedex_nr", e.target.value ? Number(e.target.value) : null)}
                        placeholder={t.form_pokedex_nr}
                        className="min-w-0 bg-gray-800 border border-gray-700 rounded px-2 py-1 text-white text-xs"
                      />
                      <input
                        value={String(s.karten_nr ?? "")}
                        onChange={(e) => updateField(idx, "karten_nr", e.target.value)}
                        onBlur={() => void refreshCandidate(idx)}
                        placeholder={t.scan_field_nr}
                        className="min-w-0 bg-gray-800 border border-gray-700 rounded px-2 py-1 text-white text-xs font-mono"
                      />
                      <select
                        value={String(s.sprache ?? "DE")}
                        onChange={(e) => { updateField(idx, "sprache", e.target.value); void refreshCandidate(idx, { sprache: e.target.value }); }}
                        className="min-w-0 bg-gray-800 border border-gray-700 rounded px-2 py-1 text-white text-xs"
                      >
                        {LANGS.map((l) => <option key={l} value={l}>{l}</option>)}
                      </select>
                      {/* Bildsprache: Katalogbild z. B. deutsch zeigen, obwohl der Treffer englisch kam */}
                      {baseImgLang && (
                        <select value={c.imageLang ?? ""} title={t.image_lang_hint} aria-label={t.image_lang_label}
                          data-testid="scan-image-lang"
                          onChange={(e) => { const v = e.target.value || null; patchCand(c.key, (cc) => ({ ...cc, imageLang: v })); }}
                          className="min-w-0 bg-gray-800 border border-gray-700 rounded px-2 py-1 text-white text-xs">
                          <option value="">🖼 {t.image_lang_auto} ({baseImgLang})</option>
                          {IMAGE_LANGS.filter((l) => l !== baseImgLang).map((l) => <option key={l} value={l}>🖼 {l}</option>)}
                        </select>
                      )}
                    </div>
                    <div className="grid grid-cols-2 gap-1.5 items-start">
                      {/* Folierung – alle Möglichkeiten; laut Karte mögliche zuerst */}
                      <select
                        value={String(s.folierung ?? "")}
                        onChange={(e) => {
                          updateField(idx, "folierung", e.target.value);
                          // Muster hängt an folierter Grundform — beim Wechsel
                          // auf Normal zurücksetzen (kein stale Muster im Commit)
                          if (!e.target.value.includes("Holo")) updateField(idx, "muster", "");
                        }}
                        className="min-w-0 bg-gray-800 border border-gray-700 rounded px-2 py-1.5 text-white text-xs"
                      >
                        <option value="">{t.scan_field_foiling}</option>
                        {(() => {
                          const all = enums?.folierung ?? foilOptions;
                          const pref = foilOptions.filter((f) => all.includes(f));
                          const rest = all.filter((f) => !pref.includes(f));
                          return [...pref, ...rest].map((f) => <option key={f} value={f}>{f}</option>);
                        })()}
                      </select>
                      {/* Muster (#63): nur bei Foil-Grundformen sinnvoll */}
                      <select
                        value={String(s.muster ?? "")}
                        onChange={(e) => updateField(idx, "muster", e.target.value)}
                        disabled={!String(s.folierung ?? "").includes("Holo")}
                        className="min-w-0 bg-gray-800 border border-gray-700 rounded px-2 py-1.5 text-white text-xs disabled:opacity-40"
                      >
                        <option value="">{t.field_pattern}</option>
                        {(enums?.muster ?? []).map((m) => <option key={m} value={m}>{valueLabel(m, lang)}</option>)}
                      </select>
                      {/* Seltenheit mit Symbol (wie beim manuellen Anlegen) */}
                      <div className="min-w-0">
                        <RaritySelect
                          value={String(s.seltenheit ?? "")}
                          onChange={(v) => updateField(idx, "seltenheit", v)}
                          options={(() => {
                            const opts = enums?.seltenheit ?? [];
                            const cur = String(s.seltenheit ?? "");
                            return cur && !opts.includes(cur) ? [cur, ...opts] : opts;
                          })()}
                          language={String(s.sprache ?? "DE")}
                        />
                      </div>
                    </div>
                  </div>
                </div>
                {/* Je Karte: neu erkennen, weiteres Exemplar, verwerfen, einzeln speichern */}
                <div className="mt-2 flex flex-wrap items-center gap-2 border-t border-gray-800 pt-2 text-xs">
                  {sourceUrl && (
                    <button type="button" onClick={() => void rescan(idx)} disabled={c.rematching || c.saving}
                      data-testid="scan-rescan"
                      className="rounded bg-gray-800 px-2 py-1 text-gray-300 hover:text-white disabled:opacity-40">↻ {t.scan_rescan}</button>
                  )}
                  <button type="button" onClick={() => addVariant(idx)} title={t.scan_add_variant_hint}
                    data-testid="scan-add-variant"
                    className="rounded bg-gray-800 px-2 py-1 text-gray-300 hover:text-white">＋ {t.scan_add_variant}</button>
                  <span className="flex-1" />
                  <button type="button" onClick={() => discardOne(idx)} disabled={c.saving}
                    data-testid="scan-discard-one"
                    className="rounded px-2 py-1 text-gray-400 hover:bg-gray-800 hover:text-red-300 disabled:opacity-40">✕ {t.scan_discard_one}</button>
                  <button type="button" onClick={() => void saveOne(idx)}
                    data-testid="scan-save-one"
                    disabled={busy || c.saving || c.rematching || c.needs_choice}
                    className="rounded bg-green-700 px-3 py-1 text-white hover:bg-green-600 disabled:opacity-40">
                    {c.saving ? t.scan_saving : `✓ ${t.scan_save_one}`}
                  </button>
                </div>
              </div>
            );
          })}

          <div className="fixed bottom-0 inset-x-0 bg-pokemon-dark/95 border-t border-gray-800 p-3">
            <div className="max-w-3xl mx-auto flex gap-3">
              {(() => {
                // Alles einzeln gespeichert oder verworfen → nur noch weiter bzw. fertig
                const open = candidates.some((c) => c.include && !c.saved);
                const blocked = busy || candidates.some((c) => c.rematching || c.saving);
                return (
                  <>
                    <button type="button" onClick={() => void handleSave(false)} disabled={blocked}
                      data-testid="scan-save-stop"
                      className="flex-1 bg-gray-700 text-white px-4 py-2.5 rounded hover:bg-gray-600 disabled:opacity-50">
                      {busy ? t.scan_saving : open ? t.scan_save_stop : t.scan_done}
                    </button>
                    <button type="button" onClick={() => void handleSave(true)} disabled={blocked}
                      data-testid="scan-save-next"
                      className="flex-[1.4] bg-green-600 text-white px-4 py-2.5 rounded hover:bg-green-700 disabled:opacity-50">
                      {busy ? t.scan_saving : open ? t.scan_save_next : t.scan_next_photo}
                    </button>
                  </>
                );
              })()}
            </div>
          </div>
        </div>
      )}

      {addingMissed && sourceUrl && (
        <CornerEditor
          imageUrl={sourceUrl}
          initialQuad={[[0.38, 0.33], [0.62, 0.33], [0.62, 0.67], [0.38, 0.67]]}
          onCancel={() => setAddingMissed(false)}
          onApply={(quad, transform) => void addMissedCard(quad, transform)}
        />
      )}

      {editorIdx != null && sourceUrl && candidates[editorIdx] && (
        <CornerEditor
          imageUrl={sourceUrl}
          initialQuad={initialQuadFor(candidates[editorIdx])}
          onCancel={() => setEditorIdx(null)}
          onApply={(quad, transform) => void applyCorners(editorIdx, quad, transform)}
        />
      )}

    </div>
  );
}


/**
 * Rahmen über dem Live-Bild, in Video-Pixeln (viewBox = Videoformat, das Video
 * wird unverzerrt in voller Breite gezeigt): Bereich außerhalb abgedunkelt,
 * bei der Mappe das Raster, und die gefundene Karte als grüne Kontur.
 */
function CameraGuide({ w, h, aspect, cols, rows, quad, steady, boxes }: {
  w: number; h: number; aspect: number | null; cols: number; rows: number; quad: Pt[] | null; steady: boolean;
  boxes?: Pt[][] | null;   // vom Bild-Abgleich gefundene Karten (mehrere Karten / Mappe)
}) {
  const g = aspect !== null ? guideRect(w, h, aspect) : null;
  const r = g ? (Math.min(g.w / cols, g.h / rows) * 0.045) : 0;   // Kartenecken ~3 mm von 63 mm
  const color = quad ? (steady ? "#22c55e" : "#86efac") : "#facc15";
  return (
    <svg className="pointer-events-none absolute inset-0 h-full w-full" viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none">
      {g && (
        <>
          <path fillRule="evenodd" fill="rgba(0,0,0,0.38)"
            d={`M0 0H${w}V${h}H0Z M${g.x} ${g.y}V${g.y + g.h}H${g.x + g.w}V${g.y}Z`} />
          {Array.from({ length: cols - 1 }, (_, i) => (
            <line key={`c${i}`} x1={g.x + (g.w * (i + 1)) / cols} x2={g.x + (g.w * (i + 1)) / cols} y1={g.y} y2={g.y + g.h}
              stroke={color} strokeOpacity={0.45} strokeWidth={1} vectorEffect="non-scaling-stroke" />
          ))}
          {Array.from({ length: rows - 1 }, (_, i) => (
            <line key={`r${i}`} y1={g.y + (g.h * (i + 1)) / rows} y2={g.y + (g.h * (i + 1)) / rows} x1={g.x} x2={g.x + g.w}
              stroke={color} strokeOpacity={0.45} strokeWidth={1} vectorEffect="non-scaling-stroke" />
          ))}
          <rect x={g.x} y={g.y} width={g.w} height={g.h} rx={r} ry={r} fill="none" stroke={color}
            strokeWidth={2.5} vectorEffect="non-scaling-stroke" />
        </>
      )}
      {quad && (
        <polygon points={quad.map(([x, y]) => `${x},${y}`).join(" ")} fill="rgba(34,197,94,0.12)" stroke="#22c55e"
          strokeWidth={2} vectorEffect="non-scaling-stroke" />
      )}
      {boxes?.map((b, i) => (
        <polygon key={i} points={b.map(([x, y]) => `${x},${y}`).join(" ")} fill="rgba(56,189,248,0.14)" stroke="#38bdf8"
          strokeWidth={2} strokeLinejoin="round" vectorEffect="non-scaling-stroke" />
      ))}
    </svg>
  );
}
