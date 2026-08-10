"use client";

// The kiosk intake (doc 03 §1a, doc 04 §3), rebuilt for S-UX.6.
//
// The shape of the screen is one idea: **a rail that remembers, and a stage that
// asks one thing**. The rail on the left is the patient's own record filling up
// as they answer — it is on every screen from the first question to the final
// read-back, because a patient who cannot see what the machine already knows has
// no way to catch it being wrong, and the moment it disappears (it used to, on
// the read-back) the intake stops feeling like a conversation and starts feeling
// like a form. The stage on the right holds exactly one question.
//
// Three rules the rebuild enforces that the previous build did not:
//
//   1. **Identity is asked once, and typed.** Name, age, sex and phone come from
//      one details screen before the clinical walk. They ride to the token slip,
//      the queue, the doctor console and the prescription. A misheard name is a
//      different patient, so this screen has no microphone.
//   2. **The microphone appears only where speech is the better answer** — the
//      chief complaint, and the closing questions the server marks `voice_input`.
//      A mic on a two-option yes/no screen reads as an instruction to talk.
//   3. **Everything on the screen is also spoken**, options included. Many
//      patients here cannot read; a choice they never heard is not a choice.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import s from "./kiosk.module.css";
import { KIOSK_LANGS, KioskLang, hospitalName, t, tb } from "./_lib/i18n";
import {
  ApiError,
  BundleHospital,
  ConfirmResult,
  Dept,
  KioskNode,
  PatientDetails,
  StartResult,
} from "./_lib/api";
import { useOffline } from "./_lib/offline/useOffline";
import { isLocalSession } from "./_lib/offline/local";
import { OfflineNeedsDepartment, OfflineUnavailableForDept } from "./_lib/offline/flow";
// The boarding pass (doc 23). `printSlip`/`escposSlip` are still in `print.ts`
// and still tested — they are the path of record until a real printer at the
// real kiosk has printed a pass — but the token screen's button is the pass now.
import { PassSvg } from "./_lib/pass/PassSvg";
import { passGeometry } from "./_lib/pass/geometry";
import { layoutPass } from "./_lib/pass/layout";
import { canvasMeasure } from "./_lib/pass/measure";
import { svgToEscpos } from "./_lib/pass/raster";
import { printPass } from "./_lib/print";
import {
  cancelSpeech,
  kioskAdaptiveEnabled,
  listen,
  recordToServer,
  serverSttEnabled,
  speak,
  sttSupported,
} from "./_lib/speech";
import { Icon } from "./_lib/icons";
import { AssistantAvatar } from "./_components/AssistantAvatar";
import { AudioBar } from "./_components/AudioBar";
import { OptionCard } from "./_components/OptionCard";
import { FacesScale } from "./_components/FacesScale";
import { Stepper } from "./_components/Stepper";
import { BodyMap } from "./_components/BodyMap";
import { MicButton } from "./_components/MicButton";
import { Keypad } from "./_components/Keypad";
import { DetailsForm, emptyDetails, detailsComplete } from "./_components/DetailsForm";
import { StaffStrip } from "./_components/StaffStrip";

type Screen =
  | "welcome"
  | "caregiver"
  | "returning"
  | "arrivalPhone"
  | "arrivalId"
  | "details"
  | "complaint"
  | "chooser"
  | "question"
  | "allergy"
  | "readback"
  | "token";

type SummaryAnswer = {
  nodeId: string;
  /** Presentation-only placement from the tree author (STATE.md invariant: it
   *  never touches traversal or red flags). Null for the many nodes that carry
   *  no role — those still belong on the rail, just not in a headline slot. */
  role: KioskNode["summary_role"];
  question: string;
  answer: string;
};

type IntakeSummary = {
  details: PatientDetails;
  caregiver: boolean;
  complaint: string;
  department: Dept | null;
  answers: SummaryAnswer[];
  /** Where the patient is, for the rail's own progress line. */
  stage: Screen;
  /** The named screens before the clinical walk, for *this* patient — a
   *  returning one is asked two more things than a first-timer, and a progress
   *  line that counts screens nobody will see is a lie in the reassuring
   *  direction. */
  leadSteps: Screen[];
  questionsLeft: number | null;
};

// Idle protects patient privacy on a shared terminal (doc 04 law 12 / doc 03 §1a).
const IDLE_PROMPT_MS = 60_000;
const IDLE_BLUR_MS = 90_000;

/** The named steps before the clinical questions start, for the rail's progress
 *  line. The questions themselves count down from the server's `remaining`.
 *  The two arrival screens are spliced in only for a patient who said they have
 *  been here before — see `IntakeSummary.leadSteps`. */
const LEAD_STEPS: Screen[] = ["caregiver", "returning", "details", "complaint"];
const ARRIVAL_STEPS: Screen[] = ["arrivalPhone", "arrivalId"];

/** How many answers the rail shows before folding the rest into a count. Enough
 *  to see the shape of the conversation, few enough that the rail never becomes
 *  the thing the patient is reading instead of the question. */
const RAIL_ANSWER_ROWS = 5;

