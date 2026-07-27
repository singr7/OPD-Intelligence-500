import Link from "next/link";
import {
  Activity,
  ArrowUpRight,
  ClipboardPlus,
  LayoutDashboard,
  Monitor,
  Settings2,
  Stethoscope,
} from "lucide-react";
import styles from "./home.module.css";

const pathways = [
  {
    href: "/kiosk",
    title: "Patient kiosk",
    description: "Start a guided multilingual intake and issue a queue token.",
    action: "Open kiosk",
    icon: ClipboardPlus,
    primary: true,
    meta: "Patient pathway",
  },
  {
    href: "/doctor",
    title: "Doctor workspace",
    description: "Review risk-first patient summaries, dictate notes, and issue prescriptions.",
    action: "Open workspace",
    icon: Stethoscope,
    meta: "Clinical",
  },
  {
    href: "/coordinator",
    title: "Queue operations",
    description: "Coordinate departments, patient flow, downtime, and reconciliation.",
    action: "Open operations",
    icon: LayoutDashboard,
    meta: "Staff",
  },
  {
    href: "/board",
    title: "Public queue board",
    description: "Display now-serving tokens and room queues on the OPD television.",
    action: "Open board",
    icon: Monitor,
    meta: "Public display",
  },
  {
    href: "/admin",
    title: "Administration",
    description: "Manage channels, people, clinical content, costs, and operating controls.",
    action: "Open control room",
    icon: Settings2,
    meta: "Restricted",
  },
];

export default function Home() {
  return (
    <main className={styles.page}>
      <header className={styles.topbar}>
        <Link href="/" className={styles.brand} aria-label="Dhara OPD home">
          <span className={styles.mark} aria-hidden="true">
            <Activity />
          </span>
          <span>
            <strong>Dhara OPD</strong>
            <small>Oncology operations</small>
          </span>
        </Link>
        <div className={styles.site}>
          <span className={styles.siteDot} aria-hidden="true" />
          Alwar pilot
        </div>
      </header>

      <section className={styles.intro} aria-labelledby="gateway-title">
        <div>
          <p className={styles.eyebrow}>OPD Intelligence Platform</p>
          <h1 id="gateway-title">Choose your workspace</h1>
          <p className={styles.lead}>
            Patient intake, clinical review, and queue operations in one local hospital system.
          </p>
        </div>
        <div className={styles.localNote}>
          <Activity aria-hidden="true" />
          <span>
            <strong>Local-first operations</strong>
            <small>Designed to continue safely through provider and network interruptions.</small>
          </span>
        </div>
      </section>

      <section className={styles.grid} aria-label="OPD workspaces">
        {pathways.map(({ icon: Icon, ...pathway }) => (
          <Link
            href={pathway.href}
            className={`${styles.tile} ${pathway.primary ? styles.primaryTile : ""}`}
            key={pathway.href}
          >
            <div className={styles.tileTop}>
              <span className={styles.tileIcon}>
                <Icon aria-hidden="true" />
              </span>
              <span className={styles.meta}>{pathway.meta}</span>
            </div>
            <div className={styles.tileBody}>
              <h2>{pathway.title}</h2>
              <p>{pathway.description}</p>
            </div>
            <span className={styles.open}>
              {pathway.action}
              <ArrowUpRight aria-hidden="true" />
            </span>
          </Link>
        ))}
      </section>

      <footer className={styles.footer}>
        <span>Government Cancer Hospital, Alwar</span>
        <span>Clinical decisions remain with the care team.</span>
      </footer>
    </main>
  );
}
