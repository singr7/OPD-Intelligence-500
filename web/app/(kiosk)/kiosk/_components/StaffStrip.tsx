"use client";

// The coordinator's strip on the kiosk's token screen (AR3, plan §1.2).
//
// One kiosk, one coordinator standing at it. Two questions get settled here in
// one action: **is this the returning patient the arrival screen matched**, and
// **which doctor is going to see them**. Both are questions the kiosk cannot
// answer and a human standing there can.
//
// The rules this component exists to keep, in the order they matter:
//
//   1. **Locked is the resting state.** An idle public terminal shows an
//      `Unlock` affordance and nothing else. The candidate — a real patient's
//      name, MRN and last visit date — is fetched only *after* a PIN is
//      accepted, is held in this component's state alone, and is thrown away the
//      moment the strip relocks. `test_kiosk_strip.py` asserts the server half of
//      that promise; this file is the other half.
//   2. **The token screen is still the patient's.** The strip is a band along
//      the bottom. It never covers the token, never pushes it off screen, and
//      never renders a patient's prior history in the space a stranger reads.
//   3. **A reissued token is shouted.** Changing the department renumbers the
//      patient, who is standing there holding a printed slip with the old number
//      on it. That outcome takes over the strip in marigold until the
//      coordinator acknowledges having told them.
//   4. **`Skip` is a first-class outcome**, not an escape hatch — it lands the
//      visit in the department pool, where the console and the doctor's
//      `Unassigned` count pick it up.
//
// The unlock token lives in React state and nowhere else: not localStorage, not
// sessionStorage, not a cookie. It expires server-side, it dies with the tab,
// and it dies when the strip relocks on idle — a PIN typed in a public corridor
// must not outlive the shift that typed it.

import { useCallback, useEffect, useRef, useState } from "react";
import s from "../kiosk.module.css";
import { KioskLang, t, tb } from "../_lib/i18n";
import {
  ApiError,
  AssignResult,
  PinHolder,
  StripResult,
  kioskApi,
} from "../_lib/api";
import { Icon } from "../_lib/icons";
import { Keypad } from "./Keypad";

/** How long an unlocked strip survives without a touch. Short: the coordinator
 *  is standing at the kiosk while they use it, and the next patient walking up
 *  must not find a colleague's session open on a prior arrival. */
const RELOCK_MS = 45_000;

const PIN_MAX = 8;

type Phase = "locked" | "picking" | "pin" | "open" | "reissued";