export function KioskApp() {
  const [lang, setLang] = useState<KioskLang>("hi");
  const [screen, setScreen] = useState<Screen>("welcome");
  const [caregiver, setCaregiver] = useState(false);
  /** Did the patient say they have been here before? Null until asked. Drives
   *  only which screens are shown — nothing clinical, and never a gate. */
  const [returning, setReturning] = useState<boolean | null>(null);
  const [details, setDetails] = useState<PatientDetails>(emptyDetails);
  const [complaint, setComplaint] = useState("");
  const [summaryAnswers, setSummaryAnswers] = useState<SummaryAnswer[]>([]);

  const [sessionId, setSessionId] = useState<string | null>(null);
  const [node, setNode] = useState<KioskNode | null>(null);
  const [department, setDepartment] = useState<Dept | null>(null);
  const [depts, setDepts] = useState<Dept[]>([]);
  const [redFlags, setRedFlags] = useState<{ id: string; severity: string }[]>([]);
  // Adaptive intake (S-ADAPT.1, doc 11): the current node's spoken clarify (if
  // any) and how many voice attempts it has had — both reset when the node moves.
  const [clarify, setClarify] = useState<string | null>(null);
  const [voiceAttempt, setVoiceAttempt] = useState(0);
  // Allergies (SESSION-ALLERGY). Two sub-steps on one screen — the yes/no, then
  // what she reacts to — because they are one question to the patient even
  // though they are two decisions to us. `allergyAsking` is which half is up.
  const [allergyAsking, setAllergyAsking] = useState<"choice" | "which">("choice");
  const [allergyText, setAllergyText] = useState("");
  const [readback, setReadback] = useState("");
  const [token, setToken] = useState<ConfirmResult | null>(null);

  const [speaking, setSpeaking] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [idle, setIdle] = useState(false);

  // Offline lifecycle (S7): the flow that drives an intake online-or-local, plus
  // the downtime signal and the pending-sync count for the banner.
  // `hospitalName` is stored, not compiled in (AYUR-1): the name an admin sets
  // in the console is what the brand bar shows and what the boarding pass
  // prints, so a rename cannot leave the pass disagreeing with the patient's
  // prescription. It rides on the offline bundle, so it survives an outage.
  const { flow, downtime, pending, cachedDepartments, hospital } = useOffline();

  // --- audio: speak the current prompt whenever it changes -----------------
  const say = useCallback(
    (text: string) => {
      cancelSpeech();
      if (!text) return;
      setSpeaking(true);
      void speak(text, lang, sessionId).then(() => setSpeaking(false));
    },
    [lang, sessionId]
  );

  // --- idle watchdog -------------------------------------------------------
  const idleTimers = useRef<{ prompt?: number; blur?: number }>({});
  const kick = useCallback(() => {
    window.clearTimeout(idleTimers.current.prompt);
    window.clearTimeout(idleTimers.current.blur);
    if (screen === "welcome" || screen === "token") return;
    idleTimers.current.prompt = window.setTimeout(
      () => say(t("stillThere", lang)),
      IDLE_PROMPT_MS
    );
    idleTimers.current.blur = window.setTimeout(
      () => setIdle(true),
      IDLE_BLUR_MS
    );
  }, [screen, lang, say]);

  useEffect(() => {
    kick();
    const timers = idleTimers.current; // stable ref object; ids read at cleanup
    return () => {
      window.clearTimeout(timers.prompt);
      window.clearTimeout(timers.blur);
    };
  }, [kick]);

  const reset = useCallback(() => {
    cancelSpeech();
    setScreen("welcome");
    setCaregiver(false);
    setReturning(null);
    setDetails(emptyDetails);
    setComplaint("");
    setSummaryAnswers([]);
    setSessionId(null);
    setNode(null);
    setDepartment(null);
    setDepts([]);
    setRedFlags([]);
    setAllergyAsking("choice");
    setAllergyText("");
    setReadback("");
    setToken(null);
    setError(null);
    setIdle(false);
  }, []);

  // --- transitions ---------------------------------------------------------
  const withBusy = async (fn: () => Promise<void>) => {
    setBusy(true);
    setError(null);
    try {
      await fn();
    } catch (e) {
      const msg = e instanceof ApiError ? e.message : String(e);
      setError(t("genericError", lang));
      console.error(msg);
    } finally {
      setBusy(false);
    }
  };

  const applyStart = (res: StartResult) => {
    if (res.status === "needs_department") {
      setDepts(res.departments);
      setScreen("chooser");
      return;
    }
    setSessionId(res.session_id);
    setDepartment(res.department);
    setNode(res.node);
    if (res.complete || !res.node) {
      void finish(res.session_id);
    } else {
      setScreen("question");
    }
  };

  const summary: IntakeSummary = {
    details,
    caregiver,
    complaint,
    department,
    answers: summaryAnswers,
    stage: screen,
    leadSteps: returning
      ? [...LEAD_STEPS.slice(0, 2), ...ARRIVAL_STEPS, ...LEAD_STEPS.slice(2)]
      : LEAD_STEPS,
    questionsLeft: node?.remaining ?? null,
  };

  /** True once the patient has handed over something we can match a prior file
   *  on. It is what the details screen's reassurance line is keyed to — *not* a
   *  match, which the kiosk deliberately never learns. */
  const gaveIdentity = Boolean(details.phone.trim() || details.externalId.trim());
  const detailsSpoken = gaveIdentity
    ? `${tb("arrivalAck", lang)} ${t("detailsTitle", lang)}`
    : t("detailsTitle", lang);

  const captureSummary = (current: KioskNode, acceptedValue: unknown, rawText?: string) => {
    const answer = describeDisplayedAnswer(current, acceptedValue, rawText);
    if (!answer) return;
    setSummaryAnswers((previous) => [
      ...previous.filter((item) => item.nodeId !== current.id),
      {
        nodeId: current.id,
        role: current.summary_role ?? null,
        question: current.text,
        answer,
      },
    ]);
  };

  const start = (dept?: Dept) =>
    withBusy(async () => {
      try {
        const res = await flow.start({
          lang,
          chiefComplaint: complaint || "—",
          caregiver,
          details,
          deptKey: dept?.key,
          deptName: dept?.name,
          deptCareSystem: dept?.care_system,
        });
        applyStart(res);
      } catch (e) {
        if (e instanceof OfflineNeedsDepartment) {
          // No server to classify Q1 — go straight to the chooser from the
          // cached bundle (doc 03 §1a: the tap fallback is always available).
          setDepts(cachedDepartments);
          setScreen("chooser");
          return;
        }
        if (e instanceof OfflineUnavailableForDept) {
          setError(t("offlineDeptUnavailable", lang));
          return;
        }
        throw e;
      }
    });

  const submitAnswer = (value: unknown, rawText?: string) =>
    withBusy(async () => {
      if (!sessionId || !node) return;
      const res = await flow.answer(sessionId, {
        node_id: node.id,
        value,
        raw_text: rawText ?? null,
      });
      if (!res.ok) {
        setError(t("sttFailed", lang));
        return;
      }
      captureSummary(node, res.accepted_value ?? value, rawText);
      // Flags are recomputed by the walker on every save (STATE.md invariant:
      // never accumulated) — take the server's current set, don't merge.
      setRedFlags(res.red_flags);
      setClarify(null);
      if (res.complete || !res.node) {
        askAllergies();
      } else {
        setNode(res.node);
      }
    });

  // Adaptive intake (S-ADAPT.1, doc 11 §2): a *voice* answer to the current node.
  // The server maps it onto the node's own allowed answers; a vague answer earns
  // one spoken clarify (re-listen), a second falls back to the taps that are
  // always on screen (doc 11 §5). Taps use submitAnswer above — unchanged.
  const submitVoiceAnswer = (rawText: string) =>
    withBusy(async () => {
      if (!sessionId || !node || !rawText.trim()) return;
      const res = await flow.answer(sessionId, {
        node_id: node.id,
        value: null,
        raw_text: rawText,
        attempt: voiceAttempt,
      });
      if (res.ok) {
        captureSummary(node, res.accepted_value, rawText);
        setRedFlags(res.red_flags);
        setClarify(null);
        if (res.complete || !res.node) {
          askAllergies();
        } else {
          setNode(res.node);
        }
        return;
      }
      if (res.clarify) {
        // One follow-up, spoken and shown; re-open the mic on the same node.
        setClarify(res.clarify);
        setVoiceAttempt((n) => n + 1);
        say(res.clarify);
        return;
      }
      // adaptive_exhausted (or any other non-ok): drop the voice loop, keep taps.
      setClarify(null);
    });

  // When the walk moves to a new node, the voice loop starts fresh (doc 11 §5).
  useEffect(() => {
    setClarify(null);
    setVoiceAttempt(0);
  }, [node?.id]);

  /** The last question of every intake, asked after the tree has run out.
   *
   *  It sits here rather than before the walk because the session — and so the
   *  visit the statement hangs off — does not exist until `/start`, which needs
   *  a complaint and a department. Asking it last also puts it where the patient
   *  is already answering questions rather than filling in a form, which is
   *  where a drug name is most likely to be remembered. */
  const askAllergies = () => {
    setAllergyAsking("choice");
    setAllergyText("");
    setScreen("allergy");
  };

  /** Record the answer and move on to the read-back.
   *
   *  Moving on is unconditional. If the statement could not be saved she is told
   *  to tell the doctor herself, and the intake continues — a kiosk that trapped
   *  a patient on this screen because the network blinked would cost her the
   *  token she is queuing for, which is a worse outcome than a doctor asking
   *  about allergies the way they do today.
   */
  const submitAllergies = (input: { none_known: boolean; text: string }) =>
    withBusy(async () => {
      if (!sessionId) return;
      // **Her words, unsplit.** If she says "penicillin and sulfa" that is what
      // the doctor reads, because splitting a sentence into two clinical facts
      // means inventing the boundary between them — and this module's whole
      // rule is that it records statements rather than deriving conclusions
      // from them. The wire still takes a list; other clients may have one.
      const said = input.text.trim();
      const items = said ? [{ substance: said }] : [];
      // A patient who tapped "yes" and then named nothing has told us nothing.
      // Sending it would ask the server to store an alarm with no substance in
      // it, which it refuses anyway.
      if (!input.none_known && items.length === 0) {
        await finish(sessionId);
        return;
      }
      const saved = await flow.allergies(sessionId, { none_known: input.none_known, items });
      if (!saved) setError(t("allergyNotSaved", lang));
      await finish(sessionId);
    });

  const finish = (sid: string) =>
    withBusy(async () => {
      const res = await flow.finish(sid);
      setReadback(res.readback);
      setRedFlags(res.red_flags);
      setScreen("readback");
    });

  const confirm = () =>
    withBusy(async () => {
      if (!sessionId) return;
      const res = await flow.confirm(sessionId);
      setToken(res);
      setScreen("token");
    });

  // --- render --------------------------------------------------------------
  return (
    <main
      className={s.shell}
      data-screen={screen}
      data-node-type={node?.type ?? ""}
      data-node-id={node?.id ?? ""}
      onPointerDown={kick}
      onKeyDown={kick}
    >
      <TopBar
        lang={lang}
        hospital={hospital}
        onLang={(l) => {
          setLang(l);
          cancelSpeech();
        }}
        onRestart={screen === "welcome" ? undefined : reset}
      />

      {downtime && <DowntimeBanner lang={lang} pending={pending} />}

      {error ? <div className={s.errorToast}>{error}</div> : null}

      {screen === "welcome" && (
        <Welcome
          onPick={(l) => {
            setLang(l);
            setScreen("caregiver");
          }}
        />
      )}

      {screen === "caregiver" && (
        <Stage
          lang={lang}
          speaking={speaking}
          promptText={t("caregiverTitle", lang)}
          hint={t("caregiverHelp", lang)}
          onReplay={() => say(t("caregiverTitle", lang))}
          autoSpeak={t("caregiverTitle", lang)}
          say={say}
          summary={summary}
        >
          <div className={s.bigChoices}>
            <button
              className={s.bigChoice}
              onClick={() => {
                setCaregiver(false);
                setScreen("returning");
              }}
              data-testid="caregiver-self"
            >
              <span className={s.bigChoiceIcon}>
                <Icon name="body" />
              </span>
              <span className={s.bigChoiceText}>{t("itsForMe", lang)}</span>
            </button>
            <button
              className={s.bigChoice}
              onClick={() => {
                setCaregiver(true);
                setScreen("returning");
              }}
              data-testid="caregiver-other"
            >
              <span className={s.bigChoiceIcon}>
                <Icon name="hands-holding" />
              </span>
              <span className={s.bigChoiceText}>{t("itsForSomeone", lang)}</span>
            </button>
          </div>
        </Stage>
      )}

      {/* --- arrival identity (AR3, plan §1.1) --------------------------------
          Three screens, one decision each, every one of them skippable. They
          exist so a coordinator can be *offered* a prior file three screens
          later; they are not registration, they never gate a token, and a
          patient who taps straight through reaches exactly the intake they
          would have had before this session existed. */}
      {screen === "returning" && (
        <Stage
          lang={lang}
          speaking={speaking}
          promptText={tb("returningTitle", lang)}
          hint={tb("returningHint", lang)}
          onReplay={() => say(tb("returningTitle", lang))}
          autoSpeak={tb("returningTitle", lang)}
          say={say}
          summary={summary}
        >
          <div className={s.bigChoices}>
            <button
              className={s.bigChoice}
              onClick={() => {
                setReturning(true);
                setScreen("arrivalPhone");
              }}
              data-testid="returning-yes"
            >
              <span className={s.bigChoiceIcon}>
                <Icon name="folder" />
              </span>
              <span className={s.bigChoiceText}>{tb("returningYes", lang)}</span>
            </button>
            <button
              className={s.bigChoice}
              onClick={() => {
                setReturning(false);
                setScreen("details");
              }}
              data-testid="returning-no"
            >
              <span className={s.bigChoiceIcon}>
                <Icon name="user" />
              </span>
              <span className={s.bigChoiceText}>{tb("returningNo", lang)}</span>
            </button>
          </div>
          <div className={s.footer}>
            <button
              className={`${s.btn} ${s.btnGhost}`}
              onClick={() => setScreen("caregiver")}
            >
              ← {t("back", lang)}
            </button>
          </div>
        </Stage>
      )}

      {screen === "arrivalPhone" && (
        <Stage
          lang={lang}
          speaking={speaking}
          promptText={tb("arrivalPhoneTitle", lang)}
          hint={tb("arrivalPhoneHint", lang)}
          onReplay={() => say(tb("arrivalPhoneTitle", lang))}
          autoSpeak={tb("arrivalPhoneTitle", lang)}
          say={say}
          summary={summary}
        >
          <Keypad
            lang={lang}
            value={details.phone.replace(/\D/g, "")}
            onChange={(next) => setDetails({ ...details, phone: next })}
            maxLength={10}
            label={tb("arrivalPhoneTitle", lang)}
            testId="arrival-phone"
          />
          <div className={s.footer}>
            <button
              className={`${s.btn} ${s.btnGhost}`}
              onClick={() => setScreen("returning")}
            >
              ← {t("back", lang)}
            </button>
            <div className={s.spacer} />
            {/* Skip is a peer of Next, not a link hidden under it: the number is
                optional, and an optional field that looks mandatory is a field
                people lie in. */}
            <button
              className={`${s.btn} ${s.btnGhost} ${s.btnBig}`}
              onClick={() => setScreen("arrivalId")}
              data-testid="arrival-phone-skip"
            >
              {tb("skipThis", lang)}
            </button>
            <button
              className={`${s.btn} ${s.btnPrimary} ${s.btnBig}`}
              onClick={() => setScreen("arrivalId")}
              data-testid="arrival-phone-next"
            >
              {t("next", lang)} →
            </button>
          </div>
        </Stage>
      )}

      {screen === "arrivalId" && (
        <Stage
          lang={lang}
          speaking={speaking}
          promptText={tb("arrivalIdTitle", lang)}
          hint={tb("arrivalIdHint", lang)}
          onReplay={() => say(tb("arrivalIdTitle", lang))}
          autoSpeak={tb("arrivalIdTitle", lang)}
          say={say}
          summary={summary}
        >
          <label className={`${s.field} ${s.fieldWide} ${s.arrivalIdField}`}>
            <span className={s.fieldLabel}>
              <span className={s.fieldIcon} aria-hidden="true">
                <Icon name="card" />
              </span>
              {tb("arrivalIdInput", lang)}
              <em className={s.fieldOptional}>{t("optionalLabel", lang)}</em>
            </span>
            <input
              className={s.fieldInput}
              value={details.externalId}
              maxLength={64}
              autoComplete="off"
              autoCapitalize="characters"
              spellCheck={false}
              placeholder="—"
              onChange={(e) => setDetails({ ...details, externalId: e.target.value })}
              data-testid="arrival-external-id"
            />
          </label>
          <div className={s.footer}>
            <button
              className={`${s.btn} ${s.btnGhost}`}
              onClick={() => setScreen("arrivalPhone")}
            >
              ← {t("back", lang)}
            </button>
            <div className={s.spacer} />
            <button
              className={`${s.btn} ${s.btnGhost} ${s.btnBig}`}
              onClick={() => setScreen("details")}
              data-testid="arrival-id-skip"
            >
              {tb("skipThis", lang)}
            </button>
            <button
              className={`${s.btn} ${s.btnPrimary} ${s.btnBig}`}
              onClick={() => setScreen("details")}
              data-testid="arrival-id-next"
            >
              {t("next", lang)} →
            </button>
          </div>
        </Stage>
      )}

      {screen === "details" && (
        <Stage
          lang={lang}
          speaking={speaking}
          promptText={t("detailsTitle", lang)}
          hint={t("detailsHint", lang)}
          // The acknowledgement is read out before the screen's own question —
          // it answers what the patient just did, and doc 04 law 1 makes audio
          // the channel, not the caption.
          onReplay={() => say(detailsSpoken)}
          autoSpeak={detailsSpoken}
          say={say}
          summary={summary}
          banner={gaveIdentity ? <ArrivalAck lang={lang} /> : undefined}
        >
          <DetailsForm
            lang={lang}
            value={details}
            onChange={setDetails}
            disabled={busy}
            caregiver={caregiver}
          />
          <div className={s.footer}>
            <button
              className={`${s.btn} ${s.btnGhost}`}
              onClick={() => setScreen(returning ? "arrivalId" : "returning")}
            >
              ← {t("back", lang)}
            </button>
            <div className={s.spacer} />
            <button
              className={`${s.btn} ${s.btnPrimary} ${s.btnBig}`}
              disabled={busy || !detailsComplete(details)}
              onClick={() => setScreen("complaint")}
              data-testid="details-next"
            >
              {t("next", lang)} →
            </button>
          </div>
        </Stage>
      )}

      {screen === "complaint" && (
        <Stage
          lang={lang}
          speaking={speaking}
          promptText={t("ccTitle", lang)}
          hint={t("ccHint", lang)}
          onReplay={() => say(t("ccTitle", lang))}
          autoSpeak={t("ccTitle", lang)}
          say={say}
          summary={summary}
        >
          <VoiceCapture
            lang={lang}
            value={complaint}
            onChange={setComplaint}
            busy={busy}
          />
          <div className={s.footer}>
            <button
              className={`${s.btn} ${s.btnGhost}`}
              onClick={() => setScreen("details")}
            >
              ← {t("back", lang)}
            </button>
            <div className={s.spacer} />
            <button
              className={`${s.btn} ${s.btnPrimary} ${s.btnBig}`}
              disabled={busy || complaint.trim().length === 0}
              onClick={() => start()}
              data-testid="cc-next"
            >
              {t("next", lang)} →
            </button>
          </div>
        </Stage>
      )}

      {screen === "chooser" && (
        <Stage
          lang={lang}
          speaking={speaking}
          promptText={t("chooseDept", lang)}
          hint={t("chooseOne", lang)}
          onReplay={() => say(t("chooseDept", lang))}
          autoSpeak={t("chooseDept", lang)}
          say={say}
          summary={summary}
        >
          <div className={s.deptGrid}>
            {depts.map((d) => (
              <OptionCard
                key={d.key}
                text={d.name}
                icon={deptIcon(d.key)}
                // The value, not a flag, and not compared here: doc 24 §2 keeps
                // the enum out of components, and `CareSystemCapabilities`
                // deliberately excludes it because a card's styling is one of
                // the two places the raw system genuinely *is* the data. It
                // lands on the DOM and the stylesheet decides what it looks
                // like, which is the same shape the admin console's selector
                // uses.
                careSystem={d.care_system}
                onSelect={() => start(d)}
              />
            ))}
          </div>
        </Stage>
      )}

      {screen === "question" && node && sessionId && (
        <QuestionScreen
          key={node.id}
          lang={lang}
          sessionId={sessionId}
          node={node}
          speaking={speaking}
          busy={busy}
          say={say}
          onSubmit={submitAnswer}
          onVoiceAnswer={submitVoiceAnswer}
          clarify={clarify}
          redFlags={redFlags}
          summary={summary}
        />
      )}

      {screen === "allergy" && (
        <AllergyScreen
          lang={lang}
          speaking={speaking}
          busy={busy}
          say={say}
          summary={summary}
          asking={allergyAsking}
          text={allergyText}
          onText={setAllergyText}
          onAsk={setAllergyAsking}
          onSubmit={submitAllergies}
        />
      )}

      {screen === "readback" && (
        <ReadbackScreen
          lang={lang}
          readback={readback}
          redFlags={redFlags}
          speaking={speaking}
          busy={busy}
          say={say}
          onConfirm={confirm}
          onEdit={reset}
          summary={summary}
        />
      )}

      {screen === "token" && token && (
        <TokenScreen
          lang={lang}
          hospital={hospital}
          token={token}
          details={details}
          complaint={complaint}
          answers={summaryAnswers}
          // The strip talks to the server about *this* session. An offline
          // intake has no server session to settle against, which is the
          // accepted debt in the plan (§8): those visits sync unassigned and are
          // assigned from the coordinator console instead.
          sessionId={sessionId && !isLocalSession(sessionId) ? sessionId : null}
          onDone={reset}
          say={say}
        />
      )}

      {idle && (
        <div
          className={s.idle}
          onClick={() => {
            setIdle(false);
            kick();
          }}
        >
          <div className={s.idleTitle}>{t("stillThere", lang)}</div>
          <div className={s.idleHint}>{t("tapToContinue", lang)}</div>
        </div>
      )}
    </main>
  );
}

