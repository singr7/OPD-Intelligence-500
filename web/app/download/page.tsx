"use client";

import { useEffect, useState } from "react";
import styles from "./download.module.css";

type Manifest = {
  version_name: string;
  size_bytes: number;
  sha256: string;
  certificate_sha256: string;
  built_at: string;
};

export default function DownloadPage() {
  const [manifest, setManifest] = useState<Manifest | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    fetch("/downloads/opd-patient-latest.json", { cache: "no-store" })
      .then((response) => {
        if (!response.ok) throw new Error("release unavailable");
        return response.json();
      })
      .then(setManifest)
      .catch(() => setFailed(true));
  }, []);

  const size = manifest ? `${(manifest.size_bytes / 1024 / 1024).toFixed(2)} MB` : "—";
  return (
    <main className={styles.shell}>
      <section className={styles.card}>
        <div className={styles.mark} aria-hidden="true">OPD</div>
        <p className={styles.eyebrow}>Alwar hospital patient app</p>
        <h1>Carry your cancer care file on your phone.</h1>
        <p className={styles.lead}>
          One hospital-signed app for prescriptions, queue updates, medicine reminders,
          and speaking with Dhara. Works on Android 8 and newer.
        </p>

        {failed ? (
          <div className={styles.notice}>The verified download is not available yet. Please ask the hospital desk.</div>
        ) : (
          <>
            <dl className={styles.facts}>
              <div><dt>Version</dt><dd>{manifest?.version_name ?? "Checking…"}</dd></div>
              <div><dt>Size</dt><dd>{size}</dd></div>
              <div><dt>Released</dt><dd>{manifest ? new Date(manifest.built_at).toLocaleDateString() : "—"}</dd></div>
              <div><dt>Android</dt><dd>8.0 or newer</dd></div>
            </dl>
            <a className={styles.download} href="/downloads/opd-patient-latest.apk" download>
              Download verified APK
            </a>
          </>
        )}

        <ol className={styles.steps}>
          <li>When the hospital confirms it is ready, download the APK from this HTTPS page.</li>
          <li>Open it and allow installation from this browser when Android asks.</li>
          <li>Open “My Cancer Care” and let hospital staff verify the server before login.</li>
        </ol>

        {manifest && (
          <details className={styles.proof}>
            <summary>Verify the hospital signature and checksum</summary>
            <p>APK SHA-256</p><code>{manifest.sha256}</code>
            <p>Signing certificate SHA-256</p><code>{manifest.certificate_sha256}</code>
          </details>
        )}
        <p className={styles.caution}>
          This is a direct hospital download. It has not been reviewed or approved by Google Play.
        </p>
      </section>
    </main>
  );
}
