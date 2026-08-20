"use client";

// The doctor's recorder, shared by the two surfaces that have one.
//
// Extracted in M4, when the ambient note became the second. It is the Session-C
// capture stack unchanged — Web Speech as the fast path, `MediaRecorder` running
// alongside it, a real analyser driving the meter, and a server STT pass as the
// fallback — lifted out of `DictationPanel` so the note surface gets the same
// behaviour rather than a second implementation of it.
//
// **The rule that survived the extraction is the important one: no bars without
// an analyser.** An evenly spaced waveform is a claim that audio is being
// captured, and on a browser that gives us no analyser node that claim would be
// false. `levels` stays empty there and the caller shows an elapsed timer, which
// is simply true. Both callers are built on that distinction, so it lives here
// rather than in either of them.
//
// The `endpoint` parameter is the one thing the two callers differ on, and it is
// not cosmetic: `/dictation/stt` and `/notes/stt` are the same implementation
// (`app/routes/_stt.py`) metered under different purposes, because dividing
// dictation spend by the count of signed dictations gives a nonsense number if
// observations are billed into it.

import { useCallback, useEffect, useRef, useState } from "react";
import { API_BASE, AuthError } from "@/app/_lib/queue";

/** How many past samples the meter shows. Each bar is a real reading. */
export const METER_BARS = 28;

// Chrome ships this prefixed; Firefox does not ship it at all. Typed here rather
// than pulled from a DOM lib because it is not in the standard one.
type SpeechRecognitionLike = {
  lang: string;
  continuous: boolean;
  interimResults: boolean;
  start(): void;
  stop(): void;
  onresult: ((e: { results: ArrayLike<ArrayLike<{ transcript: string }>> }) => void) | null;
  onerror: (() => void) | null;
  onend: (() => void) | null;
};

type SpeechCtor = new () => SpeechRecognitionLike;

function speechCtor(): SpeechCtor | null {
  if (typeof window === "undefined") return null;
  const w = window as unknown as {
    SpeechRecognition?: SpeechCtor;
    webkitSpeechRecognition?: SpeechCtor;
  };
  return w.SpeechRecognition ?? w.webkitSpeechRecognition ?? null;
}

