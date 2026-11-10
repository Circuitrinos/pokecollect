"use client";
import { useEffect, useState } from "react";
import toast from "react-hot-toast";
import axios from "axios";
import { api, authApi, AuthConfig, clearClientCaches } from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import { APP_VERSION } from "@/lib/version";

const INPUT = "w-full bg-gray-900 border border-gray-700 rounded px-3 py-2 text-white text-sm focus:outline-none focus:border-blue-500";

export default function LoginPage() {
  const { t } = useI18n();
  const [username, setUsername] = useState("admin");
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  // Anmeldewege (Mehrbenutzer): SSO-Knopf und/oder lokales Passwort.
  // Bis die Antwort da ist: Passwortformular wie bisher (ältere Backends).
  const [cfg, setCfg] = useState<AuthConfig | null>(null);
  const [showPassword, setShowPassword] = useState(false);

  useEffect(() => {
    authApi.config().then((r) => setCfg(r.data)).catch(() => setCfg(null));
    // Fehler aus dem SSO-Rückweg (/auth/oidc/callback → /login?sso_error=…)
    const err = new URLSearchParams(window.location.search).get("sso_error");
    if (err) {
      toast.error(t.login_sso_failed(err), { duration: 8000 });
      window.history.replaceState(null, "", "/login");
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const sso = cfg?.oidc_enabled ?? false;
  const passwordAllowed = cfg ? cfg.password_login : true;
  const passwordVisible = passwordAllowed && (!sso || showPassword);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (submitting || !password) return;
    setSubmitting(true);
    try {
      const r = await api.post<{ access_token: string }>("/auth/login", { username, password });
      // Bewusste Entscheidung localStorage (ADR-0003): der Request-Interceptor
      // in api.ts liest den Key "token".
      localStorage.setItem("token", r.data.access_token);
      await clearClientCaches();
      // Harte Navigation statt Router: alle Seiten-States frisch laden
      window.location.href = "/";
    } catch (err) {
      if (axios.isAxiosError(err) && err.response?.status === 401) {
        toast.error(t.login_failed);
      } else if (axios.isAxiosError(err) && err.response?.status === 403) {
        toast.error(t.login_password_disabled);
      } else {
        toast.error(t.login_error);
      }
      setSubmitting(false);
    }
  };

  return (
    <div className="min-h-[70vh] flex items-center justify-center">
      <div className="bg-pokemon-card rounded-lg p-6 w-full max-w-sm space-y-4">
        <div className="text-center space-y-1">
          <h1 className="text-xl font-bold text-pokemon-yellow">PokéCollect</h1>
          <p className="text-gray-500 text-xs">v{APP_VERSION}</p>
          <h2 className="text-white font-semibold text-sm pt-2">{t.login_title}</h2>
        </div>

        {sso && (
          <a
            href={authApi.ssoLoginUrl("/")}
            className="block w-full text-center bg-blue-700 text-white text-sm px-4 py-2 rounded hover:bg-blue-600"
          >
            {t.login_sso(cfg?.oidc_provider_name || "SSO")}
          </a>
        )}

        {sso && passwordAllowed && !showPassword && (
          <button
            type="button"
            onClick={() => setShowPassword(true)}
            className="block w-full text-center text-gray-500 text-xs hover:text-gray-300 underline decoration-dotted underline-offset-2"
          >
            {t.login_or_password}
          </button>
        )}

        {passwordVisible && (
          <form onSubmit={submit} className={`space-y-4 ${sso ? "border-t border-gray-700 pt-4" : ""}`}>
            <label className="block">
              <span className="block text-gray-400 text-xs mb-1">{t.login_username}</span>
              <input
                type="text"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                className={INPUT}
                autoComplete="username"
                autoCapitalize="none"
                required
              />
            </label>
            <label className="block">
              <span className="block text-gray-400 text-xs mb-1">{t.login_password}</span>
              <input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                className={INPUT}
                autoComplete="current-password"
                required
              />
            </label>
            <button
              type="submit"
              disabled={submitting || !password}
              className={`w-full text-white text-sm px-4 py-2 rounded disabled:opacity-50 ${sso ? "bg-gray-700 hover:bg-gray-600" : "bg-blue-700 hover:bg-blue-600"}`}
            >
              {submitting ? t.login_submitting : t.login_submit}
            </button>
          </form>
        )}

        {cfg && !sso && !passwordAllowed && (
          <p className="text-gray-400 text-xs text-center">{t.login_password_disabled}</p>
        )}
      </div>
    </div>
  );
}
