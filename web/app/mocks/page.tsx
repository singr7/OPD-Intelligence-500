import Link from "next/link";
import styles from "./mocks.module.css";

const metricTiles = [
  { label: "Patients checked in", value: "186", delta: "+12%", tone: "green" },
  { label: "Median wait", value: "24m", delta: "-8m", tone: "amber" },
  { label: "Red flags today", value: "11", delta: "3 urgent", tone: "red" },
  { label: "Voice completion", value: "94%", delta: "4 langs", tone: "indigo" },
];

const productTiles = [
  {
    name: "Kiosk intake",
    detail: "Guided voice flow, local language capture, offline token issue.",
    meta: "12 live stations",
    status: "Healthy",
  },
  {
    name: "Doctor console",
    detail: "One patient story, risks first, dictation ready in the same workspace.",
    meta: "6 rooms active",
    status: "Needs review",
  },
  {
    name: "Queue command",
    detail: "Department load, urgent arrivals, room status and downtime handoff.",
    meta: "38 waiting",
    status: "Busy",
  },
  {
    name: "Admin cockpit",
    detail: "Trees, protocol versions, channel health and operational costs.",
    meta: "99.2% uptime",
    status: "Stable",
  },
];

const doctorQueue = [
  { token: 214, name: "Shanti Devi", age: "58F", state: "In room", flag: "Urgent" },
  { token: 215, name: "Ramesh Khan", age: "44M", state: "Waiting", flag: "Pain 8/10" },
  { token: 216, name: "Meena Kumari", age: "36F", state: "Lab return", flag: "" },
  { token: 217, name: "Mahavir Singh", age: "63M", state: "Waiting", flag: "Breathless" },
  { token: 218, name: "Pooja Saini", age: "27F", state: "Waiting", flag: "" },
];

const queueRows = [
  {
    dept: "Medical Oncology",
    room: "Room 03",
    now: 214,
    next: "215, 216, 217",
    waiting: 18,
    wait: "31-42m",
    risk: "2 urgent",
  },
  {
    dept: "Radiation Review",
    room: "Room 07",
    now: 109,
    next: "110, 111, 112",
    waiting: 9,
    wait: "16-22m",
    risk: "Clear",
  },
  {
    dept: "ENT Routing",
    room: "Room 11",
    now: 52,
    next: "53, 54",
    waiting: 6,
    wait: "11-18m",
    risk: "1 red flag",
  },
  {
    dept: "Palliative Care",
    room: "Room 02",
    now: 88,
    next: "89, 90, 91",
    waiting: 5,
    wait: "8-14m",
    risk: "High pain",
  },
];

export const metadata = {
  title: "Design mocks - OPD Intelligence",
};

