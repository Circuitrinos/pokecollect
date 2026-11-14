"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import toast from "react-hot-toast";
import {
  Collection, DeckSummary, FoilCheck, ScanCandidate, ScanCommitItem, ScanMode, ScanStatus,
  cardApi, collectionApi, deckApi, scanApi,
  ScanAlternative, scanDraftApi, DraftSaved,
} from "@/lib/api";
import { ScanDraftPanel } from "@/components/ScanDraftList";
import { refreshScanDrafts, removeScanDraft, useScanDrafts } from "@/lib/useScanDrafts";
import { useEnums } from "@/lib/useEnums";
import { useSets } from "@/lib/useSets";
import { useSettings } from "@/lib/useSettings";
import SetPicker from "@/components/SetPicker";
import RaritySelect from "@/components/RaritySelect";
import CornerEditor, { CornerSuggestion } from "@/components/CornerEditor";
import { outlineSuggestion } from "@/lib/outline";
import SlotPicker, { pageSizeOf } from "@/components/SlotPicker";
import { cropToCardPhoto, loadImg, normalizeOrientation, shrinkForUpload, CropTransform } from "@/lib/cardCrop";
import { ringOrder } from "@/lib/cropOrient";
import { defaultCardLang, setCodeFromEdition } from "@/lib/utils";
import { findCardInGuide, guideAspect, guideRect, lockMovement, toGray, type Lock, type Pt } from "@/lib/cardLock";
import { fromGuide, squareToQuad, straightenToGuide, tiltedGuide, turnDegrees, type Quad } from "@/lib/tiltGrid";
import { useTiltGrid } from "@/lib/useTiltGrid";
import { useMotion } from "@/lib/useMotion";
import { FrameJudge } from "@/lib/frameQuality";
import {
  addFrame, allSure, choices, counts, emptyLive, finish, pick, signature, skipPockets, verdict,
  type Level, type LiveState, type Verdict,
} from "@/lib/liveScan";
import { safeReturnPath } from "@/lib/navRegistry";
import { useI18n } from "@/lib/i18n";
import { joinCardNr, splitCardNr } from "@/lib/cardNumber";
import { valueLabel } from "@/lib/enumLabels";
import { IMAGE_LANGS, imageLangOf, withImageLang } from "@/lib/imageLang";

type Step = "setup" | "review";
type ScanTarget = "pokedex" | "collection" | "wishlist" | "deck";

type EditableCandidate = ScanCandidate & {
  include: boolean;
  usePhoto: boolean;        // eigenes Foto als Kartenbild (true) vs. Katalogbild (false; Foto lernt trotzdem mit)
  cropUrl: string | null;   // Vorschau des zugeschnittenen eigenen Fotos
  imPokedex: boolean;       // je Karte: in den Pokédex
  prizePack: boolean;       // Play!-Pokémon-Stempel (Prize Pack) — angehakt (von Hand oder vom Stempel-Fund)
  prizePackSet?: boolean;   // von Hand gesetzt: ein neuer Stempel-Befund ändert es nicht mehr
  foil?: Pick<FoilCheck, "hint" | "score"> | null;   // Reverse holo? (undefined = noch nicht geprüft)
  firstEdition: boolean;    // 1st-Edition-Stempel — vom Nutzer angehakt
  condition: string;        // Zustand (leer = nicht angegeben); bleibt beim Neu-Auflösen
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
  saved?: DraftSaved | null;   // schon einzeln gespeichert
};

// Katalogbild in der gewählten Bildsprache (TCGdex, westliche Karten)
const matchImage = (c: EditableCandidate): string | null =>
  (c.imageLang ? withImageLang(c.match?.image_url, c.imageLang) : null) ?? c.match?.image_url ?? null;

// TCGdex liefert jede Karte auch klein (/low.webp) – reicht für Picker-Vorschauen.
function thumbUrl(url: string | null): string | null {
  return url ? url.replace(/\/high\.(webp|png|jpg)$/, "/low.$1") : null;
}

const LANGS = ["DE", "EN", "CN", "JP", "FR", "ES", "IT"];
const TILT_KEY = "scan_tilt_grid";
const MULTI_GRID_KEY = "scan_multi_grid";   // Raster-Hilfe bei losen Karten (localStorage)   // „Raster neigen" bleibt eingeschaltet (localStorage)
const TILT_MIN_DEG = 2;              // darunter gilt das Handy als gerade: normales Raster, keine Ecken an den Server
const LIVE_KEY = "scan_live";        // Live-Scan bleibt eingeschaltet (localStorage)

function parseLayout(layout: string): { cols: number; rows: number } {
  const m = layout.match(/^(\d+)x(\d+)$/);
  if (!m) return { cols: 3, rows: 3 };
  return { cols: Number(m[1]), rows: Number(m[2]) };
}

