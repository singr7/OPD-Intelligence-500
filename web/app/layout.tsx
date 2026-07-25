import type { Metadata } from "next";
import { Noto_Sans, Noto_Sans_Devanagari, Noto_Sans_Telugu } from "next/font/google";
import "./globals.css";

// Self-hosted at build (doc 04 §1: "self-hosted, subset"); no runtime font
// request, which the kiosk's offline mode (S7) needs. Devanagari covers both Hindi
// and Marathi; Telugu is its own script and its own subset (S13 — the fourth
// pilot language; doc 04 §4 wants Telugu rendered, not tofu).
const notoSans = Noto_Sans({
  subsets: ["latin"],
  weight: ["400", "500", "600", "700", "800"],
  variable: "--font-sans",
  display: "swap",
});

const notoDevanagari = Noto_Sans_Devanagari({
  subsets: ["devanagari"],
  weight: ["400", "500", "600", "700", "800"],
  variable: "--font-deva",
  display: "swap",
});

const notoTelugu = Noto_Sans_Telugu({
  subsets: ["telugu"],
  weight: ["400", "500", "600", "700", "800"],
  variable: "--font-telugu",
  display: "swap",
});

export const metadata: Metadata = {
  title: "OPD Intelligence Platform",
  description: "Voice-first OPD intake for oncology care — Alwar pilot",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html
      lang="en"
      className={`${notoSans.variable} ${notoDevanagari.variable} ${notoTelugu.variable}`}
    >
      <body>{children}</body>
    </html>
  );
}
