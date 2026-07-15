/* Shared S1 placeholder shell for each route-group landing page.
   Deliberately on-brand (tokens from doc 04 §1) rather than a generic admin
   card, but explicitly marked as scaffold — the real designed screens land in
   their own sessions (kiosk S6/S7, board+coordinator S8, doctor S9, admin S18). */
import Link from "next/link";

export function Scaffold({
  surface,
  job,
  session,
  dark = false,
}: {
  surface: string;
  job: string;
  session: string;
  dark?: boolean;
}) {
  return (
    <main
      style={{
        minHeight: "100vh",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        padding: "48px",
        background: dark ? "var(--primary-d)" : "var(--bg)",
        color: dark ? "#fff" : "var(--ink)",
      }}
    >
      <section
        style={{
          maxWidth: 560,
          width: "100%",
          background: dark ? "rgba(255,255,255,0.06)" : "var(--surface)",
          border: `1px solid ${dark ? "rgba(255,255,255,0.14)" : "var(--line)"}`,
          borderRadius: "var(--radius)",
          boxShadow: dark ? "none" : "var(--shadow)",
          padding: "40px",
        }}
      >
        <div
          style={{
            display: "inline-block",
            fontSize: 13,
            fontWeight: 700,
            letterSpacing: "0.08em",
            textTransform: "uppercase",
            color: "var(--accent)",
            background: "var(--accent-soft)",
            borderRadius: 999,
            padding: "6px 14px",
            marginBottom: 20,
          }}
        >
          {surface}
        </div>
        <h1 style={{ fontSize: 30, lineHeight: 1.2, margin: "0 0 12px" }}>
          {job}
        </h1>
        <p
          style={{
            fontSize: 16,
            lineHeight: 1.6,
            color: dark ? "rgba(255,255,255,0.75)" : "var(--ink-soft)",
            margin: "0 0 28px",
          }}
        >
          Scaffold only — the designed screen is built in {session}. Route group
          and design tokens are wired up now so later sessions drop straight into
          this shell.
        </p>
        <Link
          href="/"
          style={{
            fontSize: 15,
            fontWeight: 600,
            color: dark ? "var(--accent)" : "var(--primary)",
          }}
        >
          ← All surfaces
        </Link>
      </section>
    </main>
  );
}
