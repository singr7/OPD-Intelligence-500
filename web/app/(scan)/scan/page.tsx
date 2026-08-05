"use client";

// The coordinator's scanner (doc 21 §1.2). A phone, at the desk, with the
// patient's folder in the other hand.
//
// Same gate as /coordinator: staff token in localStorage, so a screen lock
// mid-shift doesn't cost a login. It shares the token key deliberately — this
// is the same person on the same shift, and asking them to log in twice on a
// phone keyboard is how a scanning step gets skipped.

import { useEffect, useState } from "react";
import { StaffLogin } from "@/app/_components/StaffLogin";
import { clearToken, getToken, setToken } from "@/app/(coordinator)/coordinator/_lib/session";
import { Scanner } from "./_components/Scanner";

export default function ScanPage() {
  const [token, setTok] = useState<string | null>(null);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    setTok(getToken());
    setReady(true);
  }, []);

  if (!ready) return null;
  if (!token) {
    return (
      <StaffLogin
        role="Records scanning"
        description="Photograph a patient's reports so the doctor has them before the consult."
        defaultPhone="+915550000002"
        onToken={(t) => {
          setToken(t);
          setTok(t);
        }}
      />
    );
  }
  return (
    <Scanner
      token={token}
      onSignOut={() => {
        clearToken();
        setTok(null);
      }}
    />
  );
}
