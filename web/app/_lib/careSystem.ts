// The browser's half of the care-system derivation (doc 24 §2).
//
// The port of `backend/app/care_system.py`, and it is a port for the same reason
// `_lib/tree/walker.ts` is one: the kiosk renders department cards with the API
// unreachable, and the doctor console decides which sections exist before any
// second request comes back. A capability the two sides disagree about is a
// console that hides the cycle sparkline while the server still writes cycle
// events into the note — so the two are held together by a golden fixture
// (`backend/app/care_system_fixtures.py` → `e2e/care-system.spec.ts`) in exactly
// the way the two walkers are, and `make test` diffs it.
//
// The rule this module exists to enforce, from doc 24 §2:
//
//     const caps = capabilitiesFor(dept.care_system);
//     if (caps.showsCycles) …                    // yes
//     if (dept.care_system === "ayurveda") …     // no
//
// Components take flags. They never import `CareSystem` and never compare
// against a member — that is what makes adding Unani later one enum value, one
// row and content, rather than a grep across every screen with a clinical
// consequence for each site missed. `e2e/care-system.spec.ts` fails if any
// component under `app/` names a member.

/** The stored value. The wire carries these strings verbatim. */
export type CareSystem = "allopathy" | "ayurveda";

export const CARE_SYSTEMS: readonly CareSystem[] = ["allopathy", "ayurveda"] as const;

/**
 * What one system of medicine switches on.
 *
 * Deliberately does **not** carry the `CareSystem` value: a component holding
 * both would branch on the enum the first time a flag did not quite fit. Where
 * the raw value is genuinely the data — the admin selector, a kiosk card's
 * styling — it travels beside this object, not inside it.
 *
 * Field names are camelCase here and snake_case in Python; the fixture maps
 * between them in one place so the rest of the app reads like TypeScript.
 */
export type CareSystemCapabilities = {
  /** The chemo cycle sparkline, and anything presuming numbered cycles. */
  showsCycles: boolean;
  /** Regimen/cycle lines in the dictation panel's event list. */
  showsRegimenEvents: boolean;
  /** Whether the S17 check-in protocol surfaces appear at all. */
  checkinProtocols: boolean;
  /** Research-tab framing only — a label and a prompt's register. */
  guidelinePack: "nccn" | "ayush";
  /** Which formulary entries `validate_meds` may call known. */
  formularyScope: "allopathy" | "ayurveda";
  /** The prakriti / agni / nidana note fields (doc 24 §6.1). */
  ayurvedaAssessment: boolean;
  /** The pathya–apathya section of the Rx composer and print (doc 24 §6.2). */
  pathyaApathya: boolean;
  /** Which system-prompt variants the server dispatches to (doc 24 §6.4). */
  promptPack: "oncology" | "ayurveda";
};

// The whole mapping. Adding a system of medicine is one entry here and one row
// in the Python file — if it ever takes more, something downstream has started
// branching on the value instead of reading a flag.
const CAPABILITIES: Readonly<Record<CareSystem, CareSystemCapabilities>> = {
  // Today's behaviour, bit-for-bit. Every existing department is this one.
  allopathy: {
    showsCycles: true,
    showsRegimenEvents: true,
    checkinProtocols: true,
    guidelinePack: "nccn",
    formularyScope: "allopathy",
    ayurvedaAssessment: false,
    pathyaApathya: false,
    promptPack: "oncology",
  },
  ayurveda: {
    showsCycles: false,
    showsRegimenEvents: false,
    checkinProtocols: false,
    guidelinePack: "ayush",
    formularyScope: "ayurveda",
    ayurvedaAssessment: true,
    pathyaApathya: true,
    promptPack: "ayurveda",
  },
};

export class CareSystemError extends Error {}

/**
 * One value off the wire as a `CareSystem`.
 *
 * `null`/`undefined` means the payload predates doc 24 and is allopathy — the
 * same reading the seed loader and the column default take. An unknown *string*
 * throws instead: "ayurved" and "AYURVEDA" are mistakes, and quietly rendering
 * an ayurveda clinic's console as an oncology one would look correct on screen.
 */
export function careSystemOf(value: string | null | undefined): CareSystem {
  if (value == null) return "allopathy";
  if ((CARE_SYSTEMS as readonly string[]).includes(value)) return value as CareSystem;
  throw new CareSystemError(
    `unknown system of medicine ${JSON.stringify(value)}; expected one of ${CARE_SYSTEMS.join(", ")}`,
  );
}

/** The capability row for one system of medicine. */
export function capabilitiesFor(value: string | null | undefined): CareSystemCapabilities {
  if (value == null) {
    throw new CareSystemError("no system of medicine given");
  }
  return CAPABILITIES[careSystemOf(value)];
}

/**
 * The capabilities object as the doctor payloads carry it (snake_case), widened
 * into the camelCase shape the components read.
 *
 * The server sends flags rather than the raw value precisely so a console
 * cannot branch on the system of medicine; this is the one adapter that turns
 * that payload into the local type, so a field added on one side and forgotten
 * on the other fails to compile here rather than rendering as `undefined`
 * (falsy — which would silently *hide* a section) somewhere downstream.
 */
export type CapabilitiesPayload = {
  shows_cycles: boolean;
  shows_regimen_events: boolean;
  checkin_protocols: boolean;
  guideline_pack: "nccn" | "ayush";
  formulary_scope: "allopathy" | "ayurveda";
  ayurveda_assessment: boolean;
  pathya_apathya: boolean;
  prompt_pack: "oncology" | "ayurveda";
};

export function fromPayload(payload: CapabilitiesPayload): CareSystemCapabilities {
  return {
    showsCycles: payload.shows_cycles,
    showsRegimenEvents: payload.shows_regimen_events,
    checkinProtocols: payload.checkin_protocols,
    guidelinePack: payload.guideline_pack,
    formularyScope: payload.formulary_scope,
    ayurvedaAssessment: payload.ayurveda_assessment,
    pathyaApathya: payload.pathya_apathya,
    promptPack: payload.prompt_pack,
  };
}