function describeDisplayedAnswer(
  node: KioskNode,
  value: unknown,
  rawText?: string
): string {
  const optionLabel = (id: string) =>
    node.options.find((option) => option.id === id)?.text ?? id;
  if (Array.isArray(value)) {
    return value.map((item) => optionLabel(String(item))).join(", ");
  }
  if (typeof value === "string" && node.options.length > 0) {
    return optionLabel(value);
  }
  if (node.type === "free_voice") return (rawText ?? String(value ?? "")).trim();
  if (typeof value === "number") {
    return `${value}${node.unit ? ` ${node.unit}` : ""}`;
  }
  return value == null ? "" : String(value);
}

// -- the live rail ------------------------------------------------------------

/** What the kiosk has understood so far, on every screen that has anything to
 *  show — including the read-back, which is precisely where a patient is being
 *  asked to confirm and most needs to see the detail behind the sentence.
 *
 *  Truthful by construction: every line is either something the patient typed or
 *  an answer label the walker accepted. Nothing here is inferred, and a fact that
 *  has not been given reads as "not answered yet" rather than being hidden. */
function SummaryRail({
  lang,
  summary,
  speaking,
}: {
  lang: KioskLang;
  summary: IntakeSummary;
  speaking: boolean;
}) {
  const [open, setOpen] = useState(false);
  const empty = t("notAnswered", lang);
  const primary = [...summary.answers]
    .reverse()
    .find((answer) => answer.role === "primary_symptom");
  const duration = [...summary.answers]
    .reverse()
    .find((answer) => answer.role === "duration");
  const detailRows = summary.answers.filter(
    (answer) => answer.role !== "primary_symptom" && answer.role !== "duration"
  );
  const shownRows = detailRows.slice(-RAIL_ANSWER_ROWS).reverse();
  const hiddenRows = detailRows.length - shownRows.length;

  // Open by default only where the rail is its own column. Elsewhere — narrow
  // screens, and the 1080×1920 portrait kiosk, which is wide but not wide enough
  // for two columns — it is a strip the patient taps open, so the question is
  // still the first thing on the screen. The query mirrors the stylesheet's.
  useEffect(() => {
    const asColumn = window.matchMedia("(min-width: 1024px) and (orientation: landscape)");
    const sync = () => setOpen(asColumn.matches);
    sync();
    asColumn.addEventListener("change", sync);
    return () => asColumn.removeEventListener("change", sync);
  }, []);

  const age = summary.details.age;
  const sexLabel = summary.details.sex
    ? t(
        summary.details.sex === "male"
          ? "sexMale"
          : summary.details.sex === "female"
            ? "sexFemale"
            : "sexOther",
        lang
      )
    : "";
  const ageSex =
    age == null && !sexLabel
      ? empty
      : [age == null ? null : `${age} ${t("yearsShort", lang)}`, sexLabel]
          .filter(Boolean)
          .join(" · ");

  return (
    <aside className={s.summaryRail} aria-label={t("liveSummary", lang)}>
      <details
        className={s.summaryDetails}
        open={open}
        onToggle={(event) => setOpen(event.currentTarget.open)}
      >
        <summary className={s.summaryToggle}>
          <span className={s.summaryToggleLabel}>{t("liveSummary", lang)}</span>
          <strong>{summary.details.name || empty}</strong>
          <span className={s.summaryChevron} aria-hidden="true" />
        </summary>
        <div className={s.summaryBody}>
          <div className={s.summaryHead}>
            <AssistantAvatar speaking={speaking} />
            <div className={s.summaryHeadText}>
              <h2 className={s.summaryTitle}>{t("liveSummary", lang)}</h2>
              <p className={s.summaryStage}>{stageLabel(lang, summary)}</p>
            </div>
          </div>

          <dl className={s.summaryFacts}>
            <div>
              <dt>{t("summaryPatient", lang)}</dt>
              <dd data-testid="summary-patient">{summary.details.name || empty}</dd>
            </div>
            <div>
              <dt>{t("summaryAge", lang)}</dt>
              <dd data-testid="summary-age">{ageSex}</dd>
            </div>
            <div>
              <dt>{t("summaryConcern", lang)}</dt>
              <dd data-testid="summary-concern">
                {primary?.answer || summary.complaint.trim() || empty}
              </dd>
            </div>
            <div>
              <dt>{t("summaryDepartment", lang)}</dt>
              <dd data-testid="summary-department">{summary.department?.name || empty}</dd>
            </div>
            <div>
              <dt>{t("summaryDuration", lang)}</dt>
              <dd data-testid="summary-duration">{duration?.answer || empty}</dd>
            </div>
          </dl>

          <div className={s.summarySymptoms}>
            <h3>{t("answersTitle", lang)}</h3>
            {shownRows.length ? (
              <ul>
                {shownRows.map((answer) => (
                  <li key={answer.nodeId}>
                    <span>{answer.question}</span>
                    <strong>{answer.answer}</strong>
                  </li>
                ))}
              </ul>
            ) : (
              <p>{empty}</p>
            )}
            {hiddenRows > 0 ? (
              <p className={s.summaryMore}>
                {t("moreAnswers", lang).replace("{n}", String(hiddenRows))}
              </p>
            ) : null}
          </div>
        </div>
      </details>
    </aside>
  );
}

