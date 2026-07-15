import type { Metadata } from "next";
import "./globals.css";

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
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
