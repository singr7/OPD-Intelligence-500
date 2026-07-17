import type { Metadata } from "next";
import { Noto_Sans, Noto_Sans_Devanagari } from "next/font/google";
import "./globals.css";

// Self-hosted at build (doc 04 §1: "self-hosted, subset"); no runtime font
// request, which the kiosk's offline mode (S7) needs. Telugu/Marathi land in S13.
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
      className={`${notoSans.variable} ${notoDevanagari.variable}`}
    >
      <body>{children}</body>
    </html>
  );
}