/** The rail's one-line "where am I" — a real position, not a decorative bar.
 *  Before the walk it counts the named lead-in screens; during it, it counts the
 *  server's own `remaining`, which is derived from the tree rather than from a
 *  client-side step counter that drifts the moment a branch is taken. */
function stageLabel(lang: KioskLang, summary: IntakeSummary): string {
  if (summary.stage === "readback") return t("reviewStep", lang);
  // Named rather than counted. It sits after the tree has run out, so the
  // countdown below has nothing left to count and would render "last question"
  // for a question the tree never contained.
  if (summary.stage === "allergy") return t("allergyStep", lang);
  const lead = summary.leadSteps.indexOf(summary.stage);
  if (lead >= 0) {
    return t("stepProgress", lang)
      .replace("{n}", String(lead + 1))
      .replace("{total}", String(summary.leadSteps.length));
  }
  // Deliberately a countdown, not "3 of 8". The tree branches, so a total is a
  // promise the walk cannot keep — and a progress bar that jumps backwards is
  // worse than none. `remaining` is the server's own count down the default path.
  const left = summary.questionsLeft;
  if (left == null) return "";
  if (left <= 1) return t("lastQuestion", lang);
  return t("questionsLeft", lang).replace("{n}", String(left));
}

// -- downtime banner (S7, doc 01 §5) ------------------------------------------

