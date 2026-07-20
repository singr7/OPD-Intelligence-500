"use client";

// The coordinator surface (doc 03 §6). A thin gate: show the login until we hold
// a staff token, then the console. The token lives in localStorage so a reload
// mid-shift doesn't sign the coordinator out (see _lib/session.ts).

import { useEffect, useState } from "react";
import { Login } from "./_components/Login";
import { Console } from "./_components/Console";
import { clearToken, getToken, setToken } from "./_lib/session";

export default function CoordinatorPage() {
  const [token, setTok] = useState<string | null>(null);
  const [ready, setReady] = useState(false);

  // localStorage is client-only; read it after mount to avoid a hydration gap.
  useEffect(() => {
    setTok(getToken());
    setReady(true);
  }, []);

  const signIn = (t: string) => {
    setToken(t);
    setTok(t);
  };
  const signOut = () => {
    clearToken();
    setTok(null);
  };

  if (!ready) return null;
  if (!token) return <Login onToken={signIn} />;
  return <Console token={token} onSignOut={signOut} />;
}
