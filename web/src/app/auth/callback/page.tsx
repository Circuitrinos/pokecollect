"use client";
import { useEffect, useState } from "react";
import { clearClientCaches } from "@/lib/api";
import { useI18n } from "@/lib/i18n";

/**
 * SSO-Rückweg (Mehrbenutzer): das Backend leitet nach erfolgreichem OIDC-Login
 * hierher um — /auth/callback#token=…&next=/…. Das Token steht im Fragment
 * (geht nie an einen Server, landet nicht in Logs), wird wie beim Passwort-
 * Login in localStorage abgelegt; danach harte Navigation zum Ziel.
 */
export default function AuthCallbackPage() {
  const { t } = useI18n();
  const [missing, setMissing] = useState(false);

  useEffect(() => {
    const params = new URLSearchParams(window.location.hash.replace(/^#/, ""));
    const token = params.get("token");
    let next = params.get("next") || "/";
    if (!next.startsWith("/") || next.startsWith("//")) next = "/";
    // Fragment sofort aus der Adresszeile/History entfernen
    window.history.replaceState(null, "", "/auth/callback");
    if (!token) {
      setMissing(true);
      return;
    }
    localStorage.setItem("token", token);
    clearClientCaches().finally(() => window.location.replace(next));
  }, []);

  return (
    <div className="min-h-[50vh] flex items-center justify-center">
      <div className="text-center space-y-3">
        <p className="text-gray-300 text-sm">{missing ? t.login_callback_missing : t.login_callback_busy}</p>
        {missing && (
          <a href="/login" className="inline-block bg-blue-700 text-white text-sm px-4 py-1.5 rounded hover:bg-blue-600">
            {t.login_submit}
          </a>
        )}
      </div>
    </div>
  );
}
