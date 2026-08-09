// The drift gate between the two care-system mappings (doc 24 §2, SESSION-AYUR-0).
//
// `app/_lib/careSystem.ts` is a second implementation of what a system of
// medicine *means*, for the same reason `_lib/tree/walker.ts` is a second
// implementation of the walker: the kiosk draws department cards with the API
// unreachable, and the doctor console decides which sections exist before a
// second request lands. A divergence here is silent — a console that hides the
// cycle sparkline while the server still writes cycle events into the note is a
// doctor who cannot see what they just dictated, and no error anywhere.
//
// So this suite does not test the TS mapping against my understanding of the
// Python one. It replays the golden export of the real Python mapping
// (backend/app/care_system_fixtures.py) and demands identical values, identical
// field names, and identical refusals. `make care-system-fixtures` regenerates;
// `make test` diffs.
//
// The last describe block is the other half of doc 24 §2, and the one that
// decays without a test: **no component may branch on the care system.** It
// greps `app/` for a comparison against a member and fails if it finds one. If
// no capability flag fits what a screen needs, the fix is a new flag in both
// mappings — not a comparison here.
//
// Pure logic — no browser, no server. Runs in the `conformance` project.

import { expect, test } from "@playwright/test";
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";

import {
  CARE_SYSTEMS,
  CareSystemError,
  capabilitiesFor,
  careSystemOf,
  fromPayload,
} from "@/app/_lib/careSystem";
import type { CapabilitiesPayload, CareSystem, CareSystemCapabilities } from "@/app/_lib/careSystem";

type Fixture = {
  version: number;
  field_names: Record<string, string>;
  systems: string[];
  capabilities: Record<string, Record<string, boolean | string>>;
  coerced: { value: string | null; expected: string }[];
  refused: { value: string; reason: string }[];
};

const fixture: Fixture = JSON.parse(
  readFileSync(join(__dirname, "fixtures", "care-system-conformance.json"), "utf8"),
);

test.describe("care-system conformance: the TS mapping matches the Python one", () => {
  test("the fixture is the format this suite reads", () => {
    // A bumped version with an unchanged spec means the traces below are being
    // read with the wrong shape — fail loudly rather than pass vacuously.
    expect(fixture.version).toBe(1);
  });

  test("both sides know the same set of systems", () => {
    expect([...CARE_SYSTEMS].sort()).toEqual([...fixture.systems].sort());
  });

  for (const system of Object.keys(fixture.capabilities)) {
    test(`${system}: every flag matches Python`, () => {
      const derived = capabilitiesFor(system) as unknown as Record<string, unknown>;
      const expected = fixture.capabilities[system];

      // Field-by-field rather than one deep-equal on a renamed object, so a
      // failure names the flag that drifted instead of printing two objects.
      for (const [pythonName, value] of Object.entries(expected)) {
        const localName = fixture.field_names[pythonName];
        expect(localName, `no local name recorded for ${pythonName}`).toBeTruthy();
        expect(derived[localName], `${system}.${pythonName}`).toEqual(value);
      }
    });
  }

  test("neither side has a flag the other does not", () => {
    // The direction the loop above cannot catch: a flag added in TypeScript
    // only. It would be read by a component and never set by the server.
    const local = Object.keys(capabilitiesFor("allopathy")).sort();
    const ported = Object.values(fixture.field_names).sort();
    expect(local).toEqual(ported);
  });

  test("the capabilities object carries no care-system value", () => {
    // Doc 24 §2: consumers get flags, never the thing they could branch on.
    const keys = Object.keys(capabilitiesFor("ayurveda"));
    expect(keys).not.toContain("careSystem");
    expect(keys).not.toContain("care_system");
    expect(keys).not.toContain("system");
  });

  test("allopathy is today's behaviour", () => {
    // Written out rather than read from the fixture: this is the row that says
    // every existing department keeps its console exactly as it is.
    expect(capabilitiesFor("allopathy")).toEqual({
      showsCycles: true,
      showsRegimenEvents: true,
      checkinProtocols: true,
      guidelinePack: "nccn",
      formularyScope: "allopathy",
      ayurvedaAssessment: false,
      pathyaApathya: false,
      promptPack: "oncology",
    });
  });

  for (const { value, expected } of fixture.coerced) {
    test(`careSystemOf(${JSON.stringify(value)}) is ${expected}`, () => {
      expect(careSystemOf(value)).toBe(expected as CareSystem);
    });
  }

  test("undefined reads the same as null — a payload that predates doc 24", () => {
    expect(careSystemOf(undefined)).toBe("allopathy");
  });

  for (const { value } of fixture.refused) {
    test(`careSystemOf(${JSON.stringify(value)}) throws, like Python`, () => {
      // The failure mode being bought: quietly rendering an ayurveda clinic's
      // console as an oncology one looks correct on every screen.
      expect(() => careSystemOf(value)).toThrow(CareSystemError);
    });
  }

  test("capabilitiesFor refuses a missing value rather than defaulting", () => {
    // Different from `careSystemOf(null)` on purpose: "the department did not
    // say" is a legitimate authored state, but "the payload had no capabilities
    // in it" is a bug in whoever built the payload.
    expect(() => capabilitiesFor(null)).toThrow(CareSystemError);
    expect(() => capabilitiesFor(undefined)).toThrow(CareSystemError);
  });
});

