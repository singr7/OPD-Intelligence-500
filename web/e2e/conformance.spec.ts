// The drift gate between the two walkers (S7).
//
// The kiosk walks the tree itself when the API is unreachable (doc 01 §5), so
// `_lib/tree/` is a second implementation of the clinical logic STATE.md says no
// model and no vendor may decide. A silent divergence here is a patient who is
// urgent on the server and routine on the kiosk — during exactly the outage the
// offline mode exists for.
//
// So this suite does not test the TS walker against my understanding of the
// Python one. It replays golden traces recorded from the real Python walker over
// the real seeded trees (backend/app/tree_fixtures.py) and demands identical
// results at every step. `make tree-fixtures` regenerates; `make test` diffs, so
// changing walker.py or rules.py without changing the port fails the build.
//
// Pure logic — no browser, no server. It runs in Playwright only because that is
// the runner this project already has.

import { expect, test } from "@playwright/test";
import { readFileSync } from "node:fs";
import { join } from "node:path";

import { AnswerError, Walk } from "../app/(kiosk)/kiosk/_lib/tree/walker";
import type { AnswersJson } from "../app/(kiosk)/kiosk/_lib/tree/walker";
import type { Tree } from "../app/(kiosk)/kiosk/_lib/tree/types";

type Snapshot = {
  current: string | null;
  complete: boolean;
  path: string[];
  values: Record<string, unknown>;
  answers: string[];
  red_flags: { id: string; severity: string; source_node: string | null }[];
  priority: string;
};

type Fixture = {
  version: number;
  seed: number;
  cases: {
    ref: string;
    tree: Tree;
    walks: {
      initial: Snapshot;
      steps: { node_id: string; value: unknown; after: Snapshot }[];
      amendment: { node_id: string; value: unknown; after: Snapshot } | null;
      answers_json: AnswersJson;
    }[];
    rejections: {
      answers: AnswersJson;
      node_id: string;
      value: unknown;
      reason?: string;
    }[];
  }[];
};

/** The `at` the Python fixture used. The walker never reads it; passing the same
 *  value keeps the recorded answers comparable. */
const FIXED_AT = "2026-07-17T09:00:00+00:00";

const FIXTURE_VERSION = 1;

const fixture: Fixture = JSON.parse(
  readFileSync(join(__dirname, "fixtures", "walk-conformance.json"), "utf8")
);

/** The TS mirror of `_snapshot` in backend/app/tree_fixtures.py. */
function snapshot(walk: Walk): Snapshot {
  const current = walk.current;
  return {
    current: current ? current.id : null,
    complete: walk.isComplete,
    path: walk.path(),
    values: walk.values(),
    answers: [...walk.answers.keys()].sort(),
    red_flags: walk.redFlags().map((hit) => ({
      id: hit.id,
      severity: hit.severity,
      source_node: hit.source_node,
    })),
    priority: walk.priority(),
  };
}

function stripAt(answers: AnswersJson): Record<string, unknown> {
  return Object.fromEntries(
    Object.entries(answers).map(([nodeId, answer]) => {
      const { at: _at, ...rest } = answer;
      return [nodeId, rest];
    })
  );
}

test.describe("walker conformance: the TS port matches the Python walker", () => {
  test("the fixture is the format this suite understands", () => {
    // A fixture written by a newer generator could pass vacuously against an
    // older reader. Fail loudly instead.
    expect(fixture.version).toBe(FIXTURE_VERSION);
    expect(fixture.cases.length).toBeGreaterThan(0);
  });

  for (const testCase of fixture.cases) {
    test.describe(testCase.ref, () => {
      test("every recorded walk replays identically", () => {
        expect(testCase.walks.length).toBeGreaterThan(0);

        for (const [index, recorded] of testCase.walks.entries()) {
          const walk = new Walk(testCase.tree);

          // A fresh walk must already agree about the first question.
          expect(snapshot(walk), `${testCase.ref} walk#${index} initial`).toEqual(
            recorded.initial
          );

          for (const step of recorded.steps) {
            walk.save(step.node_id, step.value, { at: FIXED_AT });
            expect(
              snapshot(walk),
              `${testCase.ref} walk#${index} after ${step.node_id}=${JSON.stringify(step.value)}`
            ).toEqual(step.after);
          }

          if (recorded.amendment) {
            // The branch moves and the stranded answers must vanish — with their
            // red flags.
            walk.save(recorded.amendment.node_id, recorded.amendment.value, {
              at: FIXED_AT,
            });
            expect(
              snapshot(walk),
              `${testCase.ref} walk#${index} after amending ${recorded.amendment.node_id}`
            ).toEqual(recorded.amendment.after);
          }

          expect(
            stripAt(walk.toJSON()),
            `${testCase.ref} walk#${index} answers JSONB`
          ).toEqual(stripAt(recorded.answers_json as AnswersJson));
        }
      });

      test("a walk rebuilt from stored answers lands in the same place", () => {
        // This is the tier-downgrade / reconnect property: position is derived
        // from the answers, so rehydrating from IndexedDB must not move it.
        for (const [index, recorded] of testCase.walks.entries()) {
          const walk = new Walk(testCase.tree);
          for (const step of recorded.steps) {
            walk.save(step.node_id, step.value, { at: FIXED_AT });
          }
          const rebuilt = Walk.fromJSON(testCase.tree, walk.toJSON());
          expect(snapshot(rebuilt), `${testCase.ref} walk#${index} rehydrated`).toEqual(
            snapshot(walk)
          );
        }
      });

      test("every answer the server refuses, the kiosk refuses", () => {
        expect(testCase.rejections.length).toBeGreaterThan(0);

        for (const rejection of testCase.rejections) {
          const walk = Walk.fromJSON(testCase.tree, rejection.answers);
          expect(
            () => walk.save(rejection.node_id, rejection.value),
            `${testCase.ref}: ${rejection.node_id} must reject ${JSON.stringify(
              rejection.value
            )}${rejection.reason ? ` (${rejection.reason})` : ""}`
          ).toThrow(AnswerError);
        }
      });
    });
  }
});
