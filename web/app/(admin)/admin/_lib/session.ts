// The admin's access token. Reuses the same localStorage key as the coordinator
// console (`opd_staff_token`): both authenticate through the shared /auth phone-OTP
// flow, and an operator who is already signed in as staff should not have to sign
// in again to open admin. The backend still gates every /admin route on the ADMIN
// role, so holding a non-admin token here just yields 403s, not access.
//
// Not httpOnly — a pilot on a trusted LAN behind Caddy; the httpOnly-cookie
// hardening pass is noted for S19/S20, same as the coordinator console.

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
