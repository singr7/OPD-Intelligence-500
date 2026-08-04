"use client";

// Assigning a queued visit from the desk (AR3, plan §1.3 / §3).
//
// This is the same three verbs as the kiosk's staff strip — link the identity,
// set the department, set the doctor — reached from the coordinator's own
// screen instead of the terminal. It exists because the strip does not settle
// every arrival: a `Skip` is a legal outcome, and an **offline** kiosk cannot
// assign at all, so those visits sync into the department pool with nobody's
// name on them. Without this panel the only way out of that state would be for
// the patient to walk back to the kiosk.
//
// It opens in place, under the row it belongs to. Not a modal: a coordinator
// assigning a patient is looking at the queue *around* them — who is already
// with which doctor, who is next — and a dialog that hides the queue to ask
// about one row takes away the context the decision is made from.

import { useCallback, useEffect, useState } from "react";
import {
  AssignableDoctor,
  AssignEntryResult,
  ConsoleEntry,
  assignEntry,
  fetchAssignable,
} from "@/app/_lib/queue";

export function AssignPanel({
  entry,
  token,
  departments,
  departmentKey,
  onClose,
  onDone,
}: {
  entry: ConsoleEntry;
  token: string;
  /** Every active department — a wrongly-routed visit has to be movable into a
   *  department that has nobody queued in it yet. */
  departments: { key: string; name: string }[];
  departmentKey: string;
  onClose: () => void;
  onDone: () => Promise<void> | void;
}) {
  const [doctors, setDoctors] = useState<AssignableDoctor[] | null>(null);
  const [doctorId, setDoctorId] = useState<string | null>(entry.assigned_doctor_id);
  const [deptKey, setDeptKey] = useState(departmentKey);
  const [link, setLink] = useState<boolean | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [reissued, setReissued] = useState<AssignEntryResult | null>(null);

  const load = useCallback(async () => {
    try {
      setDoctors(await fetchAssignable(token, entry.id));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not load the roster");
    }
  }, [token, entry.id]);

  useEffect(() => {
    void load();
  }, [load]);

  const save = async (doctor: string | null) => {
    setBusy(true);
    setError(null);
    try {
      const result = await assignEntry(token, entry.id, {
        link_candidate: link,
        department_key: deptKey === departmentKey ? null : deptKey,
        doctor_id: doctor,
      });
      await onDone();
      if (result.token_reissued) {
        // The department moved, so the token was reissued in the new series.
        // The patient is holding a slip with the old number: the panel stays
        // open on this, because it is now a message for a human to deliver.
        setReissued(result);
        return;
      }
      onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Something went wrong");
    } finally {
      setBusy(false);
    }
  };

  if (reissued) {
    return (
      <div className="assign reissued" role="alert" data-testid="assign-reissued">
        <div className="reissue-copy">
          <strong>Token reissued — tell the patient</strong>
          <span>
            {reissued.department_name} · their printed slip says{" "}
            {reissued.previous_token_no ?? "—"}, which is no longer valid.
          </span>
        </div>
        <span className="reissue-token" data-testid="assign-new-token">
          {reissued.token_no ?? "—"}
        </span>
        <button className="act primary" onClick={onClose} data-testid="assign-reissue-ack">
          I&rsquo;ve told them
        </button>
      </div>
    );
  }

  return (
    <div className="assign" data-testid="assign-panel">
      {entry.link_state === "candidate" && (
        // The console never shows *who* the candidate is — that lives on the
        // patient card behind the doctor's auth, and the coordinator resolving a
        // queue does not need a second patient's demographics on screen. What
        // they need to know is that a human has not ruled on it yet.
        <div className="assign-link" data-testid="assign-link">
          <span>A possible existing file was matched at the kiosk.</span>
          <div className="assign-link-btns">
            <button
              className={link === true ? "act primary" : "act ghost"}
              aria-pressed={link === true}
              onClick={() => setLink(link === true ? null : true)}
              data-testid="assign-link-yes"
            >
              Same person — link
            </button>
            <button
              className={link === false ? "act primary" : "act ghost"}
              aria-pressed={link === false}
              onClick={() => setLink(link === false ? null : false)}
              data-testid="assign-link-no"
            >
              Not the same person
            </button>
          </div>
        </div>
      )}

      <div className="assign-row">
        <label>
          Department
          <select
            value={deptKey}
            disabled={busy}
            onChange={(e) => {
              setDeptKey(e.target.value);
              // A different department has a different roster; keeping the old
              // doctor would assign across departments, which the server
              // refuses and which would drop the patient off both worklists.
              setDoctorId(null);
            }}
            data-testid="assign-department"
          >
            {departments.map((d) => (
              <option key={d.key} value={d.key}>
                {d.name}
              </option>
            ))}
          </select>
        </label>
        <label>
          Doctor
          <select
            value={doctorId ?? ""}
            // The roster we loaded belongs to the *current* department. Once the
            // department is changed this list is stale, and offering it would be
            // offering doctors who do not work there.
            disabled={busy || deptKey !== departmentKey || doctors === null}
            onChange={(e) => setDoctorId(e.target.value || null)}
            data-testid="assign-doctor"
          >
            <option value="">Nobody yet — leave in the department pool</option>
            {(doctors ?? []).map((d) => (
              <option key={d.id} value={d.id}>
                {d.name}
                {d.qualification ? ` · ${d.qualification}` : ""}
                {d.on_duty ? " · on duty today" : " · not rostered today"}
              </option>
            ))}
          </select>
        </label>
      </div>

      {deptKey !== departmentKey && (
        <p className="assign-warn" data-testid="assign-reissue-warning">
          Moving departments reissues the token in the new series. The patient is
          holding a slip with the old number — you will have to hand them the new one.
        </p>
      )}

      {doctors !== null && doctors.length === 0 && deptKey === departmentKey && (
        <p className="assign-warn">No doctors are on record for this department.</p>
      )}

      {error && <p className="assign-err">{error}</p>}

      <div className="assign-actions">
        <button className="act ghost" onClick={onClose} disabled={busy}>
          Cancel
        </button>
        <button
          className="act primary"
          disabled={busy}
          onClick={() => save(doctorId)}
          data-testid="assign-save"
        >
          {busy ? "Saving…" : "Save"}
        </button>
      </div>
    </div>
  );
}
