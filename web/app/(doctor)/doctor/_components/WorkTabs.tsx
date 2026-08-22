"use client";

// Four working tabs (plan §4.3). Not five, not seven.
//
// The tab row sits under the context spine, which never unmounts — so switching
// tabs changes the work area and nothing else. Identity, diagnosis, allergies
// and red flags stay on screen through all four.
//
// The trailing "Coming soon" item (plan §4.4) is the alternative to shipping
// four dead tabs. Imaging, Lab reports, AI Research and NCCN Guidelines are all
// unbuilt; rendering them as tabs would make four of eight tabs dead and force
// the two carrying real clinical content to compete with placeholders. So they
// are one muted disclosure at the end of the row, with these constraints:
//
//   * feature-flagged, so a pilot build can hide the entry entirely
//   * no mock clinical content behind any of them, at any fidelity
//   * not focusable as navigation and no tab hover-highlight, so a placeholder
//     is never mistaken for a broken feature
//   * **Lab reports carries a caveat**: this system already has a `lab_requeue`
//     queue state, so labs are a live workflow and a doctor will tap it
//     expecting results. Its line has to say both what it will do and that it is
//     not live yet — it is the one of the four with a real chance of being read
//     as broken rather than absent.
//
// Dictation is deliberately absent from that list. This repo ships it, and a
// card advertising it as upcoming teaches doctors not to look for it.
//
// **Session MRD2 graduated the fifth tab.** Reports is what the coordinator's
// phone has been filling since M1, and it is the tab this disclosure was built
// to hand over to. Note what did *not* happen: "Lab reports" did not disappear
// from the list, it was rewritten. Paper results a patient carries in are now
// live, and results delivered electronically from the hospital lab still are
// not — collapsing those two into one graduated feature would tell a doctor
// their lab orders come back here, and they do not.
//
// **Session M5 graduated the sixth**, the same way: "AI Research" leaves the
// list and becomes Research. "NCCN Guidelines" stays on it and was rewritten
// for the same reason "Lab reports" was. The assistant answers from a model's
// own knowledge, dated and uncited; looking a guideline up against a retrieved
// source is a different feature with a different failure mode (plan §8.4 —
// "a wrong-but-cited answer needs real retrieval engineering"). Letting the
// graduated tab absorb that line would tell a doctor their NCCN lookup is live.
//
// Six is the ceiling this row can carry, and the next module to graduate should
// have to argue for a seventh rather than assume it.
//
// **Session M3 argued it and lost, deliberately.** Imaging shipped and left this
// list, but it did *not* become a seventh tab: it is a section of Reports, which
// is already "what is on file about this patient from outside this consult". A
// doctor asking what investigations a patient has had should find the scanned
// histopath and the CT in one place. See `ImagingSection.tsx` for the argument
// in full. The row therefore still ends at six, and the disclosure is down to
// two entries — which is the direction it should keep going.

import { useState } from "react";

export type WorkTab =
  | "overview"
  | "answers"
  | "history"
  | "reports"
  | "research"
  | "consult";

const TABS: { id: WorkTab; label: string }[] = [
  { id: "overview", label: "Overview" },
  { id: "answers", label: "Intake answers" },
  { id: "history", label: "History" },
  { id: "reports", label: "Reports" },
  { id: "research", label: "Research" },
  { id: "consult", label: "Consult" },
];

/** What the guideline lookup would be looking *up* (doc 24 §6).
 *
 *  Framing, and only framing: the tab is not live in either system, and what the
 *  Research tab is allowed to say does not change with the label. It is here
 *  because "NCCN Guidelines" on an ayurveda console is not a coming-soon
 *  promise, it is a promise about the wrong body — and a doctor reading it would
 *  reasonably conclude this console was built for somebody else.
 *
 *  Keyed by `capabilities.guidelinePack`, so a third system of medicine is one
 *  more row here and nothing else. */
const GUIDELINE_LABEL: Record<string, string> = {
  nccn: "NCCN Guidelines",
  ayush: "AYUSH Guidelines",
};

const soonFor = (guidelinePack: string): { name: string; line: string }[] => [
  {
    name: "Lab reports ordered here",
    line: "Results delivered electronically from the hospital lab. Not live yet — lab work ordered here still comes back on paper, and that paper is scanned into the Reports tab at the desk.",
  },
  {
    name: GUIDELINE_LABEL[guidelinePack] ?? GUIDELINE_LABEL.nccn,
    line: "Guideline reference looked up against the published source, with citations. Not live yet — the Research tab answers from a model's own knowledge and says where that is thin, which is a different thing from a guideline lookup.",
  },
];

/** Off hides the entry entirely — a pilot build should be able to show a doctor
 *  four tabs and nothing else. Defaults on outside production pilots. */
const SHOW_SOON = process.env.NEXT_PUBLIC_DOCTOR_COMING_SOON !== "0";

export function WorkTabs({
  tab,
  onTab,
  answerCount,
  noteSigned,
  reportCount,
  reportsUnverified,
  guidelinePack,
}: {
  tab: WorkTab;
  onTab: (tab: WorkTab) => void;
  answerCount: number;
  noteSigned: boolean;
  /** Documents on file. Zero renders no badge — an empty badge and a missing
   *  one would be the same pixel, and the tab itself is the honest statement. */
  reportCount: number;
  /** Any reading nobody has checked against the pages. Amber, not red: red on
   *  this console belongs to the deterministic red-flag lane. */
  reportsUnverified: boolean;
  /** Which guideline body the not-yet-live lookup names. Framing only — see
   *  `GUIDELINE_LABEL`. A string from the capabilities row, never the system of
   *  medicine's own name. */
  guidelinePack: string;
}) {
  const [soonOpen, setSoonOpen] = useState(false);
  const soon = soonFor(guidelinePack);

  return (
    <div className="worktabs-wrap">
      <div className="worktabs" role="tablist" aria-label="Patient record">
        {TABS.map((t) => (
          <button
            key={t.id}
            role="tab"
            aria-selected={tab === t.id}
            className={`wtab ${tab === t.id ? "is-open" : ""}`}
            onClick={() => onTab(t.id)}
            data-testid={`tab-${t.id}`}
          >
            {t.label}
            {t.id === "answers" && answerCount > 0 && <span className="wtab-n">{answerCount}</span>}
            {t.id === "reports" && reportCount > 0 && (
              <span
                className={`wtab-n ${reportsUnverified ? "unread" : ""}`}
                data-testid="reports-badge"
              >
                {reportCount}
              </span>
            )}
            {t.id === "consult" && noteSigned && (
              <span className="wtab-signed" title="This visit has a signed note">
                signed
              </span>
            )}
          </button>
        ))}

        {SHOW_SOON && (
          <button
            className="wtab-soon"
            aria-expanded={soonOpen}
            onClick={() => setSoonOpen((v) => !v)}
            data-testid="coming-soon"
          >
            <span aria-hidden="true">⌄</span> Coming soon ({soon.length})
          </button>
        )}
      </div>

      {SHOW_SOON && soonOpen && (
        <div className="soon-panel" data-testid="coming-soon-panel">
          {soon.map((item) => (
            <p key={item.name}>
              <strong>{item.name}</strong> — {item.line}
            </p>
          ))}
        </div>
      )}
    </div>
  );
}