export default function MocksPage() {
  return (
    <main className={styles.page}>
      <header className={styles.reviewBar}>
        <Link href="/" className={styles.backLink}>
          Back
        </Link>
        <div>
          <p className={styles.eyebrow}>Design review mocks</p>
          <h1>OPD Intelligence command experience</h1>
        </div>
        <nav className={styles.reviewNav} aria-label="Mock sections">
          <a href="#landing">Landing</a>
          <a href="#doctor">Doctor</a>
          <a href="#queue">Queue</a>
        </nav>
      </header>

      <section id="landing" className={`${styles.screen} ${styles.landingScreen}`}>
        <div className={styles.landingShell}>
          <header className={styles.topNav}>
            <div className={styles.brand}>
              <span className={styles.brandMark} aria-hidden="true" />
              <span>OPD Intelligence</span>
            </div>
            <nav className={styles.navItems} aria-label="Product areas">
              <a>Command</a>
              <a>Care teams</a>
              <a>Protocols</a>
              <a>Admin</a>
            </nav>
            <button className={styles.iconButton} aria-label="Open operator switcher">
              <span />
              <span />
              <span />
            </button>
          </header>

          <div className={styles.heroGrid}>
            <section className={styles.heroCopy}>
              <p className={styles.kicker}>Alwar oncology pilot</p>
              <h2>One operating layer for intake, queueing, consults and follow-up.</h2>
              <p>
                A calm, enterprise-grade first screen that makes the product feel alive
                immediately: live tiles, care operations, risk signals and clear entry points.
              </p>
              <div className={styles.heroActions}>
                <button className={styles.primaryButton}>Open command center</button>
                <button className={styles.secondaryButton}>Review live queue</button>
              </div>
            </section>

            <section className={styles.commandPreview} aria-label="Live command preview">
              <div className={styles.previewHeader}>
                <span>Today at a glance</span>
                <strong>Live</strong>
              </div>
              <div className={styles.metricGrid}>
                {metricTiles.map((tile) => (
                  <article
                    className={`${styles.metricTile} ${styles[`tone_${tile.tone}`]}`}
                    key={tile.label}
                  >
                    <span>{tile.label}</span>
                    <strong>{tile.value}</strong>
                    <em>{tile.delta}</em>
                  </article>
                ))}
              </div>
              <div className={styles.flowMap}>
                <span>Kiosk</span>
                <i />
                <span>Triage</span>
                <i />
                <span>Doctor</span>
                <i />
                <span>Follow-up</span>
              </div>
            </section>
          </div>

          <section className={styles.productTiles} aria-label="Surface tiles">
            {productTiles.map((tile) => (
              <article className={styles.productTile} key={tile.name}>
                <div>
                  <p>{tile.meta}</p>
                  <h3>{tile.name}</h3>
                  <span>{tile.detail}</span>
                </div>
                <strong>{tile.status}</strong>
              </article>
            ))}
          </section>
        </div>
      </section>

      <section id="doctor" className={`${styles.screen} ${styles.doctorScreen}`}>
        <header className={styles.workspaceHeader}>
          <div>
            <p className={styles.eyebrow}>Doctor console mock</p>
            <h2>Medical Oncology - Room 03</h2>
          </div>
          <div className={styles.headerActions}>
            <button className={styles.secondaryButton}>Dictate</button>
            <button className={styles.primaryButton}>Call next</button>
          </div>
        </header>

        <div className={styles.doctorWorkspace}>
          <aside className={styles.patientRail}>
            <div className={styles.railSummary}>
              <strong>18</strong>
              <span>waiting</span>
              <em>31-42m median</em>
            </div>
            <ol>
              {doctorQueue.map((row, index) => (
                <li className={index === 0 ? styles.currentPatient : ""} key={row.token}>
                  <button>
                    <span className={styles.token}>{row.token}</span>
                    <span className={styles.patientName}>
                      {row.name}
                      <em>{row.age}</em>
                    </span>
                    <span className={styles.patientState}>{row.state}</span>
                    {row.flag && <strong>{row.flag}</strong>}
                  </button>
                </li>
              ))}
            </ol>
          </aside>

          <section className={styles.patientStage}>
            <div className={styles.redFlagStrip}>
              <strong>Urgent red flag</strong>
              <span>New breathlessness with fever after chemotherapy. Patient was told to alert staff.</span>
            </div>

            <header className={styles.patientHeader}>
              <div>
                <p>Token 214 - MRN ALW-20481</p>
                <h2>Shanti Devi</h2>
                <span>58F - Tijara - Hindi intake - Caregiver present</span>
              </div>
              <div className={styles.vitalsPanel}>
                <span>BP 146/92</span>
                <span>SpO2 92%</span>
                <span>Pain 7/10</span>
              </div>
            </header>

            <section className={styles.clinicalGrid}>
              <article>
                <p>Chief concern</p>
                <h3>Fever, chest tightness and increasing breathlessness since yesterday.</h3>
                <blockquote>
                  &quot;Saans phool rahi hai, raat se bukhar bhi hai.&quot;
                </blockquote>
              </article>
              <article>
                <p>Decision support</p>
                <ul>
                  <li>Possible neutropenic infection - check CBC before chemo clearance.</li>
                  <li>Route to vitals bay if oxygen saturation remains below 94%.</li>
                  <li>Confirm last paclitaxel cycle and home antibiotics.</li>
                </ul>
              </article>
            </section>

            <section className={styles.symptomTable} aria-label="Symptoms">
              <div>
                <span>Symptom</span>
                <span>Duration</span>
                <span>Severity</span>
                <span>Trend</span>
              </div>
              <div>
                <strong>Breathlessness</strong>
                <span>18 hours</span>
                <span>Severe</span>
                <em className={styles.badTrend}>Worse</em>
              </div>
              <div>
                <strong>Fever</strong>
                <span>1 day</span>
                <span>101.8 F</span>
                <em className={styles.badTrend}>New</em>
              </div>
              <div>
                <strong>Nausea</strong>
                <span>3 days</span>
                <span>Mild</span>
                <em>Stable</em>
              </div>
            </section>

            <footer className={styles.consultActions}>
              <button className={styles.primaryButton}>Start consult</button>
              <button className={styles.secondaryButton}>Send to vitals</button>
              <button className={styles.secondaryButton}>Lab and re-queue</button>
              <button className={styles.secondaryButton}>Close visit</button>
            </footer>
          </section>
        </div>
      </section>

      <section id="queue" className={`${styles.screen} ${styles.queueScreen}`}>
        <header className={styles.workspaceHeader}>
          <div>
            <p className={styles.eyebrow}>Queue dashboard mock</p>
            <h2>Operational queue command</h2>
          </div>
          <div className={styles.headerActions}>
            <button className={styles.secondaryButton}>Print backup sheets</button>
            <button className={styles.primaryButton}>Enter downtime</button>
          </div>
        </header>

        <section className={styles.queueKpis}>
          <article>
            <span>Total waiting</span>
            <strong>38</strong>
            <em>Across 4 departments</em>
          </article>
          <article>
            <span>Urgent tokens</span>
            <strong>4</strong>
            <em>2 not yet called</em>
          </article>
          <article>
            <span>Longest wait</span>
            <strong>47m</strong>
            <em>Medical Oncology</em>
          </article>
          <article>
            <span>Rooms idle</span>
            <strong>1</strong>
            <em>Radiation Review</em>
          </article>
        </section>

        <section className={styles.queueBoardMock}>
          <div className={styles.queueTable}>
            <div className={styles.queueHead}>
              <span>Department</span>
              <span>Now</span>
              <span>Next tokens</span>
              <span>Waiting</span>
              <span>Wait</span>
              <span>Risk</span>
            </div>
            {queueRows.map((row) => (
              <article className={styles.queueRow} key={row.dept}>
                <span>
                  <strong>{row.dept}</strong>
                  <em>{row.room}</em>
                </span>
                <b>{row.now}</b>
                <span>{row.next}</span>
                <span>{row.waiting}</span>
                <span>{row.wait}</span>
                <mark>{row.risk}</mark>
              </article>
            ))}
          </div>

          <aside className={styles.opsPanel}>
            <section>
              <h3>Useful queue pieces</h3>
              <ul>
                <li>Longest wait by department and room.</li>
                <li>Urgent token count with not-called breakdown.</li>
                <li>Idle room detection and last call age.</li>
                <li>Paper token reconciliation status.</li>
              </ul>
            </section>
            <section>
              <h3>Next best actions</h3>
              <button>Call Medical Oncology 215</button>
              <button>Route token 217 to vitals</button>
              <button>Rebalance ENT room load</button>
            </section>
          </aside>
        </section>
      </section>
    </main>
  );
}
