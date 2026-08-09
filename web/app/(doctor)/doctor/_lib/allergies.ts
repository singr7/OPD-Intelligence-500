// How the three allergy states become words on a doctor's screen.
//
// Pure functions, no React, so the rules that matter here can be tested without
// a browser — and they are, in `e2e/allergy-line.spec.ts`, which runs in
// `make test` alongside the walker conformance gate.
//
// The rules, in the order they matter:
//
//  1. **The phrase "no known allergies" never appears.** Not here, not anywhere.
//     It is the summary of a chart review nobody in this system has performed,
//     and a doctor who reads it prescribes on it. What this record can say is
//     who said what, and when — so `none_stated` always renders with its source
//     and its date attached.
//  2. **"Nobody asked" and "asked, told none" never share a rendering.** They
//     are different clinical situations: one is an instruction to the doctor,
//     the other is a (weak) fact about the patient.
//  3. **Only a live, named substance is danger.** The spine's red is reserved
//     for the deterministic red-flag lane; an allergy line is amber at its
//     loudest unless a clinician has actually marked a reaction severe. Styling
//     an unverified kiosk statement as an alarm is how a console teaches doctors
//     to scroll past alarms.

import type { AllergyEntry, AllergyView } from "./doctor";

/** How loudly the line is drawn. Never colour alone — every caller pairs this
 *  with a word, per doc 04's rule and plan §4.2's red-flag treatment. */
export type AllergyTone = "quiet" | "attn" | "danger";

export type AllergyLine = {
  tone: AllergyTone;
  /** The line itself, minus the "ALLERGIES" label the spine draws. */
  text: string;
  /** Provenance, rendered smaller and after `text`. Empty when there is none to
   *  state — which is only ever the `never_asked` case. */
  note: string;
};

/** Who said it, in the words the doctor should weigh it by. */
export function sourceLabel(entry: AllergyEntry): string {
  switch (entry.source) {
    case "doctor":
      return entry.recorded_by_name ? `recorded by ${entry.recorded_by_name}` : "recorded by a doctor";
    case "caregiver_kiosk":
      // Deliberately not folded into "the patient": her son saying it is
      // weaker evidence than her saying it, and the doctor should see which.
      return "stated by the family at intake";
    case "patient_kiosk":
    default:
      return "stated by the patient at intake";
  }
}

/** `8 Aug` — short, because it sits inside a line that must not wrap. */
export function shortDate(iso: string): string {
  const at = new Date(iso);
  if (Number.isNaN(at.getTime())) return "";
  return at.toLocaleDateString("en-IN", { day: "numeric", month: "short" });
}

/** One substance, as the spine names it: the patient's own word, with English
 *  beside it only when it adds something. */
export function substanceText(entry: AllergyEntry): string {
  const said = (entry.substance ?? "").trim();
  const english = (entry.substance_en ?? "").trim();
  if (!said) return english;
  if (!english || english.toLowerCase() === said.toLowerCase()) return said;
  return `${said} (${english})`;
}

/** The spine's third slot, in one line that never wraps to two.
 *
 *  Severity is rendered as a *word* next to the substance rather than as colour
 *  on it, so it survives a monochrome screen, a colour-blind reader, and a
 *  photograph of the console in a WhatsApp group. */
export function spineLine(view: AllergyView): AllergyLine {
  if (view.state === "known") {
    const names = view.entries.map((entry) => {
      const name = substanceText(entry);
      return entry.severity === "severe" ? `${name} — severe` : name;
    });
    const unconfirmed = view.entries.filter((entry) => entry.confirmed_at === null).length;
    return {
      tone: view.entries.some((entry) => entry.severity === "severe") ? "danger" : "attn",
      text: names.join(" · "),
      // Said once, for the whole line, rather than per substance: the doctor
      // needs to know whether anyone clinical has been through these, and a
      // per-item badge would make the line wrap on the second drug.
      note: unconfirmed > 0 ? `${unconfirmed} not yet confirmed by a doctor` : "confirmed",
    };
  }

  if (view.state === "none_stated" && view.none_statement) {
    const entry = view.none_statement;
    return {
      tone: "quiet",
      // "None stated", not "no known allergies". The difference is the whole
      // reason this module exists: one reports an answer somebody gave, the
      // other asserts a conclusion nobody reached.
      text: "none stated",
      note: `${sourceLabel(entry)} · ${shortDate(entry.stated_at)}`,
    };
  }

  // `never_asked`, including the case where every statement on file has been
  // withdrawn — which is not reassurance, and reads as an instruction because
  // that is what it is.
  //
  // **Quiet, not amber**, and the screenshots are what settled it. Every patient
  // in the pilot is in this state until the kiosks have asked them, so an amber
  // band here is an amber band on every console all day — which is how amber
  // stops meaning anything by Thursday. Worse, it rendered *louder* than a
  // severe allergy did, so the state where we know nothing outshouted the state
  // where we know something dangerous. The instruction carries itself in words.
  return { tone: "quiet", text: "not established — ask the patient", note: "" };
}

/** Whether there is something here for the doctor to do — an unasked patient or
 *  a statement no clinician has been through. Deliberately *not* wired to the
 *  spine's colour (see `spineLine`): it is true for the state every patient
 *  starts in, so painting it would paint every console. It exists for callers
 *  that want to count or sort by it. */
export function needsAttention(view: AllergyView): boolean {
  if (view.state === "known") return view.entries.some((entry) => entry.confirmed_at === null);
  return view.state === "never_asked";
}
