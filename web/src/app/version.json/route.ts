// Build-Kennung des laufenden Servers — offene Seiten vergleichen sie mit ihrer
// eigenen und laden nach einem Update neu (PWARegister).
export const dynamic = "force-dynamic";

export function GET() {
  return Response.json(
    { build: process.env.NEXT_PUBLIC_BUILD_STAMP ?? "" },
    { headers: { "Cache-Control": "no-store" } },
  );
}
