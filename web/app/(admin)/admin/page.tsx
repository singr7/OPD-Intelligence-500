"use client";

// The admin surface (doc 03 §10/§11, S18). A thin gate: show the login until we
// hold a token, then the console. The token lives in localStorage (shared with the
// coordinator console) so a reload doesn't sign the operator out. Every /admin
// route is ADMIN-gated server-side, so a non-admin token just yields 403s inside.

import { useEffect, useState } from "react";
import { Login } from "./_components/Login";
import { Console } from "./_components/Console";
import { clearToken, getToken, setToken } from "./_lib/session";

export default function AdminPage() {
  const [token, setTok] = useState<string | null>(null);
  const [ready, setReady] = useState(false);

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
