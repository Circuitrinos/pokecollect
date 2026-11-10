"use client";
import { Me, authApi } from "@/lib/api";
import { createCachedResource } from "@/lib/apiCache";

// Angemeldeter Benutzer (Mehrbenutzer) — ein Fetch pro Seitenladen. Logout/
// Login laufen per harter Navigation, der Modul-Cache ist danach frisch.
const resource = createCachedResource<Me>(() => authApi.me().then((r) => r.data));

export function useMe(): Me | null {
  return resource.useResource().data;
}
