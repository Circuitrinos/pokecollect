"use client";
// Konfig-UI der BottomNav (Einstellungen → Abschnitt "Navigation"): die Leiste
// zeigt alle Ziele; per Häkchen lassen sich einzelne ausblenden. Ausgeblendetes
// bleibt über das Burger-Menü (☰) erreichbar. Speicherung per Gerät.
import { visibleNavTargets } from "@/lib/navRegistry";
import { useMe } from "@/lib/useMe";
import { useBottomNavConfig } from "@/lib/useBottomNav";
import { useI18n } from "@/lib/i18n";

export default function BottomNavSettings() {
  const { t } = useI18n();
  const { keys, toggle } = useBottomNavConfig();
  const isAdmin = useMe()?.is_admin ?? false;

  return (
    <div className="space-y-3">
      <p className="text-gray-400 text-xs">{t.settings_nav_hint}</p>
      <div className="space-y-1">
        {visibleNavTargets(isAdmin).map((target) => {
          const { key, Icon, labelKey } = target;
          return (
            <label key={key} className="flex items-center gap-3 py-1.5 text-sm cursor-pointer">
              <input
                type="checkbox"
                checked={keys.includes(key)}
                onChange={() => toggle(key)}
                className="h-4 w-4 shrink-0"
              />
              <Icon size={18} className="text-gray-400 shrink-0" />
              <span className="text-gray-200">{t[labelKey]}</span>
            </label>
          );
        })}
      </div>
    </div>
  );
}