/** The accuracy pass behind Web Speech — and the only path on a browser without it. */
export async function transcribeAudio(
  token: string,
  endpoint: string,
  blob: Blob,
  seconds: number,
): Promise<{ text: string; provider: string; uncertain: boolean }> {
  const form = new FormData();
  form.append("file", blob, "consult.webm");
  form.append("lang", "en");
  form.append("duration_seconds", String(Math.max(0, Math.round(seconds))));
  const res = await fetch(`${API_BASE}${endpoint}`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}` },
    body: form,
  });
  if (res.status === 401) throw new AuthError();
  if (!res.ok) throw new Error("Speech recognition is unavailable — type the note instead.");
  return res.json();
}

export type VoiceCapture = {
  recording: boolean;
  /** Seconds since the recording started. True even with no analyser. */
  elapsed: number;
  /** Real RMS readings, newest last. **Empty means no analyser** — draw nothing. */
  levels: number[];
  /** True while the server STT pass is running. */
  transcribing: boolean;
  start: () => Promise<void>;
  stop: () => void;
};

export function useVoiceCapture({
  token,
  endpoint,
  /** Called with text from Web Speech as it arrives, and once from the server
   *  pass if Web Speech produced nothing. */
  onTranscript,
  onError,
  /** The transcript as the caller currently holds it. Read at stop time to
   *  decide whether the server pass is worth paying for. */
  currentTranscript,
}: {
  token: string;
  endpoint: string;
  onTranscript: (text: string) => void;
  onError: (message: string) => void;
  currentTranscript: string;
}): VoiceCapture {
  const [recording, setRecording] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const [levels, setLevels] = useState<number[]>([]);
  const [transcribing, setTranscribing] = useState(false);
  const recorderRef = useRef<MediaRecorder | null>(null);
  const speechRef = useRef<SpeechRecognitionLike | null>(null);
  const startedAt = useRef<number>(0);
  const audioRef = useRef<{ ctx: AudioContext; raf: number } | null>(null);
  // Read inside `onstop`, which closes over the value it was created with.
  const transcriptRef = useRef(currentTranscript);
  transcriptRef.current = currentTranscript;

  const stopMeter = useCallback(() => {
    if (!audioRef.current) return;
    cancelAnimationFrame(audioRef.current.raf);
    void audioRef.current.ctx.close().catch(() => {});
    audioRef.current = null;
  }, []);

  const stop = useCallback(() => {
    speechRef.current?.stop();
    speechRef.current = null;
    recorderRef.current?.stop();
    recorderRef.current = null;
    stopMeter();
    setLevels([]);
    setRecording(false);
  }, [stopMeter]);

  /** The meter, driven by the real stream. No stream, no bars. */
  const startMeter = useCallback((stream: MediaStream) => {
    const Ctx =
      typeof window !== "undefined"
        ? (window.AudioContext ??
          (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext)
        : null;
    if (!Ctx) return;
    try {
      const ctx = new Ctx();
      const analyser = ctx.createAnalyser();
      analyser.fftSize = 256;
      ctx.createMediaStreamSource(stream).connect(analyser);
      const buf = new Uint8Array(analyser.fftSize);
      let last = 0;
      const tick = (now: number) => {
        analyser.getByteTimeDomainData(buf);
        // RMS around the 128 midpoint: how loud the room actually is, not a
        // shape chosen in CSS.
        let sum = 0;
        for (let i = 0; i < buf.length; i += 1) {
          const v = (buf[i] - 128) / 128;
          sum += v * v;
        }
        const level = Math.min(1, Math.sqrt(sum / buf.length) * 3.2);
        if (now - last > 70) {
          last = now;
          setLevels((prev) => [...prev, level].slice(-METER_BARS));
        }
        if (audioRef.current) audioRef.current.raf = requestAnimationFrame(tick);
      };
      audioRef.current = { ctx, raf: requestAnimationFrame(tick) };
    } catch {
      // No analyser is a state, not a failure: the timer and the indicator still
      // tell the truth, and inventing bars would not.
    }
  }, []);

  const start = useCallback(async () => {
    startedAt.current = Date.now();
    setElapsed(0);
    setLevels([]);

    // Web Speech is the fast path: text appears as the doctor talks. It is also
    // the one that ships their voice to a cloud recogniser, so the recording
    // below runs alongside it — on a V-OSS box the server pass is local Whisper
    // and strictly better, and it is the only path at all in Firefox.
    const Ctor = speechCtor();
    if (Ctor) {
      const rec = new Ctor();
      rec.lang = "en-IN";
      rec.continuous = true;
      rec.interimResults = false;
      rec.onresult = (e) => {
        let text = "";
        for (let i = 0; i < e.results.length; i += 1) text += `${e.results[i][0].transcript} `;
        onTranscript(text.trim());
      };
      rec.onerror = () => onError("Live transcription stopped — the recording is still running.");
      // Deliberately NOT `setRecording(false)`. Web Speech ends on its own — on
      // error, and spontaneously after a pause even with `continuous = true` —
      // while the MediaRecorder below is still capturing. Letting the recogniser's
      // lifecycle drive the recording state flipped the button to "stopped" mid
      // dictation, directly contradicting the message above it, and lost the
      // doctor the rest of the note. `stop()` owns this state; the recorder is
      // the recording.
      speechRef.current = rec;
      rec.start();
    }

    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const chunks: Blob[] = [];
      const recorder = new MediaRecorder(stream);
      recorder.ondataavailable = (e) => e.data.size && chunks.push(e.data);
      recorder.onstop = async () => {
        stream.getTracks().forEach((t) => t.stop());
        const seconds = (Date.now() - startedAt.current) / 1000;
        const blob = new Blob(chunks, { type: recorder.mimeType || "audio/webm" });
        // Only fall back to the server when Web Speech produced nothing: no
        // point paying for a second transcription of the same audio.
        if (!Ctor || !transcriptRef.current.trim()) {
          setTranscribing(true);
          try {
            const out = await transcribeAudio(token, endpoint, blob, seconds);
            onTranscript(out.text);
            if (out.uncertain) onError("That recording was hard to hear — please read it through.");
          } catch (err) {
            onError(err instanceof Error ? err.message : "Could not transcribe that.");
          } finally {
            setTranscribing(false);
          }
        }
      };
      recorderRef.current = recorder;
      recorder.start();
      startMeter(stream);
      setRecording(true);
    } catch {
      if (!Ctor) onError("No microphone available — type the note instead.");
      else setRecording(true);
    }
  }, [token, endpoint, onTranscript, onError, startMeter]);

  // The honest half of the recording state: a timer that is simply true, and is
  // the whole of it on a browser that gives us no analyser.
  useEffect(() => {
    if (!recording) return;
    const id = setInterval(
      () => setElapsed(Math.floor((Date.now() - startedAt.current) / 1000)),
      250,
    );
    return () => clearInterval(id);
  }, [recording]);

  useEffect(() => () => stop(), [stop]);

  return { recording, elapsed, levels, transcribing, start, stop };
}

export function clock(seconds: number): string {
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return `${m}:${String(s).padStart(2, "0")}`;
}
