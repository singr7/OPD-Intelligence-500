"use client";

import { ArrowLeft, LockKeyhole } from "lucide-react";
import { useState } from "react";
import { requestOtp, verifyOtp } from "@/app/_lib/queue";
import styles from "./staffLogin.module.css";

type StaffLoginProps = {
  defaultPhone: string;
  description: string;
  onToken: (token: string) => void;
  role: string;
};

export function StaffLogin({
  defaultPhone,
  description,
  onToken,
  role,
}: StaffLoginProps) {
  const [phone, setPhone] = useState(defaultPhone);
  const [code, setCode] = useState("");
  const [step, setStep] = useState<"phone" | "code">("phone");
  const [hint, setHint] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function sendCode(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const res = await requestOtp(phone.trim());
      setHint(res.debug_code ? `Demo code: ${res.debug_code}` : null);
      setStep("code");
    } catch {
      setError("Could not send a code. Check that the API is available.");
    } finally {
      setBusy(false);
    }
  }

  async function submitCode(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const { access_token } = await verifyOtp(phone.trim(), code.trim());
      onToken(access_token);
    } catch (err) {
      setError(err instanceof Error ? err.message : "The code was not accepted.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className={`login ${styles.page}`}>
      <section className={`card ${styles.card}`}>
        <div className={styles.identity}>
          <span className={styles.mark}>D</span>
          <span>Dhara OPD</span>
        </div>
        <div className={styles.role}>{role} workspace</div>
        <h1>Sign in</h1>
        <p className={styles.description}>{description}</p>

        {step === "phone" ? (
          <form onSubmit={sendCode}>
            <label htmlFor="phone">Phone number</label>
            <input
              id="phone"
              value={phone}
              onChange={(e) => setPhone(e.target.value)}
              inputMode="tel"
              autoComplete="tel"
            />
            <button className={styles.primary} type="submit" disabled={busy}>
              <LockKeyhole aria-hidden="true" />
              {busy ? "Sending..." : "Send secure code"}
            </button>
          </form>
        ) : (
          <form onSubmit={submitCode}>
            <label htmlFor="code">Enter the 6-digit code</label>
            <input
              id="code"
              value={code}
              onChange={(e) => setCode(e.target.value)}
              inputMode="numeric"
              autoComplete="one-time-code"
              autoFocus
            />
            {hint && (
              <p className={styles.hint} data-testid="otp-hint">
                {hint}
              </p>
            )}
            <button className={styles.primary} type="submit" disabled={busy}>
              <LockKeyhole aria-hidden="true" />
              {busy ? "Checking..." : "Sign in"}
            </button>
            <button
              type="button"
              className={styles.back}
              onClick={() => setStep("phone")}
            >
              <ArrowLeft aria-hidden="true" />
              Change number
            </button>
          </form>
        )}
        {error && (
          <p className={styles.error} role="alert">
            {error}
          </p>
        )}
        <p className={styles.security}>Authorised hospital personnel only</p>
      </section>
    </main>
  );
}
