/* Dev index — a directory of the five PWA surfaces this one codebase serves
   via route groups (doc 02 §2). Not a patient-facing screen; it exists so a
   developer can jump between surfaces during the build. */
import Link from "next/link";

const surfaces = [
  { href: "/kiosk", label: "Kiosk", note: "Patient self check-in · S6–S7" },
  { href: "/board", label: "Queue board", note: "TV train-board · S8" },
  { href: "/doctor", label: "Doctor console", note: "Summary + dictation · S9–S11" },
  { href: "/coordinator", label: "Coordinator", note: "Queue ops + downtime · S8" },
  { href: "/admin", label: "Admin", note: "Trees, protocols, analytics · S18" },
];

export default function Home() {
  return (
    <main style={{ minHeight: "100vh", padding: "56px 24px" }}>
      <div style={{ maxWidth: 760, margin: "0 auto" }}>
        <p
          style={{
            fontSize: 13,
            fontWeight: 700,
            letterSpacing: "0.08em",
            textTransform: "uppercase",
            color: "var(--primary)",
            margin: "0 0 8px",
          }}
        >
          OPD Intelligence Platform · Oncology Pilot
        </p>
        <h1 style={{ fontSize: 34, margin: "0 0 8px" }}>Surfaces</h1>
        <p style={{ color: "var(--ink-soft)", margin: "0 0 32px", fontSize: 16 }}>
          One Next.js codebase, five route groups. Design tokens live; screens
          arrive in their sessions.
        </p>
        <div style={{ display: "grid", gap: 14 }}>
          {surfaces.map((s) => (
            <Link
              key={s.href}
              href={s.href}
              style={{
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
                background: "var(--surface)",
                border: "1px solid var(--line)",
                borderRadius: "var(--radius)",
                boxShadow: "var(--shadow)",
                padding: "20px 24px",
              }}
            >
              <span style={{ fontSize: 19, fontWeight: 600 }}>{s.label}</span>
              <span style={{ fontSize: 14, color: "var(--ink-soft)" }}>
                {s.note}
              </span>
            </Link>
          ))}
        </div>
      </div>
    </main>
  );
}
