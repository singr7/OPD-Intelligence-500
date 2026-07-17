// Audio-first plumbing (doc 04 §2 law 1: audio is the primary channel). V3's kiosk
// speaks with the browser's Web Speech (SpeechSynthesis) and listens with
// SpeechRecognition — the offline/zero-AI floor. node.audio (pre-recorded packs,
// S7/S21) takes precedence when present; until then the browser voice fills the gap.
//
// Everything here degrades silently: a kiosk in a browser without Web Speech
// still completes the intake by tapping (law 8 / doc 03 §1a tap-to-type fallback).

const BCP47: Record<string, string> = { hi: "hi-IN", en: "en-IN" };

export function speechSupported(): boolean {
  return typeof window !== "undefined" && "speechSynthesis" in window;
}

export function sttSupported(): boolean {
  if (typeof window === "undefined") return false;
  return "SpeechRecognition" in window || "webkitSpeechRecognition" in window;
}

let _current: SpeechSynthesisUtterance | null = null;

/** Speak `text`; resolves when playback ends (or immediately if unsupported). */
export function speak(text: string, lang: string): Promise<void> {
  if (!speechSupported() || !text) return Promise.resolve();
  cancelSpeech();
  return new Promise((resolve) => {
    const u = new SpeechSynthesisUtterance(text);
    u.lang = BCP47[lang] ?? lang;
    u.rate = 0.95; // a touch slow — elderly, stressed listeners (doc 04 law 7)
    u.onend = () => resolve();
    u.onerror = () => resolve();
    _current = u;
    window.speechSynthesis.speak(u);
  });
}

export function cancelSpeech(): void {
  if (speechSupported()) window.speechSynthesis.cancel();
  _current = null;
}

type Recognition = {
  lang: string;
  interimResults: boolean;
  continuous: boolean;
  onresult: ((e: unknown) => void) | null;
  onerror: ((e: unknown) => void) | null;
  onend: (() => void) | null;
  start: () => void;
  stop: () => void;
};

/**
 * Start dictation. Calls `onText` with the best transcript as it grows and once
 * more when final; `onDone` when the mic closes. Returns a stop() handle. If the
 * browser has no recognition, returns null and the caller shows tap-to-type.
 */
export function listen(
  lang: string,
  handlers: {
    onText: (text: string, isFinal: boolean) => void;
    onError?: (err: string) => void;
    onDone?: () => void;
  }
): (() => void) | null {
  if (typeof window === "undefined") return null;
  const Ctor =
    (window as unknown as { SpeechRecognition?: new () => Recognition })
      .SpeechRecognition ??
    (window as unknown as { webkitSpeechRecognition?: new () => Recognition })
      .webkitSpeechRecognition;
  if (!Ctor) return null;

  const rec = new Ctor();
  rec.lang = BCP47[lang] ?? lang;
  rec.interimResults = true;
  rec.continuous = false;
  rec.onresult = (e: unknown) => {
    const ev = e as {
      results: ArrayLike<ArrayLike<{ transcript: string }> & { isFinal: boolean }>;
    };
    let text = "";
    let isFinal = false;
    for (let i = 0; i < ev.results.length; i++) {
      text += ev.results[i][0].transcript;
      if (ev.results[i].isFinal) isFinal = true;
    }
    handlers.onText(text.trim(), isFinal);
  };
  rec.onerror = (e: unknown) =>
    handlers.onError?.((e as { error?: string }).error ?? "speech-error");
  rec.onend = () => handlers.onDone?.();
  rec.start();
  return () => rec.stop();
}