/** The "OFFLINE — tokens continue" card (doc 04 §3: coordinator/kiosk downtime
 *  flips the bar to marigold with a clear banner). It reassures rather than
 *  alarms: the whole point of downtime mode is that the patient's intake still
 *  works, so the copy says what still works, not what is broken. */
function DowntimeBanner({ lang, pending }: { lang: KioskLang; pending: number }) {
  return (
    <div className={s.downtimeBanner} role="status" data-testid="downtime-banner">
      <span className={s.downtimeDot} aria-hidden />
      <span className={s.downtimeText}>{t("downtimeBanner", lang)}</span>
      {pending > 0 && (
        <span className={s.downtimePending}>
          {t("downtimePending", lang).replace("{n}", String(pending))}
        </span>
      )}
    </div>
  );
}

// -- shared shell pieces ------------------------------------------------------

function TopBar({
  lang,
  hospital,
  onLang,
  onRestart,
}: {
  lang: KioskLang;
  /** The hospital's stored names, or null before a bundle has ever been fetched. */
  hospital: BundleHospital | null;
  onLang: (l: KioskLang) => void;
  onRestart?: () => void;
}) {
  return (
    <div className={s.topbar}>
      <div className={s.brand}>
        <div className={s.brandMark} aria-hidden="true">
          <Icon name="stethoscope" />
        </div>
        <div className={s.brandName}>{hospitalName(hospital, lang)}</div>
      </div>
      <div className={s.topbarRight}>
        <div className={s.langBar} role="group" aria-label={t("chooseLanguage", lang)}>
          {KIOSK_LANGS.map((l) => (
            <button
              key={l.code}
              className={`${s.langChip} ${l.code === lang ? s.langChipActive : ""}`}
              onClick={() => onLang(l.code)}
              lang={l.code}
            >
              {l.label}
            </button>
          ))}
        </div>
        {onRestart && (
          <button className={s.restartBtn} onClick={onRestart} data-testid="restart">
            <Icon name="refresh" /> <span>{t("startOver", lang)}</span>
          </button>
        )}
      </div>
    </div>
  );
}

function Welcome({ onPick }: { onPick: (l: KioskLang) => void }) {
  return (
    <div className={s.welcome}>
      <AssistantAvatar speaking={false} />
      <h1 className={s.display}>{T_WELCOME}</h1>
      <p className={s.lead}>{t("chooseLanguage", "hi")}</p>
      <div className={s.welcomeLangs}>
        {KIOSK_LANGS.map((l) => (
          <button
            key={l.code}
            className={s.welcomeLang}
            onClick={() => onPick(l.code)}
            lang={l.code}
            data-testid={`welcome-lang-${l.code}`}
          >
            {l.label}
          </button>
        ))}
      </div>
      <span className={s.trust}>
        <Lock /> {t("trust", "hi")}
      </span>
    </div>
  );
}
const T_WELCOME = "नमस्ते · Welcome";

/** The two-column intake surface: the rail that remembers, the stage that asks.
 *  Everything between the caregiver question and the read-back uses it, which is
 *  what makes the rail continuous — there is no screen that quietly drops it. */
