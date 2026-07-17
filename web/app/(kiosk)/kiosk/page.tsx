import { KioskApp } from "./KioskApp";

// The kiosk PWA (doc 03 §1a) — a V3 client of the intake engine. The whole flow
// (language → caregiver → voice chief complaint → tree questions → read-back →
// token) lives in the client component; this route just mounts it.
export const metadata = {
  title: "Dhara · OPD Kiosk",
};

export default function KioskPage() {
  return <KioskApp />;
}
