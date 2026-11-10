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
  grid_sort?: "manual" | "name" | "pokedex" | "set" | "value" | "newest";   // Sortierung im Raster
  erstellt_am: string | null;
  karten_anzahl: number;
  // Set-Sammlung / Sammelziel (Issue #16)
  typ: string; // "frei" | "set_ziel"
  ziel_set_id: string | null;
  ziel_folierung: string | null;
  ziel_sprache: string | null;
  ziel_master_set: boolean;
  fortschritt: CollectionProgress | null; // nur bei typ="set_ziel"
};

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

  deleteImage: (id: number) => api.delete<Card>(`/cards/${id}/image`),
  // Katalogbild statt eigenem Foto zeigen; sucht eins, falls noch keins bekannt
  useCatalogImage: (id: number) => api.post<Card>(`/cards/${id}/catalog-image`),

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
  update: (id: number, data: { name?: string; beschreibung?: string | null; binder_layout?: string; binder_slots?: number; view_mode?: "grid" | "binder"; grid_sort?: "manual" | "name" | "pokedex" | "set" | "value" | "newest" }) =>
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
  exportCsv: () => api.get<Blob>("/data/export.csv", { responseType: "blob" }),
  backup: () => api.get<Blob>("/data/backup", { responseType: "blob" }),
  // Scan-Fotos + bestätigte Karten als Trainings-/Testdatensatz (Admin)
  scanDataset: () => api.get<Blob>("/data/scan-dataset", { responseType: "blob" }),
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
  target: "pokedex" | "collection" | "wishlist";
  collection_id?: number | null;
  // Gewähltes Fach (0-basiert über alle Seiten); Mappenseiten-Scan: Seitenanfang
  start_position?: number | null;
  items: ScanCommitItem[];
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

export const catalogApi = {
  list: (params: Record<string, unknown> = {}) => api.get<CatalogListResponse>("/catalog", { params }),
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

export const scanApi = {
  status: () => api.get<ScanStatus>("/scan/status"),
  // Live-Vorschau: Kartenumrisse im Kamerabild (normiert 0..1)
  detect: (frame: Blob) => {
    const form = new FormData();
    form.append("file", frame, "frame.jpg");
    return api.post<{ available: boolean; cards: { quad: number[][] }[] }>("/scan/detect", form);
  },
  usage: () => api.get<ScanUsage>("/scan/usage"),
  models: (provider: string) =>
    api.get<ScanModelList>("/scan/models", { params: { provider } }),
  scan: (file: Blob, opts: { mode: ScanMode; rows?: number; cols?: number; default_language?: string }) => {
    const form = new FormData();
    form.append("file", file, "scan.jpg");
    form.append("mode", opts.mode);
    form.append("rows", String(opts.rows ?? 0));
    form.append("cols", String(opts.cols ?? 0));
    form.append("default_language", opts.default_language ?? "DE");
    return api.post<ScanResponse>("/scan", form);
  },
  resolve: (read: ScanRawRead) => api.post<ScanCandidate>("/scan/resolve", read),
  commit: (payload: ScanCommitRequest) =>
    api.post<{ created: number; card_ids: number[]; all_card_ids?: number[]; copy_ids?: number[][];
      positions?: number[]; next_position?: number | null }>("/scan/commit", payload),
};
