"use client";
import { useEffect } from "react";
import { reportClientError } from "@/components/ClientErrorReporter";
import { useI18n } from "@/lib/i18n";

// Fehler beim Anzeigen einer Seite: statt der leeren „Application error"-Seite eine
// Meldung mit „Nochmal versuchen"; der Fehler geht ins Server-Log (api-errors.log).
export default function PageError({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  const { t } = useI18n();
  useEffect(() => { reportClientError("render", error); }, [error]);
  return (
    <div className="max-w-lg mx-auto mt-10 rounded-lg bg-pokemon-card p-5 space-y-3" data-testid="page-error">
      <h2 className="text-white font-semibold">⚠ {t.error_page_title}</h2>
      <p className="text-gray-400 text-sm">{t.error_page_hint}</p>
      <pre className="text-xs text-red-300 whitespace-pre-wrap break-words bg-black/30 rounded p-2 max-h-40 overflow-auto">{error.message}</pre>
      <button type="button" onClick={reset}
        className="rounded bg-pokemon-blue px-3 py-1.5 text-sm text-white hover:opacity-90">{t.error_page_retry}</button>
    </div>
  );
}
