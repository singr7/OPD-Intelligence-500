"use client";

// Hospital and departments (AYUR-1, doc 24 §7).
//
// **The screen's single job: say what this hospital is called and which of its
// departments a patient can actually reach today — and let both be changed
// safely.** Until this tab, both facts were only editable by editing
// `seeds/hospital.json` on the box and re-running the seed, which is not
// something a hospital administrator can do.
//
// Three things, in this order:
//
// 1. **The letterhead.** The hospital's name is shown as the top of the page it
//    is *printed on*, not as a form field labelled "Name" — because renaming
//    this facility renames a prescription and a boarding pass, and that is the
//    consequence the operator should be looking at while they type. This is the
//    tab's one deliberate risk (doc 04 §5); everything below it stays quiet.
// 2. **The departments, as doors.** Open or closed is the first column, because
//    it is the only one that changes what a patient standing at the kiosk sees.
//    A department that cannot be opened says why on its own row rather than
//    failing when the toggle is pressed.
// 3. **The system of medicine**, changed only against a list of consequences the
//    server derives from the capability mapping. This console never composes a
//    sentence about ayurveda: a third system of medicine arrives with its own
//    capabilities row and this file does not change (doc 24 §2).

import { useState } from "react";
import type { TabProps } from "./Console";
import { useLoad } from "../_lib/useLoad";
import * as api from "../_lib/api";

const SYSTEMS: { value: string; label: string }[] = [
  { value: "allopathy", label: "Allopathy" },
  { value: "ayurveda", label: "Ayurveda" },
];

/** The stored value as a person reads it. Falls through to the raw string, so a
 *  system of medicine added on the server shows up here as itself rather than
 *  disappearing behind a label this file forgot to add. */
function systemLabel(value: string): string {
  return SYSTEMS.find((s) => s.value === value)?.label ?? value;
}

const LANGS = [
  { value: "hi", label: "Hindi" },
  { value: "en", label: "English" },
  { value: "mr", label: "Marathi" },
  { value: "te", label: "Telugu" },
];

/** The languages the hospital's *name* can be translated into, mirroring the
 *  backend's `TRANSLATABLE_LANGUAGES`. English is absent because it is the name
 *  itself. Hindi only for now — Marathi and Telugu fall back to English rather
 *  than carry a guess at a facility's own name, which is the first line of the
 *  kiosk and the top band of the pass a patient carries out. Widening this is
 *  one entry here and one in `app/facility.py`. */
const TRANSLATED_LANGS = [{ value: "hi", label: "हिंदी", english: "Hindi" }];

export function FacilityTab({ token, onError }: TabProps) {
  const facility = useLoad(() => api.fetchFacility(token), onError);
  const [flash, setFlash] = useState<string | null>(null);

  return (
    <>
      {flash && (
        <div className="notice flash" role="status">
          {flash}
        </div>
      )}

      {facility.error && <p className="error">{facility.error}</p>}

      {facility.data && (
        <>
          <Identity
            token={token}
            hospital={facility.data.hospital}
            onChanged={facility.reload}
            onFlash={setFlash}
            onError={onError}
          />
          <Departments
            token={token}
            departments={facility.data.departments}
            onChanged={facility.reload}
            onFlash={setFlash}
            onError={onError}
          />
        </>
      )}
    </>
  );
}

// -- 1. the letterhead ---------------------------------------------------------