export function StaffStrip({
  lang,
  sessionId,
  /** Spoken feedback, so a reissued token is heard as well as seen. */
  say,
}: {
  lang: KioskLang;
  sessionId: string;
  say: (text: string) => void;
}) {
  const [phase, setPhase] = useState<Phase>("locked");
  const [holders, setHolders] = useState<PinHolder[]>([]);
  const [holder, setHolder] = useState<PinHolder | null>(null);
  const [pin, setPin] = useState("");
  const [token, setToken] = useState<string | null>(null);
  const [strip, setStrip] = useState<StripResult | null>(null);
  const [outcome, setOutcome] = useState<AssignResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Local edits to the two pickers before `Confirm` writes them.
  const [deptKey, setDeptKey] = useState<string | null>(null);
  const [doctorId, setDoctorId] = useState<string | null>(null);
  const [linkChoice, setLinkChoice] = useState<boolean | null>(null);

  const lock = useCallback(() => {
    // Everything the PIN bought goes at once. Order is irrelevant to React but
    // not to the reader: the candidate is the sensitive one.
    setStrip(null);
    setOutcome(null);
    setToken(null);
    setHolder(null);
    setPin("");
    setDeptKey(null);
    setDoctorId(null);
    setLinkChoice(null);
    setError(null);
    setPhase("locked");
  }, []);

  // --- idle relock ---------------------------------------------------------
  const idleTimer = useRef<number | undefined>(undefined);
  const kick = useCallback(() => {
    window.clearTimeout(idleTimer.current);
    if (phase === "locked") return;
    idleTimer.current = window.setTimeout(lock, RELOCK_MS);
  }, [phase, lock]);

  useEffect(() => {
    kick();
    return () => window.clearTimeout(idleTimer.current);
  }, [kick]);

  const guard = async (fn: () => Promise<void>) => {
    setBusy(true);
    setError(null);
    try {
      await fn();
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) {
        // The token expired mid-action, or the PIN was wrong. Either way the
        // strip goes back to locked rather than sitting there half-open.
        lock();
        setError(tb("staffWrongPin", lang));
      } else {
        setError(t("genericError", lang));
      }
      console.error(e);
    } finally {
      setBusy(false);
    }
  };

  const openPicker = () =>
    guard(async () => {
      const list = await kioskApi.staffHolders();
      setHolders(list);
      setPhase("picking");
    });

  const unlock = (who: PinHolder, code: string) =>
    guard(async () => {
      const issued = await kioskApi.staffUnlock({ user_id: who.id, pin: code });
      const data = await kioskApi.strip(sessionId, issued.token);
      setToken(issued.token);
      setStrip(data);
      setDeptKey(data.department_key);
      setDoctorId(data.assigned_doctor_id ?? data.default_doctor_id);
      setPin("");
      setPhase("open");
    });

  const submit = (link: boolean | null, doctor: string | null) =>
    guard(async () => {
      if (!token || !strip) return;
      const result = await kioskApi.assign(sessionId, token, {
        link_candidate: link,
        department_key: deptKey,
        doctor_id: doctor,
      });
      setOutcome(result);
      if (result.token_reissued) {
        // The patient's printed slip is now wrong. This is the loudest thing
        // this component ever does, and it is spoken as well as shown.
        setPhase("reissued");
        say(`${tb("staffNewToken", lang)} ${result.token_no ?? ""}`);
        return;
      }
      lock();
    });

  return (
    <section
      className={`${s.staffStrip} ${phase === "locked" ? s.staffStripLocked : ""}`}
      data-testid="staff-strip"
      data-phase={phase}
      onPointerDown={kick}
      onKeyDown={kick}
      aria-label={tb("staffTitle", lang)}
    >
      <header className={s.staffHead}>
        <span className={s.staffLabel}>
          <Icon name="user" /> {tb("staffTitle", lang)}
        </span>
        {phase === "locked" ? (
          <button
            className={s.staffUnlockBtn}
            onClick={openPicker}
            disabled={busy}
            data-testid="staff-unlock"
          >
            {tb("staffUnlock", lang)}
          </button>
        ) : (
          <span className={s.staffWho}>
            {holder?.name}
            <button className={s.staffLockBtn} onClick={lock} data-testid="staff-lock">
              {tb("staffLock", lang)}
            </button>
          </span>
        )}
      </header>

      {error ? (
        <p className={s.staffError} role="alert" data-testid="staff-error">
          {error}
        </p>
      ) : null}

      {/* Locked: a sentence, and no trace of who is standing at the kiosk. */}
      {phase === "locked" && <p className={s.staffLockedNote}>{tb("staffLocked", lang)}</p>}

      {phase === "picking" && (
        <div className={s.staffBody} data-testid="staff-holders">
          <h3 className={s.staffQuestion}>{tb("staffWhoAreYou", lang)}</h3>
          {holders.length === 0 ? (
            <p className={s.staffLockedNote}>{tb("staffNoHolders", lang)}</p>
          ) : (
            <div className={s.staffHolderRow}>
              {holders.map((h) => (
                <button
                  key={h.id}
                  className={s.staffHolder}
                  onClick={() => {
                    setHolder(h);
                    setPhase("pin");
                  }}
                  data-testid="staff-holder"
                >
                  {h.name}
                </button>
              ))}
            </div>
          )}
        </div>
      )}

      {phase === "pin" && holder && (
        <div className={s.staffBody}>
          <h3 className={s.staffQuestion}>{tb("staffEnterPin", lang)}</h3>
          <Keypad
            lang={lang}
            value={pin}
            onChange={setPin}
            maxLength={PIN_MAX}
            masked
            compact
            disabled={busy}
            label={tb("staffEnterPin", lang)}
            testId="staff-pin"
          />
          <div className={s.staffActions}>
            <button className={s.staffGhostBtn} onClick={lock}>
              {t("back", lang)}
            </button>
            <button
              className={s.staffPrimaryBtn}
              disabled={busy || pin.length === 0}
              onClick={() => unlock(holder, pin)}
              data-testid="staff-pin-submit"
            >
              {busy ? tb("staffChanging", lang) : tb("staffUnlock", lang)}
            </button>
          </div>
        </div>
      )}

      {phase === "open" && strip && (
        <div className={s.staffBody} data-testid="staff-open">
          <StripCandidate
            lang={lang}
            strip={strip}
            choice={linkChoice}
            onChoose={setLinkChoice}
          />

          <div className={s.staffPickers}>
            <label className={s.staffPicker}>
              <span>{tb("staffDepartment", lang)}</span>
              <select
                value={deptKey ?? strip.department_key}
                disabled={busy}
                onChange={(e) => {
                  setDeptKey(e.target.value);
                  // A different department has a different roster; carrying the
                  // old doctor over would be assigning across departments, which
                  // the server refuses and the coordinator should never be
                  // offered.
                  setDoctorId(null);
                }}
                data-testid="staff-department"
              >
                {strip.departments.map((d) => (
                  <option key={d.key} value={d.key}>
                    {d.name}
                  </option>
                ))}
              </select>
            </label>

            <label className={s.staffPicker}>
              <span>{tb("staffDoctor", lang)}</span>
              <select
                value={doctorId ?? ""}
                // Changing the department invalidates this list until the strip
                // is re-read, so the picker is honest about not knowing.
                disabled={busy || deptKey !== strip.department_key}
                onChange={(e) => setDoctorId(e.target.value || null)}
                data-testid="staff-doctor"
              >
                <option value="">{tb("staffNoDoctor", lang)}</option>
                {strip.doctors.map((d) => (
                  <option key={d.id} value={d.id}>
                    {d.name}
                    {d.qualification ? ` · ${d.qualification}` : ""}
                    {" · "}
                    {d.on_duty ? tb("staffOnDuty", lang) : tb("staffOffDuty", lang)}
                  </option>
                ))}
              </select>
            </label>
          </div>

          {strip.doctors.length === 0 && (
            <p className={s.staffLockedNote}>{tb("staffNoDoctors", lang)}</p>
          )}

          <div className={s.staffActions}>
            <span className={s.staffSkipHint}>{tb("staffSkipHint", lang)}</span>
            <button
              className={s.staffGhostBtn}
              disabled={busy}
              // Skip still records the identity decision if one was taken —
              // "not the same person" is a fact a human established, and losing
              // it would re-offer the same wrong match at the desk.
              onClick={() => submit(linkChoice, null)}
              data-testid="staff-skip"
            >
              {tb("staffSkip", lang)}
            </button>
            <button
              className={s.staffPrimaryBtn}
              disabled={busy}
              onClick={() => submit(linkChoice, doctorId)}
              data-testid="staff-confirm"
            >
              {busy ? tb("staffChanging", lang) : tb("staffConfirm", lang)}
            </button>
          </div>
        </div>
      )}

      {phase === "reissued" && outcome && (
        <div className={s.staffReissue} role="alert" data-testid="staff-reissued">
          <div className={s.staffReissueText}>
            <strong>{tb("staffNewToken", lang)}</strong>
            <span>
              {tb("staffOldToken", lang).replace(
                "{n}",
                String(outcome.previous_token_no ?? "—")
              )}
            </span>
          </div>
          <div className={s.staffReissueNumber} data-testid="staff-new-token">
            {outcome.token_no ?? "—"}
          </div>
          <button className={s.staffPrimaryBtn} onClick={lock} data-testid="staff-token-ack">
            {tb("staffTokenAck", lang)}
          </button>
        </div>
      )}
    </section>
  );
}

