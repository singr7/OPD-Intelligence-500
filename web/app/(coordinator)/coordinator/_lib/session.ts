// The coordinator's access token, kept in localStorage so a reload doesn't force
// a re-login mid-shift. This is a minimal staff session for S8; the fuller login
// (refresh rotation, /me, role display) is S9's doctor console, which reuses the
// same /auth endpoints. Not httpOnly — a pilot on a trusted LAN behind Caddy; a
// production hardening pass (httpOnly cookie) is noted for S19/S20.

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