/** Raster-Hilfe für lose Karten („3x3", Groß/Klein und Leerzeichen egal); leer/ungültig → keins. */
function parseGrid(grid: string): { cols: number; rows: number } | null {
  const m = grid.trim().toLowerCase().match(/^(\d{1,2})\s*[x×]\s*(\d{1,2})$/);
  if (!m) return null;
  const cols = Number(m[1]), rows = Number(m[2]);
  return cols >= 1 && rows >= 1 && cols <= 10 && rows <= 10 && cols * rows > 1 ? { cols, rows } : null;
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

// Scan-Entwurf: die Prüfseite ohne Laufzeit-Felder (Vorschau-URLs, „läuft gerade")
type DraftState = {
  v: 1; mode: ScanMode; target: ScanTarget; collectionId: number | null; deckId: number | null;
  priority: string; layout: string; setPokedexRep: boolean; slot: number | null;
  candidates: Partial<EditableCandidate>[];
  noneDetected?: boolean;   // nichts erkannt: Foto zum Markieren von Hand behalten
};
const forDraft = (c: EditableCandidate): Partial<EditableCandidate> => {
  const o: Partial<EditableCandidate> = { ...c };
  delete o.cropUrl;
  delete o.saving;
  delete o.rematching;
  return o;
};
// Wo die Karte im Foto liegt: von Hand gesetzte Ecken, sonst die erkannten (Ecken oder Rechteck).
// Weitere Exemplare (variantOf) haben keine eigene Stelle.
const outlineOf = (c: EditableCandidate): number[][] | null => {
  if (c.variantOf) return null;
  if (c.editedQuad?.length === 4) return c.editedQuad;
  if (c.raw?.quad?.length === 4) return c.raw.quad;
  const b = c.raw?.bbox;
  return b?.length === 4 ? [[b[0], b[1]], [b[0] + b[2], b[1]], [b[0] + b[2], b[1] + b[3]], [b[0], b[1] + b[3]]] : null;
};

const conflictDetail = (e: unknown): string | null => {
  const r = (e as { response?: { status?: number; data?: { detail?: unknown } } })?.response;
  return r?.status === 409 && typeof r.data?.detail === "string" ? r.data.detail : null;
};

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
  // Raster-Hilfe bei losen Karten (z.B. auf dem Tisch ausgelegt), gemerkt im Browser; leer = keins
  const [multiGrid, setMultiGrid] = useState("");
  useEffect(() => {
    try { setMultiGrid(localStorage.getItem(MULTI_GRID_KEY) ?? ""); } catch { /* privat */ }
  }, []);
  const changeMultiGrid = (v: string) => {
    setMultiGrid(v);
    try { localStorage.setItem(MULTI_GRID_KEY, v); } catch { /* privat */ }
  };
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
  const keyPrefix = useRef(Math.random().toString(36).slice(2, 7));   // eindeutig auch nach Fortsetzen
  const nextKey = () => `${keyPrefix.current}-${++keySeq.current}`;
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
  // Live-Scan (nur mit dem Bild-Abgleich): Kamera an, Bilder werden laufend geprüft
  const [live, setLive] = useState(false);
  useEffect(() => {
    try { setLive(localStorage.getItem(LIVE_KEY) === "1"); } catch { /* privat */ }
  }, []);
  const liveAvailable = status?.provider === "local";
  const liveOn = live && liveAvailable && cameraOn && step === "setup";

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
    if (!cameraOn || step !== "setup" || mode === "single" || liveOn) return;   // Live zeichnet seine eigenen Umrisse
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
  }, [cameraOn, step, mode, liveOn]);

  // Rahmen in Kartenform (Einzelkarte) bzw. Seitenform (Mappe); bei losen Karten nur mit Raster-Hilfe.
  const looseGrid = mode === "multi" ? parseGrid(multiGrid) : null;
  const gridShown = mode === "binder" || looseGrid !== null;
  const { cols: layoutCols, rows: layoutRows } = looseGrid ?? parseLayout(layout);
  const gAspect = looseGrid ? guideAspect("binder", layoutCols, layoutRows) : guideAspect(mode, layoutCols, layoutRows);
  // Auto-Aufnahme sucht vier gerade Kanten im Rahmen — lose Karten haben keine, dort nur die Raster-Hilfe
  const lockAspect = looseGrid ? null : gAspect;
  const handleCaptureRef = useRef<() => void>(() => {});

  // Mappenseite schräg fotografieren (gegen Spiegelungen): der Lagesensor neigt
  // das Raster so, wie die flache Seite aus dem geneigten Handy aussieht. Beim
  // Einschalten (und mit „Gerade") gilt die Haltung als gerade über der Seite.
  const tilt = useTiltGrid();
  const tiltOn = gridShown && cameraOn;   // läuft über mehrere Aufnahmen weiter (Serienscan, gleiche Haltung)
  const tiltDeg = tilt.state === "on" ? turnDegrees(tilt.turn) : 0;
  const tiltQuad: Quad | null = tiltOn && step === "setup" && tilt.state === "on" && vidSize && gAspect !== null && tiltDeg >= TILT_MIN_DEG
    ? tiltedGuide(guideRect(vidSize.w, vidSize.h, gAspect), vidSize.w, vidSize.h, tilt.turn)
    : null;
  const tiltQuadRef = useRef<Quad | null>(null);
  tiltQuadRef.current = tiltQuad;
  const { stop: stopTilt, start: startTilt } = tilt;
  useEffect(() => {   // Mappe verlassen / Kamera aus → Sensor aus; eingeschaltet gemerkt → beim Öffnen wieder an
    if (!tiltOn) { stopTilt(); return; }
    let wanted = false;
    try { wanted = localStorage.getItem(TILT_KEY) === "1"; } catch { /* privat */ }
    if (wanted) void startTilt(true);   // still: wo das ohne Tipp nicht geht (iOS beim ersten Mal), bleibt es aus
  }, [tiltOn, stopTilt, startTilt]);
  const toggleTilt = () => {
    const on = tilt.state === "off" || tilt.state === "unavailable" || tilt.state === "denied";
    try { localStorage.setItem(TILT_KEY, on ? "1" : "0"); } catch { /* privat */ }
    if (on) void tilt.start(); else tilt.stop();
  };
  useEffect(() => {
    if (tilt.state !== "unavailable" && tilt.state !== "denied") return;
    toast(tilt.state === "unavailable" ? t.scan_tilt_unavailable : t.scan_tilt_denied, { icon: "📐" });
    try { localStorage.setItem(TILT_KEY, "0"); } catch { /* privat */ }
  }, [tilt.state, t]);

  // Kartensuche im Live-Bild: ~7×/s vier gerade Kanten am Rahmen (Karte bzw.
  // Kartenraster liegt drin) → grüne Kontur, auch ohne Auto-Aufnahme. Mit
  // Auto-Aufnahme löst sie aus, sobald der Fund ~½ s ruhig steht.
  // Verwacklungstolerant; nur der Auslöser – die Erkennung macht der Server.
  useEffect(() => {
    setLockQuad(null);
    setLockSteady(false);
    if (!cameraOn || lockAspect === null || step !== "setup" || liveOn) return;   // Live nimmt selbst auf
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
      const guide = guideRect(w, h, lockAspect);
      let gray = toGray(ctx.getImageData(0, 0, w, h).data, w, h);
      // geneigtes Raster: das Bild erst so umrechnen, als stünde das Handy gerade über der Seite
      const tq = tiltQuadRef.current;
      const small = tq ? (tq.map(([x, y]) => [x * k, y * k]) as Quad) : null;
      if (small) gray = straightenToGuide(gray, w, h, small, guide);
      const lock = findCardInGuide(gray, w, h, guide);
      steady = lock && prev && lockMovement(prev, lock, guide) <= 0.022 ? steady + 1 : 0;
      prev = lock;
      setLockQuad(lock ? lock.quad.map((p) => {
        const [x, y] = small ? fromGuide(p, small, guide) : p;
        return [x / k, y / k] as Pt;
      }) : null);
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
  }, [cameraOn, autoCapture, lockAspect, step, liveOn]);

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

  // ── Live-Scan ───────────────────────────────────────────────────────────
  // Die Kamera läuft, der Bild-Abgleich prüft immer wieder ein ruhiges, scharfes
  // Bild (nacheinander, nie parallel). Jede Karte sammelt Befunde über die Bilder
  // (lib/liveScan): grün sicher, gelb wahrscheinlich, rot unklar. Bei der Mappe
  // lässt der Abgleich sichere Fächer im nächsten Bild aus. Alle grün → Prüfseite.
  const [liveState, setLiveState] = useState<LiveState>(emptyLive);
  const liveRef = useRef<LiveState>(liveState);
  const liveFramesRef = useRef(new Map<number, Blob>());   // geprüfte Bilder (eins davon wird das Foto)
  const commitLive = (fn: (s: LiveState) => LiveState): LiveState => {
    const next = fn(liveRef.current);
    liveRef.current = next;
    setLiveState(next);
    return next;
  };
  type LiveChip = "hold" | "checking" | "paused" | null;
  const [liveChip, setLiveChip] = useState<LiveChip>(null);
  // Zuletzt abgeschlossen: liegt danach noch dieselbe Seite/Karte da, geht es nicht
  // von selbst noch einmal weiter (sonst doppelt) — ✓ von Hand geht trotzdem.
  const lastDoneRef = useRef<string | null>(null);
  const [livePick, setLivePick] = useState<string | null>(null);   // Karte, deren Treffer zur Wahl offen sind
  const livePickRef = useRef<string | null>(null);
  livePickRef.current = livePick;
  const [livePicking, setLivePicking] = useState(false);
  const motion = useMotion(liveOn);
  const toggleLive = () => {
    const on = !live;
    setLive(on);
    try { localStorage.setItem(LIVE_KEY, on ? "1" : "0"); } catch { /* privat */ }
    if (on) void motion.ask();     // iOS gibt den Bewegungssensor nur nach einem Tipp frei
  };
  // Von vorn: Live an/aus, anderer Modus, anderes Raster, zurück von der Prüfseite
  const liveGrid = mode === "multi" ? multiGrid : mode === "binder" ? layout : "";
  useEffect(() => {
    liveFramesRef.current = new Map();
    liveRef.current = emptyLive();
    setLiveState(liveRef.current);
    setLivePick(null);
    setLiveChip(null);
  }, [liveOn, mode, liveGrid]);
  useEffect(() => { lastDoneRef.current = null; }, [mode, liveGrid]);
  // Alle sicher → von selbst weiter, außer es ist genau das, was gerade schon abgeschlossen wurde
  const autoFinish = (st: LiveState): boolean => {
    if (!allSure(st, mode)) return false;
    if (signature(st) === lastDoneRef.current) return false;
    finishLiveRef.current();
    return true;
  };
  const autoFinishRef = useRef(autoFinish);
  autoFinishRef.current = autoFinish;

  const finishLive = () => {
    const st = liveRef.current;
    if (!st.tracks.length) { handleCaptureRef.current(); return; }   // noch nichts gefunden → normales Foto
    const { frame, items } = finish(st, Array.from(liveFramesRef.current.keys()));
    const photo = frame != null ? liveFramesRef.current.get(frame) : null;
    if (!photo || !items.length) return;
    lastDoneRef.current = signature(st);
    liveFramesRef.current = new Map();
    commitLive(() => emptyLive());
    setLivePick(null);
    openReviewRef.current(photo, items.map((i) => toEditableRef.current(i.cand, i.chosenId)));
  };
  const finishLiveRef = useRef(finishLive);
  finishLiveRef.current = finishLive;

  // Kandidat von Hand: die Karte gilt dann als sicher
  const pickLive = async (key: string, alt: ScanAlternative, v: Verdict) => {
    setLivePicking(true);
    try {
      const lang = (v.cand?.suggested as Record<string, unknown> | undefined)?.sprache as string | undefined;
      const r = await scanApi.resolve({ tcgdex_card_id: alt.tcgdex_card_id, name: alt.name, language: lang ?? defaultLang });
      const next = commitLive((st) => pick(st, key, alt.tcgdex_card_id, r.data));
      setLivePick(null);
      autoFinish(next);
    } catch {
      toast.error(t.scan_error);
    } finally {
      setLivePicking(false);
    }
  };

  useEffect(() => {
    if (!liveOn) return;
    let stop = false;
    const judge = new FrameJudge();
    const small = document.createElement("canvas");
    const sctx = small.getContext("2d", { willReadFrequently: true });
    const full = document.createElement("canvas");
    const fctx = full.getContext("2d");
    if (!sctx || !fctx) return;
    const sleep = (ms: number) => new Promise((r) => window.setTimeout(r, ms));
    let chip: LiveChip = null;
    const showChip = (c: LiveChip) => { if (c !== chip) { chip = c; setLiveChip(c); } };
    const grid = mode === "multi" ? parseGrid(multiGrid) : null;
    const { cols, rows } = grid ?? parseLayout(layout);
    const useGrid = mode === "binder" || grid !== null;
    (async () => {
      while (!stop) {
        // 1. auf ein ruhiges, scharfes Bild warten — Sensor (falls da) und Kamerabild;
        //    nach ein paar Sekunden genügt ein ruhiges, zur Not irgendein Bild
        const t0 = performance.now();
        let v: HTMLVideoElement | null = null;
        while (!stop) {
          v = videoRef.current;
          if (!v || !v.videoWidth || busyRef.current || livePickRef.current) { await sleep(250); continue; }
          const k = 240 / Math.max(v.videoWidth, v.videoHeight);
          const w = Math.round(v.videoWidth * k), h = Math.round(v.videoHeight * k);
          if (small.width !== w || small.height !== h) { small.width = w; small.height = h; }
          sctx.drawImage(v, 0, 0, w, h);
          const q = judge.judge(toGray(sctx.getImageData(0, 0, w, h).data, w, h), w, h, performance.now());
          const sensor = motion.still();
          const calm = q.still && sensor !== false;
          const waited = performance.now() - t0;
          // ruhig und scharf; sonst bald das schärfste der letzten Bilder (sofern der Sensor nicht
          // gerade Bewegung meldet); spätestens ein ruhiges bzw. irgendein Bild
          if ((calm && q.sharp) || (waited > 1500 && q.sharp && sensor !== false)
            || (waited > 3000 && (q.still || sensor === true)) || waited > 6000) break;
          showChip(calm ? null : "hold");
          await sleep(110);
        }
        if (stop || !v) break;
        // 2. genau dieses Bild (max. 1600 px) an den Bild-Abgleich
        const kf = Math.min(1, 1600 / Math.max(v.videoWidth, v.videoHeight));
        full.width = Math.round(v.videoWidth * kf);
        full.height = Math.round(v.videoHeight * kf);
        fctx.drawImage(v, 0, 0, full.width, full.height);
        const vw = v.videoWidth, vh = v.videoHeight;
        const tq = tiltQuadRef.current;
        const page = useGrid && tq ? tq.map(([x, y]) => [x / vw, y / vh] as Pt) : null;
        const photo = await new Promise<Blob | null>((res) => full.toBlob(res, "image/jpeg", 0.88));
        if (!photo || stop) continue;
        showChip("checking");
        try {
          const res = await scanApi.scan(photo, {
            mode, rows: useGrid ? rows : 0, cols: useGrid ? cols : 0, default_language: defaultLang, page,
            skip: mode === "binder" ? skipPockets(liveRef.current) : undefined, live: true,
          });
          if (stop) break;
          if (res.data.engine !== "local") { showChip("paused"); await sleep(4000); continue; }
          showChip(null);
          const next = commitLive((st) => addFrame(st, res.data.candidates, mode));
          const frames = liveFramesRef.current;
          frames.set(next.frames, photo);
          // behalten: die letzten Bilder und das, das gerade das beste Foto wäre
          const keep = finish(next, Array.from(frames.keys())).frame;
          for (const f of Array.from(frames.keys())) if (f <= next.frames - 6 && f !== keep) frames.delete(f);
          if (autoFinishRef.current(next)) break;
        } catch {
          if (stop) break;
          showChip("paused");
          await sleep(2500);
          continue;
        }
        await sleep(200);
      }
    })();
    return () => { stop = true; };
  }, [liveOn, mode, layout, multiGrid, defaultLang]);   // eslint-disable-line react-hooks/exhaustive-deps

  // ── Scan-Entwurf ────────────────────────────────────────────────────────
  // Die Prüfseite liegt als Entwurf auf dem Server: Seite verlassen und später
  // oder an einem anderen Gerät weitermachen (am Handy scannen, am Rechner
  // bestätigen). Fertig bestätigt oder alles verworfen → Entwurf gelöscht.
  const [draftId, setDraftId] = useState<number | null>(null);
  const draftRef = useRef<number | null>(null);
  const draftSessionRef = useRef(0);              // ein verspätet angelegter Entwurf einer beendeten Prüfung fliegt
  const lastSentRef = useRef("");
  const [draftStatus, setDraftStatus] = useState<"saving" | "saved" | "error" | null>(null);
  const drafts = useScanDrafts() ?? [];          // offene Entwürfe (auch anderer Geräte), oben im Aufnahme-Schritt
  const candidatesRef = useRef(candidates);
  candidatesRef.current = candidates;

  // nichts erkannt (Foto als offener Scan behalten): leer heißt dann nicht „fertig"
  const noneDetectedRef = useRef(false);
  const serializeDraft = (list: EditableCandidate[]): DraftState => ({
    v: 1, mode, target, collectionId, deckId, priority, layout, setPokedexRep, slot,
    candidates: list.map(forDraft),
    ...(noneDetectedRef.current ? { noneDetected: true } : {}),
  });
  const serializeRef = useRef(serializeDraft);
  serializeRef.current = serializeDraft;

  const draftCreateRef = useRef<Promise<void> | null>(null);   // Anlegen läuft (Später bestätigen wartet darauf)
  const startDraft = (photo: Blob, list: EditableCandidate[]) => {
    const p = createDraft(photo, list);
    draftCreateRef.current = p;
    return p;
  };
  const createDraft = async (photo: Blob, list: EditableCandidate[]) => {
    const session = ++draftSessionRef.current;
    draftRef.current = null;
    setDraftId(null);
    lastSentRef.current = "";
    const st = serializeRef.current(list);
    setDraftStatus("saving");
    try {
      const r = await scanDraftApi.create(photo, st);
      if (draftSessionRef.current !== session) { void scanDraftApi.remove(r.data.id).catch(() => {}); return; }
      draftRef.current = r.data.id;
      lastSentRef.current = JSON.stringify(st);
      setDraftId(r.data.id);            // ab jetzt gleicht der Effekt unten Änderungen ab
      setDraftStatus("saved");
      refreshScanDrafts();
    } catch {
      if (draftSessionRef.current === session) setDraftStatus("error");
    }
  };
  const startDraftRef = useRef(startDraft);
  startDraftRef.current = startDraft;

  const syncDraft = async () => {
    const id = draftRef.current;
    if (id == null) return;
    const st = serializeRef.current(candidatesRef.current);
    const body = JSON.stringify(st);
    if (body === lastSentRef.current) return;
    lastSentRef.current = body;
    setDraftStatus("saving");
    try {
      const r = await scanDraftApi.update(id, st);
      if (draftRef.current !== id) return;
      setDraftStatus("saved");
      // an einem anderen Gerät gespeichert → hier auch als gespeichert zeigen
      const saved = r.data.saved ?? {};
      if (candidatesRef.current.some((c) => !c.saved && saved[c.key])) {
        setCandidates((prev) => prev.map((c) => (!c.saved && saved[c.key] ? { ...c, saved: saved[c.key] } : c)));
      }
    } catch (e) {
      if (draftRef.current !== id) return;
      if ((e as { response?: { status?: number } })?.response?.status === 404) {
        draftGoneRef.current();
      } else {
        lastSentRef.current = "";
        setDraftStatus("error");
      }
    }
  };
  const syncRef = useRef(syncDraft);
  syncRef.current = syncDraft;

  // Entwurf an einem anderen Gerät fertig bestätigt oder verworfen: diese Prüfung
  // ist überholt — zurück zur Aufnahme, damit nichts doppelt gespeichert wird.
  const draftGone = () => {
    draftSessionRef.current++;
    noneDetectedRef.current = false;
    draftRef.current = null;
    setDraftId(null);
    setDraftStatus(null);
    lastSentRef.current = "";
    setEditorIdx(null);
    setCandidates([]);
    setStep("setup");
    refreshScanDrafts();
    toast(t.scan_draft_gone, { icon: "ℹ️" });
  };
  const draftGoneRef = useRef(draftGone);
  draftGoneRef.current = draftGone;

  // Zurück in diesem Tab: hat ein anderes Gerät weitergemacht, dessen Stand übernehmen
  // (nur ohne eigene, noch nicht gesicherte Änderungen).
  const refreshDraft = async () => {
    const id = draftRef.current;
    if (id == null || busyRef.current) return;
    const mine = JSON.stringify(serializeRef.current(candidatesRef.current));
    if (mine !== lastSentRef.current) return;      // eigene Änderung unterwegs → die gewinnt
    try {
      const r = await scanDraftApi.get<DraftState>(id);
      if (draftRef.current !== id || JSON.stringify(serializeRef.current(candidatesRef.current)) !== mine) return;
      if (JSON.stringify(r.data.state) !== mine) void resumeRef.current(id);
    } catch (e) {
      if (draftRef.current === id && (e as { response?: { status?: number } })?.response?.status === 404) draftGoneRef.current();
    }
  };
  const refreshRef = useRef(refreshDraft);
  refreshRef.current = refreshDraft;
  const stepRef = useRef(step);
  stepRef.current = step;
  useEffect(() => {
    if (step !== "review" || draftId == null) return;
    const h = window.setTimeout(() => void syncRef.current(), draftStatus === "error" ? 5000 : 800);
    return () => window.clearTimeout(h);
  }, [candidates, target, collectionId, deckId, priority, slot, step, draftId, draftStatus]);

  // Tab im Hintergrund / Seite verlassen: letzten Stand noch schicken. Ist nichts
  // mehr offen (alles gespeichert oder verworfen), braucht es den Entwurf nicht mehr.
  const flushDraft = (leaving: "unload" | "navigate" | null) => {
    const id = draftRef.current;
    if (id == null) return;
    const list = candidatesRef.current;
    // alles gespeichert/verworfen → Entwurf weg; ein leerer Scan ohne Erkennung bleibt
    const done = list.length ? list.every((c) => c.saved) : !noneDetectedRef.current;
    if (leaving && done) {
      if (leaving === "unload") scanDraftApi.removeOnLeave(id);
      else void removeScanDraft(id);
      draftRef.current = null;
      return;
    }
    const st = serializeRef.current(list);
    const body = JSON.stringify(st);
    if (body !== lastSentRef.current && scanDraftApi.updateOnLeave(id, st)) lastSentRef.current = body;
  };
  const flushRef = useRef(flushDraft);
  flushRef.current = flushDraft;
  useEffect(() => {
    const onVis = () => {
      if (document.visibilityState === "hidden") flushRef.current(null);
      else if (stepRef.current === "review") void refreshRef.current();
    };
    const onHide = () => flushRef.current("unload");
    document.addEventListener("visibilitychange", onVis);
    window.addEventListener("pagehide", onHide);
    return () => {
      document.removeEventListener("visibilitychange", onVis);
      window.removeEventListener("pagehide", onHide);
      flushRef.current("navigate");           // Navigation innerhalb der App
    };
  }, []);

  // Prüfung beendet (bestätigt / alles verworfen): Entwurf löschen
  const closeDraft = () => {
    draftSessionRef.current++;
    noneDetectedRef.current = false;
    const id = draftRef.current;
    draftRef.current = null;
    setDraftId(null);
    setDraftStatus(null);
    lastSentRef.current = "";
    if (id != null) void removeScanDraft(id);
  };
  useEffect(() => { if (step === "setup") refreshScanDrafts(); }, [step]);

  const resumeDraft = async (id: number) => {
    setBusy(true);
    try {
      const [d, p] = await Promise.all([scanDraftApi.get<DraftState>(id), scanDraftApi.photo(id)]);
      const st = d.data.state;
      draftSessionRef.current++;
      sourceBlobRef.current = p.data;
      setSourceUrl((prev) => {
        if (prev) URL.revokeObjectURL(prev);
        return URL.createObjectURL(p.data);
      });
      noneDetectedRef.current = !!st.noneDetected;
      if (st.mode) setMode(st.mode);
      if (st.target) setTarget(st.target);
      setCollectionId(st.collectionId ?? null);
      setDeckId(st.deckId ?? null);
      setPriority(st.priority ?? "");
      if (st.layout) setLayout(st.layout);
      setSetPokedexRep(!!st.setPokedexRep);
      setSlot(st.slot ?? null);
      // Fehlende Felder auffüllen (ältere oder unvollständige Entwürfe) — nur anhängen,
      // damit ein vollständiger Entwurf unverändert bleibt (sonst gleich wieder gesendet)
      const list = (st.candidates ?? []).map((c0) => {
        const c: Partial<EditableCandidate> = { ...c0 };
        c.key ??= nextKey();
        c.uncertain_fields ??= [];
        c.foil_options ??= [];
        c.alternatives ??= [];
        c.suggested ??= {};
        c.raw ??= { name: null, set_code: null, number: null, language: null, position: null, confidence: null, bbox: null, quad: null };
        c.match ??= null;
        c.position ??= null;
        c.confidence ??= 0;
        c.include ??= true;
        c.copies ??= 1;
        c.condition ??= "";
        return { ...c, cropUrl: null, saving: false, rematching: false } as EditableCandidate;
      });
      setCandidates(list);
      draftRef.current = id;
      lastSentRef.current = JSON.stringify(st);
      setDraftId(id);
      setDraftStatus("saved");
      setEditorIdx(null);
      setStep("review");
      window.scrollTo({ top: 0 });
      list.forEach(async (c) => {
        if (!c.editedQuad && !c.raw?.bbox && !c.raw?.quad) return;
        try {
          const f = await cropToCardPhoto(p.data, cropOptsFor(c));
          const u = URL.createObjectURL(f);
          patchCand(c.key, (cc) => ({ ...cc, cropUrl: u }));
        } catch { /* Vorschau optional */ }
      });
    } catch {
      toast.error(t.scan_draft_load_error);
      refreshScanDrafts();
    } finally {
      setBusy(false);
    }
  };

  const resumeRef = useRef(resumeDraft);
  resumeRef.current = resumeDraft;

  // Von der Seite „Offene Scans" (/scan?draft=<id>): diesen Entwurf gleich öffnen
  useEffect(() => {
    const url = new URL(window.location.href);
    const id = Number(url.searchParams.get("draft"));
    if (!Number.isInteger(id) || id <= 0) return;
    url.searchParams.delete("draft");                  // Neu laden nach dem Abschluss → normale Aufnahme
    window.history.replaceState(window.history.state, "", url.pathname + url.search);
    void resumeRef.current(id);
  }, []);

  // Schon gespeichert (anderes Gerät): Hinweis und den aktuellen Stand des Entwurfs laden
  const onConflict = (detail: string) => {
    toast.error(detail);
    if (draftRef.current != null) void resumeDraft(draftRef.current);
  };

  // Nichts erkannt (oder die Erkennung schlug fehl): das Foto trotzdem als offenen
  // Scan behalten — die Erkennung kann sich irren; später „Übersehene Karte
  // nachtragen". Die Aufnahme bleibt offen (Serienscan), „Öffnen" geht gleich hin.
  const keepUnrecognised = async (photo: Blob, failed: boolean) => {
    const prev = noneDetectedRef.current;
    noneDetectedRef.current = true;
    const st = serializeRef.current([]);
    noneDetectedRef.current = prev;
    try {
      const r = await scanDraftApi.create(photo, st);
      refreshScanDrafts();
      toast((tt) => (
        <span className="flex flex-wrap items-center gap-2" data-testid="scan-kept-empty">
          <span>{failed ? t.scan_kept_failed : t.scan_kept_none}</span>
          <button type="button" className="rounded bg-pokemon-blue px-2 py-1 text-xs text-white"
            onClick={() => { toast.dismiss(tt.id); void resumeRef.current(r.data.id); }}>
            {t.scan_kept_open}
          </button>
        </span>
      ), { icon: "📌", duration: 7000 });
    } catch {
      toast.error(failed ? t.scan_error : t.scan_no_results);
    }
  };
  const keepRef = useRef(keepUnrecognised);
  keepRef.current = keepUnrecognised;

  // Treffer des Bild-Abgleichs → Eintrag der Prüfseite
  const toEditable = (c: ScanCandidate, chosenId: string | null = null): EditableCandidate => ({
    ...c,
    include: !c.needs_choice,   // ohne eindeutigen Treffer erst nach der Auswahl übernehmen
    // Erkannt → das saubere Katalogbild anzeigen; das Foto wird trotzdem
    // gespeichert, damit der Bild-Abgleich daraus lernt. Ohne Treffer bleibt
    // das Foto das einzige Bild.
    usePhoto: !!(c.raw?.bbox || c.raw?.quad) && !c.match?.image_url,
    cropUrl: null,
    imPokedex: setPokedexRep,
    prizePack: !!c.stamp?.sure,              // Play!-Stempel sicher gefunden → vorab angehakt
    firstEdition: false,
    condition: "",
    copies: 1,
    key: nextKey(),
    imageLang: null,
    chosenId,
  });

  // Prüfseite öffnen: dieses Foto (Quelle der Zuschnitte) und die gefundenen Karten
  const openReview = (photo: Blob, list: EditableCandidate[]) => {
    sourceBlobRef.current = photo;
    setSourceUrl((prev) => {
      if (prev) URL.revokeObjectURL(prev);
      return URL.createObjectURL(photo);
    });
    noneDetectedRef.current = false;
    setCandidates(list);
    setStep("review");
    void startDraftRef.current(photo, list);
    // Foto-Zuschnitte je Karte (aus der bbox) für Vorschau erzeugen
    list.forEach(async (c) => {
      if (!c.raw?.bbox && !c.raw?.quad) return;
      try {
        const f = await cropToCardPhoto(photo, { bbox: c.raw.bbox, quad: c.raw.quad, autoOrient: true });
        const u = URL.createObjectURL(f);
        patchCand(c.key, (cc) => ({ ...cc, cropUrl: u }));
      } catch { /* Vorschau optional */ }
    });
  };
  const openReviewRef = useRef(openReview);
  openReviewRef.current = openReview;
  const toEditableRef = useRef(toEditable);
  toEditableRef.current = toEditable;

  const runScan = useCallback(async (rawBlob: Blob, page?: Pt[] | null) => {
    setBusy(true);
    let blob: Blob | null = null;
    try {
      // EXIF-Orientierung fest einbacken (Anzeige == Zuschnitt) und vor dem
      // Senden verkleinern → schnellere Analyse. bbox/quad beziehen sich auf
      // genau dieses Bild, daher merken wir es als Quelle für die Zuschnitte.
      const photo = await downscaleImage(await normalizeOrientation(rawBlob));
      blob = photo;
      sourceBlobRef.current = photo;
      setSourceUrl((prev) => {
        if (prev) URL.revokeObjectURL(prev);
        return URL.createObjectURL(photo);
      });
      // lose Karten mit Raster-Hilfe: das Raster sagt dem Bild-Abgleich, wie die geneigte Fläche gerade aussieht
      const grid = mode === "multi" ? parseGrid(multiGrid) : null;
      const { cols, rows } = grid ?? parseLayout(layout);
      const useGrid = mode === "binder" || (grid !== null && !!page);
      const res = await scanApi.scan(blob, {
        mode,
        rows: useGrid ? rows : 0,
        cols: useGrid ? cols : 0,
        default_language: defaultLang,
        page: mode === "binder" || mode === "multi" ? page : null,
      });
      const list = res.data.candidates.map((c) => toEditableRef.current(c));
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
        await keepRef.current(blob, false);
        return;
      }
      openReviewRef.current(blob, list);
    } catch {
      if (blob) await keepRef.current(blob, true);
      else toast.error(t.scan_error);
    } finally {
      setBusy(false);
    }
  }, [layout, multiGrid, mode, t, defaultLang]);

  const handleCapture = async () => {
    // geneigtes Raster: wo die Seite im Foto liegt (Anteile des Videobilds = des Fotos)
    const v = videoRef.current;
    const tq = tiltQuadRef.current;
    const page = tq && v && v.videoWidth ? tq.map(([x, y]) => [x / v.videoWidth, y / v.videoHeight] as Pt) : null;
    const blob = await blobFromVideo();
    // Kamera läuft weiter (Serienscan); Auto-Aufnahme erst nach Kartenwechsel wieder scharf.
    needClearRef.current = true;
    setWaitingNext(true);
    if (blob) runScan(blob, page);
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
    setCandidates((prev) => prev.map((c, i) => (i === idx ? { ...c, prizePack: !c.prizePack, prizePackSet: true } : c)));

  const updateField = (idx: number, key: string, value: unknown) => {
    setCandidates((prev) =>
      prev.map((c, i) => (i === idx ? { ...c, suggested: { ...c.suggested, [key]: value } } : c))
    );
  };
  const toggleFirstEdition = (idx: number) =>
    setCandidates((prev) => prev.map((c, i) => (i === idx ? { ...c, firstEdition: !c.firstEdition } : c)));
  const setCondition = (idx: number, v: string) =>
    setCandidates((prev) => prev.map((c, i) => (i === idx ? { ...c, condition: v } : c)));
  const toggleInclude = (idx: number) =>
    setCandidates((prev) => prev.map((c, i) => (i === idx ? { ...c, include: !c.include } : c)));

  // Start-Viereck für den Eck-Editor: korrigierte Ecken › erkannte Ecken ›
  // Bounding-Box › grober Default.
  // Eck-Vorschlag (übersehene Karte nachtragen, Ecken anpassen): grobe Ecken → echte
  // Kartenkanten; mit erkannter Karte über ihr Artwork, sonst Kartenrahmen/Kanten.
  const outlineSmallRef = useRef<{ src: Blob; small: Blob } | null>(null);
  const outlineSuggest = (cardId: string | null) => async (quad: number[][]): Promise<CornerSuggestion | null> => {
    const src = sourceBlobRef.current;
    if (!src) return null;
    if (outlineSmallRef.current?.src !== src) outlineSmallRef.current = { src, small: await shrinkForUpload(src) };
    const r = await scanApi.outline(outlineSmallRef.current.small, quad, cardId);
    return outlineSuggestion(r.data, t);
  };

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
      patchCand(key, (cc) => ({ ...cc, editedQuad: quad, editedOrient: transform, cropUrl: u, foil: undefined,
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
          // neuer Zuschnitt, neuer Stempel-Befund — außer Prize Pack wurde von Hand gesetzt
          prizePack: c.prizePackSet ? c.prizePack : !!n.stamp?.sure,
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
        firstEdition: false, condition: "",
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
        erste_edition: c.firstEdition,
        zustand: target === "wishlist" ? null : (c.condition || null),
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
        draft_key: c.key,
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
      draft_id: draftRef.current,
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
        const extras = (copyIds[k] ?? []).filter((id) => id !== ids[k]);
        if (c.usePhoto) {
          for (const extra of extras) {
            await cardApi.uploadImage(extra, file).catch(() => {});
          }
        } else if (extras.length) {
          // weitere Exemplare bekommen das Scan-Foto auch (sonst gelten sie als ohne Foto)
          await cardApi.sharePhoto(ids[k], extras).catch(() => {});
        }
      } catch { /* Foto-Upload optional */ }
    }));
    return res.data;
  };

  const finishReview = (next: boolean) => {
    if (next) {
      backToCapture();
    } else {
      closeDraft();
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
    } catch (e) {
      const conflict = conflictDetail(e);
      if (conflict) onConflict(conflict);
      else toast.error(t.scan_save_error);
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
    } catch (e) {
      patchCand(c.key, (cc) => ({ ...cc, saving: false }));
      const conflict = conflictDetail(e);
      if (conflict) onConflict(conflict);
      else toast.error(t.scan_save_error);
    }
  };

  // Zurück zur Aufnahme (Kamera ist noch an, falls sie lief) – oben, ohne Scrollen.
  const backToCapture = () => {
    closeDraft();
    setCandidates([]);
    setStep("setup");
    window.scrollTo({ top: 0 });
  };

  // Reverse holo? — mit einem Folien-Modell im Bild-Abgleich wird jede normale Karte,
  // die es als Reverse Holo gibt, einmal geprüft: im Hintergrund, eine nach der anderen.
  const foilModelRef = useRef<boolean | null>(null);     // false = kein Modell → nicht mehr fragen
  const foilKeyRef = useRef<string | null>(null);         // läuft gerade
  const [foilTick, setFoilTick] = useState(0);
  useEffect(() => {
    if (step !== "review" || foilModelRef.current === false || foilKeyRef.current) return;
    const next = candidates.find((c) => {
      const f = String((c.suggested as Record<string, unknown>)?.folierung ?? "") || "Normal";
      return c.foil === undefined && !c.saved && !c.variantOf && !!c.cropUrl && !!c.match?.tcgdex_card_id
        && f === "Normal" && c.match?.variants_reverse !== false;
    });
    if (!next) return;
    foilKeyRef.current = next.key;
    void (async () => {
      try {
        const crop = await (await fetch(next.cropUrl as string)).blob();
        const r = await scanApi.foil(crop, next.match?.tcgdex_card_id as string);
        if (!r.data.model) { foilModelRef.current = false; return; }
        foilModelRef.current = true;
        patchCand(next.key, (cc) => ({ ...cc, foil: r.data.aligned ? { hint: !!r.data.hint, score: r.data.score ?? 0 } : null }));
      } catch {
        patchCand(next.key, (cc) => ({ ...cc, foil: null }));
      } finally {
        foilKeyRef.current = null;
        setFoilTick((n) => n + 1);
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [candidates, step, foilTick]);

  // „Später bestätigen": diese Prüfung bleibt als offener Scan liegen, gleich das
  // nächste Foto — viele Karten am Stück scannen, alles später unter „Offene Scans".
  const [parked, setParked] = useState(0);
  // Prüfung: das ganze Foto mit den gefundenen Karten oben (ein-/ausklappbar, gemerkt)
  const [showPhoto, setShowPhoto] = useState(true);
  useEffect(() => {
    try { setShowPhoto(localStorage.getItem("scan_show_photo") !== "0"); } catch { /* egal */ }
  }, []);
  const toggleShowPhoto = () => setShowPhoto((v) => {
    try { localStorage.setItem("scan_show_photo", v ? "0" : "1"); } catch { /* egal */ }
    return !v;
  });
  const scrollToCard = (key: string) =>
    document.getElementById(`scan-card-${key}`)?.scrollIntoView({ behavior: "smooth", block: "start" });
  const confirmLater = async () => {
    if (busy) return;
    setBusy(true);
    try {
      await draftCreateRef.current;                        // Anlegen läuft noch
      if (draftRef.current == null && sourceBlobRef.current) {
        await startDraftRef.current(sourceBlobRef.current, candidatesRef.current);   // fehlgeschlagen → noch einmal
      }
      const id = draftRef.current;
      if (id == null) throw new Error("no draft");
      const st = serializeRef.current(candidatesRef.current);
      const body = JSON.stringify(st);
      if (body !== lastSentRef.current) {
        await scanDraftApi.update(id, st);                 // letzter Stand, bevor die Seite geleert wird
        lastSentRef.current = body;
      }
      // wie „Bestätigen & weiter": das nächste Foto kommt hinter dieses
      if (target === "collection" && slot != null) {
        const ps = pageSizeOf(targetCollection?.binder_layout);
        const n = candidates.filter((c) => c.include && !c.saved).reduce((a, c) => a + c.copies, 0);
        setSlot(mode === "binder" ? (Math.floor(slot / ps) + 1) * ps : slot + n);
      }
      // loslassen, nicht löschen
      draftSessionRef.current++;
      noneDetectedRef.current = false;
      draftRef.current = null;
      setDraftId(null);
      setDraftStatus(null);
      lastSentRef.current = "";
      setEditorIdx(null);
      setCandidates([]);
      setStep("setup");
      setParked((n) => n + 1);
      window.scrollTo({ top: 0 });
      refreshScanDrafts();
      toast.success(t.scan_parked);
    } catch {
      toast.error(t.scan_park_error);
    } finally {
      setBusy(false);
    }
  };

  // ── Render ────────────────────────────────────────────────────────────────
  const engineActive = status?.active ?? "none";

  return (
    <div className={`max-w-3xl mx-auto ${step === "review" ? "pb-36 sm:pb-24" : "pb-24"}`}>
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
          <ScanDraftPanel drafts={drafts} onResume={(id) => void resumeDraft(id)} busy={busy} compact={parked > 0 || cameraOn} />
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
                    <CameraGuide w={vidSize.w} h={vidSize.h} aspect={gAspect} cols={gridShown ? layoutCols : 1}
                      rows={gridShown ? layoutRows : 1} quad={lockQuad} steady={lockSteady} boxes={detQuads}
                      tilted={tiltQuad} />
                  )}
                  {vidSize && liveOn && (
                    <LiveOverlay w={vidSize.w} h={vidSize.h} state={liveState} onTap={(k) => setLivePick(k)} />
                  )}
                  {vidSize && liveOn && !busy && (() => {
                    const n = counts(liveState);
                    const total = n.green + n.yellow + n.red;
                    const same = allSure(liveState, mode) && signature(liveState) === lastDoneRef.current;
                    const text = liveChip === "paused" ? t.scan_live_paused : same ? t.scan_live_same
                      : liveChip === "hold" ? t.scan_live_hold
                      : liveChip === "checking" ? t.scan_live_checking : !total ? t.scan_live_looking : null;
                    return (
                      <div className="pointer-events-none absolute top-2 left-1/2 flex w-max max-w-[94%] -translate-x-1/2 flex-wrap items-center justify-center gap-x-2 rounded-2xl bg-black/60 px-3 py-1 text-center text-xs text-white"
                        data-testid="scan-live-status">
                        {total > 0 && (
                          <span className="flex items-center gap-1.5 font-medium">
                            <span className="text-green-400" data-testid="scan-live-green">● {n.green}</span>
                            <span className="text-yellow-300" data-testid="scan-live-yellow">● {n.yellow}</span>
                            <span className="text-red-400" data-testid="scan-live-red">● {n.red}</span>
                          </span>
                        )}
                        {text && <span>{text}</span>}
                      </div>
                    );
                  })()}
                  {vidSize && !(liveOn && !busy) && (() => {
                    const chip = busy ? t.scan_analyzing
                      : lockAspect !== null && autoCapture ? (waitingNext ? t.scan_cam_next : lockQuad ? t.scan_cam_hold : t.scan_cam_searching)
                      : detQuads ? t.scan_cam_found(detQuads.length) : null;
                    return chip && (
                      <div className="pointer-events-none absolute top-2 left-1/2 -translate-x-1/2 whitespace-nowrap rounded-full bg-black/60 px-3 py-1 text-xs text-white">
                        {chip}
                      </div>
                    );
                  })()}
                  {liveAvailable && step === "setup" && (
                    <button type="button" onClick={toggleLive} aria-pressed={live} title={t.scan_live_toggle_hint}
                      data-testid="scan-live"
                      className={`absolute top-10 left-2 rounded-full px-3 py-1.5 text-xs text-white ${live
                        ? "bg-pokemon-blue/80" : "bg-black/50 hover:bg-black/70"}`}>
                      <span className={live ? "text-green-300" : "text-gray-400"}>●</span> {t.scan_live}
                    </button>
                  )}
                  {liveOn && livePick && (() => {
                    const tr = liveState.tracks.find((x) => x.key === livePick);
                    if (!tr) return null;
                    const v = verdict(tr);
                    const opts = choices(tr);
                    return (
                      <div className="absolute inset-x-2 bottom-2 z-10 max-h-[75%] overflow-auto rounded-lg bg-gray-900/95 p-2 text-white shadow-xl"
                        data-testid="scan-live-pick">
                        <div className="mb-2 flex items-center justify-between gap-2 text-sm">
                          <span>
                            {t.scan_live_pick_title}{" "}
                            <span className="text-xs text-gray-400" data-testid="scan-live-why">· {t.scan_live_why(v.why)}</span>
                          </span>
                          <button type="button" onClick={() => setLivePick(null)} className="shrink-0 text-xs text-gray-300 hover:text-white">
                            {t.scan_live_pick_close}
                          </button>
                        </div>
                        {opts.length ? (
                          <div className="grid grid-cols-3 gap-2 sm:grid-cols-4">
                            {opts.map((a) => (
                              <button key={a.tcgdex_card_id} type="button" disabled={livePicking}
                                onClick={() => void pickLive(tr.key, a, v)} data-testid="scan-live-choice"
                                className={`rounded border p-1 text-left text-[11px] hover:border-white disabled:opacity-50 ${
                                  a.tcgdex_card_id === v.id ? "border-pokemon-yellow" : "border-gray-700"}`}>
                                {a.image_url && (
                                  // eslint-disable-next-line @next/next/no-img-element
                                  <img src={thumbUrl(a.image_url) ?? undefined} alt="" loading="lazy"
                                    className="mb-1 aspect-[63/88] w-full rounded object-cover" />
                                )}
                                <span className="block truncate">{a.name}</span>
                                <span className="block truncate text-gray-400">{[a.set_name, a.local_id].filter(Boolean).join(" · ")}</span>
                              </button>
                            ))}
                          </div>
                        ) : (
                          <p className="text-xs text-gray-400">{t.scan_live_pick_none}</p>
                        )}
                      </div>
                    );
                  })()}
                  {tiltOn && step === "setup" && (
                    <div className="absolute top-10 right-2 flex flex-row-reverse items-center gap-1">
                      <button type="button" onClick={toggleTilt} aria-pressed={tilt.state === "on" || tilt.state === "starting"}
                        title={t.scan_tilt_hint} data-testid="scan-tilt"
                        className={`rounded-full px-3 py-1.5 text-xs text-white ${tilt.state === "on" || tilt.state === "starting"
                          ? "bg-pokemon-blue/80" : "bg-black/50 hover:bg-black/70"}`}>
                        {t.scan_tilt}{tilt.state === "on" ? ` · ${Math.round(tiltDeg)}°` : ""}
                      </button>
                      {tilt.state === "on" && (
                        <button type="button" onClick={tilt.level} title={t.scan_tilt_level_hint} data-testid="scan-tilt-level"
                          className="rounded-full bg-black/50 px-3 py-1.5 text-xs text-white hover:bg-black/70">
                          {t.scan_tilt_level}
                        </button>
                      )}
                    </div>
                  )}
                  {/* Bedienung auf dem Bild (wie eine Kamera-App) – kein Scrollen nötig */}
                  <div className="pointer-events-none absolute inset-x-0 bottom-0 grid grid-cols-3 items-center bg-gradient-to-t from-black/75 to-transparent px-3 pb-3 pt-10 [&>*]:pointer-events-auto">
                    <button type="button" onClick={stopCamera}
                      className="justify-self-start rounded-full bg-black/50 px-3 py-1.5 text-xs text-white hover:bg-black/70">
                      {t.scan_stop_camera}
                    </button>
                    {liveOn ? (
                      <button type="button" onClick={() => finishLive()} disabled={busy} aria-label={t.scan_live_done}
                        title={t.scan_live_done} data-testid="scan-live-done"
                        className="justify-self-center flex h-16 w-16 items-center justify-center rounded-full border-4 border-green-400 bg-green-500/30 text-2xl text-white shadow-lg transition hover:bg-green-500/50 active:scale-95 disabled:opacity-50">
                        {busy ? <span className="block h-full w-full animate-pulse rounded-full bg-white/50" /> : "✓"}
                      </button>
                    ) : (
                    <button type="button" onClick={() => void handleCapture()} disabled={busy} aria-label={t.scan_capture}
                      title={t.scan_capture}
                      className="justify-self-center h-16 w-16 rounded-full border-4 border-white bg-white/25 shadow-lg transition hover:bg-white/40 active:scale-95 disabled:opacity-50">
                      {busy && <span className="block h-full w-full animate-pulse rounded-full bg-white/50" />}
                    </button>
                    )}
                    {lockAspect !== null && !liveOn ? (
                      <label className="justify-self-end flex items-center gap-1 rounded-full bg-black/50 px-3 py-1.5 text-xs text-white">
                        <input type="checkbox" checked={autoCapture} onChange={(e) => setAutoCapture(e.target.checked)} />
                        {t.scan_autocapture}
                      </label>
                    ) : <span />}
                  </div>
                </div>
                <p className="text-center text-gray-500 text-xs">
                  {liveOn ? t.scan_live_hint : mode === "single" ? t.scan_cam_hint_card : mode === "binder" ? t.scan_cam_hint_page : t.scan_cam_hint_multi}
                  {gridShown && <span className="block">{tilt.state === "on" ? t.scan_tilt_on_hint : t.scan_tilt_hint}</span>}
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
            {mode === "multi" && (
              <div>
                <label htmlFor="scan-multi-grid" className="text-gray-400 text-xs block mb-1">{t.scan_multi_grid}</label>
                <input
                  id="scan-multi-grid"
                  type="text"
                  value={multiGrid}
                  onChange={(e) => changeMultiGrid(e.target.value)}
                  placeholder={t.scan_multi_grid_placeholder}
                  title={t.scan_multi_grid_hint}
                  data-testid="scan-multi-grid"
                  className="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1.5 text-white text-sm font-mono"
                />
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
          <div className="flex items-center justify-between gap-2">
            <div className="min-w-0">
              <h2 className="text-white font-medium">{t.scan_results_title(candidates.length)}</h2>
              {draftStatus && (
                <p className={`text-[11px] ${draftStatus === "error" ? "text-amber-400" : "text-gray-500"}`} data-testid="scan-draft-status">
                  {draftStatus === "saving" ? t.scan_draft_saving : draftStatus === "saved" ? t.scan_draft_saved : t.scan_draft_error}
                </p>
              )}
            </div>
            <button type="button" onClick={() => backToCapture()} className="shrink-0 text-gray-500 hover:text-white text-sm">{t.scan_discard_all}</button>
          </div>
          {candidates.length === 0 && (
            <p className="rounded border border-amber-700/50 bg-amber-950/30 p-2 text-xs text-amber-200" data-testid="scan-none-found">
              {t.scan_none_found_hint}
            </p>
          )}
          {sourceUrl && (() => {
            // Das ganze Foto: jede gefundene Karte umrandet und nummeriert wie in der Liste —
            // eine Karte ohne Rahmen wurde übersehen
            const marked = candidates.map((c) => ({ c, q: outlineOf(c) })).filter((x): x is { c: EditableCandidate; q: number[][] } => !!x.q);
            return (
              <div className="rounded-lg bg-pokemon-card p-2" data-testid="scan-photo-overview">
                <div className="mb-1 flex items-center justify-between gap-2 text-xs text-gray-400">
                  <span>{showPhoto ? t.scan_photo_hint : t.scan_photo_title}</span>
                  <button type="button" onClick={toggleShowPhoto} data-testid="scan-photo-toggle"
                    className="shrink-0 text-pokemon-blue hover:underline">
                    {showPhoto ? t.scan_photo_hide : t.scan_photo_show}
                  </button>
                </div>
                {showPhoto && (
                  <div className="flex justify-center">
                    <div className="relative inline-block max-w-full">
                      {/* eslint-disable-next-line @next/next/no-img-element */}
                      <img src={sourceUrl} alt="" className="block h-auto max-h-[50vh] w-auto max-w-full rounded" />
                      <svg className="pointer-events-none absolute inset-0 h-full w-full" viewBox="0 0 1 1" preserveAspectRatio="none">
                        {marked.map(({ c, q }) => (
                          <polygon key={c.key} points={ringOrder(q).map(([x, y]) => `${x},${y}`).join(" ")}
                            fill={c.saved ? "rgba(34,197,94,0.18)" : "rgba(250,204,21,0.08)"}
                            stroke={c.saved ? "#22c55e" : c.include ? "#facc15" : "#9ca3af"} strokeWidth={2}
                            strokeDasharray={c.include || c.saved ? undefined : "4 3"} vectorEffect="non-scaling-stroke"
                            data-testid="scan-photo-outline" />
                        ))}
                      </svg>
                      {marked.map(({ c, q }, i) => (
                        <button type="button" key={c.key} onClick={() => scrollToCard(c.key)}
                          title={String((c.suggested as Record<string, unknown>)?.kartenname ?? c.raw?.name ?? "")}
                          data-testid="scan-photo-badge"
                          className={`absolute flex h-5 w-5 -translate-x-1/2 -translate-y-1/2 items-center justify-center rounded-full text-[10px] font-bold shadow ${
                            c.saved ? "bg-green-500 text-black" : "bg-pokemon-yellow text-black"}`}
                          style={{ left: `${(q.reduce((a, p) => a + p[0], 0) / 4) * 100}%`, top: `${(q.reduce((a, p) => a + p[1], 0) / 4) * 100}%` }}>
                          {i + 1}
                        </button>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            );
          })()}
          {sourceUrl && (mode !== "single" || candidates.length === 0) && (
            <button type="button" onClick={() => setAddingMissed(true)} title={t.scan_add_missed_hint}
              data-testid="scan-add-missed"
              className="w-full rounded-lg border border-dashed border-gray-600 py-2 text-sm text-gray-300 hover:border-gray-400 hover:text-white">
              ＋ {t.scan_add_missed}
            </button>
          )}

          {candidates.map((c, idx) => {
            const s = c.suggested as Record<string, unknown>;
            // Nummer wie im Foto oben (nur Karten mit eigener Stelle im Foto)
            const photoNo = outlineOf(c) ? candidates.slice(0, idx + 1).filter((x) => outlineOf(x)).length : null;
            const noBadge = photoNo != null && (
              <span className={`mr-2 inline-flex h-5 w-5 shrink-0 items-center justify-center rounded-full text-[10px] font-bold text-black ${
                c.saved ? "bg-green-500" : "bg-pokemon-yellow"}`} data-testid="scan-card-no">{photoNo}</span>
            );
            const uncertain = c.uncertain_fields.length > 0;
            const foilOptions = c.foil_options.length ? c.foil_options : (enums?.folierung ?? ["Normal"]);
            const pokedexNr = (s.pokedex_nr as number) ?? c.match?.dex_id ?? null;
            const catalogImg = matchImage(c);
            const baseImgLang = imageLangOf(c.match?.image_url);
            if (c.saved) {
              return (
                <div key={c.key} data-testid="scan-card-saved" id={`scan-card-${c.key}`}
                  className="flex scroll-mt-20 items-center justify-between gap-2 rounded-lg border border-green-800 bg-green-900/20 px-3 py-2 text-sm">
                  <span className="flex min-w-0 items-center text-green-300">{noBadge}<span className="truncate">✓ {t.scan_card_saved((lang === "EN" && c.saved.name_en) || c.saved.name, c.saved.ids.length)}</span></span>
                  <Link href={`/cards/${c.saved.ids[0]}`} className="shrink-0 text-xs text-pokemon-blue hover:underline">{t.scan_open_card}</Link>
                </div>
              );
            }
            return (
              <div key={c.key} data-testid="scan-card" id={`scan-card-${c.key}`}
                className={`scroll-mt-20 rounded-lg border p-3 ${
                  c.needs_choice ? "border-pokemon-yellow"
                  : !c.include ? "border-gray-800 opacity-50" : uncertain ? "border-pokemon-red" : "border-gray-700"
                } bg-pokemon-card`}>
                {/* Kopfzeile: Pokédex-Nr. + Übernehmen / Im Pokédex */}
                <div className="flex items-center justify-between gap-2 mb-2 flex-wrap">
                  <span className="flex items-center text-xs text-gray-400">
                    {noBadge}
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
                      <input type="checkbox" checked={c.prizePack} onChange={() => togglePrizePack(idx)} data-testid="scan-prize-pack" />
                      {t.scan_prize_pack}
                      {c.stamp?.ok && (
                        // Play!-Stempel gefunden (sicher: schon angehakt) bzw. vielleicht: bitte prüfen
                        <span data-testid="scan-stamp-found" data-sure={c.stamp.sure ? "1" : "0"}
                          title={c.stamp.sure ? t.scan_stamp_found_hint : t.scan_stamp_maybe_hint}
                          className={`rounded px-1 py-0.5 text-[10px] ${c.stamp.sure ? "bg-yellow-900/50 text-yellow-200" : "bg-gray-700 text-gray-200"}`}>
                          {c.stamp.sure ? t.scan_stamp_found(Math.round(c.stamp.score * 100)) : t.scan_stamp_maybe(Math.round(c.stamp.score * 100))}
                        </span>
                      )}
                    </label>
                    {/* 1st Edition: immer anhakbar — der Katalog kennt die Varianten nicht
                        zuverlässig; kennt er keine, nur blasser mit Hinweis */}
                    <label className={`flex items-center gap-1 ${c.match?.variants_firstedition === false && !c.firstEdition ? "text-gray-500" : "text-gray-300"}`}
                      title={c.match?.variants_firstedition === false ? t.scan_first_edition_unlisted : t.scan_first_edition_hint}>
                      <input type="checkbox" checked={c.firstEdition} onChange={() => toggleFirstEdition(idx)}
                        data-testid="scan-first-edition" />
                      {t.first_edition_label}
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
                  <div className={`mb-2 rounded p-2 ${c.needs_choice ? "border border-pokemon-yellow bg-yellow-500/10"
                    : c.print_choice && !c.chosenId ? "border border-pokemon-yellow/40 bg-yellow-500/5" : "bg-gray-800/40"}`}
                    data-testid={c.print_choice ? "scan-print-choice" : undefined}>
                    <div className={`text-xs mb-1 ${c.needs_choice || (c.print_choice && !c.chosenId) ? "text-pokemon-yellow" : "text-gray-400"}`}>
                      {c.needs_choice ? t.scan_pick_title : c.print_choice && !c.chosenId ? t.scan_pick_print : t.scan_pick_others}
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
                  {/* Bilder: das Foto (Zuschnitt) UND der Katalog-Treffer nebeneinander — das
                      Foto hilft beim Prüfen der Ecken, auch wenn das Katalogbild das Kartenbild wird */}
                  {(() => {
                    const own = c.cropUrl ?? (mode === "single" ? sourceUrl : null);
                    const both = !!own && !!catalogImg;
                    const tile = (src: string, kind: "photo" | "catalog") => {
                      const chosen = both && (kind === "photo" ? c.usePhoto : !c.usePhoto);
                      return (
                        <div className={both ? "w-28 sm:w-32" : "w-32 sm:w-36"} data-testid={`scan-img-${kind}`}>
                          <a href={src} target="_blank" rel="noreferrer" title={t.scan_view_large}
                            className={`relative block rounded ${chosen ? "ring-2 ring-pokemon-blue" : ""}`}>
                            {/* eslint-disable-next-line @next/next/no-img-element */}
                            <img src={src} alt="" className="w-full rounded hover:opacity-90"
                              onError={() => {
                                // Bild in dieser Sprache gibt es nicht → zurück zur gelieferten Fassung
                                if (kind === "catalog" && c.imageLang) {
                                  toast(t.image_lang_missing, { icon: "🖼" });
                                  patchCand(c.key, (cc) => ({ ...cc, imageLang: null }));
                                }
                              }} />
                            {c.rematching && kind === "photo" && (
                              <span className="absolute inset-0 flex items-center justify-center rounded bg-black/60 text-xs text-white animate-pulse">
                                {t.scan_rechecking}
                              </span>
                            )}
                          </a>
                          {both && (
                            <div className={`mt-0.5 text-center text-[10px] ${chosen ? "text-pokemon-blue" : "text-gray-500"}`}>
                              {kind === "photo" ? t.scan_img_photo : t.scan_img_catalog}{chosen ? ` · ${t.scan_img_used}` : ""}
                            </div>
                          )}
                        </div>
                      );
                    };
                    return (
                      <div className="mx-auto shrink-0 sm:mx-0">
                        <div className="flex justify-center gap-2">
                          {own && tile(own, "photo")}
                          {catalogImg && tile(catalogImg, "catalog")}
                          {!own && !catalogImg && (
                            <div className="aspect-[63/88] w-32 bg-gray-800 rounded flex items-center justify-center text-gray-600 text-xs">?</div>
                          )}
                        </div>
                        <div className="mt-1 text-center text-[10px] text-gray-500">
                          {t.scan_confidence}: {Math.round(c.confidence * 100)}%
                        </div>
                        {both && (
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
                    );
                  })()}

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
                      <div className="flex min-w-0 items-center gap-0.5" data-testid="scan-card-nr">
                        <input
                          value={splitCardNr(s.karten_nr)[0]}
                          onChange={(e) => updateField(idx, "karten_nr", e.target.value.includes("/")
                            ? joinCardNr(...splitCardNr(e.target.value)) : joinCardNr(e.target.value, splitCardNr(s.karten_nr)[1]))}
                          onBlur={() => void refreshCandidate(idx)}
                          placeholder={t.scan_field_nr}
                          aria-label={t.form_card_nr}
                          className="min-w-0 flex-1 bg-gray-800 border border-gray-700 rounded px-2 py-1 text-white text-xs font-mono"
                        />
                        <span className="text-gray-500 text-xs">/</span>
                        <input
                          value={splitCardNr(s.karten_nr)[1]}
                          onChange={(e) => updateField(idx, "karten_nr", joinCardNr(splitCardNr(s.karten_nr)[0], e.target.value.replace("/", "")))}
                          onBlur={() => void refreshCandidate(idx)}
                          aria-label={t.form_card_total} title={t.form_card_total}
                          className="w-12 shrink-0 bg-gray-800 border border-gray-700 rounded px-1 py-1 text-white text-xs font-mono"
                        />
                      </div>
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
                    {c.foil?.hint && String(s.folierung ?? "") !== "Reverse Holo" && (
                      // Folien-Modell: der Rahmen glänzt wie bei einer Reverse Holo — antippen setzt es
                      <button type="button" data-testid="scan-foil-hint"
                        onClick={() => { updateField(idx, "folierung", "Reverse Holo"); }}
                        title={t.scan_foil_hint_title((c.foil.score ?? 0).toFixed(1))}
                        className="w-full rounded border border-purple-500/60 bg-purple-950/40 px-2 py-1 text-left text-xs text-purple-200 hover:bg-purple-900/40">
                        ✨ {t.scan_foil_hint} <span className="text-purple-300/80">— {t.scan_foil_set}</span>
                      </button>
                    )}
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
                      {/* Zustand (nicht bei der Wunschliste) */}
                      {target !== "wishlist" && (
                        <select value={c.condition} onChange={(e) => setCondition(idx, e.target.value)}
                          data-testid="scan-condition" aria-label={t.field_condition}
                          className="min-w-0 bg-gray-800 border border-gray-700 rounded px-2 py-1.5 text-white text-xs">
                          <option value="">{t.field_condition}</option>
                          {(enums?.zustand ?? []).map((z) => <option key={z} value={z}>{valueLabel(z, lang)}</option>)}
                        </select>
                      )}
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
            <div className="max-w-3xl mx-auto flex flex-wrap sm:flex-nowrap gap-2 sm:gap-3">
              {(() => {
                // Alles einzeln gespeichert oder verworfen → nur noch weiter bzw. fertig
                const open = candidates.some((c) => c.include && !c.saved);
                const blocked = busy || candidates.some((c) => c.rematching || c.saving);
                return (
                  <>
                    {(candidates.some((c) => !c.saved) || candidates.length === 0) && (
                      // Handy: eigene Zeile über den beiden; breiter: links daneben
                      <button type="button" onClick={() => void confirmLater()} disabled={blocked}
                        data-testid="scan-later" title={t.scan_later_hint}
                        className="w-full sm:w-auto sm:flex-1 rounded border border-pokemon-blue/60 px-3 py-1.5 sm:py-2.5 text-sm text-pokemon-blue hover:bg-pokemon-blue/10 disabled:opacity-50">
                        {t.scan_later}
                      </button>
                    )}
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
          suggest={outlineSuggest(null)}
        />
      )}

      {editorIdx != null && sourceUrl && candidates[editorIdx] && (
        <CornerEditor
          imageUrl={sourceUrl}
          initialQuad={initialQuadFor(candidates[editorIdx])}
          autoOrder={!candidates[editorIdx].editedQuad}
          onCancel={() => setEditorIdx(null)}
          onApply={(quad, transform) => void applyCorners(editorIdx, quad, transform)}
          suggest={outlineSuggest(candidates[editorIdx].match?.tcgdex_card_id ?? null)}
          suggestOnOpen={!!candidates[editorIdx].match?.tcgdex_card_id}
        />
      )}

    </div>
  );
}


/** Das Raster, wie die flache Seite aus dem geneigten Handy aussieht (sonst wie CameraGuide). */
function TiltedGuide({ w, h, cols, rows, quad, found, steady, boxes }: {
  w: number; h: number; cols: number; rows: number; quad: Quad; found: Pt[] | null; steady: boolean; boxes?: Pt[][] | null;
}) {
  const at = squareToQuad(quad);
  const color = found ? (steady ? "#22c55e" : "#86efac") : "#facc15";
  const pts = (ps: Pt[]) => ps.map(([x, y]) => `${x},${y}`).join(" ");
  const line = (a: Pt, b: Pt, key: string) => (
    <line key={key} x1={a[0]} y1={a[1]} x2={b[0]} y2={b[1]} stroke={color} strokeOpacity={0.45} strokeWidth={1}
      vectorEffect="non-scaling-stroke" />
  );
  return (
    <svg className="pointer-events-none absolute inset-0 h-full w-full" viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none"
      data-testid="scan-tilted-guide">
      <path fillRule="evenodd" fill="rgba(0,0,0,0.38)"
        d={`M0 0H${w}V${h}H0Z M${quad.map(([x, y]) => `${x} ${y}`).join(" L")}Z`} />
      {Array.from({ length: cols - 1 }, (_, i) => line(at((i + 1) / cols, 0), at((i + 1) / cols, 1), `c${i}`))}
      {Array.from({ length: rows - 1 }, (_, i) => line(at(0, (i + 1) / rows), at(1, (i + 1) / rows), `r${i}`))}
      <polygon points={pts(quad)} fill="none" stroke={color} strokeWidth={2.5} strokeLinejoin="round"
        vectorEffect="non-scaling-stroke" />
      {found && (
        <polygon points={pts(found)} fill="rgba(34,197,94,0.12)" stroke="#22c55e" strokeWidth={2}
          vectorEffect="non-scaling-stroke" />
      )}
      {boxes?.map((b, i) => (
        <polygon key={i} points={pts(b)} fill="rgba(56,189,248,0.14)" stroke="#38bdf8"
          strokeWidth={2} strokeLinejoin="round" vectorEffect="non-scaling-stroke" />
      ))}
    </svg>
  );
}

/**
 * Rahmen über dem Live-Bild, in Video-Pixeln (viewBox = Videoformat, das Video
 * wird unverzerrt in voller Breite gezeigt): Bereich außerhalb abgedunkelt,
 * bei der Mappe das Raster, und die gefundene Karte als grüne Kontur.
 */
const LIVE_COLOURS: Record<Level, { stroke: string; fill: string }> = {
  green: { stroke: "#22c55e", fill: "rgba(34,197,94,0.18)" },
  yellow: { stroke: "#facc15", fill: "rgba(250,204,21,0.16)" },
  red: { stroke: "#ef4444", fill: "rgba(239,68,68,0.16)" },
};

/** Live-Scan: jede gefundene Karte in ihrer Farbe, mit Namen; antippen → Treffer zur Wahl. */
function LiveOverlay({ w, h, state, onTap }: { w: number; h: number; state: LiveState; onTap: (key: string) => void }) {
  const fs = Math.max(w, h) * 0.024;
  return (
    <svg className="pointer-events-none absolute inset-0 h-full w-full" viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none"
      data-testid="scan-live-overlay">
      {state.tracks.map((tr) => {
        const v = verdict(tr);
        const c = LIVE_COLOURS[v.level];
        const pts = tr.quad.map(([x, y]) => [x * w, y * h]);
        const x0 = Math.min(...pts.map((p) => p[0])), y0 = Math.min(...pts.map((p) => p[1]));
        const s = (v.cand?.suggested ?? {}) as Record<string, unknown>;
        const raw = v.level === "red" && v.why !== "conflict" ? "?" : String(s.kartenname ?? v.cand?.match?.name ?? "?");
        const name = raw.length > 16 ? `${raw.slice(0, 15)}…` : raw;
        return (
          <g key={tr.key} data-testid="scan-live-card" data-level={v.level} data-why={v.why}
            style={{ pointerEvents: "all", cursor: "pointer" }} onClick={() => onTap(tr.key)}>
            <polygon points={pts.map(([x, y]) => `${x},${y}`).join(" ")} fill={c.fill} stroke={c.stroke}
              strokeWidth={3} strokeLinejoin="round" vectorEffect="non-scaling-stroke" />
            <text x={x0 + fs * 0.3} y={y0 + fs * 1.15} fontSize={fs} fill="#fff" stroke="rgba(0,0,0,0.75)"
              strokeWidth={fs * 0.16} paintOrder="stroke" fontWeight={600}>
              {v.level === "green" ? "✓ " : ""}{name}
            </text>
          </g>
        );
      })}
    </svg>
  );
}

function CameraGuide({ w, h, aspect, cols, rows, quad, steady, boxes, tilted }: {
  w: number; h: number; aspect: number | null; cols: number; rows: number; quad: Pt[] | null; steady: boolean;
  boxes?: Pt[][] | null;   // vom Bild-Abgleich gefundene Karten (mehrere Karten / Mappe)
  tilted?: Quad | null;    // Mappenseite aus dem geneigten Handy (Lagesensor): Raster als Viereck
}) {
  const g = aspect !== null ? guideRect(w, h, aspect) : null;
  if (g && tilted) return <TiltedGuide w={w} h={h} cols={cols} rows={rows} quad={tilted} found={quad} steady={steady} boxes={boxes} />;
  const r = g ? (Math.min(g.w / cols, g.h / rows) * 0.045) : 0;   // Kartenecken ~3 mm von 63 mm
  const color = quad ? (steady ? "#22c55e" : "#86efac") : "#facc15";
  return (
    <svg className="pointer-events-none absolute inset-0 h-full w-full" viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none"
      data-testid="scan-guide">
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
