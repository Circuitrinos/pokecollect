import { getUiLang } from "@/lib/uiLang";
import axios from "axios";

// Zentrale API-Basis-URL — einzige Quelle, keine Kopien in Seiten (Issue #14).
// Abschließende Schrägstriche fallen weg (#91-Nacharbeit): imageUrl() hängt
// "/images/…" an, aus "http://host/" würde sonst "http://host//images/…" —
// ein Pfad, den das remotePatterns-Muster ("/images/**") NICHT deckt. Am
// echten Optimizer gemessen: 400 "url parameter is not allowed". Ein
// abschließender Schrägstrich in der .env ist ein plausibler Tippfehler und
// hätte still alle eigenen Fotos ausgeblendet.
export const API_BASE = (process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:3010")
  .replace(/\/+$/, "");

export const api = axios.create({
  baseURL: `${API_BASE}/api/v1`,
});

api.interceptors.request.use((config) => {
  const token = typeof window !== "undefined" ? localStorage.getItem("token") : null;
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  config.headers["X-UI-Lang"] = getUiLang();   // Fehlermeldungen in der UI-Sprache
  return config;
});

// 401 → Token verwerfen und zur Login-Seite (Issue #1). window.location statt
// Next-Router: der Interceptor läuft außerhalb von React. Ausgenommen sind der
// Login-Call selbst (falsches Passwort soll ein Toast sein, kein Redirect) und
// Aufrufe, die schon auf /login passieren (keine Redirect-Schleife).
api.interceptors.response.use(
  (response) => response,
  (error) => {
    if (
      typeof window !== "undefined" &&
      error?.response?.status === 401 &&
      !String(error?.config?.url ?? "").includes("/auth/login") &&
      window.location.pathname !== "/login" &&
      window.location.pathname !== "/auth/callback"
    ) {
      localStorage.removeItem("token");
      window.location.href = "/login";
    }
    return Promise.reject(error);
  }
);

// ── Types ─────────────────────────────────────────────────────────────────

export type Card = {
  id: number;
  kartenname: string;
  pokedex_nr: number | null;
  englischer_name: string | null;
  set_edition: string | null;
  karten_nr: string | null;
  seltenheit: string | null;
  kartenversion: string | null;
  folierung: string | null;
  muster: string | null;       // Folierungs-Muster (#63): Pokéball/Masterball/…
  erste_edition: boolean;
  prize_pack: boolean;         // Play! Pokémon-Stempel (Prize Pack Series)
  // Turnier-Legalität (backend services/regulation.py): aufgedrucktes Zeichen,
  // für Standard maßgebliches Zeichen (inkl. Reprint-Regel; "-" = keins,
  // null = unbekannt) und Basis-Energie (immer legal).
  regulation_mark?: string | null;
  legal_mark?: string | null;
  basic_energy?: boolean;
  name_mark?: string | null;         // gleichnamiger legaler Druck mit anderem/unbekanntem Text
  legal_expanded?: boolean | null;   // TCGdex-Flag, nur Einzelkarten
  sprache: string;
  besessen: boolean;
  wunschliste: boolean;
  im_pokedex: boolean;
  prioritaet: string | null;
  wert_eur: string | null;
  wert_aktualisiert: string | null;
  kaufpreis_eur: string | null;
  kaufdatum: string | null;
  notizen: string | null;
  zustand: string | null;
  bild_pokedex_url: string | null;
  bild_karte_url: string | null;       // auto: TCGdex high.webp
  bild_sprache?: string | null;       // Sprache des Katalogbilds (TCGdex), null = nicht umschaltbar
  bild_quad?: number[][] | null;      // Ecken des Zuschnitts im Originalfoto (normiert)
  stamp_quad?: number[][] | null;     // Play!-Stempel im Kartenfoto (Kartenkoordinaten), von Hand markiert
  // #120: dieselben Bilder als signierter Spiegel-Pfad der eigenen API —
  // NUR diese Felder fließen in die Anzeige (lib/utils.ts cardImageSrc).
  // null bei fehlender URL oder fremdem Host.
  bild_karte_spiegel?: string | null;
  bild_pokedex_spiegel?: string | null;
  bild_platzhalter_spiegel?: string | null;  // Pokédex-Artwork (Platzhalter)
  bild_karte_pfad: string | null;
  bild_thumbnail_pfad: string | null;
  bild_original_pfad: string | null;
  bild_scan_pfad?: string | null;     // eigenes Scan-Foto, während das Katalogbild angezeigt wird
  katalog_bild?: boolean | null;      // Katalogbild vorhanden (nur Einzelkarten)
  // Drucke laut Katalog (Folierung/Muster wählen), nur Einzelkarten-Antworten
  katalog_varianten?: { normal: boolean | null; reverse: boolean | null; holo: boolean | null;
    first_edition: boolean | null; patterns: string[] } | null;
  deck_id?: number | null;            // Deck, in dem die Karte steckt (höchstens eines)
  deck_name?: string | null;
  // TCGdex-Anreicherung (v0.7.0)
  tcgdex_card_id: string | null;
  set_id: string | null;
  dex_id: number | null;
  illustrator: string | null;
  variants_normal: boolean | null;
  variants_reverse: boolean | null;
  variants_holo: boolean | null;
  variants_firstedition: boolean | null;
  hinzugefuegt_am: string;
  aktualisiert_am: string;
  // TCGplayer-$ aus dem Katalog-Cache (Epic #41) — nur Einzelkarten-Antworten
  katalog_preis_usd?: string | null;
  katalog_preis_usd_stand?: string | null;
  // Variante des $-Preises + Herkunft des zuletzt gesetzten Werts (v1.8.2)
  katalog_preis_usd_variante?: string | null;
  wert_quelle?: string | null;
  // Daten-/Bild-Herkunft für die Herkunfts-Zeile in der Detailansicht (#47) —
  // wie wert_quelle nur in Einzelkarten-Antworten gefüllt, in Listen None.
  bild_quelle?: string | null;
  daten_quelle?: string | null;
};

export type CardListResponse = {
  items: Card[];
  total: number;
  page: number;
  limit: number;
  pages: number;
};

export type StatsResponse = {
  gesamt: number;
  besessen: number;
  nicht_besessen: number;
  gesamtwert_eur: string | null;
  gesamt_einstand_eur: string | null;
  unrealisierter_gv_eur: string | null;
  // Sealed-Produkte (#35): getrennt + Karten+Sealed kombiniert
  sealed_anzahl: number;
  sealed_wert_eur: string | null;
  sealed_einstand_eur: string | null;
  sealed_unrealisierter_gv_eur: string | null;
  kombiniert_wert_eur: string | null;
  kombiniert_einstand_eur: string | null;
  kombiniert_unrealisierter_gv_eur: string | null;
  sets: Record<string, number>;
  seltenheiten: Record<string, number>;
  sprachen: Record<string, number>;
  top10_teuerste: Card[];
  zuletzt_hinzugefuegt: Card[];
};

export type Enums = {
  seltenheit: string[];
  kartenversion: string[];
  folierung: string[];
  muster?: string[];           // Folierungs-Muster (#63)
  sprache: string[];
  zustand: string[];
  prioritaet: string[];
};

// Sealed-Produkt (#35) — eigene Entität, n:m-Set-Bezug, manueller Wert.
export type SealedProduct = {
  id: number;
  name: string;
  typ: string | null;
  zustand: string | null;
  kaufpreis_eur: string | null;
  kaufdatum: string | null;
  wert_eur: string | null;
  notizen: string | null;
  set_codes: string[];
  bild_pfad: string | null;
  bild_thumbnail_pfad: string | null;
  // Sealed-Katalog (#46): TCGplayer-Verknüpfung + CDN-Bild + Auto-Wert-Stand
  tcgplayer_product_id?: number | null;
  bild_url?: string | null;
  bild_spiegel?: string | null;   // #120: bild_url über die eigene API
  wert_aktualisiert?: string | null;
  hinzugefuegt_am: string | null;
  unrealisierter_gv_eur: string | null;
};

export type SealedEnums = {
  typ: string[];
  zustand: string[];
};

export type SealedCatalogItem = {
  product_id: number;
  region: string;
  set_code: string | null;
  name: string;
  image_url: string | null;
  image_spiegel?: string | null;  // #120: image_url über die eigene API
  price_usd: number | null;
  price_usd_updated: string | null;
};

export type CollectionProgress = {
  erfuellt: number;
  soll: number;
};

export type Collection = {
  id: number;
  name: string;
  beschreibung: string | null;
  binder_layout: string | null;
  binder_slots: number | null;
  view_mode?: "grid" | "binder";   // Ansicht, serverseitig je Sammlung gemerkt
  grid_sort?: "manual" | "name" | "pokedex" | "set" | "value" | "regulation" | "newest";   // Sortierung im Raster
  grid_sort_reverse?: boolean;   // Sortierung umgekehrt
  grid_sort_then?: string;       // Untersortierungen, z.B. "name,-set"
  erstellt_am: string | null;
  karten_anzahl: number;
  // Set-Sammlung / Sammelziel (Issue #16)
  typ: string; // "frei" | "set_ziel"
  ziel_set_id: string | null;
  ziel_folierung: string | null;
  ziel_sprache: string | null;
  ziel_master_set: boolean;
  fortschritt: CollectionProgress | null; // nur bei typ="set_ziel"
  geteilt_mit?: number;                   // mit so vielen Freunden geteilt
};

// Freunde + geteilte Sammlungen (nur ansehen)
export type FriendUser = { id: number; username: string; display_name: string | null };
export type Friend = FriendUser & { since: string | null };
export type FriendRequest = { id: number; user: FriendUser; created_at: string | null };
export type FriendsOverview = { friends: Friend[]; incoming: FriendRequest[]; outgoing: FriendRequest[] };
export type SharedCollection = {
  id: number;
  name: string;
  beschreibung: string | null;
  owner: FriendUser;
  karten_anzahl: number;
  binder_layout: string;
  binder_slots: number | null;
  view_mode: "grid" | "binder";
  grid_sort: Collection["grid_sort"];
  grid_sort_reverse: boolean;
  grid_sort_then: string;
};
export type SharedCollectionDetail = SharedCollection & { cards: CollectionCard[] };

// Soll-Slot einer Set-Sammlung: Katalog-Karte + erfüllt-Status (Issue #16)
export type SollSlot = {
  id: number;
  tcgdex_card_id: string;
  soll_folierung: string | null; // null = Regel (ziel_folierung) gilt
  position: number | null;
  erfuellt: boolean;
  karte_id: number | null;
  karte: Card | null; // erfüllende Bestandskarte
  name: string | null;
  name_en: string | null;
  local_id: string | null;
  image_url: string | null;
  rarity: string | null;
  set_id: string | null;
  set_code: string | null;
  set_name: string | null;
  set_name_en?: string | null;
  dex_id: number | null;
};

export type CollectionCard = Card & { position: number | null };

export const BINDER_LAYOUTS = ["1x1", "2x2", "3x3", "4x3", "3x4", "4x4"] as const;

// ── API Calls ─────────────────────────────────────────────────────────────

export type BulkAction = "catalog_image" | "own_photo" | "prize_pack" | "zustand"
  | "add_to_collection" | "remove_from_collection" | "delete";
export type BulkResult = { done: number; unchanged: number; missing: number };

export const cardApi = {
  // Sammel-Aktion im Auswahl-Modus; allScanned nur für catalog_image (alle gescannten eigenen Karten)
  bulk: (action: BulkAction, ids: number[], value?: unknown, allScanned = false) =>
    api.post<BulkResult>("/cards/meta/bulk", { action, ids, value: value ?? null, all_scanned: allScanned }),
  list: (params: Record<string, unknown> = {}) =>
    api.get<CardListResponse>("/cards", { params }),

  get: (id: number) => api.get<Card>(`/cards/${id}`),

  create: (data: Partial<Card>) => api.post<Card>("/cards", data),

  update: (id: number, data: Partial<Card>) => api.put<Card>(`/cards/${id}`, data),

  delete: (id: number) => api.delete(`/cards/${id}`),

  uploadImage: (id: number, file: File, original?: File | null, quad?: number[][] | null) => {
    const form = new FormData();
    form.append("file", file);
    if (original) form.append("original", original);
    // Ecken des Zuschnitts im Original (normiert) – Trainingsdaten für die Erkennung
    if (original && quad && quad.length === 4) form.append("quad", JSON.stringify(quad));
    return api.post<Card>(`/cards/${id}/image`, form);
  },

  // Scan-Foto nur für die Bilderkennung – die Karte zeigt weiter das Katalogbild
  uploadScanPhoto: (id: number, file: File, original: File, quad?: number[][] | null) => {
    const form = new FormData();
    form.append("file", file);
    form.append("original", original);
    if (quad && quad.length === 4) form.append("quad", JSON.stringify(quad));
    return api.post<Card>(`/cards/${id}/scan-photo`, form);
  },

  // Zuschnitt aus dem gespeicherten Original neu (korrigierte Ecken)
  recrop: (id: number, file: File, quad: number[][]) => {
    const form = new FormData();
    form.append("file", file);
    form.append("quad", JSON.stringify(quad));
    return api.post<Card>(`/cards/${id}/recrop`, form);
  },
  scanCheck: (id: number) => api.get<ScanCheck | null>(`/cards/${id}/scan-check`),
  setStamp: (id: number, quad: number[][] | null) => api.put<Card>(`/cards/${id}/stamp`, { quad }),
  // Play!-Stempel im eigenen Foto vorschlagen (gelernte Vorlage; um quad herum, wenn gegeben)
  stampSuggest: (id: number, quad: number[][] | null) =>
    api.post<StampSuggestion>(`/cards/${id}/stamp-suggest`, { quad }),
  // Grobe Ecken → Vorschlag vom Bild-Abgleich (nichts wird gespeichert)
  cropSuggest: (id: number, file: Blob, quad: number[][]) => {
    const form = new FormData();
    form.append("file", file, "try.jpg");
    form.append("quad", JSON.stringify(quad));
    return api.post<{ status: string; inliers: number; corner_err: number | null; suggested_quad: number[][] | null }>(
      `/cards/${id}/crop-suggest`, form);
  },

  deleteImage: (id: number) => api.delete<Card>(`/cards/${id}/image`),
  // Katalogbild statt eigenem Foto zeigen; sucht eins, falls noch keins bekannt
  useCatalogImage: (id: number) => api.post<Card>(`/cards/${id}/catalog-image`),
  // Katalogbild in einer anderen Sprache (TCGdex, westliche Karten)
  setImageLanguage: (id: number, lang: string) => api.post<Card>(`/cards/${id}/image-language`, { lang }),
  // Mit einer Katalogkarte verknüpfen („Richtige Karte finden")
  // Link von pokemon.com (Kartenseite/Bildadresse), im eigenen Browser gefunden
  linkPokemonCom: (id: number, url: string) => api.post<Card>(`/cards/${id}/link-pokemon-com`, { url }),
  linkCatalog: (id: number, catalogCardId: string, imageUrl?: string | null) =>
    api.post<Card>(`/cards/${id}/link-catalog`, { card_id: catalogCardId, image_url: imageUrl ?? null }),
  // Neu abgleichen: Druck (Foto → Verknüpfung → Set + Nummer) und fehlende Daten aus dem Katalog
  rematch: (id: number, print?: string) =>
    api.get<RematchProposal>(`/cards/${id}/rematch`, { params: print ? { print } : {} }),
  applyRematch: (id: number, body: { tcgdex_card_id: string; fields?: string[] | null; folierung?: string | null }) =>
    api.post<Card>(`/cards/${id}/rematch`, body),

  stats: () => api.get<StatsResponse>("/cards/meta/stats"),


  enums: () => api.get<Enums>("/cards/meta/enums"),


  backfillImages: (force = false) =>
    api.post(`/cards/meta/backfill-images?force=${force}`),
  // Pokédex auffüllen: Platzhalter für alle Spezies ohne eigene Zeile (idempotent)
  fillPokedex: () =>
    api.post<{ created: number; existing: number; total: number }>("/cards/meta/fill-pokedex"),
};

export const pricesApi = {
  refresh: () => api.post("/prices/refresh"),
  history: (id: number) => api.get(`/prices/history/${id}`),
};

export type PokemonSet = {
  code: string;
  name: string;
  max_card_nr: number | null;
  // TCGdex-Anreicherung (v0.7.0)
  set_id?: string | null;
  name_en?: string | null;
  series_id?: string | null;
  card_count_official?: number | null;
  card_count_total?: number | null;
  logo_url?: string | null;
  symbol_url?: string | null;
};

export const setsApi = {
  list: () => api.get<PokemonSet[]>("/sets"),
  create: (data: Partial<PokemonSet>) => api.post<PokemonSet>("/sets", data),
};

// Secrets kommen vom Backend nur noch maskiert (Issue #1): je Secret ein
// *_set-Flag + *_masked ("•••• XXXX"). Klartext gibt es nur in Richtung
// PUT (AppSettingsUpdate), nie in der Response.
export type AppSettings = {
  placeholder_images_enabled: boolean;
  cards_per_page: number;
  default_sort: string;
  default_sort_reverse: boolean;
  bottom_nav_hidden: string[];   // ausgeblendete Seiten der unteren Leiste (je Konto)
  price_update_enabled: boolean;
  price_update_hour: number;
  price_source: string;
  default_language: string;
  default_condition: string;
  cardmarket_app_token_set: boolean;
  cardmarket_app_token_masked: string;
  cardmarket_app_secret_set: boolean;
  cardmarket_app_secret_masked: string;
  cardmarket_access_token_set: boolean;
  cardmarket_access_token_masked: string;
  cardmarket_access_secret_set: boolean;
  cardmarket_access_secret_masked: string;
  gemini_api_key_set: boolean;
  gemini_api_key_masked: string;
  gemini_model: string;
  gemini_daily_limit: number;
  // Scan-Stufe B (Issue #57): pluggbares Lese-Modell
  scan_reader_provider: string;
  openai_api_key_set: boolean;
  openai_api_key_masked: string;
  openai_model: string;
  openrouter_api_key_set: boolean;
  openrouter_api_key_masked: string;
  openrouter_model: string;
  // Lokaler Katalog-Bildcache (#43): "urls" | "owned" | "all"
  catalog_image_cache_level: string;
  // Niedrigstes Standard-legales Regulation Mark ("H" = Saison 2026/27)
  standard_min_mark?: string;
};

export type AppSettingsUpdate = Partial<{
  placeholder_images_enabled: boolean;
  cards_per_page: number;
  default_sort: string;
  default_sort_reverse: boolean;
  bottom_nav_hidden: string[];
  price_update_enabled: boolean;
  price_update_hour: number;
  price_source: string;
  default_language: string;
  default_condition: string;
  cardmarket_app_token: string;
  cardmarket_app_secret: string;
  cardmarket_access_token: string;
  cardmarket_access_secret: string;
  gemini_api_key: string;
  gemini_model: string;
  gemini_daily_limit: number;
  // Scan-Stufe B (Issue #57)
  scan_reader_provider: string;
  openai_api_key: string;
  openai_model: string;
  openrouter_api_key: string;
  openrouter_model: string;
  // Lokaler Katalog-Bildcache (#43)
  catalog_image_cache_level: string;
  standard_min_mark: string;
}>;

// ── Decks ──────────────────────────────────────────────────────────────────
export type DeckFormat = "standard" | "expanded" | "unlimited";
export type DeckSection = "pokemon" | "trainer" | "energy";
export type DeckMatch = "exact" | "same_name" | "other_print";
export type DeckIssue = { level: "error" | "warning"; code: string; count: number; name?: string | null; slots: number[] };
export type DeckLegality = {
  status: "legal" | "check" | "illegal";
  format: DeckFormat;
  min_mark: string;
  total: number;
  sections: Record<DeckSection, number>;
  issues: DeckIssue[];
  need_data: boolean;
};
export type DeckSlot = {
  id: number;
  tcgdex_card_id: string | null;
  name: string;
  set_code: string | null;
  number: string | null;
  count: number;
  section: DeckSection;
  match: "set" | "name" | null;
  image_url: string | null;
  rarity: string | null;
  stage: string | null;
  trainer_type: string | null;
  status: "ok" | "reprint_text" | "basic" | "not_legal" | "unknown" | "unmatched";
  legal_mark: string | null;
  ace_spec: boolean;
  cards: { match: DeckMatch; card: Card }[];
  available: number;
  available_other: number;
};
export type DeckPrint = {
  tcgdex_card_id: string;
  name: string;
  set_code: string | null;
  set_name: string | null;
  local_id: string | null;
  rarity: string | null;
  regulation_mark: string | null;
  image_url: string | null;
  current: boolean;
};
export type DeckSummary = {
  id: number;
  name: string;
  format: DeckFormat;
  notizen: string | null;
  erstellt_am: string | null;
  aktualisiert_am: string | null;
  total: number;
  in_deck: number;
  status: DeckLegality["status"];
  cover_url: string | null;
};
export type DeckDetail = DeckSummary & { legality: DeckLegality; slots: DeckSlot[]; extra: Card[] };
export type DeckImportResult = {
  deck: DeckDetail;
  lines: number;
  matched: number;
  unmatched: number;
  not_found: string[];
  unreadable: string[];
};

export const deckApi = {
  list: () => api.get<DeckSummary[]>("/decks"),
  get: (id: number) => api.get<DeckDetail>(`/decks/${id}`),
  create: (data: { name: string; format?: DeckFormat; notizen?: string | null; text?: string | null; fill?: boolean }) =>
    api.post<DeckImportResult>("/decks", data),
  update: (id: number, data: { name?: string; format?: DeckFormat; notizen?: string | null }) =>
    api.put<DeckDetail>(`/decks/${id}`, data),
  delete: (id: number) => api.delete(`/decks/${id}`),
  importList: (id: number, text: string, replace = false) =>
    api.post<DeckImportResult>(`/decks/${id}/import`, { text, replace }),
  exportList: (id: number) => api.get<string>(`/decks/${id}/export`, { responseType: "text" }),
  fill: (id: number) => api.post<{ deck: DeckDetail; added: number }>(`/decks/${id}/fill`),
  refresh: (id: number) => api.post<DeckDetail>(`/decks/${id}/refresh`),
  addSlot: (id: number, tcgdexCardId: string, count = 1) =>
    api.post<DeckDetail>(`/decks/${id}/slots`, { tcgdex_card_id: tcgdexCardId, count }),
  updateSlot: (id: number, slotId: number,
    data: { count?: number; section?: DeckSection; tcgdex_card_id?: string; link_cards?: boolean }) =>
    api.put<DeckDetail>(`/decks/${id}/slots/${slotId}`, data),
  prints: (id: number, slotId: number) => api.get<DeckPrint[]>(`/decks/${id}/slots/${slotId}/prints`),
  removeSlot: (id: number, slotId: number) => api.delete<DeckDetail>(`/decks/${id}/slots/${slotId}`),
  candidates: (id: number, slotId: number) =>
    api.get<{ match: DeckMatch; card: Card }[]>(`/decks/${id}/slots/${slotId}/candidates`),
  addCards: (id: number, cardIds: number[], move = false) =>
    api.post<DeckDetail>(`/decks/${id}/cards`, { card_ids: cardIds, move }),
  removeCard: (id: number, cardId: number) => api.delete<DeckDetail>(`/decks/${id}/cards/${cardId}`),
};

export const collectionApi = {
  list: () => api.get<Collection[]>("/collections"),
  get: (id: number) => api.get<Collection>(`/collections/${id}`),
  create: (data: {
    name: string;
    beschreibung?: string | null;
    typ?: string;
    ziel_set_id?: string | null;
    ziel_folierung?: string | null;
    ziel_sprache?: string | null;
    ziel_master_set?: boolean;
  }) => api.post<Collection>("/collections", data),
  update: (id: number, data: { name?: string; beschreibung?: string | null; binder_layout?: string; binder_slots?: number; view_mode?: "grid" | "binder"; grid_sort?: "manual" | "name" | "pokedex" | "set" | "value" | "regulation" | "newest"; grid_sort_reverse?: boolean; grid_sort_then?: string }) =>
    api.put<Collection>(`/collections/${id}`, data),
  delete: (id: number) => api.delete(`/collections/${id}`),
  cards: (id: number) => api.get<CollectionCard[]>(`/collections/${id}/cards`),
  addCard: (id: number, cardId: number) =>
    api.post<CollectionCard>(`/collections/${id}/cards`, { card_id: cardId }),
  removeCard: (id: number, cardId: number) =>
    api.delete(`/collections/${id}/cards/${cardId}`),
  reorder: (id: number, order: number[]) =>
    api.put(`/collections/${id}/cards/order`, { order }),
  moveToSlot: (id: number, cardId: number, slot: number) =>
    api.put(`/collections/${id}/cards/${cardId}/slot`, { slot }),
  setPositions: (id: number, positions: { card_id: number; position: number }[]) =>
    api.put(`/collections/${id}/cards/positions`, { positions }),
  forCard: (cardId: number) => api.get<Collection[]>(`/cards/${cardId}/collections`),
  shares: (id: number) => api.get<FriendUser[]>(`/collections/${id}/shares`),
  setShares: (id: number, friendIds: number[]) =>
    api.put<FriendUser[]>(`/collections/${id}/shares`, { friend_ids: friendIds }),
  // Soll-Liste / Fortschritt einer Set-Sammlung (Issue #16)
  soll: (id: number) => api.get<SollSlot[]>(`/collections/${id}/soll`),
  addSoll: (id: number, tcgdexCardId: string, sollFolierung?: string | null) =>
    api.post<SollSlot>(`/collections/${id}/soll`, {
      tcgdex_card_id: tcgdexCardId,
      soll_folierung: sollFolierung ?? null,
    }),
  updateSoll: (id: number, sollId: number, data: { soll_folierung?: string | null; position?: number }) =>
    api.put<SollSlot>(`/collections/${id}/soll/${sollId}`, data),
  removeSoll: (id: number, sollId: number) =>
    api.delete(`/collections/${id}/soll/${sollId}`),
  sollToWishlist: (id: number, sollId: number) =>
    api.post<{ card_id: number }>(`/collections/${id}/soll/${sollId}/wishlist`),
  progress: (id: number) => api.get<CollectionProgress>(`/collections/${id}/progress`),
};

// ── Sealed-Produkte (#35) ──────────────────────────────────────────────────
export const sealedApi = {
  list: (params: Record<string, unknown> = {}) =>
    api.get<SealedProduct[]>("/sealed", { params }),
  get: (id: number) => api.get<SealedProduct>(`/sealed/${id}`),
  create: (data: Partial<SealedProduct>) => api.post<SealedProduct>("/sealed", data),
  update: (id: number, data: Partial<SealedProduct>) =>
    api.put<SealedProduct>(`/sealed/${id}`, data),
  delete: (id: number) => api.delete(`/sealed/${id}`),
  enums: () => api.get<SealedEnums>("/sealed/meta/enums"),
  catalog: (search: string, region?: string) =>
    api.get<SealedCatalogItem[]>("/sealed/catalog", { params: { search, ...(region ? { region } : {}) } }),
  uploadImage: (id: number, file: File) => {
    const form = new FormData();
    form.append("file", file);
    return api.post<SealedProduct>(`/sealed/${id}/image`, form);
  },
  deleteImage: (id: number) => api.delete<SealedProduct>(`/sealed/${id}/image`),
};

// ── Schwache Scans (Admin) ─────────────────────────────────────────────────
export type ScanCheck = {
  status: "ok" | "low" | "no_reference" | "error" | "dismissed";
  inliers: number;
  art_inliers: number;
  corner_err: number | null;
  templates: number;
  suggested_quad: number[][] | null;
  checked_at: string | null;
};

export type ScanQualityItem = {
  card_id: number; status: string; inliers: number; art_inliers: number; corner_err: number | null;
  templates: number; has_suggestion: boolean; checked_at: string | null; own: boolean;
  username: string | null; name: string; english_name: string | null; set: string | null;
  number: string | null; tcgdex_card_id: string | null; photo: string | null; has_original: boolean;
  original: string | null; quad: number[][] | null; suggested_quad: number[][] | null;
  catalog_image: string | null;   // gespiegeltes Katalogbild zum Vergleich
};

export type ScanRecheck = { status: string; inliers: number; corner_err: number | null;
  suggested_quad: number[][] | null; photo: string | null };

export type StampItem = {
  card_id: number; own: boolean; username: string | null; name: string; english_name: string | null;
  set: string | null; number: string | null; tcgdex_card_id: string | null; photo: string;
  stamp_quad: number[][] | null; updated_at: string | null;
};
export interface StampSuggestion { quad: number[][] | null; score: number | null; ok: boolean; template: boolean }
export interface StampTemplate { exists: boolean; samples?: number; marked?: number; built_at?: string | null }
// Reverse-Holo-Modell im Bild-Abgleich (matcher/tools/foil_eval.py --save)
export interface FoilModel {
  exists: boolean; supported?: boolean; precision?: number; recall?: number; threshold?: number;
  trained_on?: Record<string, number>; saved_at?: string | null; features?: string[];
}
// Ergebnis der Folien-Prüfung eines Scan-Zuschnitts
export type FoilCheck = { model: boolean; reference?: boolean; aligned?: boolean; hint?: boolean; score?: number };
export interface StampLearnResult { samples: number; marked: number; guessed: number; built_at: string }

export type StampOverview = { counts: { photos: number; marked: number; unmarked: number }; items: StampItem[] };

export type ScanQualityOverview = {
  job: { running: boolean; done: number; total: number; low: number; started_at: string | null;
    finished_at: string | null; error: string | null };
  counts: { ok: number; low: number; no_reference: number; error: number; dismissed: number;
    unchecked: number; photos: number };
  thresholds: { inliers: number; corner_err: number };
  items: ScanQualityItem[];
};

export type RematchPrint = {
  tcgdex_card_id: string;
  name: string;
  set_code: string | null;
  set_name: string | null;
  local_id: string | null;
  rarity: string | null;
  image_url: string | null;
  current: boolean;
  photo: boolean;
};
export type RematchChange = { field: string; old: string | null; new: string | null; kind: "fill" | "change" };
export type RematchProposal = {
  found: RematchPrint | null;
  how: "photo" | "link" | "number" | "name" | "picked" | null;
  sure: boolean;
  safe: boolean;
  photo: "used" | "no_photo" | "off" | "down" | "nothing";
  changes: RematchChange[];
  foil: { current: string | null; options: string[]; suggested: string | null; why: "only" | "photo" | null; photo_hint: boolean | null };
  alternatives: RematchPrint[];
};
export type CardGap = "print" | "number" | "illustrator" | "rarity" | "set" | "english_name" | "foiling" | "image" | "differs";
export type CardGapItem = {
  card_id: number;
  own: boolean;
  username: string | null;
  name: string;
  english_name: string | null;
  set: string | null;
  number: string | null;
  language: string | null;
  foiling: string | null;
  tcgdex_card_id: string | null;
  gaps: CardGap[];
  photo: string | null;
  has_photo: boolean;
  image: string | null;
};
export type CardGapOverview = { total: number; counts: Record<CardGap, number>; shown: number; matching: number; items: CardGapItem[] };

export const cardGapsApi = {
  overview: (gap: CardGap | null, owner: "all" | "mine" | "others" = "all") =>
    api.get<CardGapOverview>("/card-gaps", { params: { ...(gap ? { gap } : {}), owner } }),
};

export const scanQualityApi = {
  overview: (status = "low") => api.get<ScanQualityOverview>("/scan-quality", { params: { status } }),
  run: (full = false) => api.post("/scan-quality/run", null, { params: { full } }),
  dismiss: (cardId: number) => api.post(`/scan-quality/${cardId}/dismiss`),
  check: (cardId: number) => api.post<ScanRecheck>(`/scan-quality/${cardId}/check`),
  stamps: (marked = false) => api.get<StampOverview>("/scan-quality/stamps", { params: { marked } }),
  stampTemplate: () => api.get<StampTemplate>("/scan-quality/stamps/template"),
  learnStampTemplate: () => api.post<StampLearnResult>("/scan-quality/stamps/template"),
  foilModel: () => api.get<FoilModel>("/scan-quality/foil/model"),
  uploadFoilModel: (file: File) => {
    const form = new FormData();
    form.append("file", file, file.name || "foil_model.json");
    return api.put<FoilModel>("/scan-quality/foil/model", form);
  },
  deleteFoilModel: () => api.delete<FoilModel>("/scan-quality/foil/model"),
  // Zuschnitt prüfen, ohne ihn zu speichern („Alle korrigieren")
  tryCrop: (cardId: number, file: Blob, quad: number[][]) => {
    const form = new FormData();
    form.append("file", file, "try.jpg");
    form.append("quad", JSON.stringify(quad));
    return api.post<Omit<ScanRecheck, "photo">>(`/scan-quality/${cardId}/try`, form);
  },
};

// ── Anmeldung / Mehrbenutzer ──────────────────────────────────────────────

export type AuthConfig = {
  oidc_enabled: boolean;
  oidc_provider_name: string;
  password_login: boolean;
};

export type Me = {
  id: number;
  username: string;
  display_name: string | null;
  email: string | null;
  is_admin: boolean;
  sso_linked: boolean;
  oidc_enabled: boolean;
  oidc_provider_name: string;
  password_login: boolean;
};

export type UserRow = {
  id: number;
  username: string;
  display_name: string | null;
  email: string | null;
  is_admin: boolean;
  is_active: boolean;
  sso_linked: boolean;
  created_at: string | null;
  last_login_at: string | null;
  owned_cards: number;
  collections: number;
  sealed: number;
};

/**
 * Benutzerwechsel: Offline-Kopien der API-Antworten (Service-Worker-Cache)
 * gehören dem bisherigen Benutzer — bei Login/Logout verwerfen.
 */
export async function clearClientCaches(): Promise<void> {
  try {
    if (typeof caches === "undefined") return;
    const keys = await caches.keys();
    await Promise.all(keys.filter((k) => k.startsWith("pokecollect-")).map((k) => caches.delete(k)));
  } catch {
    /* best-effort */
  }
}

/** Abmelden: Token + Offline-Cache weg, harte Navigation zur Login-Seite. */
export async function logout(): Promise<void> {
  localStorage.removeItem("token");
  await clearClientCaches();
  window.location.href = "/login";
}

export const authApi = {
  config: () => api.get<AuthConfig>("/auth/config"),
  me: () => api.get<Me>("/auth/me"),
  // SSO-Einstieg ist eine volle Seiten-Navigation (302 zum Provider), kein XHR.
  // origin nur, wenn die API auf einem anderen Origin liegt als das Web-UI —
  // das Backend leitet das Token nur an erlaubte Origins zurück.
  ssoLoginUrl: (next: string = "/") => {
    const params = new URLSearchParams({ next });
    if (typeof window !== "undefined") params.set("origin", window.location.origin);
    return `${API_BASE}/api/v1/auth/oidc/login?${params.toString()}`;
  },
};

export const usersApi = {
  list: () => api.get<UserRow[]>("/users"),
  update: (id: number, patch: { is_admin?: boolean; is_active?: boolean }) =>
    api.patch<UserRow[]>(`/users/${id}`, patch),
};

export const settingsApi = {
  get: () => api.get<AppSettings>("/settings"),
  update: (data: AppSettingsUpdate) => api.put<AppSettings>("/settings", data),
  changePassword: (current_password: string, new_password: string) =>
    api.post("/settings/change-password", { current_password, new_password }),
};

// ── Daten: CSV-Export / Backup / Restore (Issue #17) ──────────────────────
// Downloads laufen als authentifizierte Blob-GETs über die axios-Instanz —
// ein nackter <a href> hätte keinen Authorization-Header und liefe in 401.
export const dataApi = {
  // Englisch: Komma-CSV mit englischen Spalten (englisches Excel/Sheets)
  exportCsv: () => api.get<Blob>("/data/export.csv", { responseType: "blob", params: { lang: getUiLang() } }),
  backup: () => api.get<Blob>("/data/backup", { responseType: "blob" }),
  // Scan-Fotos + bestätigte Karten als Trainings-/Testdatensatz (Admin)
  scanDataset: () => api.get<Blob>("/data/scan-dataset", { responseType: "blob" }),
  scanDatasetSummary: () => api.get<ScanDatasetSummary>("/data/scan-dataset/summary"),
  // Destruktiv — das Backend verlangt confirm="JA_WIRKLICH"; das UI sichert
  // zusätzlich mit Checkbox + Bestätigungsdialog ab.
  restore: (file: File) => {
    const form = new FormData();
    form.append("file", file);
    form.append("confirm", "JA_WIRKLICH");
    return api.post<{ detail: string }>("/data/restore", form);
  },
};

// ── Scan ──────────────────────────────────────────────────────────────────

// "card": bereits entzerrte Karte (nach dem Ecken-Editor) → nur neu abgleichen
export type ScanMode = "single" | "multi" | "binder" | "card";

export type ScanMatch = {
  tcgdex_card_id: string | null;
  name: string | null;
  englischer_name: string | null;
  set_id: string | null;
  set_code: string | null;
  set_name: string | null;
  local_id: string | null;
  rarity: string | null;
  dex_id: number | null;
  image_url: string | null;
  variants_normal: boolean | null;
  variants_reverse: boolean | null;
  variants_holo: boolean | null;
  variants_firstedition: boolean | null;
};

export type ScanCandidate = {
  position: number | null;
  confidence: number;
  uncertain_fields: string[];
  raw: {
    name: string | null;
    set_code: string | null;
    number: string | null;
    language: string | null;
    position: number | null;
    confidence: number | null;
    bbox: number[] | null;
    quad: number[][] | null;
    tcgdex_card_id?: string | null;
  };
  match: ScanMatch | null;
  suggested: Record<string, unknown>;
  foil_options: string[];
  // Bild-Abgleich („local"): weitere Treffer + „bitte wählen" (kein eindeutiger Treffer)
  alternatives?: ScanAlternative[];
  needs_choice?: boolean;
  print_choice?: boolean;   // unsicher nur beim Druck (gleichnamige Treffer): bester vorgewählt
  // Play!-Stempel (Prize Pack) auf der erkannten Karte: ok = Hinweis, sure = vorab angehakt
  stamp?: { score: number; quad: number[][]; ok: boolean; sure: boolean } | null;
};

export type ScanAlternative = {
  tcgdex_card_id: string;
  name: string | null;
  set_name: string | null;
  local_id: string | null;
  image_url: string | null;
  art_inliers: number;
};

export type GeminiHinweisArt = "tageslimit" | "rate_limit" | "key_ungueltig" | "gemini_fehler" | "reader_fehler"
  | "bild_abgleich_startet";

export type ScanResponse = {
  engine: string;
  mode: ScanMode;
  candidates: ScanCandidate[];
  limit_erreicht?: boolean;  // ein Limit (Tages- oder Rate-Limit) griff → Server fiel auf OCR zurück
  hinweis?: string | null;
  hinweis_art?: GeminiHinweisArt | null;  // maschinenlesbare Fallback-Ursache (Issue #21)
};

export type ScanStatus = {
  gemini: boolean;
  ocr: boolean;
  active: string;
  provider?: string;   // Scan-Stufe B (Issue #57): gewähltes Lese-Modell
  model?: string;
  // Bild-Abgleich: Zustand des matcher-Dienstes (nur bei provider "local")
  local?: { status: "ready" | "loading" | "converting" | "error" | "unreachable" | string; phase?: string | null } | null;
};

export type ScanCommitItem = {
  kartenname: string;
  pokedex_nr?: number | null;
  englischer_name?: string | null;
  set_edition?: string | null;
  karten_nr?: string | null;
  seltenheit?: string | null;
  kartenversion?: string | null;
  folierung?: string | null;
  muster?: string | null;      // Folierungs-Muster (#63)
  prize_pack?: boolean;        // Play! Pokémon-Stempel (Prize Pack)
  erste_edition?: boolean;     // 1st-Edition-Stempel
  draft_key?: string;          // Schlüssel der Karte im Scan-Entwurf (draft_id)
  sprache?: string | null;
  zustand?: string | null;
  notizen?: string | null;
  tcgdex_card_id?: string | null;
  set_id?: string | null;
  dex_id?: number | null;
  bild_karte_url?: string | null;
  position?: number | null;
  im_pokedex?: boolean;
  prioritaet?: string | null;
  copies?: number;          // gleiche Exemplare (1 = nur diese Karte)
};

export type ScanCommitRequest = {
  target: "pokedex" | "collection" | "wishlist" | "deck";
  collection_id?: number | null;
  deck_id?: number | null;
  // Gewähltes Fach (0-basiert über alle Seiten); Mappenseiten-Scan: Seitenanfang
  start_position?: number | null;
  items: ScanCommitItem[];
  draft_id?: number | null;    // aus einem Scan-Entwurf: dort als gespeichert markieren
};

export type ScanRawRead = {
  name?: string | null;
  set_code?: string | null;
  number?: string | null;
  language?: string | null;
  tcgdex_card_id?: string | null;  // bekannte Karte (Auswahl im Picker)
};

export type ScanUsage = {
  today: { day: string; requests: number; tokens: number };
  total: { requests: number; tokens: number };
  avg_tokens_per_scan: number;
  model: string;
  limits: { rpd: number; rpm: number; tpm: number };
  rpm_used: number;  // Live-Verbrauch der letzten 60s (Issue #22)
  tpm_used: number;
  days: { day: string; requests: number; tokens: number }[];
};

// ── Katalog (alle TCGdex-Karten) ────────────────────────────────────────────

export type CatalogItem = {
  card_id: string;
  region?: string | null;   // "west" | "ja" | … (Regionsfilter)
  set_id: string | null;
  set_code: string | null;
  set_name: string | null;
  set_name_en?: string | null;   // englischer Set-Name (Anzeige bei UI-Sprache EN)
  local_id: string | null;
  name: string | null;
  name_en: string | null;
  dex_id: number | null;
  rarity: string | null;
  illustrator: string | null;
  category: string | null;
  regulation_mark?: string | null;   // Turnier-Legalität
  image_url: string | null;
  image_fallback?: string | null;    // offizielle Datenbank, falls TCGdex kein Bild hat
  variants_normal: boolean | null;
  variants_reverse: boolean | null;
  variants_holo: boolean | null;
  variants_firstedition: boolean | null;
  enriched: boolean | null;
  owned: boolean;
  in_pokedex: boolean;
  // Gecachte Preise (Epic #41 Slice 4) — auch in Listen, können null sein
  price_eur?: number | null;
  price_usd?: number | null;
  // Datenstand € (#66): Stand DER QUELLE — im Grid dezent gezeigt, wenn ein
  // €-Preis angezeigt wird, damit ein eingefrorener Cache-Preis nicht als
  // tagesfrisch gilt.
  price_eur_updated?: string | null;
};

export type CatalogListResponse = {
  items: CatalogItem[];
  total: number;
  page: number;
  limit: number;
  pages: number;
};

// Felder beim Übernehmen einer Katalog-Karte (#28). Nicht gesetzte Felder füllt
// das Backend aus den Einstellungs-Defaults auf (#27).
export type CatalogAddOpts = {
  sprache?: string | null;
  zustand?: string | null;
  folierung?: string | null;
  erste_edition?: boolean;
  prioritaet?: string | null;
};

const catalogAddBody = (opts: CatalogAddOpts) => ({
  sprache: opts.sprache ?? null,
  zustand: opts.zustand ?? null,
  folierung: opts.folierung ?? null,
  erste_edition: opts.erste_edition ?? false,
});

export type CatalogDetail = CatalogItem & {
  price_eur: number | null;        // € Cardmarket (live oder gecacht)
  price_eur_low: number | null;
  price_eur_trend: number | null;
  price_eur_updated: string | null;  // Datenstand €
  price_usd: number | null;        // $ TCGplayer (West live, JP gecacht via TCGCSV)
  price_usd_updated: string | null;  // Datenstand $
  // Varianten-$-Preise aus dem Cache (#63)
  price_usd_holo?: number | null;
  price_usd_reverse?: number | null;
  price_usd_pokeball?: number | null;
  price_usd_masterball?: number | null;
};

export type PictureCandidate = { url: string; spiegel: string | null; source: string; label: string };
export type PictureCandidates = { card_id: string; current: string | null; query: string; candidates: PictureCandidate[] };
export type PictureResult = { image_url: string | null; image_source: string | null; cards_updated: number };

export const catalogApi = {
  list: (params: Record<string, unknown> = {}) => api.get<CatalogListResponse>("/catalog", { params }),
  // Bild finden (Admin): Vorschläge, Bildadresse übernehmen, eingefügtes Bild hochladen
  pictures: (cardId: string) => api.get<PictureCandidates>(`/catalog/${encodeURIComponent(cardId)}/pictures`),
  usePicture: (cardId: string, url: string) =>
    api.post<PictureResult>(`/catalog/${encodeURIComponent(cardId)}/picture`, { url }),
  // Zuschneiden im Bild-Finder: grobe Ecken an die Kartenkanten anlegen (matcher /outline)
  pictureOutline: (cardId: string, file: Blob, quad: number[][] | null) => {
    const form = new FormData();
    form.append("file", file, "picture.jpg");
    if (quad) form.append("quad", JSON.stringify(quad));
    return api.post<OutlineResult>(`/catalog/${encodeURIComponent(cardId)}/picture/outline`, form);
  },
  uploadPicture: (cardId: string, file: Blob) => {
    const form = new FormData();
    form.append("file", file, "picture");
    return api.post<PictureResult>(`/catalog/${encodeURIComponent(cardId)}/picture/upload`, form);
  },
  detail: (cardId: string, opts?: { signal?: AbortSignal }) =>
    api.get<CatalogDetail>(`/catalog/${encodeURIComponent(cardId)}/detail`, opts),
  meta: () => api.get<{ total: number; enriched: number; cached_images: number }>("/catalog/meta"),
  sync: () => api.post<{ detail: string }>("/catalog/sync"),
  // Turnier-Legalität: Stand + Nachholen der Regulation Marks (Admin)
  regulation: () => api.get<{ min_mark: string; pending_owned: number; pending_catalog: number }>("/catalog/regulation"),
  loadRegulation: () => api.post<{ detail: string }>("/catalog/regulation"),
  illustrators: () => api.get<string[]>("/catalog/illustrators"),
  addWishlist: (cardId: string, opts: CatalogAddOpts = {}) =>
    api.post<{ card_id: number }>(`/catalog/${encodeURIComponent(cardId)}/wishlist`, {
      ...catalogAddBody(opts),
      prioritaet: opts.prioritaet ?? null,
    }),
  addCollection: (cardId: string, collectionId: number, opts: CatalogAddOpts = {}) =>
    api.post<{ card_id: number }>(
      `/catalog/${encodeURIComponent(cardId)}/collection?collection_id=${collectionId}`,
      catalogAddBody(opts),
    ),
};

export type ScanModelSuggestion = { id: string; name: string };
export type ScanModelList = { models: ScanModelSuggestion[]; source: "live" | "curated" };

// Scan-Entwürfe: die Prüfseite eines Scans auf dem Server (später / an einem
// anderen Gerät fortsetzen). state gehört der Scan-Seite; der Server liest nur
// candidates[].key/saved und die Übersicht.
export type DraftSaved = { ids: number[]; name: string; name_en?: string | null };
export type ScanDraftSummary = {
  id: number;
  summary: {
    open?: number; saved?: number; names?: string[]; target?: string | null; mode?: string | null;
    collection_id?: number | null; deck_id?: number | null;
    outlines?: { quad: number[][]; saved: boolean }[];   // wo die Karten im Foto liegen (normiert)
  };
  erstellt_am: string | null;
  aktualisiert_am: string | null;
};
export const scanDraftApi = {
  list: () => api.get<ScanDraftSummary[]>("/scan/drafts"),
  get: <S,>(id: number) => api.get<ScanDraftSummary & { state: S }>(`/scan/drafts/${id}`),
  photo: (id: number) => api.get<Blob>(`/scan/drafts/${id}/photo`, { responseType: "blob" }),
  thumb: (id: number) => api.get<Blob>(`/scan/drafts/${id}/thumb`, { responseType: "blob" }),
  create: (photo: Blob, state: unknown) => {
    const form = new FormData();
    form.append("photo", photo, "scan.jpg");
    form.append("state", JSON.stringify(state));
    return api.post<ScanDraftSummary>("/scan/drafts", form);
  },
  update: (id: number, state: unknown) =>
    api.put<ScanDraftSummary & { saved: Record<string, DraftSaved> }>(`/scan/drafts/${id}`, { state }),
  remove: (id: number) => api.delete(`/scan/drafts/${id}`),
  // Beim Verlassen der Seite: kommt auch noch an, wenn der Tab schon zu ist (keepalive ≤ 64 KB)
  updateOnLeave: (id: number, state: unknown): boolean => {
    const body = JSON.stringify({ state });
    if (body.length > 60_000 || typeof fetch === "undefined") return false;
    const token = typeof window !== "undefined" ? localStorage.getItem("token") : null;
    void fetch(`${api.defaults.baseURL}/scan/drafts/${id}`, {
      method: "PUT", keepalive: true, body,
      headers: { "Content-Type": "application/json", ...(token ? { Authorization: `Bearer ${token}` } : {}) },
    }).catch(() => {});
    return true;
  },
  removeOnLeave: (id: number) => {
    const token = typeof window !== "undefined" ? localStorage.getItem("token") : null;
    void fetch(`${api.defaults.baseURL}/scan/drafts/${id}`, {
      method: "DELETE", keepalive: true, headers: token ? { Authorization: `Bearer ${token}` } : {},
    }).catch(() => {});
  },
};

// Grobe Ecken an die Kartenkanten angelegt (matcher /outline): über das Artwork
// (reference), einen typischen Kartenrahmen (frame) oder nur die Kanten (edges);
// frame_ok = die Kanten sehen wie eine Karte aus.
export type OutlineResult = {
  quad: number[][] | null; method: "reference" | "frame" | "edges"; frame_ok?: boolean;
  reference: string | null; reference_label: string | null;
};

export const scanApi = {
  status: () => api.get<ScanStatus>("/scan/status"),
  // Ecken im Scan-Foto vorschlagen (übersehene Karte, Ecken anpassen); cardId = erkannte Karte
  outline: (file: Blob, quad: number[][] | null, cardId?: string | null) => {
    const form = new FormData();
    form.append("file", file, "photo.jpg");
    if (quad) form.append("quad", JSON.stringify(quad));
    if (cardId) form.append("card_id", cardId);
    return api.post<OutlineResult>("/scan/outline", form);
  },
  // Reverse holo? Zuschnitt einer erkannten Karte gegen ihr Katalogbild (mit Folien-Modell)
  foil: (crop: Blob, cardId: string) => {
    const form = new FormData();
    form.append("file", crop, "crop.jpg");
    form.append("card_id", cardId);
    return api.post<FoilCheck>("/scan/foil", form);
  },
  // Live-Vorschau: Kartenumrisse im Kamerabild (normiert 0..1)
  detect: (frame: Blob) => {
    const form = new FormData();
    form.append("file", frame, "frame.jpg");
    return api.post<{ available: boolean; cards: { quad: number[][] }[] }>("/scan/detect", form);
  },
  usage: () => api.get<ScanUsage>("/scan/usage"),
  models: (provider: string) =>
    api.get<ScanModelList>("/scan/models", { params: { provider } }),
  scan: (file: Blob, opts: { mode: ScanMode; rows?: number; cols?: number; default_language?: string;
    page?: [number, number][] | null }) => {
    const form = new FormData();
    form.append("file", file, "scan.jpg");
    form.append("mode", opts.mode);
    form.append("rows", String(opts.rows ?? 0));
    form.append("cols", String(opts.cols ?? 0));
    form.append("default_language", opts.default_language ?? "DE");
    // Mappenseite schräg fotografiert: ihre Ecken (Anteile), damit der Bild-Abgleich sie entzerrt
    if (opts.page) form.append("page", JSON.stringify(opts.page.map(([x, y]) => [+x.toFixed(4), +y.toFixed(4)])));
    return api.post<ScanResponse>("/scan", form);
  },
  resolve: (read: ScanRawRead) => api.post<ScanCandidate>("/scan/resolve", read),
  commit: (payload: ScanCommitRequest) =>
    api.post<{ created: number; card_ids: number[]; all_card_ids?: number[]; copy_ids?: number[][];
      positions?: number[]; next_position?: number | null }>("/scan/commit", payload),
};

export const friendsApi = {
  overview: () => api.get<FriendsOverview>("/friends"),
  request: (username: string) => api.post<FriendsOverview>("/friends/requests", { username }),
  accept: (requestId: number) => api.post<FriendsOverview>(`/friends/requests/${requestId}/accept`),
  dropRequest: (requestId: number) => api.delete<FriendsOverview>(`/friends/requests/${requestId}`),
  remove: (friendId: number) => api.delete<FriendsOverview>(`/friends/${friendId}`),
};

export const sharedApi = {
  list: () => api.get<SharedCollection[]>("/shared"),
  get: (id: number) => api.get<SharedCollectionDetail>(`/shared/${id}`),
};

// Admin: welche Kartenfotos in den Scan-Datensatz kommen (je Konto)
export type ScanDatasetCounts = { exportable: number; not_exportable: number; no_card: number; no_original: number };
export type ScanDatasetSummary = ScanDatasetCounts & {
  users: (ScanDatasetCounts & { username: string; display_name: string | null })[];
};