function Identity({
  token,
  hospital,
  onChanged,
  onFlash,
  onError,
}: {
  token: string;
  hospital: api.Hospital;
  onChanged: () => void;
  onFlash: (s: string) => void;
  onError: (e: unknown) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState(hospital.name);
  const [translated, setTranslated] = useState<Record<string, string>>(hospital.name_i18n ?? {});
  const [city, setCity] = useState(hospital.city ?? "");
  const [district, setDistrict] = useState(hospital.district ?? "");
  const [lang, setLang] = useState(hospital.default_lang);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  /** Which language the letterhead is previewing. The card is the point of this
   *  screen, so the way to check a translation is to *see the page in it* —
   *  not to read it back out of the field you just typed it into. */
  const [preview, setPreview] = useState("en");

  const editedNames: Record<string, string> = { en: name, ...translated };
  const storedNames: Record<string, string> = { en: hospital.name, ...hospital.name_i18n };
  // While editing, the letterhead previews what is being typed — the point of
  // drawing it this way is that the operator watches the printed page change.
  const shown = (editing ? editedNames : storedNames)[preview] || (editing ? name : hospital.name);
  const shownAddress = [editing ? city : hospital.city, editing ? district : hospital.district]
    .map((part) => (part ?? "").trim())
    .filter(Boolean)
    .join(" · ");

  async function save() {
    setBusy(true);
    setError(null);
    try {
      const updated = await api.patchHospital(token, {
        name,
        name_i18n: translated,
        city,
        district,
        default_lang: lang,
      });
      onFlash(
        `This facility is now “${updated.name}”. Prescriptions printed from here, ` +
          "and every intake pass the kiosk hands out, carry the new name from now on — " +
          "in the language the patient chose, where one has been given. " +
          "Paper already printed is unchanged.",
      );
      setEditing(false);
      onChanged();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not save");
      onError(e);
    } finally {
      setBusy(false);
    }
  }

  function cancel() {
    setName(hospital.name);
    setTranslated(hospital.name_i18n ?? {});
    setCity(hospital.city ?? "");
    setDistrict(hospital.district ?? "");
    setLang(hospital.default_lang);
    setError(null);
    setEditing(false);
  }

  return (
    <section>
      <h2>This hospital</h2>
      <p className="muted">
        The name below is printed on the patient&rsquo;s copy of every prescription and on the
        intake pass the kiosk hands them at the door, in the language they chose. A language
        with no name of its own falls back to English — the real name of a real place, which
        beats a guess at it.
      </p>

      <div className="letterhead" data-testid="letterhead">
        <div className="facility" lang={preview}>
          {shown || "—"}
        </div>
        {shownAddress && <div className="address">{shownAddress}</div>}
        <div className="rule" />
        <div className="caption">as it prints</div>
        {/* The check that matters is seeing the page in the other language, not
            re-reading the field you just typed into. */}
        <div className="langs" style={{ marginTop: 12, display: "inline-flex" }}>
          {[{ value: "en", label: "English" }, ...TRANSLATED_LANGS].map((l) => (
            <button
              key={l.value}
              className={l.value === preview ? "on" : ""}
              onClick={() => setPreview(l.value)}
              data-testid={`preview-${l.value}`}
            >
              {l.label}
            </button>
          ))}
        </div>
      </div>

      {editing ? (
        <div className="set-card">
          {error && <p className="error">{error}</p>}
          <div className="row">
            <label className="field" style={{ flex: "2 1 260px" }}>
              <span>Name</span>
              <input
                value={name}
                onChange={(e) => setName(e.target.value)}
                data-testid="hospital-name"
              />
            </label>
            {TRANSLATED_LANGS.map((l) => (
              <label key={l.value} className="field" style={{ flex: "2 1 260px" }}>
                <span>
                  Name in {l.english} ({l.label})
                </span>
                <input
                  lang={l.value}
                  value={translated[l.value] ?? ""}
                  placeholder="leave empty to show the English name"
                  onChange={(e) =>
                    setTranslated((prev) => ({ ...prev, [l.value]: e.target.value }))
                  }
                  data-testid={`hospital-name-${l.value}`}
                />
              </label>
            ))}
            <label className="field" style={{ flex: "1 1 140px" }}>
              <span>City</span>
              <input value={city} onChange={(e) => setCity(e.target.value)} />
            </label>
            <label className="field" style={{ flex: "1 1 140px" }}>
              <span>District</span>
              <input value={district} onChange={(e) => setDistrict(e.target.value)} />
            </label>
            <label className="field">
              <span>Default language</span>
              <select value={lang} onChange={(e) => setLang(e.target.value)}>
                {LANGS.map((l) => (
                  <option key={l.value} value={l.value}>
                    {l.label}
                  </option>
                ))}
              </select>
            </label>
          </div>
          <div className="row" style={{ marginBottom: 0 }}>
            <button
              className="action"
              onClick={save}
              disabled={busy || !name.trim()}
              data-testid="save-hospital"
            >
              {busy ? "Saving…" : "Save"}
            </button>
            <button className="ghost" onClick={cancel} disabled={busy}>
              Cancel
            </button>
          </div>
        </div>
      ) : (
        <div className="row">
          <button className="ghost" onClick={() => setEditing(true)} data-testid="edit-hospital">
            Edit this hospital
          </button>
          <span className="muted">
            Code <code>{hospital.code}</code> — the key seeds and backups use. Not editable here.
          </span>
        </div>
      )}
    </section>
  );
}

// -- 2. the departments --------------------------------------------------------

function Departments({
  token,
  departments,
  onChanged,
  onFlash,
  onError,
}: {
  token: string;
  departments: api.DepartmentRow[];
  onChanged: () => void;
  onFlash: (s: string) => void;
  onError: (e: unknown) => void;
}) {
  const [editing, setEditing] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function toggle(dept: api.DepartmentRow) {
    setBusy(true);
    setError(null);
    try {
      const updated = await api.patchDepartment(token, dept.code, { active: !dept.active });
      onFlash(
        updated.active
          ? `${updated.name} is open — it now appears on the kiosk chooser and walk-ins can be routed to it.`
          : `${updated.name} is closed. Patients can no longer choose it; nothing already booked or waiting is touched.`,
      );
      onChanged();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not change that department");
      onError(e);
    } finally {
      setBusy(false);
    }
  }

  return (
    <section>
      <h2>Departments</h2>
      <p className="muted">
        A department is offered on the kiosk the moment it is open. One with no intake tree
        cannot be opened — there would be nothing to ask the patient who chose it. A tree comes
        either from the editor (published) or from the tree bank this box shipped with.
      </p>
      {error && <p className="error">{error}</p>}

      <table>
        <thead>
          <tr>
            <th>Department</th>
            <th>System of medicine</th>
            <th className="num">Doctors</th>
            <th>Intake</th>
            <th>Patients can reach it</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {departments.map((dept) => (
            <tr key={dept.code} className={dept.active ? "" : "closed"}>
              <td>
                <span className="dept-name">
                  <span>{dept.name}</span>
                  <code>{dept.code}</code>
                </span>
              </td>
              <td>{systemLabel(dept.care_system)}</td>
              <td className="num">{dept.doctors}</td>
              <td>{intakeSource(dept)}</td>
              <td>
                {dept.active ? (
                  <span className="pill ok">open</span>
                ) : dept.has_intake ? (
                  <span className="pill draft">closed</span>
                ) : (
                  <>
                    <span className="pill draft">closed</span>{" "}
                    <span className="muted">no intake tree yet</span>
                  </>
                )}
              </td>
              <td>
                <div className="row" style={{ margin: 0, gap: 6 }}>
                  <button
                    className="ghost"
                    onClick={() => setEditing(editing === dept.code ? null : dept.code)}
                    data-testid={`edit-${dept.code}`}
                  >
                    Edit
                  </button>
                  <button
                    className="ghost"
                    onClick={() => toggle(dept)}
                    disabled={busy || (!dept.active && !dept.has_intake)}
                    title={
                      !dept.active && !dept.has_intake
                        ? "Publish an intake tree for this department first"
                        : undefined
                    }
                    data-testid={`toggle-${dept.code}`}
                  >
                    {dept.active ? "Close" : "Open"}
                  </button>
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      {editing &&
        (() => {
          const dept = departments.find((d) => d.code === editing);
          return dept ? (
            <EditDepartment
              key={dept.code}
              token={token}
              dept={dept}
              onDone={(message) => {
                onFlash(message);
                setEditing(null);
                onChanged();
              }}
              onCancel={() => setEditing(null)}
              onError={onError}
            />
          ) : null;
        })()}

      {adding ? (
        <AddDepartment
          token={token}
          onDone={(message) => {
            onFlash(message);
            setAdding(false);
            onChanged();
          }}
          onCancel={() => setAdding(false)}
          onError={onError}
        />
      ) : (
        <div className="row" style={{ marginTop: 14 }}>
          <button className="ghost" onClick={() => setAdding(true)} data-testid="add-department">
            Add a department
          </button>
        </div>
      )}
    </section>
  );
}

// -- 3. editing one, including its system of medicine --------------------------

/** Two steps whenever the system of medicine changes: the consequences first,
 *  the switch second — the same shape as deactivating a member of staff. */
function EditDepartment({
  token,
  dept,
  onDone,
  onCancel,
  onError,
}: {
  token: string;
  dept: api.DepartmentRow;
  onDone: (message: string) => void;
  onCancel: () => void;
  onError: (e: unknown) => void;
}) {
  const [name, setName] = useState(dept.name);
  const [icon, setIcon] = useState(dept.icon ?? "");
  const [system, setSystem] = useState(dept.care_system);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const switching = system !== dept.care_system;
  // Only asked for once the operator has actually picked a different system, so
  // the ordinary rename does not pay for a read it does not need.
  const impact = useLoad(
    () =>
      switching
        ? api.fetchCareSystemImpact(token, dept.code, system)
        : Promise.resolve(null as api.CareSystemImpact | null),
    onError,
    [switching, system],
  );

  async function save() {
    setBusy(true);
    setError(null);
    try {
      const updated = await api.patchDepartment(token, dept.code, {
        name,
        icon,
        care_system: system,
        acknowledge: switching,
      });
      onDone(
        switching
          ? `${updated.name} now practises ${systemLabel(updated.care_system)}. Its doctors see the ` +
            "change at their next sign-in; nothing already written has been reclassified."
          : `${updated.name} updated.`,
      );
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not save");
      onError(e);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="set-card" data-testid="edit-department">
      <b>
        {dept.name} <code>{dept.code}</code>
      </b>
      {error && <p className="error">{error}</p>}
      <div className="row" style={{ marginTop: 10 }}>
        <label className="field" style={{ flex: "2 1 220px" }}>
          <span>Name</span>
          <input value={name} onChange={(e) => setName(e.target.value)} />
        </label>
        <label className="field" style={{ flex: "1 1 140px" }}>
          <span>Icon</span>
          <input
            value={icon}
            onChange={(e) => setIcon(e.target.value)}
            placeholder="stethoscope"
          />
        </label>
        <label className="field">
          <span>System of medicine</span>
          <select
            value={system}
            onChange={(e) => setSystem(e.target.value)}
            data-testid="care-system"
          >
            {SYSTEMS.map((s) => (
              <option key={s.value} value={s.value}>
                {s.label}
              </option>
            ))}
          </select>
        </label>
      </div>

      {switching && (
        <div className="notice" data-testid="care-system-impact">
          <b>
            Changing {dept.name} from {systemLabel(dept.care_system)} to {systemLabel(system)}{" "}
            changes what it offers patients.
          </b>
          {impact.error && <p className="error">{impact.error}</p>}
          {impact.data && (
            <>
              <ul className="consequences">
                {impact.data.changes.map((change) => (
                  <li key={change.flag}>
                    <span className={`mark ${markClass(change)}`}>{mark(change)}</span>
                    <span>
                      {change.label}
                      {change.before !== "True" && change.before !== "False" && (
                        <span className="detail">
                          {" "}
                          — {change.before} becomes {change.after}
                        </span>
                      )}
                    </span>
                  </li>
                ))}
              </ul>
              <p className="muted" style={{ marginTop: 10 }}>
                {impact.data.doctors === 0
                  ? "No doctor works in this department yet."
                  : `${impact.data.doctors} doctor(s) work here — their console gains and loses these sections at their next sign-in.`}{" "}
                {`Its intake questions are unchanged by this — they keep asking what they ask, and they were written for ${systemLabel(dept.care_system)}.`}{" "}
                Notes, prescriptions and visits already recorded are not reclassified.
              </p>
            </>
          )}
        </div>
      )}

      <div className="row" style={{ marginTop: 12, marginBottom: 0 }}>
        <button
          className="action"
          onClick={save}
          disabled={busy || !name.trim() || (switching && !impact.data)}
          data-testid="save-department"
        >
          {busy
            ? "Saving…"
            : switching
              ? `Yes — ${dept.name} practises ${systemLabel(system)}`
              : "Save"}
        </button>
        <button className="ghost" onClick={onCancel} disabled={busy}>
          Cancel
        </button>
      </div>
    </div>
  );
}

/** Where this department's questions come from.
 *
 *  Not a bare count of published rows. Nine of the ten seeded departments have
 *  no published tree and are open anyway, because `resolve_tree` falls through
 *  to the bank in `seeds/trees/` — so a column reading "0" beside "open" would
 *  contradict the sentence above the table and describe an impossible state. It
 *  reports what the activation guard actually checks. */
function intakeSource(dept: api.DepartmentRow) {
  if (dept.published_trees > 0) {
    return `${dept.published_trees} published`;
  }
  if (dept.has_intake) {
    return <span className="muted">from the tree bank</span>;
  }
  return <span className="muted">none yet</span>;
}

/** A flag that switches on, off, or swaps one named value for another. */
function mark(change: api.CapabilityChange): string {
  if (change.after === "True") return "+";
  if (change.after === "False") return "−";
  return "→";
}

function markClass(change: api.CapabilityChange): string {
  if (change.after === "True") return "on";
  if (change.after === "False") return "off";
  return "swap";
}

function AddDepartment({
  token,
  onDone,
  onCancel,
  onError,
}: {
  token: string;
  onDone: (message: string) => void;
  onCancel: () => void;
  onError: (e: unknown) => void;
}) {
  const [code, setCode] = useState("");
  const [name, setName] = useState("");
  const [icon, setIcon] = useState("");
  const [system, setSystem] = useState("allopathy");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function create() {
    setBusy(true);
    setError(null);
    try {
      const created = await api.createDepartment(token, {
        code,
        name,
        icon,
        care_system: system,
      });
      onDone(
        `${created.name} (${created.code}) exists and is closed. Publish an intake tree for it, ` +
          "then open it — a department with nothing to ask cannot be offered to patients.",
      );
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not create that department");
      onError(e);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="set-card" data-testid="add-department-form">
      <b>A new department</b>
      <p className="muted">
        It is created closed. A department created a second ago has no intake tree, so opening
        it would put a patient in front of an error rather than a question.
      </p>
      {error && <p className="error">{error}</p>}
      <div className="row">
        <label className="field" style={{ flex: "1 1 130px" }}>
          <span>Code</span>
          <input
            value={code}
            onChange={(e) => setCode(e.target.value.toUpperCase())}
            placeholder="AYUR"
            data-testid="new-department-code"
          />
        </label>
        <label className="field" style={{ flex: "2 1 220px" }}>
          <span>Name</span>
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Ayurveda"
            data-testid="new-department-name"
          />
        </label>
        <label className="field" style={{ flex: "1 1 140px" }}>
          <span>Icon</span>
          <input value={icon} onChange={(e) => setIcon(e.target.value)} placeholder="leaf" />
        </label>
        <label className="field">
          <span>System of medicine</span>
          <select
            value={system}
            onChange={(e) => setSystem(e.target.value)}
            data-testid="new-department-system"
          >
            {SYSTEMS.map((s) => (
              <option key={s.value} value={s.value}>
                {s.label}
              </option>
            ))}
          </select>
        </label>
      </div>
      <div className="row" style={{ marginBottom: 0 }}>
        <button
          className="action"
          onClick={create}
          disabled={busy || !code.trim() || !name.trim()}
          data-testid="create-department"
        >
          {busy ? "Creating…" : "Create it closed"}
        </button>
        <button className="ghost" onClick={onCancel} disabled={busy}>
          Cancel
        </button>
      </div>
    </div>
  );
}