test.describe("the wire payload adapter", () => {
  test("a server capabilities object becomes the local shape", () => {
    const payload = Object.fromEntries(
      Object.entries(fixture.capabilities.ayurveda),
    ) as unknown as CapabilitiesPayload;
    expect(fromPayload(payload)).toEqual(capabilitiesFor("ayurveda"));
  });

  test("every Python field name is mapped, none dropped", () => {
    // The bug this stops: a flag added server-side and forgotten in `fromPayload`
    // arrives as `undefined`, which is falsy — so the section it gates silently
    // *disappears* instead of erroring.
    const payload = fixture.capabilities.allopathy as unknown as CapabilitiesPayload;
    const adapted = fromPayload(payload) as unknown as Record<string, unknown>;
    for (const localName of Object.values(fixture.field_names)) {
      expect(adapted[localName], `${localName} came through as undefined`).not.toBeUndefined();
    }
  });
});

test.describe("nothing branches on the care system", () => {
  // Doc 24 §2's load-bearing rule, and the mirror of
  // `backend/tests/test_care_system.py::test_only_the_mapping_names_a_care_system_member`.
  //
  // "Adding a third system later must be one enum value, one capabilities row,
  // and content" is true exactly while nothing else compares against a member.
  // The moment `careSystem === "ayurveda"` appears in a component, Unani becomes
  // a sweep of every screen with a clinical consequence for each site missed.

  const APP = join(__dirname, "..", "app");

  /** Where naming a member is the point rather than a leak. */
  const ALLOWED = new Set(["_lib/careSystem.ts"]);

  function sources(dir: string, prefix = ""): string[] {
    const out: string[] = [];
    for (const name of readdirSync(dir)) {
      if (name === "node_modules" || name.startsWith(".")) continue;
      const full = join(dir, name);
      const rel = prefix ? `${prefix}/${name}` : name;
      if (statSync(full).isDirectory()) out.push(...sources(full, rel));
      else if (/\.tsx?$/.test(rel)) out.push(rel);
    }
    return out;
  }

  test("no component compares against a care-system value", () => {
    // Comparisons only. A component may perfectly well *hold* the string (to
    // style a card, to fill a selector) — what it may not do is decide
    // behaviour from it.
    const comparison = /[=!]==?\s*["'](allopathy|ayurveda)["']|["'](allopathy|ayurveda)["']\s*[=!]==?/;
    const offenders = sources(APP)
      .filter((rel) => !ALLOWED.has(rel))
      .filter((rel) => comparison.test(readFileSync(join(APP, rel), "utf8")));

    expect(
      offenders,
      "these files branch on the system of medicine instead of reading a capability " +
        "flag from capabilitiesFor(); if no flag fits, add one to both mappings",
    ).toEqual([]);
  });

  test("the allowlist is not carrying a dead entry", () => {
    for (const rel of ALLOWED) {
      expect(readFileSync(join(APP, rel), "utf8")).toContain("ayurveda");
    }
  });
});
