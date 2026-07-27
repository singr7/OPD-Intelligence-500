"use client";

// People + roster (S-GL.2, doc 03 §2/§10).
//
// The screen's single job: **onboard somebody, give them a clinic, and see the
// consequences before committing to them.** Until this tab existed, hiring a
// doctor meant editing `seeds/doctors.json` on the box and re-running the seed.
//
// Three things, in this order:
//
// 1. **The week.** A hospital reads its roster as a timetable on a wall, not as a
//    list of rows sorted by primary key — so the grid is seven columns and one
//    row per doctor, and a doctor with an empty week is *visibly* empty rather
//    than merely absent from a table. That is the tab's one deliberate risk
//    (doc 04 §5), and everything else here stays quiet.
// 2. **The import**, whose dry run is the whole feature: the preview table and
//    the apply are the same request with one flag flipped.
// 3. **The people**, each row carrying the two numbers that make deactivating
//    them a decision rather than a toggle — clinics, and patients already booked.
//
// One distinction the backend keeps and this console must not collapse:
// **authored** and **bookable** are different. A clinic exists the moment it is
// saved; a patient can only be booked into inventory that has been *generated*
// from it. Every clinic block shows both, because "I added Tuesdays and the
// receptionist still says she is full" is the failure this tab is for.

import { useState } from "react";
import type { TabProps } from "./Console";
import { useLoad } from "../_lib/useLoad";
import * as api from "../_lib/api";

const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

const SLOT_TYPE_LABEL: Record<string, string> = {
  new_consult: "new consult",
  follow_up: "follow-up",
  chemo_review: "chemo review",
};

const ROLE_LABEL: Record<string, string> = {
  admin: "Admin",
  doctor: "Doctor",
  coordinator: "Coordinator",
  nurse: "Nurse",
};

export function PeopleTab({ token, onError }: TabProps) {
  const people = useLoad(() => api.fetchPeople(token), onError);
  const clinics = useLoad(() => api.fetchClinics(token), onError);
  const departments = useLoad(() => api.fetchDepartments(token), onError);
  const [flash, setFlash] = useState<string | null>(null);

  function reload() {
    people.reload();
    clinics.reload();
  }

  const doctors = (people.data ?? []).filter((p) => p.doctor_id !== null && p.active);

  return (
    <>
      {/* At the top, not next to whatever was clicked. Rendering this tab found
          the reason: the roster import lives above a long people table, so a
          confirmation placed after it landed several hundred pixels below the
          fold — an operator pressed Apply and saw nothing happen. */}
      {flash && (
        <div className="notice flash" role="status">
          {flash}
        </div>
      )}

      <Week
        token={token}
        doctors={doctors}
        clinics={clinics.data ?? []}
        error={clinics.error}
        onChanged={reload}
        onFlash={setFlash}
        onError={onError}
      />

      <RosterImport token={token} onApplied={reload} onFlash={setFlash} onError={onError} />

      <People
        token={token}
        people={people.data ?? []}
        departments={departments.data ?? []}
        error={people.error}
        onChanged={reload}
        onFlash={setFlash}
        onError={onError}
      />
    </>
  );
}

// -- 1. the week ---------------------------------------------------------------

