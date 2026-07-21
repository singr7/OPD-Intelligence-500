// Audio-first plumbing (doc 04 §2 law 1: audio is the primary channel). V3's kiosk
// speaks with the browser's Web Speech (SpeechSynthesis) and listens with
// SpeechRecognition — the offline/zero-AI floor. node.audio (pre-recorded packs,
// S7/S21) takes precedence when present; until then the browser voice fills the gap.
//
// Everything here degrades silently: a kiosk in a browser without Web Speech
// still completes the intake by tapping (law 8 / doc 03 §1a tap-to-type fallback).

import { API_BASE } from "./api";

const BCP47: Record<string, string> = { hi: "hi-IN", en: "en-IN" };

export function speechSupported(): boolean {
  return typeof window !== "undefined" && "speechSynthesis" in window;
}

export function sttSupported(): boolean {
  if (typeof window === "undefined") return false;
  return "SpeechRecognition" in window || "webkitSpeechRecognition" in window;
}

/**
 * Server-STT mode (doc 08 / V-OSS): when on, the chief-complaint mic records the
 * clip and posts it to `/kiosk/stt` — which runs local Whisper on the box — so the
 * audio never leaves the premises. Off (default), the kiosk uses the browser's
 * Web Speech recognition, which in Chrome ships audio to a cloud recogniser.
 * Build-time flag: `NEXT_PUBLIC_KIOSK_SERVER_STT=1`.
 */
export function serverSttEnabled(): boolean {
  const v = (process.env.NEXT_PUBLIC_KIOSK_SERVER_STT ?? "").toLowerCase();
  return v === "1" || v === "true";
}

export function recorderSupported(): boolean {
  return (
    typeof window !== "undefined" &&
    typeof MediaRecorder !== "undefined" &&
    !!navigator.mediaDevices?.getUserMedia
  );
}

/**
 * Record from the mic until the returned stop() is called, then POST the clip to
 * `/kiosk/stt` and hand back the transcript via `onText`. Resolves to a stop()
 * handle, or null if recording is unsupported / the mic is denied — in which case
 * the caller falls to tap-to-type (doc 04 law 8). Everything degrades silently.
 */
export async function recordToServer(
  lang: string,
  handlers: {
    onText: (text: string) => void;
    onError?: (err: string) => void;
    onDone?: () => void;
  }
): Promise<(() => void) | null> {
  if (!recorderSupported()) return null;
  let stream: MediaStream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  } catch {
    handlers.onError?.("mic-denied");
    return null;
  }

  const chunks: BlobPart[] = [];
  const startedAt = Date.now();
  const rec = new MediaRecorder(stream);
  rec.ondataavailable = (e) => {
    if (e.data.size) chunks.push(e.data);
  };
  rec.onstop = async () => {
    stream.getTracks().forEach((tr) => tr.stop());
    const blob = new Blob(chunks, { type: rec.mimeType || "audio/webm" });
    const seconds = ((Date.now() - startedAt) / 1000).toFixed(2);
    try {
      const fd = new FormData();
      fd.append("file", blob, "clip.webm");
      fd.append("lang", lang);
      fd.append("duration_seconds", seconds);
      const res = await fetch(`${API_BASE}/kiosk/stt`, { method: "POST", body: fd });
      if (!res.ok) throw new Error(String(res.status));
      const body = (await res.json()) as { text?: string };
      handlers.onText((body.text ?? "").trim());
    } catch {
      handlers.onError?.("stt-failed");
    } finally {
      handlers.onDone?.();
    }
  };
  rec.start();
  return () => rec.stop();
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
