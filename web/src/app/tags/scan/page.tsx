"use client";
import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { ScanQrCode } from "lucide-react";
import QrScanner from "@/components/QrScanner";
import { cameraAvailable } from "@/components/CameraCapture";
import { useI18n } from "@/lib/i18n";

// Hüllen-Aufkleber scannen, ohne die Kamera-App: Code erkannt → /T/<Code>
// (klebt er schon → Karte; frei → Karte wählen, danach geht es hierher zurück).
export default function ScanTagPage() {
  const router = useRouter();
  const { t } = useI18n();
  const [canScan, setCanScan] = useState<boolean | null>(null);
  useEffect(() => { setCanScan(cameraAvailable()); }, []);

  const onResult = useCallback((code: string) => router.push(`/T/${code}?from=scan`), [router]);
  const close = useCallback(() => {
    if (window.history.length > 1) router.back();
    else router.push("/tags");
  }, [router]);

  if (canScan === null) return null;
  if (!canScan) {
    return (
      <div className="max-w-md mx-auto mt-10 bg-pokemon-card rounded-lg p-6 text-center space-y-3">
        <ScanQrCode className="w-10 h-10 mx-auto text-gray-500" aria-hidden />
        <p className="text-white text-sm">{t.qr_no_camera}</p>
        <Link href="/tags" className="inline-block text-sky-300 text-sm hover:underline">{t.tags_title}</Link>
      </div>
    );
  }
  return <QrScanner onResult={onResult} onClose={close} />;
}
