// Währung der Instanz (Build-Zeit, wie NEXT_PUBLIC_API_URL): "EUR" | "USD".
// Muss zur Backend-Env CURRENCY passen — das Backend speichert Werte und
// Kaufpreise in dieser Währung (Spaltennamen bleiben „_eur").
export const CURRENCY: "EUR" | "USD" =
  (process.env.NEXT_PUBLIC_CURRENCY ?? "").trim().toUpperCase() === "USD" ? "USD" : "EUR";
export const USD_MODE = CURRENCY === "USD";
export const CURRENCY_SYMBOL = USD_MODE ? "$" : "€";