/** The possible prior file, and the only place in the kiosk that renders it.
 *
 *  It states what is known and what was decided, and it never pre-selects the
 *  link: confirming a match merges two patient records, and a default that a
 *  tired coordinator taps past is how the wrong history ends up on a
 *  prescription. */
function StripCandidate({
  lang,
  strip,
  choice,
  onChoose,
}: {
  lang: KioskLang;
  strip: StripResult;
  choice: boolean | null;
  onChoose: (v: boolean | null) => void;
}) {
  if (!strip.candidate) {
    return (
      <p className={s.staffLockedNote} data-testid="staff-no-candidate">
        {tb("staffNoCandidate", lang)}
      </p>
    );
  }
  const c = strip.candidate;
  const settled =
    strip.link_state === "confirmed"
      ? tb("staffLinked", lang)
      : strip.link_state === "rejected"
        ? tb("staffRejected", lang)
        : null;

  return (
    <div className={s.staffCandidate} data-testid="staff-candidate">
      <span className={s.staffCandidateLabel}>{tb("staffCandidate", lang)}</span>
      <div className={s.staffCandidateFacts}>
        <strong data-testid="staff-candidate-name">{c.name}</strong>
        <span>
          {[
            c.age == null ? null : `${c.age}${c.sex ? shortSex(c.sex) : ""}`,
            c.mrn,
            c.external_id,
            c.last_visit_on ? `${tb("staffLastVisit", lang)} ${c.last_visit_on}` : null,
          ]
            .filter(Boolean)
            .join(" · ")}
        </span>
      </div>
      {settled ? (
        <span className={s.staffSettled}>{settled}</span>
      ) : (
        <div className={s.staffCandidateActions}>
          <button
            className={`${s.staffChoice} ${choice === true ? s.staffChoiceOn : ""}`}
            aria-pressed={choice === true}
            onClick={() => onChoose(choice === true ? null : true)}
            data-testid="staff-link-yes"
          >
            {tb("staffSamePerson", lang)}
          </button>
          <button
            className={`${s.staffChoice} ${choice === false ? s.staffChoiceOn : ""}`}
            aria-pressed={choice === false}
            onClick={() => onChoose(choice === false ? null : false)}
            data-testid="staff-link-no"
          >
            {tb("staffNotSamePerson", lang)}
          </button>
        </div>
      )}
    </div>
  );
}

function shortSex(sex: string): string {
  const first = sex.trim().charAt(0).toUpperCase();
  return first ? first : "";
}
