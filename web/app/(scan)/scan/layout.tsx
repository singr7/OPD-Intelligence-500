import type { Metadata, Viewport } from "next";

// Installable on the coordinator's phone: added to the home screen it opens
// full-screen, which is what makes a fifteen-second scanning step feel like a
// tool rather than a browser tab. The manifest is the whole of the "app" —
// there is no store listing and no native build (doc 21 decision 1).
export const metadata: Metadata = {
  title: "Records scanning · OPD",
  description: "Photograph a patient's reports so the doctor has them before the consult.",
  manifest: "/scan.webmanifest",
  appleWebApp: { capable: true, title: "OPD Scan", statusBarStyle: "default" },
};

export const viewport: Viewport = {
  themeColor: "#087f68",
  // The camera is used one-handed at arm's length; a double-tap zoom while
  // reaching for the shutter is a mis-tap, not a gesture.
  maximumScale: 5,
  width: "device-width",
  initialScale: 1,
};

export default function ScanLayout({ children }: { children: React.ReactNode }) {
  return children;
}