function Week({
  token,
  doctors,
  clinics,
  error,
  onChanged,
  onFlash,
  onError,
}: {
  token: string;
  doctors: api.Person[];
  clinics: api.Clinic[];
  error: string | null;
  onChanged: () => void;
  onFlash: (s: string) => void;
  onError: (e: unknown) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [adding, setAdding] = useState<string | null>(null);
  const [retiring, setRetiring] = useState<api.Clinic | null>(null);

  const ungenerated = clinics.filter((c) => c.future_slots === 0);

  async function generate() {
    setBusy(true);
    try {
      const result = await api.generateSlots(token, { days: 60 });
      onFlash(
        result.created === 0
          ? "Every clinic in the next 60 days already has its slots — nothing to add."
          : `${result.created} bookable slots created for the next 60 days.`,
      );
      onChanged();
    } catch (e) {
      onError(e);
    } finally {
      setBusy(false);
    }
  }

  return (
    <section>
      <h2>The week</h2>
      <p className="muted">
        Each doctor’s recurring clinics. A clinic here is <b>authored</b>; a patient can only be
        booked into inventory that has been <b>generated</b> from it, which is what the button
        below does and what the small print in each block counts. Nothing is ever deleted — a
        clinic that stops is deactivated, and the appointments already in it stand.
      </p>
      {error && <p className="error">{error}</p>}

      {ungenerated.length > 0 && (
        <div className="notice">
          <b>
            {ungenerated.length} clinic{ungenerated.length > 1 ? "s have" : " has"} no bookable
            slots yet.
          </b>{" "}
          The receptionist offers generated inventory, not templates — until you generate, these
          clinics are invisible to every booker.
        </div>
      )}

      <div className="week">
        <div className="week-head">
          <span />
          {DAYS.map((day) => (
            <span key={day}>{day}</span>
          ))}
        </div>
        {doctors.length === 0 && (
          <p className="muted">
            No doctors yet. Create one below, then give them a clinic — or import the whole roster.
          </p>
        )}
        {doctors.map((doctor) => {
          const mine = clinics.filter((c) => c.doctor_id === doctor.doctor_id);
          return (
            <div className="week-row" key={doctor.user_id}>
              <div className="who">
                <b>{doctor.name}</b>
                <span className="muted">{doctor.department_code ?? "—"}</span>
                {mine.length === 0 && <span className="empty-week">no clinic</span>}
              </div>
              {DAYS.map((day, weekday) => (
                <div className="day" key={day}>
                  {mine
                    .filter((c) => c.weekday === weekday)
                    .map((clinic) => (
                      <button
                        key={clinic.template_id}
                        className={`clinic ${clinic.future_slots === 0 ? "unbuilt" : ""}`}
                        onClick={() => setRetiring(clinic)}
                        title={`Next: ${clinic.next_dates.join(", ")}`}
                      >
                        <b>
                          {clinic.start}–{clinic.end}
                        </b>
                        <span>{SLOT_TYPE_LABEL[clinic.slot_type] ?? clinic.slot_type}</span>
                        <span className="counts">
                          {clinic.future_slots === 0
                            ? `${clinic.slots_per_week}/wk · not generated`
                            : `${clinic.future_slots} slots · ${clinic.future_booked} booked`}
                        </span>
                      </button>
                    ))}
                  <button
                    className="add-clinic"
                    onClick={() =>
                      setAdding(adding === `${doctor.doctor_id}:${weekday}` ? null : `${doctor.doctor_id}:${weekday}`)
                    }
                    aria-label={`Add a clinic for ${doctor.name} on ${day}`}
                  >
                    +
                  </button>
                </div>
              ))}
            </div>
          );
        })}
      </div>

      {adding && (
        <ClinicForm
          token={token}
          doctorId={adding.split(":")[0]}
          weekday={Number(adding.split(":")[1])}
          onDone={() => {
            setAdding(null);
            onChanged();
          }}
          onCancel={() => setAdding(null)}
        />
      )}

      {retiring && (
        <RetireClinic
          token={token}
          clinic={retiring}
          onDone={(message) => {
            setRetiring(null);
            onFlash(message);
            onChanged();
          }}
          onCancel={() => setRetiring(null)}
          onError={onError}
        />
      )}

      <div className="row" style={{ marginTop: 14 }}>
        <button className="action" onClick={generate} disabled={busy}>
          {busy ? "Generating…" : "Generate slots for the next 60 days"}
        </button>
        <span className="muted">
          Safe to press twice — an instant that already has a slot is skipped, and no booking is
          ever reset.
        </span>
      </div>
    </section>
  );
}

function ClinicForm({
  token,
  doctorId,
  weekday,
  onDone,
  onCancel,
}: {
  token: string;
  doctorId: string;
  weekday: number;
  onDone: () => void;
  onCancel: () => void;
}) {
  const [form, setForm] = useState({
    start: "10:00",
    end: "13:00",
    slot_type: "follow_up",
    capacity: 2,
    slot_minutes: 15,
  });
  const [busy, setBusy] = useState(false);
  const [refusal, setRefusal] = useState<string | null>(null);

  async function save() {
    setBusy(true);
    setRefusal(null);
    try {
      await api.createClinic(token, { doctor_id: doctorId, weekday, ...form });
      onDone();
    } catch (e) {
      setRefusal(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="set-card">
      <b>New {DAYS[weekday]} clinic</b>
      <div className="cred-fields">
        <label className="field">
          <span>starts</span>
          <input value={form.start} onChange={(e) => setForm({ ...form, start: e.target.value })} />
        </label>
        <label className="field">
          <span>ends</span>
          <input value={form.end} onChange={(e) => setForm({ ...form, end: e.target.value })} />
        </label>
        <label className="field">
          <span>type</span>
          <select
            value={form.slot_type}
            onChange={(e) => setForm({ ...form, slot_type: e.target.value })}
          >
            {Object.entries(SLOT_TYPE_LABEL).map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          <span>minutes per slot</span>
          <input
            type="number"
            value={form.slot_minutes}
            onChange={(e) => setForm({ ...form, slot_minutes: Number(e.target.value) })}
          />
        </label>
        <label className="field">
          <span>seats per slot</span>
          <input
            type="number"
            value={form.capacity}
            onChange={(e) => setForm({ ...form, capacity: Number(e.target.value) })}
          />
        </label>
      </div>
      <p className="muted">
        Seats above one is deliberate and not sloppiness — an Indian government-hospital OPD
        genuinely runs two or three patients per fifteen-minute review. A first consult is not
        shared.
      </p>
      <div className="row">
        <button className="action" onClick={save} disabled={busy}>
          {busy ? "Saving…" : "Add the clinic"}
        </button>
        <button className="ghost" onClick={onCancel}>
          Cancel
        </button>
      </div>
      {refusal && <p className="error">Refused: {refusal}</p>}
    </div>
  );
}

/** Stopping a clinic: the patients in it, by name, before anything happens. */
function RetireClinic({
  token,
  clinic,
  onDone,
  onCancel,
  onError,
}: {
  token: string;
  clinic: api.Clinic;
  onDone: (message: string) => void;
  onCancel: () => void;
  onError: (e: unknown) => void;
}) {
  const impact = useLoad(() => api.fetchClinicImpact(token, clinic.template_id), onError);
  const [busy, setBusy] = useState(false);

  async function retire() {
    setBusy(true);
    try {
      const result = await api.retireClinic(token, clinic.template_id, true);
      onDone(
        result.booked.length === 0
          ? `${clinic.doctor_name}’s ${clinic.weekday_name} clinic is stopped.`
          : `${clinic.doctor_name}’s ${clinic.weekday_name} clinic is stopped. ` +
              `${result.booked.length} patient(s) still hold an appointment in it — ring them.`,
      );
    } catch (e) {
      onError(e);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="set-card">
      <b>
        {clinic.doctor_name} — {clinic.weekday_name} {clinic.start}–{clinic.end}
      </b>{" "}
      <span className="muted">next on {clinic.next_dates.join(", ")}</span>
      {impact.data && (
        <>
          <p className="muted">
            Stopping this clinic blocks its {impact.data.empty_future_slots} empty future slots so
            nobody new can book. It does <b>not</b> cancel anybody.
          </p>
          {impact.data.booked.length > 0 ? (
            <>
              <p className="error">
                {impact.data.booked.length} patient(s) are booked into it. They keep their
                appointment; somebody has to ring them.
              </p>
              <table>
                <thead>
                  <tr>
                    <th>Patient</th>
                    <th>Phone</th>
                    <th>When</th>
                  </tr>
                </thead>
                <tbody>
                  {impact.data.booked.map((b) => (
                    <tr key={b.appointment_id}>
                      <td>{b.patient_name}</td>
                      <td className="muted">{b.patient_phone}</td>
                      <td className="muted">{new Date(b.at).toLocaleString()}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </>
          ) : (
            <p className="muted">Nobody is booked into it.</p>
          )}
        </>
      )}
      <div className="row" style={{ marginTop: 12 }}>
        <button className="action" onClick={retire} disabled={busy || !impact.data}>
          {busy ? "Stopping…" : "Stop this clinic"}
        </button>
        <button className="ghost" onClick={onCancel}>
          Leave it running
        </button>
      </div>
    </div>
  );
}

// -- 2. the import -------------------------------------------------------------

function RosterImport({
  token,
  onApplied,
  onFlash,
  onError,
}: {
  token: string;
  onApplied: () => void;
  onFlash: (s: string) => void;
  onError: (e: unknown) => void;
}) {
  const [file, setFile] = useState<File | null>(null);
  const [plan, setPlan] = useState<api.RosterPlan | null>(null);
  const [busy, setBusy] = useState<"dry" | "apply" | null>(null);
  const [refusal, setRefusal] = useState<string | null>(null);

  async function run(dryRun: boolean) {
    if (!file) return;
    setBusy(dryRun ? "dry" : "apply");
    setRefusal(null);
    try {
      const result = await api.importRoster(token, file, { dryRun, acknowledge: !dryRun });
      setPlan(result.plan);
      if (result.applied) {
        const { created, updated, unchanged, slots_generated, disturbed } = result.applied;
        onFlash(
          `Imported: ${created} new, ${updated} changed, ${unchanged} already right. ` +
            `${slots_generated} bookable slots generated.` +
            (disturbed.length > 0
              ? ` ${disturbed.length} patient(s) were booked into a clinic that moved — ring them.`
              : ""),
        );
        onApplied();
      }
    } catch (e) {
      setRefusal(e instanceof Error ? e.message : String(e));
      onError(e);
    } finally {
      setBusy(null);
    }
  }

  return (
    <section>
      <h2>Import a roster</h2>
      <p className="muted">
        A CSV or XLSX of <code>doctor, weekday, start, end, slot_type, capacity</code> — the file a
        hospital already keeps its roster in. Uploading <b>previews</b>; nothing is written until
        you apply, and an import with any bad row writes <b>nothing at all</b>, because half a
        roster applied cannot be safely re-uploaded.{" "}
        <a href={`${api.API_BASE}/admin/roster/sample.csv`}>Download an example</a>.
      </p>
      <div className="row">
        <input
          type="file"
          accept=".csv,.xlsx,.xlsm,text/csv"
          onChange={(e) => {
            setFile(e.target.files?.[0] ?? null);
            setPlan(null);
          }}
        />
        <button className="ghost" disabled={!file || busy !== null} onClick={() => run(true)}>
          {busy === "dry" ? "Reading…" : "Preview"}
        </button>
        <button
          className="action"
          disabled={!plan?.ok || busy !== null}
          onClick={() => run(false)}
          title={plan && !plan.ok ? "Fix the rows below first" : undefined}
        >
          {busy === "apply" ? "Importing…" : "Apply the roster"}
        </button>
      </div>
      {refusal && <p className="error">Refused: {refusal}</p>}

      {plan && (
        <>
          {plan.ok ? (
            <div className="notice">
              <b>Ready.</b> {plan.counts.create ?? 0} new clinics, {plan.counts.update ?? 0}{" "}
              changed, {plan.counts.unchanged ?? 0} already right. Nothing has been written yet.
            </div>
          ) : (
            <div className="notice bad-notice">
              <b>
                {plan.counts.error} row{plan.counts.error > 1 ? "s" : ""} cannot be imported.
              </b>{" "}
              Fix them in the file and upload it again — nothing has been written.
            </div>
          )}
          <table>
            <thead>
              <tr>
                <th className="num">Row</th>
                <th>Doctor</th>
                <th>Clinic</th>
                <th className="num">Slots/wk</th>
                <th>What happens</th>
              </tr>
            </thead>
            <tbody>
              {plan.rows.map((row) => (
                <tr key={row.line} className={row.action === "error" ? "bad-row" : ""}>
                  <td className="num">{row.line}</td>
                  <td>
                    {row.doctor_name ?? <span className="muted">{row.doctor_label}</span>}
                    {row.department_code && <div className="muted">{row.department_code}</div>}
                  </td>
                  <td className="muted">
                    {row.action === "error"
                      ? "—"
                      : `${row.weekday_name} ${row.start}–${row.end}, ${
                          SLOT_TYPE_LABEL[row.slot_type] ?? row.slot_type
                        }, ${row.capacity} seat(s)`}
                  </td>
                  <td className="num">{row.action === "error" ? "—" : row.slots_per_week}</td>
                  <td>
                    {row.action === "error" ? (
                      <span className="error">{row.error}</span>
                    ) : (
                      <span className={`pill ${row.action === "unchanged" ? "draft" : "published"}`}>
                        {row.action}
                      </span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </section>
  );
}

// -- 3. the people -------------------------------------------------------------

function People({
  token,
  people,
  departments,
  error,
  onChanged,
  onFlash,
  onError,
}: {
  token: string;
  people: api.Person[];
  departments: api.Department[];
  error: string | null;
  onChanged: () => void;
  onFlash: (s: string) => void;
  onError: (e: unknown) => void;
}) {
  const [adding, setAdding] = useState<"doctor" | "staff" | null>(null);
  const [leaving, setLeaving] = useState<api.Person | null>(null);

  async function act(person: api.Person, what: "invite" | "activate") {
    try {
      if (what === "invite") {
        const result = await api.invitePerson(token, person.user_id);
        onFlash(
          result.sent
            ? `Told ${person.name} they can sign in with ${result.to}.`
            : `Could not text ${result.to}: ${result.detail}`,
        );
      } else {
        await api.activatePerson(token, person.user_id);
        onFlash(`${person.name} can sign in again. Their clinics do not come back with them.`);
        onChanged();
      }
    } catch (e) {
      onError(e);
    }
  }

  return (
    <section>
      <h2>People</h2>
      <p className="muted">
        Everybody who can sign in. There is no password anywhere in this system — signing in is a
        one-time code to the number on this row, so <b>inviting somebody is telling them so</b>,
        not minting a credential. Getting the number wrong is the one mistake that creates an
        account nobody can use.
      </p>
      {error && <p className="error">{error}</p>}
      <table>
        <thead>
          <tr>
            <th>Name</th>
            <th>Role</th>
            <th>Phone</th>
            <th>Department</th>
            <th className="num">Clinics</th>
            <th className="num">Booked</th>
            <th>Last signed in</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {people.map((person) => (
            <tr key={person.user_id} className={person.active ? "" : "inactive"}>
              <td>
                <b>{person.name}</b>
                {person.reg_no && <div className="muted">{person.reg_no}</div>}
              </td>
              <td>
                <span className={`pill ${person.active ? "published" : "draft"}`}>
                  {ROLE_LABEL[person.role] ?? person.role}
                  {person.active ? "" : " · off"}
                </span>
              </td>
              <td className="muted">{person.phone}</td>
              <td className="muted">{person.department_name ?? "—"}</td>
              <td className="num">{person.doctor_id ? person.clinics : "—"}</td>
              <td className="num">{person.doctor_id ? person.upcoming_appointments : "—"}</td>
              <td className="muted">
                {person.last_login_at ? (
                  new Date(person.last_login_at).toLocaleDateString()
                ) : (
                  <i>never</i>
                )}
              </td>
              <td className="num">
                {person.active ? (
                  <>
                    <button className="ghost" onClick={() => act(person, "invite")}>
                      Invite
                    </button>{" "}
                    <button className="ghost" onClick={() => setLeaving(person)}>
                      Deactivate
                    </button>
                  </>
                ) : (
                  <button className="ghost" onClick={() => act(person, "activate")}>
                    Reactivate
                  </button>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      <div className="row" style={{ marginTop: 12 }}>
        <button className="ghost" onClick={() => setAdding(adding === "doctor" ? null : "doctor")}>
          {adding === "doctor" ? "Close" : "Add a doctor"}
        </button>
        <button className="ghost" onClick={() => setAdding(adding === "staff" ? null : "staff")}>
          {adding === "staff" ? "Close" : "Add other staff"}
        </button>
      </div>

      {adding === "doctor" && (
        <NewDoctor
          token={token}
          departments={departments}
          onDone={(name) => {
            setAdding(null);
            onFlash(`${name} can sign in now. Give them a clinic in the week above.`);
            onChanged();
          }}
        />
      )}
      {adding === "staff" && (
        <NewStaff
          token={token}
          onDone={(name) => {
            setAdding(null);
            onFlash(`${name} can sign in now.`);
            onChanged();
          }}
        />
      )}

      {leaving && (
        <Deactivate
          token={token}
          person={leaving}
          onDone={(message) => {
            setLeaving(null);
            onFlash(message);
            onChanged();
          }}
          onCancel={() => setLeaving(null)}
          onError={onError}
        />
      )}
    </section>
  );
}

function NewDoctor({
  token,
  departments,
  onDone,
}: {
  token: string;
  departments: api.Department[];
  onDone: (name: string) => void;
}) {
  const [form, setForm] = useState({
    name: "",
    phone: "",
    department_code: departments[0]?.code ?? "",
    reg_no: "",
    qualification: "",
  });
  const [busy, setBusy] = useState(false);
  const [refusal, setRefusal] = useState<string | null>(null);

  async function save() {
    setBusy(true);
    setRefusal(null);
    try {
      await api.createDoctor(token, form);
      onDone(form.name);
    } catch (e) {
      setRefusal(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="set-card">
      <b>New doctor</b>
      <div className="cred-fields">
        <label className="field">
          <span>name</span>
          <input
            value={form.name}
            placeholder="Dr. Meera Joshi"
            onChange={(e) => setForm({ ...form, name: e.target.value })}
          />
        </label>
        <label className="field">
          <span>phone (this is how they sign in)</span>
          <input
            value={form.phone}
            placeholder="98765 43210"
            onChange={(e) => setForm({ ...form, phone: e.target.value })}
          />
        </label>
        <label className="field">
          <span>department</span>
          <select
            value={form.department_code}
            onChange={(e) => setForm({ ...form, department_code: e.target.value })}
          >
            {departments.map((d) => (
              <option key={d.code} value={d.code}>
                {d.name}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          <span>registration number</span>
          <input
            value={form.reg_no}
            placeholder="RMC-ONC-2001"
            onChange={(e) => setForm({ ...form, reg_no: e.target.value })}
          />
        </label>
        <label className="field">
          <span>qualification</span>
          <input
            value={form.qualification}
            placeholder="MD, DM (Medical Oncology)"
            onChange={(e) => setForm({ ...form, qualification: e.target.value })}
          />
        </label>
      </div>
      <div className="row">
        <button
          className="action"
          onClick={save}
          disabled={busy || !form.name || !form.phone || !form.reg_no}
        >
          {busy ? "Creating…" : "Create the doctor"}
        </button>
      </div>
      {refusal && <p className="error">Refused: {refusal}</p>}
    </div>
  );
}

function NewStaff({ token, onDone }: { token: string; onDone: (name: string) => void }) {
  const [form, setForm] = useState({ name: "", phone: "", role: "coordinator" });
  const [busy, setBusy] = useState(false);
  const [refusal, setRefusal] = useState<string | null>(null);

  async function save() {
    setBusy(true);
    setRefusal(null);
    try {
      await api.createStaff(token, form);
      onDone(form.name);
    } catch (e) {
      setRefusal(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="set-card">
      <b>New staff account</b>
      <div className="cred-fields">
        <label className="field">
          <span>name</span>
          <input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
        </label>
        <label className="field">
          <span>phone (this is how they sign in)</span>
          <input value={form.phone} onChange={(e) => setForm({ ...form, phone: e.target.value })} />
        </label>
        <label className="field">
          <span>role</span>
          <select value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value })}>
            <option value="coordinator">Coordinator</option>
            <option value="nurse">Nurse</option>
            <option value="admin">Admin</option>
          </select>
        </label>
      </div>
      <p className="muted">
        Patients and caregivers are not created here — a patient comes from registration and a
        caregiver from a consented grant she gave.
      </p>
      <div className="row">
        <button className="action" onClick={save} disabled={busy || !form.name || !form.phone}>
          {busy ? "Creating…" : "Create the account"}
        </button>
      </div>
      {refusal && <p className="error">Refused: {refusal}</p>}
    </div>
  );
}

/** Two steps, always: the patients first, the switch second. */
function Deactivate({
  token,
  person,
  onDone,
  onCancel,
  onError,
}: {
  token: string;
  person: api.Person;
  onDone: (message: string) => void;
  onCancel: () => void;
  onError: (e: unknown) => void;
}) {
  const impact = useLoad(() => api.fetchDeactivationImpact(token, person.user_id), onError);
  const [busy, setBusy] = useState(false);

  async function deactivate() {
    setBusy(true);
    try {
      const result = await api.deactivatePerson(token, person.user_id, true);
      onDone(
        `${person.name} can no longer sign in. ` +
          (result.clinics_retired > 0
            ? `${result.clinics_retired} clinic(s) stopped and ${result.slots_blocked} empty slots closed. `
            : "") +
          (result.appointments_left.length > 0
            ? `${result.appointments_left.length} patient(s) still hold an appointment with them — ring those patients.`
            : ""),
      );
    } catch (e) {
      onError(e);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="set-card">
      <b>Deactivate {person.name}?</b>
      {impact.error && <p className="error">{impact.error}</p>}
      {impact.data && (
        <>
          <p className="muted">
            They lose the ability to sign in.{" "}
            {impact.data.is_doctor
              ? `Their ${impact.data.active_clinics} clinic(s) stop and their ` +
                `${impact.data.open_future_slots} open future slots close, so nobody new can book them.`
              : "They hold no clinic."}{" "}
            Nothing is deleted, and a deactivation can be undone.
          </p>
          {impact.data.booked.length > 0 ? (
            <>
              <p className="error">
                <b>
                  {impact.data.booked.length} patient(s) already have an appointment with them.
                </b>{" "}
                Those appointments are <b>not</b> cancelled — deciding what happens to each of
                these people is yours, not this screen’s.
              </p>
              <table>
                <thead>
                  <tr>
                    <th>Patient</th>
                    <th>Phone</th>
                    <th>When</th>
                    <th>For</th>
                  </tr>
                </thead>
                <tbody>
                  {impact.data.booked.map((b) => (
                    <tr key={b.appointment_id}>
                      <td>{b.patient_name}</td>
                      <td className="muted">{b.patient_phone}</td>
                      <td className="muted">{new Date(b.at).toLocaleString()}</td>
                      <td className="muted">
                        {b.slot_type ? SLOT_TYPE_LABEL[b.slot_type] ?? b.slot_type : "—"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </>
          ) : (
            <p className="muted">Nobody is booked with them.</p>
          )}
        </>
      )}
      <div className="row" style={{ marginTop: 12 }}>
        <button className="action" onClick={deactivate} disabled={busy || !impact.data}>
          {busy
            ? "Deactivating…"
            : impact.data?.needs_a_decision
              ? `Deactivate anyway — we will ring those ${impact.data.booked.length} patients`
              : "Deactivate"}
        </button>
        <button className="ghost" onClick={onCancel}>
          Cancel
        </button>
      </div>
    </div>
  );
}
