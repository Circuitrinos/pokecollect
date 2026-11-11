// Screenshots for README.md, taken from a running PokéCollect with the English UI.
//
//   cd e2e && npm ci && npx playwright install chromium
//   PC_URL=https://tcg.example.com PC_USER=admin node screenshots.mjs
//
// Login: PC_TOKEN (a login token), or PC_USER + PC_PASSWORD; without a password
// it asks for one. Optional: PC_COLLECTION / PC_CARD (ids for the binder and card
// shots), PC_OUT (output folder, default ../docs/screenshots), PC_BROWSER
// (path to a Chrome/Chromium instead of Playwright's).
//
// Changes no data. A collection that isn't shown as a binder is switched to
// binder view for the shots and switched back afterwards.
import { chromium } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const BASE = (process.env.PC_URL || "http://localhost:3011").replace(/\/+$/, "");
const OUT = path.resolve(process.env.PC_OUT || fileURLToPath(new URL("../docs/screenshots/", import.meta.url)));
const DESKTOP = { viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1.5 };
const PHONE = { viewport: { width: 390, height: 844 }, deviceScaleFactor: 3, isMobile: true, hasTouch: true };

function askHidden(question) {
  return new Promise((resolve) => {
    const stdin = process.stdin;
    process.stdout.write(question);
    if (!stdin.isTTY) {
      let buf = "";
      stdin.setEncoding("utf8");
      stdin.on("data", (d) => { buf += d; });
      stdin.on("end", () => resolve(buf.split(/\r?\n/)[0]));
      return;
    }
    let pw = "";
    stdin.setRawMode(true);
    stdin.setEncoding("utf8");
    stdin.resume();
    const onData = (chunk) => {
      for (const c of chunk) {
        if (c === "\r" || c === "\n") {
          stdin.setRawMode(false);
          stdin.pause();
          stdin.off("data", onData);
          process.stdout.write("\n");
          resolve(pw);
          return;
        }
        if (c === "\u0003") process.exit(130);
        if (c === "\u007f" || c === "\b") pw = pw.slice(0, -1);
        else pw += c;
      }
    };
    stdin.on("data", onData);
  });
}

async function api(method, p, token, body) {
  const r = await fetch(BASE + "/api/v1" + p, {
    method,
    headers: { "Content-Type": "application/json", ...(token ? { Authorization: `Bearer ${token}` } : {}) },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!r.ok) throw new Error(`${method} ${p}: HTTP ${r.status} ${await r.text()}`);
  return r.status === 204 ? null : r.json();
}

async function login() {
  if (process.env.PC_TOKEN) return process.env.PC_TOKEN;
  const username = process.env.PC_USER || "admin";
  const password = process.env.PC_PASSWORD ?? await askHidden(`Password for ${username} at ${BASE}: `);
  return (await api("POST", "/auth/login", null, { username, password })).access_token;
}

// Wait until the page is quiet and the images in view have loaded.
async function settle(page, extra = 800) {
  await page.waitForLoadState("networkidle", { timeout: 20000 }).catch(() => {});
  await page.evaluate(async () => {
    const inView = [...document.images].filter((i) => {
      const r = i.getBoundingClientRect();
      return r.bottom > 0 && r.top < window.innerHeight && !i.complete;
    });
    await Promise.race([
      Promise.all(inView.map((i) => new Promise((ok) => { i.onload = i.onerror = ok; }))),
      new Promise((ok) => setTimeout(ok, 15000)),
    ]);
  });
  await page.waitForTimeout(extra);
}

const hasPicture = (c) => !!(c.bild_karte_pfad || c.bild_karte_spiegel || c.bild_pokedex_spiegel);

const token = await login();
const health = await (await fetch(BASE + "/health")).json();
const cards = (await api("GET", "/cards?besessen=true&limit=5000", token)).items;
const collections = await api("GET", "/collections", token);

const card = process.env.PC_CARD
  ? { id: Number(process.env.PC_CARD) }
  : cards.filter(hasPicture).sort((a, b) => (parseFloat(b.wert_eur) || 0) - (parseFloat(a.wert_eur) || 0))[0];
const collection = process.env.PC_COLLECTION
  ? collections.find((c) => c.id === Number(process.env.PC_COLLECTION))
  : [...collections].sort((a, b) => (b.karten_anzahl ?? 0) - (a.karten_anzahl ?? 0))[0];
if (!card) throw new Error("No owned card with a picture found (set PC_CARD).");
if (!collection) throw new Error("No collection found (set PC_COLLECTION).");
console.log(`Card ${card.id}${card.kartenname ? ` (${card.kartenname})` : ""}, collection ${collection.id} (${collection.name})`);

fs.mkdirSync(OUT, { recursive: true });
const browser = await chromium.launch({ executablePath: process.env.PC_BROWSER || undefined });

async function open(device) {
  const ctx = await browser.newContext({ ...device, locale: "en-US", colorScheme: "dark", serviceWorkers: "block" });
  await ctx.addInitScript(([t, v]) => {
    localStorage.setItem("token", t);
    localStorage.setItem("lang", "EN");
    localStorage.setItem("whatsnew_seen_version", v);
  }, [token, health.version ?? ""]);
  const page = await ctx.newPage();
  page.on("pageerror", (e) => console.log("  page error:", e.message));
  return { ctx, page };
}

// Desktop opens the filter panel by default; the grid shot looks cleaner without it.
async function closeFilters(page) {
  if (await page.getByPlaceholder(/Pokédex no/).isVisible().catch(() => false)) {
    await page.getByRole("button", { name: "Filters" }).filter({ visible: true }).first().click();
  }
}

async function shot(page, url, file, opts = {}) {
  await page.goto(BASE + url);
  await settle(page);
  if (opts.prepare) { await opts.prepare(page); await settle(page, 400); }
  await page.screenshot({ path: path.join(OUT, file), fullPage: !!opts.fullPage });
  console.log("  wrote", file);
}

const restoreView = collection.view_mode !== "binder" ? collection.view_mode : null;
if (restoreView) await api("PUT", `/collections/${collection.id}`, token, { view_mode: "binder" });
try {
  const desk = await open(DESKTOP);
  await shot(desk.page, "/pokedex", "01_pokedex.png", { prepare: closeFilters });
  await shot(desk.page, `/cards/${card.id}`, "02_card_detail.png");
  await shot(desk.page, `/collections/${collection.id}`, "03_binder.png");
  await shot(desk.page, "/stats", "04_statistics.png", { fullPage: true });
  await shot(desk.page, "/catalog", "05_catalog.png");
  await desk.ctx.close();

  const phone = await open(PHONE);
  await shot(phone.page, `/collections/${collection.id}`, "06_phone_binder.png");
  await shot(phone.page, `/cards/${card.id}`, "07_phone_card.png");
  await phone.ctx.close();
} finally {
  if (restoreView) await api("PUT", `/collections/${collection.id}`, token, { view_mode: restoreView });
  await browser.close();
}
console.log(`Done: ${OUT}`);
