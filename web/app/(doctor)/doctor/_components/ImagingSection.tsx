"use client";

// The imaging studies on the Reports tab (M3, plan §2).
//
// Its single job: tell the doctor what scans exist, let them open one in the
// viewer, and never let an empty list read as "this patient has never been
// scanned" unless that is what the PACS actually said.
//
// ## Why this is a section of Reports and not a seventh tab
//
// `WorkTabs.tsx` opens with "Four working tabs. Not five, not seven." That has
// already stretched twice — Reports graduated in MRD2, Research in M5 — and
// each time the argument was that the module had a surface of its own with its
// own failure states. Imaging does not. It is a list of five-word rows and a
// button that opens somebody else's viewer in a popup.
//
// And it belongs *here* rather than anywhere else: the Reports tab is already
// "what is on file about this patient from outside this consult". A doctor
// asking "what investigations has this patient had" should find the scanned
// histopath report and the CT in one place, not two tabs apart. Scanned paper
// first, because that is what the coordinator photographed this morning and
// what has computed flags on it; imaging second, because opening it leaves the
// console entirely.
//
// So the tab row stays at six, and the spine keeps its five slots — the plan's
// `Images (n)` slot (§2.1) is served by extending the existing Reports line
// rather than taking a sixth. `ContextSpine.tsx`'s header says a sixth should
// be refused, and nothing here is a number a doctor could act on without
// opening it, which is the test that slot has to pass.
//
// ## The deliberate aesthetic risk (doc 04 §5)
//
// **Nothing.** This surface takes none, on purpose, and it is the first in the
// build to say so. It is a list of rows that hands off to a product somebody
// else designed; a flourish here would be decoration on a doorway. The one
// place it spends any care is the four empty states, which is where the module
// actually lives.

import type { ImagingLookup, Study } from "@/app/_lib/imaging";
import { reportUrl } from "@/app/_lib/imaging";

/** What each state says, in the doctor's language.
 *
 *  These four strings are the module. `unreachable` especially: a doctor who
 *  reads "no imaging on file" stops looking, and the whole point of keeping the
 *  states apart on the server is that this line can be different. */
function emptyLine(lookup: ImagingLookup): string {
  switch (lookup.state) {
    case "disabled":
      return "Imaging lookup is switched off on this installation. Nothing was checked.";
    case "no_uhc_id":
      return "No UHC ID on file for this patient, so their scans cannot be looked up. The desk can add one.";
    case "unreachable":
      return "The imaging server could not be reached, so this is not a statement that there are no scans — it is a statement that we could not ask.";
    case "ok":
      return "No scans on file for this patient at the imaging centre.";
  }
}

export function ImagingSection({
  visitId,
  lookup,
  loading,
}: {
  visitId: string;
  lookup: ImagingLookup;
  loading: boolean;
}) {
  const empty = lookup.studies.length === 0;

  return (
    <section className="img-sec" data-testid="imaging-section">
      <header className="img-head">
        <h3>Imaging</h3>
        {/* The AE title is here rather than in an error message because the
            person who needs it is debugging a *missing* study, which looks
            like success. Muted, and absent when unconfigured. */}
        {lookup.aet && <span className="img-aet">{lookup.aet}</span>}
      </header>

      {loading ? (
        // Never "none" while we do not know — the spine's rule, and the same
        // reason: a line saying "no scans" for the half-second before the fetch
        // lands is a clinical fact the screen had not checked.
        <p className="img-empty" data-testid="imaging-loading">
          Checking the imaging centre…
        </p>
      ) : empty ? (
        <p
          className={`img-empty ${lookup.state === "unreachable" ? "warn" : ""}`}
          data-testid={`imaging-empty-${lookup.state}`}
        >
          {emptyLine(lookup)}
        </p>
      ) : (
        <ul className="img-list" data-testid="imaging-list">
          {lookup.studies.map((study) => (
            <StudyRow key={study.study_uid} visitId={visitId} study={study} />
          ))}
        </ul>
      )}
    </section>
  );
}

function StudyRow({ visitId, study }: { visitId: string; study: Study }) {
  return (
    <li className="img-row" data-testid="imaging-study">
      <div className="img-when">
        {/* An absent date says so. Defaulting it to today would sort a scan
            from 2019 to the top of a doctor's attention. */}
        {study.study_date ?? <span className="img-nodate">date not recorded</span>}
      </div>

      <div className="img-what">
        <span className="img-mod">{study.modality || "—"}</span>
        {study.description && <span className="img-desc">{study.description}</span>}
        {study.series_count != null && (
          <span className="img-series">
            {study.series_count} series
          </span>
        )}
      </div>

      <div className="img-act">
        {/* The one handoff. `noopener` is not optional: the viewer is a
            different product on a different origin, and a popup that keeps a
            handle on this window can navigate the console it came from. */}
        {study.viewer_url ? (
          <a
            className="img-open"
            href={study.viewer_url}
            target="_blank"
            rel="noopener noreferrer"
            data-testid="imaging-open"
          >
            Open viewer
          </a>
        ) : (
          <span className="img-noviewer" data-testid="imaging-no-viewer">
            no viewer configured
          </span>
        )}

        {/* Always offered, never promised. QIDO does not say whether a study
            has a report, and asking every study at list time would be a fetch
            per row for a question most doctors will not ask. So the link is
            live and the backend answers "not reported yet" with a 404 the
            browser renders — which is honest, if plain. */}
        <a
          className="img-report"
          href={reportUrl(visitId, study.study_uid)}
          target="_blank"
          rel="noopener noreferrer"
          data-testid="imaging-report"
        >
          Report
        </a>
      </div>
    </li>
  );
}