function Stage({
  lang,
  speaking,
  promptText,
  hint,
  onReplay,
  autoSpeak,
  say,
  banner,
  summary,
  children,
}: {
  lang: KioskLang;
  speaking: boolean;
  promptText: string;
  hint?: string;
  onReplay: () => void;
  autoSpeak: string;
  say: (t: string) => void;
  banner?: React.ReactNode;
  summary: IntakeSummary;
  children: React.ReactNode;
}) {
  useEffect(() => {
    if (autoSpeak) say(autoSpeak);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [autoSpeak]);

  return (
    <div className={s.stage}>
      <SummaryRail lang={lang} summary={summary} speaking={speaking} />
      <div className={s.panel}>
        <div className={s.panelInner}>
          {banner}
          <div className={s.promptRow}>
            <AudioBar playing={speaking} label={t("replay", lang)} onReplay={onReplay} />
          </div>
          <h2 className={s.question} lang={lang}>
            {promptText}
          </h2>
          {hint ? <p className={s.lead}>{hint}</p> : null}
          {children}
        </div>
      </div>
    </div>
  );
}

// -- voice capture (chief complaint / free_voice) -----------------------------

function VoiceCapture({
  lang,
  value,
  onChange,
  busy,
}: {
  lang: KioskLang;
  value: string;
  onChange: (v: string) => void;
  busy: boolean;
}) {
  // Server-STT mode records the clip and sends it to local Whisper on the box
  // (V-OSS, fully local); default mode uses the browser's Web Speech.
  const useServer = serverSttEnabled();
  const [listening, setListening] = useState(false);
  const [transcribing, setTranscribing] = useState(false);
  const [showType, setShowType] = useState(!useServer && !sttSupported());
  const stopRef = useRef<(() => void) | null>(null);

  const toggleMic = () => {
    if (listening) {
      // Stop: browser-STT ends immediately; server-STT now uploads + transcribes.
      stopRef.current?.();
      setListening(false);
      if (useServer) setTranscribing(true);
      return;
    }
    if (useServer) {
      void recordToServer(lang, {
        onText: (text) => onChange(text),
        onError: () => {
          setTranscribing(false);
          setShowType(true);
        },
        // Heard, but not confidently. She keeps her words and gets the keyboard.
        onUncertain: () => setShowType(true),
        onDone: () => setTranscribing(false),
      }).then((stop) => {
        if (!stop) {
          setShowType(true);
          return;
        }
        stopRef.current = stop;
        setListening(true);
      });
      return;
    }
    const stop = listen(lang, {
      onText: (text) => onChange(text),
      onError: () => {
        setListening(false);
        setShowType(true);
      },
      onDone: () => setListening(false),
    });
    if (!stop) {
      setShowType(true);
      return;
    }
    stopRef.current = stop;
    setListening(true);
  };

  return (
    <div className={s.micWrap}>
      <MicButton
        listening={listening}
        label={listening ? t("listening", lang) : t("tapToSpeak", lang)}
        onPress={toggleMic}
        disabled={transcribing}
      />
      <div className={s.avatarStatus}>
        {transcribing
          ? t("transcribing", lang)
          : listening
            ? t("listening", lang)
            : t("tapToSpeak", lang)}
      </div>
      <div className={`${s.transcript} ${value ? "" : s.transcriptPlaceholder}`}>
        {value ? `${t("youSaid", lang)} ${value}` : t("ccHint", lang)}
      </div>
      {showType ? (
        <textarea
          className={s.typeField}
          rows={2}
          value={value}
          disabled={busy}
          placeholder={t("typeInstead", lang)}
          onChange={(e) => onChange(e.target.value)}
          aria-label={t("typeInstead", lang)}
        />
      ) : (
        <button
          className={`${s.btn} ${s.btnGhost}`}
          onClick={() => setShowType(true)}
          data-testid="type-toggle"
        >
          <Icon name="keyboard" /> {t("typeInstead", lang)}
        </button>
      )}
    </div>
  );
}

// -- question screen ----------------------------------------------------------

function QuestionScreen({
  lang,
  sessionId,
  node,
  speaking,
  busy,
  say,
  onSubmit,
  onVoiceAnswer,
  clarify,
  redFlags,
  summary,
}: {
  lang: KioskLang;
  sessionId: string;
  node: KioskNode;
  speaking: boolean;
  busy: boolean;
  say: (t: string) => void;
  onSubmit: (value: unknown, rawText?: string) => void;
  onVoiceAnswer: (rawText: string) => void;
  clarify: string | null;
  redFlags: { id: string; severity: string }[];
  summary: IntakeSummary;
}) {
  const [multi, setMulti] = useState<string[]>([]);
  const [scale, setScale] = useState<number | null>(null);
  const [num, setNum] = useState<number>(node.min ?? 0);
  const [text, setText] = useState("");

  // Where the microphone belongs (S-UX.6). Two gates, and both must pass.
  //
  // *Where*: the server decides — a free-text node always, a tap node only in the
  // closing pair — so the kiosk never guesses from a step counter, and offline the
  // ported walker computes the same answer.
  //
  // *Whether at all*: `kioskAdaptiveEnabled()` — the build flag, server-STT and a
  // real recorder. Without it there is nothing behind the mic, and a dead
  // microphone is worse than no microphone: the patient presses it, nothing
  // happens, and they conclude the machine is broken rather than that they should
  // tap. S-UX.6 dropped this gate by accident; it is the reason a box with no STT
  // provider still showed a mic on the closing questions.
  const voiceInvited =
    (node.voice_input ?? node.type === "free_voice") && kioskAdaptiveEnabled();

  // Everything on the screen is also spoken, options included (doc 04 law 12).
  // A patient who cannot read the choices has not been offered them.
  const spokenPrompt = useMemo(() => spokenFor(node, lang), [node, lang]);
  useEffect(() => {
    say(spokenPrompt);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [node.id]);

  const needsSubmit =
    node.type === "multi" ||
    node.type === "body_map" ||
    node.type === "scale" ||
    node.type === "number" ||
    node.type === "free_voice";

  const canSubmit =
    (node.type === "multi" && multi.length > 0) ||
    (node.type === "body_map" && multi.length > 0) ||
    (node.type === "scale" && scale !== null) ||
    node.type === "number" ||
    (node.type === "free_voice" && text.trim().length > 0);

  const submit = () => {
    if (node.type === "multi" || node.type === "body_map") onSubmit(multi);
    else if (node.type === "scale") onSubmit(scale);
    else if (node.type === "number") onSubmit(num);
    else if (node.type === "free_voice") onSubmit(text, text);
  };

  const toggle = (id: string) =>
    setMulti((prev) =>
      prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]
    );

  const hint =
    node.type === "multi"
      ? t("chooseAny", lang)
      : node.type === "single"
        ? t("chooseOne", lang)
        : undefined;

  return (
    <div className={s.stage}>
      <SummaryRail lang={lang} summary={summary} speaking={speaking} />
      <div className={s.panel}>
        <div className={s.panelInner}>
          {redFlags.length > 0 ? <UrgentBanner lang={lang} /> : null}
          <div className={s.promptRow}>
            <AudioBar
              playing={speaking}
              label={t("replay", lang)}
              onReplay={() => say(spokenPrompt)}
            />
            <ProgressPill lang={lang} summary={summary} />
          </div>
          <h2 className={s.question} lang={lang}>
            {node.text}
          </h2>
          {hint ? <p className={s.lead}>{hint}</p> : null}

          {node.type === "single" && (
            <div className={s.options} data-count={node.options.length}>
              {node.options.map((o) => (
                <OptionCard
                  key={o.id}
                  text={o.text}
                  icon={o.icon}
                  onSelect={() => onSubmit(o.id, o.text)}
                />
              ))}
            </div>
          )}

          {node.type === "multi" && (
            <div className={s.options} data-count={node.options.length}>
              {node.options.map((o) => (
                <OptionCard
                  key={o.id}
                  text={o.text}
                  icon={o.icon}
                  selected={multi.includes(o.id)}
                  onSelect={() => toggle(o.id)}
                />
              ))}
            </div>
          )}

          {node.type === "body_map" && (
            <BodyMap options={node.options} selected={multi} onToggle={toggle} />
          )}

          {node.type === "scale" && (
            <FacesScale
              min={node.min ?? 0}
              max={node.max ?? 10}
              value={scale}
              onSelect={(v) => setScale(v)}
            />
          )}

          {node.type === "number" && (
            <Stepper
              min={node.min ?? 0}
              max={node.max ?? 30}
              unit={node.unit}
              value={num}
              onChange={setNum}
            />
          )}

          {node.type === "free_voice" && (
            <VoiceCapture lang={lang} value={text} onChange={setText} busy={busy} />
          )}

          {/* The mic on a *tap* node is the adaptive-intake affordance, and it
              belongs under the taps, not above them: the taps are the answer,
              speaking is the shortcut (doc 04 law 8). */}
          {voiceInvited && node.type !== "free_voice" && (
            <AdaptiveVoiceAnswer
              lang={lang}
              sessionId={sessionId}
              clarify={clarify}
              busy={busy}
              onAnswer={onVoiceAnswer}
            />
          )}

          {needsSubmit && (
            <div className={s.footer}>
              <div className={s.spacer} />
              <button
                className={`${s.btn} ${s.btnPrimary} ${s.btnBig}`}
                disabled={busy || !canSubmit}
                onClick={submit}
                data-testid="answer-submit"
              >
                {t("submit", lang)} →
              </button>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

/** The question plus its choices, as one utterance. */
function spokenFor(node: KioskNode, lang: KioskLang): string {
  if (node.type === "scale") return `${node.text} ${t("scaleSpoken", lang)}`;
  if (node.options.length === 0) return node.text;
  const join = t("optionsSpokenJoin", lang);
  const labels = node.options.map((option) => option.text);
  const spokenOptions =
    labels.length > 1
      ? `${labels.slice(0, -1).join(", ")}${join}${labels[labels.length - 1]}`
      : labels[0];
  return `${node.text} ${t("optionsSpokenIntro", lang)} ${spokenOptions}`;
}

function ProgressPill({ lang, summary }: { lang: KioskLang; summary: IntakeSummary }) {
  const label = stageLabel(lang, summary);
  if (!label) return null;
  return (
    <span className={s.progressPill} data-testid="progress-pill">
      {label}
    </span>
  );
}

/** Adaptive intake (S-ADAPT.1, doc 11 §2): "answer by voice" on a tap node. It
 *  records the spoken answer, sends it to the box (local Whisper → the answer
 *  interpreter via `/answer`), and shows the server's clarifying question when the
 *  answer was too vague. The taps above it are always available — this is an
 *  addition, never a replacement (doc 04 law 8). Degrades silently: a denied mic
 *  just leaves the patient tapping. */
function AdaptiveVoiceAnswer({
  lang,
  sessionId,
  clarify,
  busy,
  onAnswer,
}: {
  lang: KioskLang;
  sessionId: string;
  clarify: string | null;
  busy: boolean;
  onAnswer: (rawText: string) => void;
}) {
  const [listening, setListening] = useState(false);
  const [transcribing, setTranscribing] = useState(false);
  const stopRef = useRef<(() => void) | null>(null);

  const toggleMic = () => {
    if (listening) {
      stopRef.current?.();
      setListening(false);
      setTranscribing(true);
      return;
    }
    void recordToServer(
      lang,
      {
        onText: (txt) => {
          if (txt.trim()) onAnswer(txt);
        },
        onError: () => setTranscribing(false),
        onDone: () => setTranscribing(false),
      },
      sessionId
    ).then((stop) => {
      if (!stop) return; // mic denied → taps above carry the patient
      stopRef.current = stop;
      setListening(true);
    });
  };

  return (
    <div className={s.adaptiveVoice} data-testid="adaptive-voice">
      <div className={s.adaptiveVoiceLabel}>{t("answerByVoice", lang)}</div>
      <MicButton
        listening={listening}
        label={listening ? t("listening", lang) : t("tapToSpeak", lang)}
        onPress={toggleMic}
        disabled={busy || transcribing}
      />
      <div className={s.avatarStatus}>
        {transcribing
          ? t("transcribing", lang)
          : listening
            ? t("listening", lang)
            : t("orTapAnswer", lang)}
      </div>
      {clarify ? (
        <div className={s.clarify} role="status" lang={lang} data-testid="clarify">
          {clarify}
        </div>
      ) : null}
    </div>
  );
}

/** The one thing the kiosk says about a possible prior file (AR3, plan §1.1).
 *
 *  It is shown to **everyone who gave a phone number or an ID** — not only to
 *  the patients who matched. That is not a softening of the feature; it is the
 *  feature. A line that appears only on a hit turns a public terminal into an
 *  oracle: type a neighbour's ten digits, watch whether the screen changes, and
 *  you have learned that this cancer hospital holds a file on them. The
 *  recognition happens server-side and is disclosed three screens later, behind
 *  a PIN, to a human standing there. */
function ArrivalAck({ lang }: { lang: KioskLang }) {
  const text = tb("arrivalAck", lang);
  return (
    <div className={s.arrivalAck} role="status" data-testid="arrival-ack">
      <span className={s.arrivalAckIcon} aria-hidden="true">
        <Icon name="folder" />
      </span>
      <span lang={lang}>{text}</span>
    </div>
  );
}

function UrgentBanner({ lang }: { lang: KioskLang }) {
  return (
    <div className={s.redFlag}>
      <span className={s.redFlagIcon}>
        <Icon name="alert" />
      </span>
      {t("urgentNote", lang)}
    </div>
  );
}

// -- readback -----------------------------------------------------------------

/** The one clinical question the kiosk asks outside a department's tree.
 *
 *  ## Why it is not a tree node
 *
 *  An allergy is not a department's question. It has to be asked of the ENT
 *  walk-in and the palliative review alike, on the tap-only tier, in every
 *  language, and during an outage. Authored as a node it would need writing into
 *  all eleven trees, where eleven copies drift apart, ten of them get reviewed by
 *  nobody, and the twelfth tree ships without it.
 *
 *  ## Three answers, not two
 *
 *  "I don't know" is offered as loudly as the other two, and it is the reason
 *  this screen can be trusted at all. A patient forced to choose between yes and
 *  no about her own drug history will guess, and a guessed "no" reaches a
 *  prescribing doctor looking exactly like a fact. Tapping it records **nothing**
 *  — the doctor's spine goes on saying nobody has established this, which is both
 *  true and the right instruction to give them.
 *
 *  ## It never says "allergy" on its own
 *
 *  Many patients at this site would not name a drug reaction as an एलर्जी; they
 *  would say a medicine did not suit them, or that they came out in a rash. So
 *  the question is asked the way it gets answered and the examples do the
 *  defining (doc 04 law 7: plain, second person, never clinical to a patient).
 */
function AllergyScreen({
  lang,
  speaking,
  busy,
  say,
  summary,
  asking,
  text,
  onText,
  onAsk,
  onSubmit,
}: {
  lang: KioskLang;
  speaking: boolean;
  busy: boolean;
  say: (t: string) => void;
  summary: IntakeSummary;
  asking: "choice" | "which";
  text: string;
  onText: (text: string) => void;
  onAsk: (asking: "choice" | "which") => void;
  onSubmit: (input: { none_known: boolean; text: string }) => void;
}) {
  const title = asking === "choice" ? t("allergyTitle", lang) : t("allergyWhichTitle", lang);
  const hint = asking === "choice" ? t("allergyHelp", lang) : t("allergyWhichHelp", lang);

  return (
    <Stage
      lang={lang}
      speaking={speaking}
      promptText={title}
      hint={hint}
      onReplay={() => say(title)}
      autoSpeak={title}
      say={say}
      summary={summary}
    >
      {asking === "choice" ? (
        <div className={s.bigChoices} data-testid="allergy-choices">
          <button
            className={s.bigChoice}
            disabled={busy}
            onClick={() => onAsk("which")}
            data-testid="allergy-yes"
          >
            <span className={s.bigChoiceIcon}>
              <Icon name="alert" />
            </span>
            <span className={s.bigChoiceText}>{t("allergyYes", lang)}</span>
          </button>
          <button
            className={s.bigChoice}
            disabled={busy}
            onClick={() => onSubmit({ none_known: true, text: "" })}
            data-testid="allergy-no"
          >
            <span className={s.bigChoiceIcon}>
              <Icon name="ok" />
            </span>
            <span className={s.bigChoiceText}>{t("allergyNo", lang)}</span>
          </button>
          {/* Third, and the same size and treatment as the other two on purpose.
              A patient who does not know must have somewhere to say so that is
              not styled as the lesser answer, or she takes the "no" instead. */}
          <button
            className={s.bigChoice}
            disabled={busy}
            onClick={() => onSubmit({ none_known: false, text: "" })}
            data-testid="allergy-unsure"
          >
            <span className={s.bigChoiceIcon}>
              <Icon name="question" />
            </span>
            <span className={s.bigChoiceText}>{t("allergyUnsure", lang)}</span>
          </button>
        </div>
      ) : (
        <>
          {/* The same voice surface the chief complaint uses, and it has to be:
              the first cut of this screen was a row of text inputs, which asks a
              patient who may not read to type a drug name in Devanagari on a
              tablet. Everything else the kiosk asks in words, it asks by voice.

              What she says is stored **as one statement, unsplit**. "Penicillin
              and sulfa" reaches the doctor as "penicillin and sulfa", because
              splitting it means inventing the boundary between two clinical
              facts — and this module records statements rather than deriving
              conclusions from them. */}
          <VoiceCapture lang={lang} value={text} onChange={onText} busy={busy} />
          <div className={s.footer}>
            <button
              className={`${s.btn} ${s.btnGhost}`}
              disabled={busy}
              onClick={() => onAsk("choice")}
            >
              &larr; {t("back", lang)}
            </button>
            <div className={s.spacer} />
            <button
              className={`${s.btn} ${s.btnPrimary} ${s.btnBig}`}
              disabled={busy || text.trim().length === 0}
              onClick={() => onSubmit({ none_known: false, text })}
              data-testid="allergy-submit"
            >
              {t("next", lang)}
            </button>
          </div>
        </>
      )}
    </Stage>
  );
}

function ReadbackScreen({
  lang,
  readback,
  redFlags,
  speaking,
  busy,
  say,
  onConfirm,
  onEdit,
  summary,
}: {
  lang: KioskLang;
  readback: string;
  redFlags: { id: string; severity: string }[];
  speaking: boolean;
  busy: boolean;
  say: (t: string) => void;
  onConfirm: () => void;
  onEdit: () => void;
  summary: IntakeSummary;
}) {
  useEffect(() => {
    if (readback) say(readback);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [readback]);

  // The server writes the read-back as lines (the questions and answers in the
  // patient's own language); render them as lines rather than one wall of text,
  // because the patient is being asked to check them one by one.
  const lines = readback
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);

  return (
    <div className={s.stage}>
      <SummaryRail lang={lang} summary={summary} speaking={speaking} />
      <div className={s.panel}>
        <div className={s.panelInner}>
          {redFlags.length > 0 ? <UrgentBanner lang={lang} /> : null}
          <div className={s.promptRow}>
            <AudioBar
              playing={speaking}
              label={t("replay", lang)}
              onReplay={() => say(readback)}
            />
            <span className={s.progressPill}>{t("reviewStep", lang)}</span>
          </div>
          <h2 className={s.question} lang={lang}>
            {t("confirmTitle", lang)}
          </h2>
          <div className={s.readback} lang={lang} data-testid="readback">
            {lines.length > 1 ? (
              <ul className={s.readbackList}>
                {lines.map((line, i) => (
                  <li key={i}>{line}</li>
                ))}
              </ul>
            ) : (
              readback
            )}
          </div>
          <div className={s.footer}>
            <button
              className={`${s.btn} ${s.btnGhost} ${s.btnBig}`}
              onClick={onEdit}
              disabled={busy}
            >
              {t("confirmEdit", lang)}
            </button>
            <div className={s.spacer} />
            <button
              className={`${s.btn} ${s.btnPrimary} ${s.btnBig}`}
              onClick={onConfirm}
              disabled={busy}
              data-testid="confirm"
            >
              ✓ {t("confirmYes", lang)}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

// -- token --------------------------------------------------------------------

/** Print once on mount, for a kiosk with a printer bolted to it (doc 23 §7).
 *  Default off: a laptop demo must not pop a print dialog uninvited. */
const PASS_AUTOPRINT = process.env.NEXT_PUBLIC_PASS_AUTOPRINT === "1";

function TokenScreen({
  lang,
  hospital,
  token,
  details,
  complaint,
  answers,
  sessionId,
  onDone,
  say,
}: {
  lang: KioskLang;
  /** The hospital's stored names — printed on the pass in the patient's own
   *  language, so a rename in the admin console reaches the paper and not only
   *  the prescription (AYUR-1). */
  hospital: BundleHospital | null;
  token: ConfirmResult;
  details: PatientDetails;
  complaint: string;
  answers: SummaryAnswer[];
  /** Null when there is no server session to settle — an offline intake. */
  sessionId: string | null;
  onDone: () => void;
  say: (t: string) => void;
}) {
  useEffect(() => {
    const spoken = t("tokenSpoken", lang).replace("{n}", String(token.token_no ?? ""));
    say(spoken);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const passSvg = useRef<SVGSVGElement>(null);
  const autoprinted = useRef(false);
  /** The ESC/POS job, rendered ahead of the button press (§6). Null means the
   *  raster is still running or failed — either way the browser path prints the
   *  same artifact, so a patient is never left with no paper. */
  const [passBytes, setPassBytes] = useState<Uint8Array | null>(null);
  const [rasterSettled, setRasterSettled] = useState(false);
  const [printed, setPrinted] = useState(false);
  /** Frozen at mount: a re-print must be the same piece of paper as the first
   *  print, down to the issue time. */
  const [issuedAt] = useState(() => new Date().toISOString());

  /* The pass prints at a physical paper size, and the rules that make <body>
     80 x 200mm cannot live in a CSS module. This is the hook: the class is on
     while this screen is, so no other printable page in the app inherits a
     thermal-roll page box. */
  useEffect(() => {
    document.body.classList.add("pass-print");
    return () => document.body.classList.remove("pass-print");
  }, []);

  const geometry = useMemo(() => passGeometry(), []);
  const passLayout = useMemo(
    () =>
      layoutPass(
        {
          tokenNo: token.token_no,
          name: details.name,
          age: details.age,
          sex: details.sex,
          phone: details.phone,
          uhcId: details.externalId,
          department: token.department?.name ?? "",
          hospital: hospitalName(hospital, lang),
          issuedAt,
          // The band says *show this at the desk*; the reasons stay off the
          // paper, same rule as the public board (doc 23 §8).
          urgent: token.red_flags.length > 0,
          lang,
          complaint,
          answers,
          sexLabels: {
            male: t("sexMale", lang),
            female: t("sexFemale", lang),
            other: t("sexOther", lang),
          },
        },
        geometry,
        canvasMeasure()
      ),
    [token, details, complaint, answers, lang, hospital, issuedAt, geometry]
  );

  // Rasterise as soon as the pass is on screen, so pressing Print is one local
  // POST and the wait a patient feels is the printer's own feed rate.
  useEffect(() => {
    const svg = passSvg.current;
    if (!svg) return;
    let cancelled = false;
    svgToEscpos(svg, geometry)
      .then((bytes) => {
        if (!cancelled) setPassBytes(bytes);
      })
      .catch(() => {
        // No canvas, no fonts, a browser that refuses the blob — all of them
        // land on the browser print path, which is a working fallback and not
        // an error worth showing a patient.
      })
      .finally(() => {
        if (!cancelled) setRasterSettled(true);
      });
    return () => {
      cancelled = true;
    };
  }, [passLayout, geometry]);

  const print = useCallback(async () => {
    const result = await printPass(passBytes);
    // A browser print counts: paper came out of something.
    if (result !== "skipped") setPrinted(true);
  }, [passBytes]);

  useEffect(() => {
    if (!PASS_AUTOPRINT || autoprinted.current || !rasterSettled) return;
    autoprinted.current = true;
    void print();
  }, [rasterSettled, print]);

  return (
    <div className={s.tokenScreen}>
      <div className={s.tokenMain}>
      {/* The patient's half. It keeps the whole screen when there is no strip,
          and the strip below never takes space from the token numeral — a
          patient reading their number from three metres away is the one job
          this screen has. */}
      <div className={s.tokenPatient}>
        <div className={s.tokenLabel}>{t("tokenTitle", lang)}</div>
        <div className={s.tokenNumber} data-testid="token-number">
          {token.token_no ?? "—"}
        </div>
        {details.name ? (
          <div className={s.tokenName} data-testid="token-name">
            {details.name}
          </div>
        ) : null}
        {token.department ? (
          <div className={s.tokenDept}>{token.department.name}</div>
        ) : null}
        <div className={s.tokenWait}>{t("tokenWait", lang)}</div>
        {token.red_flags.length > 0 ? (
          <div className={s.tokenUrgent}>
            <Icon name="alert" /> {t("urgentNote", lang)}
          </div>
        ) : null}
        <div className={s.tokenActions}>
          <button
            className={`${s.btn} ${s.btnBig} ${s.tokenRestart}`}
            onClick={onDone}
            data-testid="token-done"
          >
            {t("startOver", lang)}
          </button>
        </div>
      </div>

      {/* The pass, at ~55% of life size, so the patient can see the paper
          before it exists. This is the same element the browser prints and the
          same element the rasteriser photographs — there is one artifact here,
          and a preview that disagrees with the paper is not representable. */}
      <div className={s.passPane}>
        <div className={s.passPaneLabel}>{t("passPreview", lang)}</div>
        <div className={s.passPaper}>
          <PassSvg
            ref={passSvg}
            layout={passLayout}
            className={s.passSvg}
            title={t("passPreview", lang)}
            testId="pass-preview"
          />
        </div>
        <button
          className={`${s.btn} ${s.btnBig} ${s.btnGhost} ${s.tokenGhost} ${s.passPrintBtn}`}
          data-testid="token-print"
          onClick={() => void print()}
        >
          <Icon name="printer" /> {t(printed ? "reprintPass" : "printPass", lang)}
        </button>
      </div>
      </div>

      {sessionId && <StaffStrip lang={lang} sessionId={sessionId} say={say} />}
    </div>
  );
}

// -- small helpers ------------------------------------------------------------

function Lock() {
  return (
    <svg className={s.trustLock} viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <rect x="5" y="11" width="14" height="9" rx="2" fill="currentColor" opacity="0.7" />
      <path d="M8 11V8a4 4 0 018 0v3" stroke="currentColor" strokeWidth="2" />
    </svg>
  );
}

function deptIcon(code: string): string {
  const map: Record<string, string> = {
    MEDONC: "iv-drip",
    RADONC: "radiation",
    SURGONC: "scalpel",
    PALL: "hands-holding",
    GENMED: "stethoscope",
    GYNAE: "gynae",
    ENT: "ear",
    PULM: "lungs",
    DERM: "skin",
    // doc 24 §5 — Ayurveda's card. `seeds/hospital.json` names the same icon on
    // the department row; this map is what the kiosk actually draws from, since
    // the chooser payload carries key/name/care_system and not an icon.
    AYUR: "leaf",
  };
  return map[code] ?? "stethoscope";
}
