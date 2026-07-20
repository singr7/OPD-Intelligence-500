"use client";

// Minimal phone-OTP login for staff (doc 03 §5 login is phone-OTP for everyone).
// Two steps: request a code, then verify. Locally OTP_DEBUG_ECHO returns the
// code, which we surface as a hint so the demo needs no SMS provider.

import { useState } from "react";
import { requestOtp, verifyOtp } from "@/app/_lib/queue";

export function Login({ onToken }: { onToken: (token: string) => void }) {
  const [phone, setPhone] = useState("+915550000002"); // seeded coordinator
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
      setError("Could not send a code. Is the API running?");
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
      setError(err instanceof Error ? err.message : "Wrong code");
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="login">
      <style>{LOGIN_CSS}</style>
      <section className="card">
        <div className="badge">Coordinator</div>
        <h1>Sign in</h1>
        <p className="sub">Keep the queue moving and run downtime without panic.</p>

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
            <button type="submit" disabled={busy}>
              {busy ? "Sending…" : "Send code"}
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
              autoFocus
            />
            {hint && <p className="hint">{hint}</p>}
            <button type="submit" disabled={busy}>
              {busy ? "Checking…" : "Sign in"}
            </button>
            <button type="button" className="link" onClick={() => setStep("phone")}>
              ← Change number
            </button>
          </form>
        )}
        {error && <p className="error">{error}</p>}
      </section>
    </main>
  );
}

const LOGIN_CSS = `
.login { min-height: 100vh; display: flex; align-items: center; justify-content: center;
  background: var(--bg); padding: 24px; }
.login .card { width: 100%; max-width: 400px; background: var(--surface);
  border: 1px solid var(--line); border-radius: var(--radius); box-shadow: var(--shadow);
  padding: 36px; }
.login .badge { display: inline-block; font-size: 12px; font-weight: 700; letter-spacing: .08em;
  text-transform: uppercase; color: var(--accent); background: var(--accent-soft);
  border-radius: 999px; padding: 5px 12px; margin-bottom: 16px; }
.login h1 { margin: 0 0 6px; font-size: 26px; color: var(--ink); }
.login .sub { margin: 0 0 22px; color: var(--ink-soft); font-size: 15px; line-height: 1.5; }
.login label { display: block; font-size: 13px; font-weight: 600; color: var(--ink-soft);
  margin-bottom: 8px; }
.login input { width: 100%; font-size: 20px; padding: 14px 16px; border: 1.5px solid var(--line);
  border-radius: 14px; margin-bottom: 16px; color: var(--ink); font-variant-numeric: tabular-nums; }
.login input:focus { outline: none; border-color: var(--primary); }
.login button[type=submit] { width: 100%; font-size: 17px; font-weight: 700; color: #fff;
  background: var(--primary); border: none; border-radius: 14px; padding: 14px; cursor: pointer; }
.login button[type=submit]:disabled { opacity: .6; cursor: default; }
.login .link { background: none; border: none; color: var(--ink-soft); font-size: 14px;
  margin-top: 12px; cursor: pointer; }
.login .hint { color: var(--primary-d); background: var(--primary-soft); border-radius: 10px;
  padding: 8px 12px; font-size: 14px; margin: 0 0 14px; font-weight: 600; }
.login .error { color: var(--danger); font-size: 14px; margin-top: 14px; }
`;
