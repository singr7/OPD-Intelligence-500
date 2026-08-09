// How the three allergy states become words (SESSION-ALLERGY), as pure-logic tests.
//
// Runs in the conformance project: no browser, no server, so these are part of
// `make test` rather than a suite somebody remembers to run. They belong there
// because what they pin is not layout — it is the set of sentences this console
// is allowed to put in front of a prescribing doctor.
//
// Three rules, and every test here is one of them:
//
//   1. the phrase "no known allergies" never appears, in any state;
//   2. "nobody asked" and "asked, told none" never render the same;
//   3. danger tone is spent only on a substance a clinician marked severe.

import { expect, test } from "@playwright/test";

import { needsAttention, spineLine, sourceLabel, substanceText } from "@/app/(doctor)/doctor/_lib/allergies";
import type { AllergyEntry, AllergyView } from "@/app/(doctor)/doctor/_lib/doctor";

function entry(over: Partial<AllergyEntry> = {}): AllergyEntry {
  return {
    id: "a1",
    kind: "substance",
    substance: "penicillin",
    substance_en: "penicillin",
    reaction: null,
    severity: "unknown",
    source: "patient_kiosk",
    stated_at: "2026-08-08T09:14:00Z",
    confirmed_at: null,
    confirmed_by_name: null,
    recorded_by_name: null,
    retracted_at: null,
    retracted_by_name: null,
    retracted_reason: null,
    ...over,
  };
}

function view(over: Partial<AllergyView> = {}): AllergyView {
  return { state: "never_asked", entries: [], none_statement: null, retracted: [], ...over };
}

test.describe("the spine's third slot", () => {
  test("never says 'no known allergies', in any state", () => {
    const states: AllergyView[] = [
      view(),
      view({ state: "none_stated", none_statement: entry({ kind: "none_known", substance: null }) }),
      view({ state: "known", entries: [entry()] }),
    ];
    for (const v of states) {
      const line = spineLine(v);
      const whole = `${line.text} ${line.note}`.toLowerCase();
      expect(whole).not.toContain("no known allerg");
      expect(whole).not.toContain("nka");
    }
  });

  test("'nobody asked' and 'asked, told none' do not render the same", () => {
    const unasked = spineLine(view());
    const told = spineLine(
      view({ state: "none_stated", none_statement: entry({ kind: "none_known", substance: null }) }),
    );

    expect(unasked.text).not.toBe(told.text);
    // The unasked state is an instruction to the doctor, and reads as one.
    expect(unasked.text).toContain("ask the patient");
    // The stated one is an answer somebody gave, and never travels without its
    // source and its date.
    expect(told.text).toBe("none stated");
    expect(told.note).toContain("stated by the patient at intake");
    expect(told.note).toMatch(/\d/);
  });

  test("an unasked patient is an open item, but not a coloured alarm", () => {
    // It is the state every patient starts in. Colouring it would put an amber
    // band on every console all day, which is how amber stops being read — and
    // it made the unknown state louder than a severe allergy. The words carry it.
    expect(needsAttention(view())).toBe(true);
    expect(spineLine(view()).tone).toBe("quiet");
    expect(spineLine(view()).text).toContain("ask the patient");
  });

  test("a substance nobody clinical has seen is amber, not red", () => {
    const line = spineLine(view({ state: "known", entries: [entry()] }));
    expect(line.tone).toBe("attn");
    expect(line.note).toContain("not yet confirmed");
  });

  test("danger is spent only on a severity a clinician set", () => {
    const line = spineLine(
      view({
        state: "known",
        entries: [entry({ severity: "severe", source: "doctor", recorded_by_name: "Dr Rao" })],
      }),
    );
    expect(line.tone).toBe("danger");
    // …and the word rides with the colour, so it survives a monochrome screen,
    // a colour-blind reader, and a photograph of this console.
    expect(line.text).toContain("severe");
  });

  test("severity is never invented for a statement that carries none", () => {
    const line = spineLine(view({ state: "known", entries: [entry()] }));
    expect(line.text).toBe("penicillin");
    expect(line.text).not.toContain("mild");
  });

  test("a confirmed list says so instead of counting nothing", () => {
    const line = spineLine(
      view({ state: "known", entries: [entry({ confirmed_at: "2026-08-08T11:00:00Z" })] }),
    );
    expect(line.note).toBe("confirmed");
  });

  test("every live substance is named, so none can hide behind a count", () => {
    const line = spineLine(
      view({
        state: "known",
        entries: [entry(), entry({ id: "a2", substance: "sulfa", substance_en: "sulfa" })],
      }),
    );
    expect(line.text).toContain("penicillin");
    expect(line.text).toContain("sulfa");
  });

  test("a retracted-only history reads as unasked, not as reassurance", () => {
    // The server already derives this; the line must not soften it back.
    const line = spineLine(view({ retracted: [entry({ retracted_at: "2026-08-08T12:00:00Z" })] }));
    expect(line.text).toContain("ask the patient");
  });
});

test.describe("provenance", () => {
  test("a family member's statement is not folded into the patient's", () => {
    expect(sourceLabel(entry({ source: "caregiver_kiosk" }))).toContain("family");
    expect(sourceLabel(entry({ source: "patient_kiosk" }))).toContain("patient");
  });

  test("a doctor's statement carries their name when there is one", () => {
    expect(sourceLabel(entry({ source: "doctor", recorded_by_name: "Dr Rao" }))).toContain("Dr Rao");
    expect(sourceLabel(entry({ source: "doctor" }))).toContain("a doctor");
  });

  test("the patient's own word is kept, with English beside it", () => {
    expect(substanceText(entry({ substance: "पेनिसिलिन", substance_en: "penicillin" }))).toBe(
      "पेनिसिलिन (penicillin)",
    );
  });

  test("English is not repeated when it is the same word", () => {
    expect(substanceText(entry({ substance: "penicillin", substance_en: "Penicillin" }))).toBe(
      "penicillin",
    );
  });
});
