// The doctor's access token. Deliberately the same localStorage key as the
// coordinator console (S8): both are staff logins against the same /auth
// endpoints, and a doctor who is also covering coordination should not have to
// sign in twice in one shift. Not httpOnly — a pilot on a trusted LAN behind
// Caddy; the cookie hardening pass is S19/S20 (STATE.md → Stubs & fakes).

const KEY = "opd_staff_token";

export function getToken(): string | null {
  if (typeof window === "undefined") return null;
  return window.localStorage.getItem(KEY);
}

export function setToken(token: string): void {
  window.localStorage.setItem(KEY, token);
}

export function clearToken(): void {
  window.localStorage.removeItem(KEY);
}
