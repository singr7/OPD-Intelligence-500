"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import s from "./kiosk.module.css";
import { KIOSK_LANGS, KioskLang, t } from "./_lib/i18n";
import {
  ApiError,
  ConfirmResult,
  Dept,
  KioskNode,
  kioskApi,
} from "./_lib/api";
import { cancelSpeech, listen, speak, sttSupported } from "./_lib/speech";
import { Icon } from "./_lib/icons";
import { AssistantAvatar } from "./_components/AssistantAvatar";
import { AudioBar } from "./_components/AudioBar";
import { OptionCard } from "./_components/OptionCard";
import { FacesScale } from "./_components/FacesScale";
import { Stepper } from "./_components/Stepper";
import { BodyMap } from "./_components/BodyMap";
import { ProgressDots } from "./_components/ProgressDots";
import { MicButton } from "./_components/MicButton";

type Screen =
  | "welcome"
  | "caregiver"
  | "complaint"
  | "chooser"
  | "question"
  | "readback"
  | "token";

// Idle protects patient privacy on a shared terminal (doc 04 law 12 / doc 03 §1a).
const IDLE_PROMPT_MS = 60_000;
const IDLE_BLUR_MS = 90_000;

export function KioskApp() {
  const [lang, setLang] = useState<KioskLang>("hi");
  const [screen, setScreen] = useState<Screen>("welcome");
  const [caregiver, setCaregiver] = useState(false);
  const [complaint, setComplaint] = useState("");

  const [sessionId, setSessionId] = useState<string | null>(null);
  const [node, setNode] = useState<KioskNode | null>(null);
  const [department, setDepartment] = useState<Dept | null>(null);
  const [depts, setDepts] = useState<Dept[]>([]);
  const [step, setStep] = useState(1);
  const [redFlags, setRedFlags] = useState<{ id: string; severity: string }[]>([]);
  const [readback, setReadback] = useState("");
  const [token, setToken] = useState<ConfirmResult | null>(null);

  const [speaking, setSpeaking] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [idle, setIdle] = useState(false);

  // --- audio: speak the current prompt whenever it changes -----------------
  const say = useCallback(
    (text: string) => {
      cancelSpeech();
      if (!text) return;
      setSpeaking(true);
      void speak(text, lang).then(() => setSpeaking(false));
    },
    [lang]
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
    return () => {
      window.clearTimeout(idleTimers.current.prompt);
      window.clearTimeout(idleTimers.current.blur);
    };
  }, [kick]);

  const reset = useCallback(() => {
    cancelSpeech();
    setScreen("welcome");
    setCaregiver(false);
    setComplaint("");
    setSessionId(null);
    setNode(null);
    setDepartment(null);
    setDepts([]);
    setStep(1);
    setRedFlags([]);
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
      setError(lang === "hi" ? "कुछ गड़बड़ हुई — फिर कोशिश कीजिए।" : "Something went wrong — please try again.");
      console.error(msg);
    } finally {
      setBusy(false);
    }
  };

  const applyStart = (
    res: Awaited<ReturnType<typeof kioskApi.start>>
  ) => {
    if (res.status === "needs_department") {
      setDepts(res.departments);
      setScreen("chooser");
      return;
    }
    setSessionId(res.session_id);
    setDepartment(res.department);
    setNode(res.node);
    setStep(1);
    if (res.complete || !res.node) {
      void finish(res.session_id);
    } else {
      setScreen("question");
    }
  };

  const start = (deptKey?: string) =>
    withBusy(async () => {
      const res = await kioskApi.start({
        lang,
        chief_complaint: complaint || "—",
        caregiver,
        dept_key: deptKey,
      });
      applyStart(res);
    });

  const submitAnswer = (value: unknown, rawText?: string) =>
    withBusy(async () => {
      if (!sessionId || !node) return;
      const res = await kioskApi.answer(sessionId, {
        node_id: node.id,
        value,
        raw_text: rawText ?? null,
      });
      if (!res.ok) {
        setError(t("sttFailed", lang));
        return;
      }
      // Flags are recomputed by the walker on every save (STATE.md invariant:
      // never accumulated) — take the server's current set, don't merge.
      setRedFlags(res.red_flags);
      if (res.complete || !res.node) {
        await finish(sessionId);
      } else {
        setNode(res.node);
        setStep((n) => n + 1);
      }
    });

  const finish = (sid: string) =>
    withBusy(async () => {
      const res = await kioskApi.finish(sid);
      setReadback(res.readback);
      setRedFlags(res.red_flags);
      setScreen("readback");
    });

  const confirm = () =>
    withBusy(async () => {
      if (!sessionId) return;
      const res = await kioskApi.confirm(sessionId);
      setToken(res);
      setScreen("token");
    });

  // --- render --------------------------------------------------------------
  return (
    <main
      className={s.shell}
      data-screen={screen}
      data-node-type={node?.type ?? ""}
      onPointerDown={kick}
      onKeyDown={kick}
    >
      <TopBar
        lang={lang}
        onLang={(l) => {
          setLang(l);
          cancelSpeech();
        }}
      />

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
          status={t("caregiverHelp", lang)}
          promptText={t("caregiverTitle", lang)}
          onReplay={() => say(t("caregiverTitle", lang))}
          autoSpeak={t("caregiverTitle", lang)}
          say={say}
        >
          <p className={s.lead}>{t("caregiverHelp", lang)}</p>
          <div className={s.bigChoices}>
            <button
              className={s.bigChoice}
              onClick={() => {
                setCaregiver(false);
                setScreen("complaint");
              }}
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
                setScreen("complaint");
              }}
            >
              <span className={s.bigChoiceIcon}>
                <Icon name="hands-holding" />
              </span>
              <span className={s.bigChoiceText}>{t("itsForSomeone", lang)}</span>
            </button>
          </div>
        </Stage>
      )}

      {screen === "complaint" && (
        <Stage
          lang={lang}
          speaking={speaking}
          status={t("ccHint", lang)}
          promptText={t("ccTitle", lang)}
          onReplay={() => say(t("ccTitle", lang))}
          autoSpeak={t("ccTitle", lang)}
          say={say}
        >
          <VoiceCapture
            lang={lang}
            value={complaint}
            onChange={setComplaint}
            busy={busy}
          />
          <div className={s.footer}>
            <button className={`${s.btn} ${s.btnGhost}`} onClick={reset}>
              {t("back", lang)}
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
          status=""
          promptText={t("chooseDept", lang)}
          onReplay={() => say(t("chooseDept", lang))}
          autoSpeak={t("chooseDept", lang)}
          say={say}
        >
          <div className={s.deptGrid}>
            {depts.map((d) => (
              <OptionCard
                key={d.key}
                text={d.name}
                icon={deptIcon(d.key)}
                onSelect={() => start(d.key)}
              />
            ))}
          </div>
        </Stage>
      )}

      {screen === "question" && node && (
        <QuestionScreen
          key={node.id}
          lang={lang}
          node={node}
          step={step}
          speaking={speaking}
          busy={busy}
          say={say}
          onSubmit={submitAnswer}
          redFlags={redFlags}
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
        />
      )}

      {screen === "token" && token && (
        <TokenScreen lang={lang} token={token} onDone={reset} say={say} />
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

// -- shared shell pieces ------------------------------------------------------

function TopBar({
  lang,
  onLang,
}: {
  lang: KioskLang;
  onLang: (l: KioskLang) => void;
}) {
  return (
    <div className={s.topbar}>
      <div className={s.brand}>
        <div className={s.brandMark}>ध</div>
        <div className={s.brandName}>{t("hospital", lang)}</div>
      </div>
      <div className={s.langBar}>
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

// A stage with the breathing avatar, the audio bar, the question, and children.
function Stage({
  lang,
  speaking,
  status,
  promptText,
  onReplay,
  autoSpeak,
  say,
  progress,
  children,
}: {
  lang: KioskLang;
  speaking: boolean;
  status: string;
  promptText: string;
  onReplay: () => void;
  autoSpeak: string;
  say: (t: string) => void;
  progress?: React.ReactNode;
  children: React.ReactNode;
}) {
  useEffect(() => {
    if (autoSpeak) say(autoSpeak);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [autoSpeak]);

  return (
    <div className={s.stage}>
      <AssistantAvatar speaking={speaking} status={status} />
      <div className={s.panel}>
        {progress}
        <AudioBar playing={speaking} label={t("replay", lang)} onReplay={onReplay} />
        <h2 className={s.question} lang={lang}>
          {promptText}
        </h2>
        {children}
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
  const [listening, setListening] = useState(false);
  const [showType, setShowType] = useState(!sttSupported());
  const stopRef = useRef<(() => void) | null>(null);

  const toggleMic = () => {
    if (listening) {
      stopRef.current?.();
      setListening(false);
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
      />
      <div className={s.avatarStatus}>
        {listening ? t("listening", lang) : t("ccHint", lang)}
      </div>
      <div className={`${s.transcript} ${value ? "" : s.transcriptPlaceholder}`}>
        {value ? `${t("youSaid", lang)} ${value}` : t("tapToSpeak", lang)}
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
          {t("typeInstead", lang)}
        </button>
      )}
    </div>
  );
}

// -- question screen ----------------------------------------------------------

function QuestionScreen({
  lang,
  node,
  step,
  speaking,
  busy,
  say,
  onSubmit,
  redFlags,
}: {
  lang: KioskLang;
  node: KioskNode;
  step: number;
  speaking: boolean;
  busy: boolean;
  say: (t: string) => void;
  onSubmit: (value: unknown, rawText?: string) => void;
  redFlags: { id: string; severity: string }[];
}) {
  const [multi, setMulti] = useState<string[]>([]);
  const [scale, setScale] = useState<number | null>(null);
  const [num, setNum] = useState<number>(node.min ?? 0);
  const [text, setText] = useState("");

  useEffect(() => {
    say(node.text);
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

  return (
    <div className={s.stage}>
      <AssistantAvatar speaking={speaking} />
      <div className={s.panel}>
        <ProgressDots current={step} total={8} ofLabel={t("ofCount", lang)} />
        {redFlags.length > 0 ? <UrgentBanner lang={lang} /> : null}
        <AudioBar
          playing={speaking}
          label={t("replay", lang)}
          onReplay={() => say(node.text)}
        />
        <h2 className={s.question} lang={lang}>
          {node.text}
        </h2>

        {node.type === "single" && (
          <div
            className={`${s.options} ${
              node.options.length <= 3 ? s.optionsFew : ""
            }`}
          >
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
          <div className={s.options}>
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

function ReadbackScreen({
  lang,
  readback,
  redFlags,
  speaking,
  busy,
  say,
  onConfirm,
  onEdit,
}: {
  lang: KioskLang;
  readback: string;
  redFlags: { id: string; severity: string }[];
  speaking: boolean;
  busy: boolean;
  say: (t: string) => void;
  onConfirm: () => void;
  onEdit: () => void;
}) {
  useEffect(() => {
    if (readback) say(readback);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [readback]);

  return (
    <div className={s.stage}>
      <AssistantAvatar speaking={speaking} />
      <div className={s.panel}>
        {redFlags.length > 0 ? <UrgentBanner lang={lang} /> : null}
        <AudioBar
          playing={speaking}
          label={t("replay", lang)}
          onReplay={() => say(readback)}
        />
        <h2 className={s.question} lang={lang}>
          {t("confirmTitle", lang)}
        </h2>
        <div className={s.readback} lang={lang}>
          {readback}
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
            {t("confirmYes", lang)} ✓
          </button>
        </div>
      </div>
    </div>
  );
}

// -- token --------------------------------------------------------------------

function TokenScreen({
  lang,
  token,
  onDone,
  say,
}: {
  lang: KioskLang;
  token: ConfirmResult;
  onDone: () => void;
  say: (t: string) => void;
}) {
  useEffect(() => {
    const spoken =
      lang === "hi"
        ? `आपका टोकन नंबर ${token.token_no ?? ""}`
        : `Your token number is ${token.token_no ?? ""}`;
    say(spoken);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div className={s.tokenScreen}>
      <div className={s.tokenLabel}>{t("tokenTitle", lang)}</div>
      <div className={s.tokenNumber}>{token.token_no ?? "—"}</div>
      {token.department ? (
        <div className={s.tokenDept}>{token.department.name}</div>
      ) : null}
      <div className={s.tokenWait}>{t("tokenWait", lang)}</div>
      {token.red_flags.length > 0 ? (
        <div className={s.tokenUrgent}>
          <Icon name="alert" /> {t("urgentNote", lang)}
        </div>
      ) : null}
      <button className={`${s.btn} ${s.btnBig} ${s.tokenRestart}`} onClick={onDone}>
        {t("startOver", lang)}
      </button>
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
  };
  return map[code] ?? "stethoscope";
}
